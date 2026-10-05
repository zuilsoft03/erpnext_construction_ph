// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The floating toolbar: seven tools over the 3D viewport.
//
// The spec's table names its implementation for each -- `model.setVisibility`,
// `OBC.MeasurementUtils`, `OBC.Clipper`, camera presets. The engine in
// geometry.js exposes none of those names, so this module is the adapter: it
// speaks the spec's vocabulary to the page and reaches the viewer through the
// API the engine actually has.
//
// Two decisions worth stating.
//
// HIDDEN AND ISOLATED ARE SETS, NOT FLAGS. The spec describes Isolate as "the
// inverse of Hide", which is true of the OPERATION and false of the STATE. If
// Isolate only hid the unselected, then showing a third element afterwards would
// leave the model in a state neither user asked for. So the module keeps the
// hidden set and derives what isolate means from it, and a user who isolates and
// then shows something sees exactly that.
//
// A TOOL THAT CANNOT ACT SAYS SO. Measure and Clip need components this build may
// not have -- and the toolbar is built to be used by someone who cannot see the
// console. A button that silently does nothing is worse than one that explains
// itself, so every tool returns a reason and the toolbar renders it in its own
// status line.

/** The seven tools, in the spec's order. */
export const TOOLS = [
	{ id: "select", label: "Select", icon: "➤", hint: "Click an element to select it", needs: [] },
	{ id: "measure", label: "Measure", icon: "📏", hint: "Point-to-point distance", needs: ["MeasurementUtils"] },
	{ id: "clip", label: "Clip", icon: "✂", hint: "Slice the model with a section plane", needs: ["Clipper"] },
	{ id: "section", label: "Section", icon: "🔲", hint: "Orthographic plan or elevation", needs: [] },
	{ id: "hide", label: "Hide", icon: "👁", hint: "Hide the selection", needs: [] },
	{ id: "isolate", label: "Isolate", icon: "🎯", hint: "Show only the selection", needs: [] },
	{ id: "reset", label: "Reset", icon: "↺", hint: "Fit the whole model", needs: [] },
];

/** The camera presets the Section tool cycles through. */
export const SECTIONS = [
	{ id: "iso", label: "Iso" },
	{ id: "top", label: "Plan" },
	{ id: "front", label: "Elevation" },
	{ id: "right", label: "Side" },
];

/**
 * The visibility state, derived rather than stored twice.
 *
 * Hidden and isolated are kept as ONE set precisely so they cannot disagree: a
 * toolbar that tracks `hidden` and `isolated` separately will eventually hide
 * something the user isolated away from, and nothing in the UI will show it.
 */
export function createVisibility(options = {}) {
	let hidden = new Set();
	let isolatedTo = null; // null = not isolating
	// How to enumerate the whole model. Isolate means "show ONLY these", and the
	// complement of a selection cannot be computed from the selection -- it needs
	// the universe. Taking that as an ARGUMENT OF `effectiveHidden` was a footgun
	// this file's own test walked straight into: `isolate(["b"])` with no list
	// quietly isolated nothing. The universe belongs to the state, not to each
	// call site.
	let universeSource = typeof options.allGuids === "function"
		? options.allGuids
		: () => options.allGuids || [];
	const universe = () => universeSource();

	return {
		/** Hide these, remembering them so Reset can bring them back. */
		hide(guids) {
			(guids || []).forEach((g) => hidden.add(g));
			return this.effectiveHidden();
		},
		/**
		 * Show these, even while isolating.
		 *
		 * Removing from the hidden set alone is NOT enough: an element hidden by the
		 * ISOLATE rule was never added to that set, so a user who isolates to one
		 * element and then reveals a second would see nothing happen -- the button
		 * would be working and doing nothing at the same time. So while isolating,
		 * showing also extends what the isolation keeps.
		 */
		show(guids) {
			(guids || []).forEach((g) => {
				hidden.delete(g);
				if (isolatedTo && !isolatedTo.includes(g)) isolatedTo.push(g);
			});
			return this.effectiveHidden();
		},
		/** Show only these. Everything else is hidden as a CONSEQUENCE. */
		isolate(guids) {
			isolatedTo = [...(guids || [])];
			return this.effectiveHidden();
		},
		/** Point the state at a (new) model, e.g. after loading another one. */
		setUniverse(guids) {
			universeSource = typeof guids === "function" ? guids : () => guids || [];
			return this.effectiveHidden();
		},
		unisolate() {
			isolatedTo = null;
			return this.effectiveHidden();
		},
		/** What is actually invisible right now, hidden-set plus the isolate rule. */
		effectiveHidden() {
			if (!isolatedTo) return [...hidden];
			const keep = new Set(isolatedTo);
			// The universe is needed because "everything else" cannot be enumerated
			// from a selection alone -- without it, an element nobody ever touched
			// would stay visible inside an isolate, which is not what Isolate means.
			const base = universe() || [];
			const extra = base.filter((g) => !keep.has(g) && !hidden.has(g));
			return [...new Set([...hidden, ...extra])];
		},
		isIsolating: () => isolatedTo !== null,
		isolatedTo: () => (isolatedTo ? [...isolatedTo] : null),
		hidden: () => [...hidden],
		reset() {
			hidden = new Set();
			isolatedTo = null;
		},
	};
}

/**
 * Build the toolbar over a host element.
 *
 * @param {HTMLElement} host
 * @param {object} handlers  { onSelect, onStatus, getSelection, getEngine, getAllGuids }
 */
export function createToolbar(host, handlers = {}) {
	const visibility = createVisibility({
		allGuids: () => (handlers.getAllGuids && handlers.getAllGuids()) || [],
	});

	host.innerHTML = `
		<div class="bim-toolbar" role="toolbar" aria-label="BIM tools">
			${TOOLS.map(
				(t) => `<button class="bim-tool" data-tool="${t.id}" title="${t.hint} (${t.label})"
					aria-label="${t.label}"><span class="bim-tool-icon">${t.icon}</span></button>`
			).join("")}
			<div class="bim-tool-sections">
				${SECTIONS.map(
					(s) => `<button class="bim-tool-section" data-section="${s.id}" title="${s.label} view">${s.label}</button>`
				).join("")}
			</div>
		</div>
		<div class="bim-toolbar-status" role="status"></div>`;

	const statusEl = host.querySelector(".bim-toolbar-status");
	const sectionBox = host.querySelector(".bim-tool-sections");

	function say(message, tone) {
		if (statusEl) {
			statusEl.textContent = message || "";
			statusEl.dataset.tone = tone || "info";
		}
		if (handlers.onStatus) handlers.onStatus(message || "", tone || "info");
	}

	/**
	 * What this tool is missing, and nothing else.
	 *
	 * An earlier version returned "the model is not loaded" whenever the engine was
	 * absent, which made EVERY tool refuse -- including Reset, which needs no engine
	 * at all -- with the message "Hide needs the model is not loaded, which this
	 * build does not provide". Two faults in one: a tool with no requirements was
	 * blocked by a requirement it does not have, and the reason was phrased as a
	 * missing BUILD rather than a missing MODEL.
	 *
	 * A tool with no `needs` is never blocked. If its action then cannot run
	 * because there is no model, that action says so itself, where the message can
	 * name what the user was trying to do.
	 */
	function missing(tool) {
		if (!tool.needs || !tool.needs.length) return [];
		const engine = handlers.getEngine && handlers.getEngine();
		if (!engine) return tool.needs;
		// `engine.OBC` is the namespace. `engine.components` is NOT: measured, it holds
		// 6 keys and none of the OBC components, not even SimpleCamera, so reading it
		// reported every tool's component as missing and disabled the Measure and Clip
		// tools in a build that ships both. The old name is kept only as a fallback.
		const have = engine.OBC || engine.components || {};
		return tool.needs.filter((n) => !have[n]);
	}

	function selection() {
		const sel = handlers.getSelection && handlers.getSelection();
		return sel && sel.length ? sel : [];
	}

	/**
	 * Apply a tool. Returns `{ ok, reason }` so a caller -- or a test -- can see
	 * exactly what happened rather than inferring it from the picture.
	 */
	function apply(toolId) {
		const tool = TOOLS.find((t) => t.id === toolId);
		if (!tool) return { ok: false, reason: `unknown tool ${toolId}` };

		const absent = missing(tool);
		if (absent.length) {
			// Named, not generic. "This build has no Clipper" is actionable;
			// "not available" is not.
			// One sentence. The earlier phrasing read as a concatenation bug
			// ("Hide needs the model is not loaded, which this build does not
			// provide") because it joined a tool name to a component list with no
			// grammar between them.
			return {
				ok: false,
				// Names what is missing AND, where the package ships a differently
				// named equivalent, what it DOES ship. "This build does not provide
				// it" is a dead end for whoever reads it; the alternative is a lead.
				// Measured, not guessed -- see ops/NOTES/OPEN-UI10-obc-components-absent.md.
				reason: `${tool.label} needs ${absent.join(" and ")}, which @thatopen/components 3.4.8 does not ship (it has ${tool.alternative || "no equivalent"}) under that name.`,
			};
		}

		switch (toolId) {
			case "select":
				host.querySelector('[data-tool="select"]').classList.add("is-active");
				say("Click an element to select it.");
				if (handlers.onSelect) handlers.onSelect();
				return { ok: true };

			case "measure": {
				const engine = handlers.getEngine();
				engine.startMeasurement && engine.startMeasurement();
				say("Click two points on the model to measure between them.");
				return { ok: true };
			}

			case "clip": {
				const engine = handlers.getEngine();
				engine.startClipping && engine.startClipping();
				say("Drag on the model to move the section plane.");
				return { ok: true };
			}

			case "section":
				// The presets are always available; the group is a submenu.
				sectionBox.classList.toggle("is-open");
				return { ok: true, presets: SECTIONS.map((s) => s.id) };

			case "hide": {
				const sel = selection();
				if (!sel.length) return { ok: false, reason: "Select something to hide." };
				visibility.hide(sel);
				say(`Hidden ${sel.length} element${sel.length === 1 ? "" : "s"}.`, "warn");
				return { ok: true, hidden: visibility.effectiveHidden() };
			}

			case "isolate": {
				if (visibility.isIsolating()) {
					visibility.unisolate();
					say("Isolate off.");
					return { ok: true, hidden: visibility.effectiveHidden() };
				}
				const sel = selection();
				if (!sel.length) return { ok: false, reason: "Select something to isolate." };
				visibility.isolate(sel);
				say(`Showing only ${sel.length} element${sel.length === 1 ? "" : "s"}.`, "warn");
				return { ok: true, hidden: visibility.effectiveHidden() };
			}

			case "reset": {
				// Reset is BOTH the camera and the visibility: a user who isolates and
				// then hits Reset expects the model back, not a perfectly framed
				// picture of an invisible building. The VISIBILITY half works with no
				// model at all, so it is done either way -- a user who hid something
				// and then changed model must not carry the hidden set across.
				visibility.reset();
				const engine = handlers.getEngine();
				if (!engine) {
					say("Nothing is loaded, so there is no view to reset.");
					return { ok: true, hidden: [] };
				}
				if (engine && engine.fit && typeof engine.fit === "function") engine.fit();
				else if (engine && engine.world && engine.world.camera && engine.world.camera.controls
					&& typeof engine.world.camera.controls.fitToCenter === "function") {
					engine.world.camera.controls.fitToCenter();
				}
				say("View reset.");
				return { ok: true, hidden: [] };
			}
			default:
				return { ok: false, reason: `tool ${toolId} has no behaviour` };
		}
	}

	host.addEventListener("click", (event) => {
		const section = event.target.closest(".bim-tool-section");
		if (section) {
			const engine = handlers.getEngine && handlers.getEngine();
			engine && engine.setView && engine.setView(section.dataset.section);
			say(`${section.textContent.trim()} view.`);
			return;
		}
		const button = event.target.closest(".bim-tool");
		if (!button) return;
		const outcome = apply(button.dataset.tool);
		if (!outcome.ok) say(outcome.reason, "error");
		button.classList.toggle("is-active", outcome.ok && button.dataset.tool === "select");
	});

	return {
		apply,
		say,
		visibility,
		tools: TOOLS,
		sections: SECTIONS,
		element: host,
	};
}
