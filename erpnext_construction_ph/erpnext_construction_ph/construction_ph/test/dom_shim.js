// A minimal DOM, for testing the BIM workspace's view modules in Node.
//
// IT LIVES HERE, NOT IN public/. Everything under public/ is served to a browser
// on every Desk page load, and a hand-rolled DOM is test scaffolding: shipping it
// costs bytes, invites someone to import it by mistake, and makes the shipped
// assets look like they contain test code.
//
// It exists because these modules are DOM code, and the two ways to check DOM code
// outside a browser are both bad: a full jsdom is a heavy dependency, and hand-rolling
// the shim inside each test file means every test carries its own subtly different
// version -- which is exactly what happened while these tests were being written.
// Two tests disagreed about whether `querySelector` understood `[data-x="y"]`, and
// the disagreement presented as a bug in the MODULE.
//
// So: one shim, one file, used by every test. It implements exactly what the modules
// use, and no more:
//
//   Element   class/tagName/dataset/style/innerHTML/value/hidden/children
//   CSS       className, classList, `[data-*]`, `.class`, `tag`
//   queries   querySelector, querySelectorAll (both via one matcher, so they cannot
//             disagree)
//   parsing   enough HTML to make `innerHTML = ...` followed by `querySelector(...)`
//             work, which is the idiom these modules use throughout
//
// If a module ever needs something not here, that is SIGNAL: either the shim grows
// or the module is reaching for more of the page than it should. It is not a reason
// to work around it in the test.

const VOID = new Set(["input", "br", "img", "hr", "hr", "meta", "link"]);

class El {
	constructor(tag) {
		this.tagName = String(tag).toUpperCase();
		this.children = [];
		this.parentNode = null;
		this.dataset = {};
		this.attributes = {};
		this.hidden = false;
		this.value = "";
		this.text = "";
		// A real element always has a style object. Without one, a module that
		// writes `el.style.left = ...` throws "Cannot set properties of undefined",
		// which reads as a bug in the MODULE rather than a gap in the shim.
		this.style = {};
		this._html = "";
		this._classes = new Set();
		this._listeners = {};
		const self = this;
		this.classList = {
			add: (c) => self._classes.add(c),
			remove: (c) => self._classes.delete(c),
			contains: (c) => self._classes.has(c),
			toggle: (c, on) => {
				if (on === undefined) return self._classes.has(c) ? (self._classes.delete(c), false) : (self._classes.add(c), true);
				return on ? (self._classes.add(c), true) : (self._classes.delete(c), false);
			},
		};
	}

	set className(v) {
		this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
	}
	get className() {
		return [...this._classes].join(" ");
	}

	set innerHTML(v) {
		this._html = String(v);
		this.children = [];
		this._parse(this._html);
	}
	get innerHTML() {
		return this._html;
	}

	appendChild(c) {
		this.children.push(c);
		c.parentNode = this;
		return c;
	}

	setAttribute(k, v) {
		this.attributes[k] = String(v);
	}
	getAttribute(k) {
		return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null;
	}
	hasAttribute(k) {
		return Object.prototype.hasOwnProperty.call(this.attributes, k);
	}

	/** Detach from the parent, as `ChildNode.remove()` does. */
	remove() {
		if (!this.parentNode) return;
		const i = this.parentNode.children.indexOf(this);
		if (i >= 0) this.parentNode.children.splice(i, 1);
		this.parentNode = null;
	}

	addEventListener(type, fn) {
		(this._listeners[type] = this._listeners[type] || []).push(fn);
	}
	removeEventListener() {}
	focus() {}
	/**
	 * A click reaches BOTH registration styles: listeners added with
	 * addEventListener and a directly assigned `onclick`. A no-op `click()` makes
	 * every "does this control work" test pass vacuously.
	 */
	click() {
		const event = { type: "click", preventDefault() {}, stopPropagation() {} };
		(this._listeners.click || []).forEach((fn) => fn(event));
		if (typeof this.onclick === "function") this.onclick(event);
	}

	/** Dispatch to handlers registered on THIS element only. */
	dispatch(type, event = {}) {
		(this._listeners[type] || []).forEach((fn) => fn({ type, preventDefault() {}, ...event }));
	}

	_parse(html) {
		const stack = [this];
		const re = /<\/?([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:="[^"]*")?)*)\s*(\/?)>|([^<]+)/g;
		let m;
		while ((m = re.exec(html)) !== null) {
			if (m[4] !== undefined) {
				const text = m[4].trim();
				if (text) {
					const top = stack[stack.length - 1];
					top.text = (top.text || "") + text;
				}
				continue;
			}
			const closing = m[0][1] === "/";
			const tag = m[1].toLowerCase();
			if (closing) {
				if (stack.length > 1) stack.pop();
				continue;
			}
			const el = new El(tag);
			const attrRe = /([\w:-]+)(?:="([^"]*)")?/g;
			let a;
			while ((a = attrRe.exec(m[2] || "")) !== null) {
				const key = a[1];
				const value = a[2] === undefined ? "" : a[2];
				if (key === "class") el.className = value;
				else if (key === "value") el.value = value;
				else if (key === "style") el.setAttribute("style", value);
				else if (key.startsWith("data-")) el.dataset[key.slice(5)] = value;
				else el.setAttribute(key, value);
			}
			stack[stack.length - 1].appendChild(el);
			if (!m[3] && !VOID.has(tag)) stack.push(el);
		}
	}

	/**
	 * The single matcher. `querySelector` and `querySelectorAll` both use it, which
	 * is the whole point of having it in one place.
	 */
	_matches(sel) {
		sel = String(sel).trim();
		// Compound: `input[type=file]` -- a tag with an attribute. Needed by the PDF
		// viewport's file input. Checked before the single-form cases, since a
		// selector can be both a tag and have an attribute.
		const compound = sel.match(
			/^([a-zA-Z][\w-]*)?(\[([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\]"']*)))?\])$/
		);
		if (compound) {
			const [, tag, , key, dq, sq, bare] = compound;
			// CSS permits an unquoted attribute value, and `input[type=file]` is the
			// common way it is written. Requiring the quotes made that selector
			// silently match nothing.
			const value = dq !== undefined ? dq : sq !== undefined ? sq : bare;
			if (tag && this.tagName !== tag.toUpperCase()) return false;
			if (value === undefined) return this.hasAttribute(key);
			if (key.startsWith("data-")) return this.dataset[key.slice(5)] === value;
			return this.getAttribute(key) === value;
		}
		if (sel.startsWith("[")) {
			const m = sel.match(/^\[([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\]"']*)))?\]$/);
			if (!m) return false;
			const [, key, dq, sq, bare] = m;
			const value = dq !== undefined ? dq : sq !== undefined ? sq : bare;
			if (value === undefined) return this.hasAttribute(key);
			if (key.startsWith("data-")) return this.dataset[key.slice(5)] === value;
			return this.getAttribute(key) === value;
		}
		if (sel.startsWith(".")) return this._classes.has(sel.slice(1));
		return this.tagName === sel.toUpperCase();
	}

	querySelectorAll(sel) {
		const out = [];
		const walk = (n) => {
			for (const c of n.children) {
				if (c._matches(sel)) out.push(c);
				walk(c);
			}
		};
		walk(this);
		return out;
	}

	querySelector(sel) {
		const walk = (n) => {
			for (const c of n.children) {
				if (c._matches(sel)) return c;
				const hit = walk(c);
				if (hit) return hit;
			}
			return null;
		};
		return walk(this);
	}
}

function installDom() {
	globalThis.document = { createElement: (t) => new El(t) };
	globalThis.__El = El;
	return globalThis.document;
}

/**
 * Load a module's source, strip its `export` keywords, and evaluate it, returning
 * the named exports. These modules are plain ES modules with no imports, so this
 * is a faithful way to run them without a bundler.
 */
function loadModule(path, names) {
	const fs = require("fs");
	const src = fs.readFileSync(path, "utf8").replace(/^export\s+(?=(const|function|class|let|var)\b)/gm, "");
	const namesList = Array.isArray(names) ? names : Object.values(names || {});
	return new Function(
		"console",
		"document",
		"module",
		`${src}\n; return { ${namesList.join(", ")} };`
	)(console, globalThis.document, {});
}

module.exports = { El, installDom, loadModule };
