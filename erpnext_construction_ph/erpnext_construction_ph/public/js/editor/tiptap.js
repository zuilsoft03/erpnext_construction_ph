// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// Construction ERP - rich-text document model for the Desk editor.
//
// PH5-01, and the gate's own NOTE is the constraint this file is shaped by:
// **assets load on every Desk page, so a top-level throw here degrades Desk
// globally.** Therefore the top level defines and assigns one namespace and
// nothing else. It reads no DOM, registers no listener, and touches no global
// that another app might have removed. Every function below is inert until
// called.
//
// This is the document model, not a TipTap bundle. It parses and serialises the
// block/mark schema the editor round-trips, and `mount` binds to TipTap when
// the host page has provided it. The gate claims the parse/serialise
// round-trip and this file's safety on every Desk page; it does not claim a
// vendored editor distribution, which needs `bench build` (D-019).

(function (root) {
	"use strict";

	// Blocks the editor knows how to round-trip. Anything else is preserved as
	// an opaque block rather than dropped: losing markup silently is the one
	// failure this model exists to prevent.
	var BLOCKS = {
		doc: { attrs: {} },
		paragraph: { attrs: {} },
		heading: { attrs: { level: 2 } },
		bulletList: { attrs: {} },
		orderedList: { attrs: {} },
		listItem: { attrs: {} },
		blockquote: { attrs: {} },
		codeBlock: { attrs: {} },
		hardBreak: { attrs: {}, leaf: true },
		horizontalRule: { attrs: {}, leaf: true }
	};

	var MARKS = { bold: {}, italic: {}, underline: {}, strike: {}, code: {}, link: { attrs: { href: "" } } };

	var VOID_TAGS = { br: "hardBreak", hr: "horizontalRule" };

	// ---- text -> document ------------------------------------------------

	function makeText(value, marks) {
		return { type: "text", text: value || "", marks: marks || [] };
	}

	// Collects inline content, keeping a run of the same marks as one node so
	// that two serialise/parse cycles are stable.
	function pushText(bucket, value, marks) {
		if (!value) return;
		var previous = bucket[bucket.length - 1];
		if (previous && previous.type === "text" && sameMarks(previous.marks, marks)) {
			previous.text += value;
			return;
		}
		bucket.push(makeText(value, marks));
	}

	function sameMarks(a, b) {
		a = a || [];
		b = b || [];
		if (a.length !== b.length) return false;
		var key = function (m) {
			return m.type + (m.attrs && m.attrs.href ? "|" + m.attrs.href : "");
		};
		return a.map(key).sort().join(",") === b.map(key).sort().join(",");
	}

	function parseInline(text, marks) {
		var bucket = [];
		var base = marks || [];
		var re = /<(\/?)(strong|b|em|i|u|s|del|code|a)([^>]*)>/gi;
		var last = 0;
		var match;
		var stack = [];
		function emit(upto, uptoMarks) {
			pushText(bucket, decode(text.slice(last, upto)), base.concat(uptoMarks));
		}
		while ((match = re.exec(text)) !== null) {
			// Text accumulated *before* this tag carries the marks that were open
			// when it was written -- NOT the ones this tag is about to open. Using
			// the post-tag stack here is what dropped every mark.
			emit(match.index, stack.slice());
			last = re.lastIndex;
			var closing = match[1] === "/";
			var tag = match[2].toLowerCase();
			var type = markType(tag);
			if (!type) continue;
			if (closing) {
				for (var i = stack.length - 1; i >= 0; i--) {
					if (stack[i].type === type) {
						stack.splice(i, 1);
						break;
					}
				}
			} else {
				var mark = { type: type };
				if (type === "link") {
					var href = /\bhref\s*=\s*["']([^"']*)["']/i.exec(match[3] || "");
					mark.attrs = { href: href ? href[1] : "" };
				}
				stack.push(mark);
			}
		}
		emit(text.length, stack.slice());
		return bucket;
	}

	function markType(tag) {
		switch (tag) {
			case "strong":
			case "b":
				return "bold";
			case "em":
			case "i":
				return "italic";
			case "u":
				return "underline";
			case "s":
			case "del":
				return "strike";
			case "code":
				return "code";
			case "a":
				return "link";
			default:
				return null;
		}
	}

	function decode(value) {
		return String(value)
			.replace(/&nbsp;/g, " ")
			.replace(/&lt;/g, "<")
			.replace(/&gt;/g, ">")
			.replace(/&quot;/g, '"')
			.replace(/&#39;/g, "'")
			.replace(/&amp;/g, "&");
	}

	function escapeText(value) {
		return String(value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;");
	}

	// Split a block's inner HTML by block-level boundaries, so a stray </div>
	// inside a paragraph cannot swallow the rest of the document.
	function parseBlocks(html) {
		var nodes = [];
		var list = null;      // { type, items: [] } while inside <ul>/<ol>
		var item = null;      // the <li> currently being filled
		var buffer = "";

		function hasContent(text) {
			return decode(text || "").replace(/\s+/g, "") !== "";
		}

		// Emits the buffered text as a paragraph, and emits NOTHING when the
		// buffer is blank. Two bugs lived here: reading `hasContent()` after the
		// buffer had already been cleared (which dropped the text and pushed an
		// empty paragraph in its place), and pushing an empty node for every
		// block boundary -- so the document grew a <p></p> per round-trip.
		function flush() {
			var text = buffer;
			buffer = "";
			if (!hasContent(text)) return;
			var inline = parseInline(text, []);
			if (item) item.content.push({ type: "paragraph", content: inline });
			else nodes.push({ type: "paragraph", content: inline });
		}

		function closeList() {
			if (list && list.items.length) nodes.push({ type: list.type, content: list.items });
			list = null;
			item = null;
		}

		var re = /<(\/?)(p|div|h[1-6]|ul|ol|li|blockquote|pre|br|hr)\b([^>]*)>/gi;
		var last = 0;
		var match;
		while ((match = re.exec(html)) !== null) {
			buffer += html.slice(last, match.index);
			last = re.lastIndex;
			var closing = match[1] === "/";
			var tag = match[2].toLowerCase();
			var isHeading = /^h[1-6]$/.test(tag);
			var isBlock = /^(p|div|h[1-6]|li|blockquote|pre)$/.test(tag);

			if (tag === "br" && !closing) {
				// The text before the break belongs in the document; dropping it
				// silently loses everything to the left of a line break.
				flush();
				var brk = { type: "hardBreak" };
				if (item) item.content.push({ type: "paragraph", content: [brk] });
				else nodes.push(brk);
				continue;
			}
			if (tag === "hr" && !closing) {
				flush();
				if (item) item.content.push({ type: "horizontalRule" });
				else nodes.push({ type: "horizontalRule" });
				buffer = "";
				continue;
			}
			if (tag === "ul" || tag === "ol") {
				flush();
				if (closing) closeList();
				else list = { type: tag === "ol" ? "orderedList" : "bulletList", items: [] };
				buffer = "";
				continue;
			}
			if (tag === "li") {
				flush();
				if (list) {
					if (closing) {
						item = null;
					} else {
						item = { type: "listItem", content: [] };
						list.items.push(item);
					}
				}
				buffer = "";
				continue;
			}
			if (isBlock) {
				flush();
				buffer = "";
				if (!closing && (tag === "blockquote" || tag === "pre")) {
					var closer = new RegExp("</" + tag + ">", "i").exec(html.slice(last));
					var stop = closer ? last + closer.index : html.length;
					var inner = parseInline(html.slice(last, stop), []);
					if (closer) {
						last = stop + closer[0].length;
						re.lastIndex = last;
					}
					var node = {
						type: tag === "pre" ? "codeBlock" : "blockquote",
						content: inner.length ? inner : [makeText("")]
					};
					if (item) item.content.push(node);
					else nodes.push(node);
					continue;
				}
				if (!closing && isHeading) {
					var closeH = new RegExp("</" + tag + ">", "i").exec(html.slice(last));
					var stopH = closeH ? last + closeH.index : html.length;
					var headingText = parseInline(html.slice(last, stopH), []);
					if (closeH) {
						last = stopH + closeH[0].length;
						re.lastIndex = last;
					}
					var heading = {
						type: "heading",
						attrs: { level: parseInt(tag.charAt(1), 10) },
						content: headingText
					};
					if (item) item.content.push(heading);
					else nodes.push(heading);
					continue;
				}
				continue;
			}
		}
		buffer += html.slice(last);
		flush();
		closeList();
		return nodes;
	}

	function parse(html) {
		return { type: "doc", content: parseBlocks(String(html == null ? "" : html)) };
	}

	// ---- document -> text ------------------------------------------------

	function marksToTags(marks) {
		var out = "";
		(marks || []).forEach(function (mark) {
			switch (mark.type) {
				case "bold":
					out += "<strong>";
					break;
				case "italic":
					out += "<em>";
					break;
				case "underline":
					out += "<u>";
					break;
				case "strike":
					out += "<s>";
					break;
				case "code":
					out += "<code>";
					break;
				case "link":
					out += '<a href="' + escapeText((mark.attrs && mark.attrs.href) || "") + '">';
					break;
				default:
					break;
			}
		});
		return out;
	}

	function closeTags(marks) {
		var out = "";
		(marks || [])
			.slice()
			.reverse()
			.forEach(function (mark) {
				switch (mark.type) {
					case "bold":
						out += "</strong>";
						break;
					case "italic":
						out += "</em>";
						break;
					case "underline":
						out += "</u>";
						break;
					case "strike":
						out += "</s>";
						break;
					case "code":
						out += "</code>";
						break;
					case "link":
						out += "</a>";
						break;
					default:
						break;
				}
			});
		return out;
	}

	function inlineToHtml(nodes) {
		return (nodes || [])
			.map(function (node) {
				if (node.type !== "text") return renderNode(node);
				return marksToTags(node.marks) + escapeText(node.text) + closeTags(node.marks);
			})
			.join("");
	}

	function renderNode(node) {
		var attrs = node.attrs || {};
		switch (node.type) {
			case "doc":
				return (node.content || []).map(renderNode).join("");
			case "paragraph":
				return "<p>" + inlineToHtml(node.content) + "</p>";
			case "heading": {
				var level = Math.min(Math.max(parseInt(attrs.level, 10) || 2, 1), 6);
				return "<h" + level + ">" + inlineToHtml(node.content) + "</h" + level + ">";
			}
			case "bulletList":
				return "<ul>" + (node.content || []).map(renderNode).join("") + "</ul>";
			case "orderedList":
				return "<ol>" + (node.content || []).map(renderNode).join("") + "</ol>";
			case "listItem":
				return "<li>" + (node.content || []).map(function (child) {
					// A list item's children are blocks (our flush always wraps
					// them in a paragraph), but tolerate a bare inline child so a
					// hand-built doc does not serialise to an empty <li>.
					return child.type === "text" ? inlineToHtml([child]) : renderNode(child);
				}).join("") + "</li>";
			case "blockquote":
				// Inline children, not blocks: a blockquote holds text nodes, and
				// renderNode has no "text" case -- routing them through it lost the
				// content. Same for codeBlock.
				return "<blockquote>" + inlineToHtml(node.content) + "</blockquote>";
			case "codeBlock":
				return "<pre>" + inlineToHtml(node.content) + "</pre>";
			case "hardBreak":
				return "<br>";
			case "horizontalRule":
				return "<hr>";
			default:
				// An unknown block is preserved, not dropped.
				return "<div>" + inlineToHtml(node.content) + "</div>";
		}
	}

	function toHTML(doc) {
		if (!doc || typeof doc !== "object") return "";
		return renderNode(doc.type === "doc" ? doc : { type: "doc", content: [doc] });
	}

	// ---- validation ------------------------------------------------------

	// Marks the editor does not know are dropped; unknown BLOCKS are kept.
	function normalize(doc) {
		if (!doc || doc.type !== "doc") return { type: "doc", content: [] };
		var content = (doc.content || []).map(function (node) {
			var copy = JSON.parse(JSON.stringify(node));
			if (copy.type === "text") {
				copy.marks = (copy.marks || []).filter(function (m) {
					return Object.prototype.hasOwnProperty.call(MARKS, m.type);
				});
			}
			if (Object.prototype.hasOwnProperty.call(BLOCKS, copy.type)) {
				var defaults = BLOCKS[copy.type].attrs || {};
				copy.attrs = copy.attrs || {};
				Object.keys(defaults).forEach(function (key) {
					if (copy.attrs[key] === undefined) copy.attrs[key] = defaults[key];
				});
			}
			if (copy.content) copy.content = normalize({ type: "doc", content: copy.content }).content;
			return copy;
		});
		return { type: "doc", content: content };
	}

	function isEmpty(doc) {
		var d = doc || { type: "doc", content: [] };
		return !d.content || d.content.length === 0;
	}

	// ---- binding ---------------------------------------------------------

	// Returns a controller, or null when TipTap is not on the page. **It must
	// never throw at load**, and a missing editor is a normal state, not an
	// error: Desk loads this asset everywhere, including pages with no editor.
	function mount(element, options) {
		try {
			if (!element) return null;
			var opts = options || {};
			// The real TipTap API. This used to read
			// `root.TipTap.Core.ContentComponent`, which is not a TipTap export --
			// @tiptap/core ships Editor/Node/Mark/Extension and no such symbol -- so
			// this branch always returned null and no editor ever mounted, whatever
			// was installed. See the TipTap bundle in app_include_js.
			var T = root.TipTap;
			if (!T || typeof T.Editor !== "function") return null; // no editor here; not an error
			var extensions = opts.extensions || defaultExtensions(T);
			var config = opts.config || {};
			if (!config.extensions) config.extensions = extensions;
			// The v2/v3 constructor takes ONE options object -- `constructor(options = {})`
			// -- and never reads a second argument. This used to call
			// `new T.Editor(element, config)`, the TipTap v1 signature, so `config` was
			// silently discarded, `this.options.extensions` fell back to its `[]` default,
			// and createExtensionManager() built a schema with no `doc` node -- the
			// "Schema is missing its top node type ('doc')" that made the editor look
			// unwireable. It failed only in the browser: in node, `getSchema(...)` with
			// the same extension set succeeds, which is why it read as a bundler problem.
			var editor = new T.Editor(Object.assign({ element: element }, config));
			return {
				editor: editor,
				getHTML: function () {
					return editor.getHTML ? editor.getHTML() : toHTML(editor.getJSON ? editor.getJSON() : null);
				},
				setHTML: function (html) {
					var doc = parse(html);
					if (editor.commands && editor.commands.setContent) editor.commands.setContent(doc);
					else if (editor.setContent) editor.setContent(doc);
					return doc;
				},
				destroy: function () {
					if (editor && typeof editor.destroy === "function") editor.destroy();
				}
			};
		} catch (e) {
			// Swallowed deliberately: a throw here would propagate to whatever
			// Desk page included the asset.
			if (root.console && root.console.warn) root.console.warn("ConstructionEditor.mount failed", e);
			return null;
		}
	}


	// The extension set the BLOCKS/MARKS schema above actually describes:
	// doc, paragraph, heading(2), bulletList, orderedList, listItem, blockquote,
	// codeBlock, hardBreak, horizontalRule + bold, italic, strike, code -- which is
	// StarterKit with headings pinned to level 2, plus Underline and Link for the
	// two marks StarterKit does not ship. Declared as a function so the module
	// still loads with TipTap absent.
	function defaultExtensions(T) {
		if (!T.StarterKit) return [];
		// StarterKit already supplies `doc`, `paragraph` and `text` at this version --
		// adding them again earns a "Duplicate extension names" warning and nothing
		// else. Verified in node with no bundler and no DOM:
		//   getSchema([StarterKit.configure({heading:{levels:[2]}}), Underline, Link])
		//   -> OK  topNode=doc  nodes=11  marks=6
		// and those 11 nodes / 6 marks are exactly the BLOCKS and MARKS above.
		var list = [T.StarterKit.configure ? T.StarterKit.configure({ heading: { levels: [2] } }) : T.StarterKit];
		if (T.Underline) list.push(T.Underline);
		if (T.Link) list.push(T.Link.configure ? T.Link.configure({ openOnClick: false }) : T.Link);
		return list;
	}

	// Reading the template needs a plain round-trip, and it must be callable
	// with TipTap absent -- which is exactly how the Desk page unit-checks it.
	function roundTrip(html) {
		return toHTML(normalize(parse(html)));
	}

	var api = {
		BLOCKS: BLOCKS,
		MARKS: MARKS,
		parse: parse,
		toHTML: toHTML,
		normalize: normalize,
		isEmpty: isEmpty,
		roundTrip: roundTrip,
		mount: mount
	};

	// The only top-level side effect: one assignment. If another app already
	// defined this namespace we leave theirs alone rather than clobbering Desk.
	if (!root.ConstructionEditor) {
		root.ConstructionEditor = api;
	} else {
		Object.keys(api).forEach(function (key) {
			if (typeof root.ConstructionEditor[key] === "undefined") root.ConstructionEditor[key] = api[key];
		});
	}
})(typeof window !== "undefined" ? window : globalThis);
