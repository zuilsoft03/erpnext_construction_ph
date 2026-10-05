// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The left panel: navigation and context for whatever the centre is showing.
//
// The spec: "The left panel **adapts its content based on the active viewport
// tab**, but the top section (Models) is always visible." Per tab:
//
//   3D     -> Models, Model Tree, Takeoff Sheets
//   PDF    -> Calibration, Capture, Captured Items
//   split  -> Models, Model Tree, Calibration (condensed), Takeoff Sheets
//
// "Adapts" is implemented the same way the tab bar switches viewports: the cards
// are all built once and TOLD whether they are shown. Nothing is re-rendered on a
// tab change, so a calibration the surveyor spent four clicks establishing is still
// there when they come back from the model to check it against a wall. Re-building
// the panel per tab is the obvious implementation and it silently throws that work
// away -- and the loss is invisible, because the panel looks correct either way.
//
// It is a module rather than more code in bim_workspace.js because the panel is
// the part most likely to be reworked by a later wave, and because its behaviour
// (per-tab visibility, preserved input state) is testable on its own.

/** Which cards each viewport mode shows. Models is always visible, per the spec. */
export const PANEL_MODES = {
	"3d": ["models", "tree", "sheets"],
	pdf: ["calibration", "capture", "captured"],
	split: ["models", "tree", "calibration", "sheets"],
};

/** The card headings. Declared before use so this reads top-down. */
export const TITLES = {
	models: "Models",
	tree: "Model Tree",
	sheets: "Takeoff Sheets",
	calibration: "Calibration",
	capture: "Capture",
	captured: "Captured Items",
};

/**
 * The order cards are declared in -- which is the order they are SHOWN in, since a
 * mode only toggles visibility and never moves a node.
 *
 * It is the spec's split-view stack, because that is the one mode showing the most
 * cards: Models, Model Tree, Calibration, Takeoff Sheets. Every other mode's
 * ordering falls out of this one correctly -- 3D shows models/tree/sheets, and PDF
 * shows calibration/capture/captured. An earlier order put `sheets` third, which
 * made the split view read Calibration last, where the spec puts it fourth and
 * Takeoff Sheets fifth.
 */
const CARD_ORDER = ["models", "tree", "calibration", "sheets", "capture", "captured"];

const esc = (s) =>
	String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/**
 * Build the panel.
 *
 * @param {HTMLElement} root
 * @param {object} handlers  { onModelChange, onIsolate, onSheetOpen, onCapture,
 *                             onCalibrate, onNewSheet }
 * @returns the panel API
 */
export function createLeftPanel(root, handlers = {}) {
	const cards = {};

	root.innerHTML = CARD_ORDER.map(
		(id) => `<div class="bim-ws-card" data-card="${id}" hidden>
			<div class="bim-ws-label">${esc(TITLES[id])}</div>
			<div class="bim-ws-card-body" data-body="${id}"></div>
		</div>`
	).join("");
	CARD_ORDER.forEach((id) => {
		cards[id] = {
			el: root.querySelector(`[data-card="${id}"]`),
			body: root.querySelector(`[data-body="${id}"]`),
		};
	});

	// Calibration is state the SURVEYOR established by clicking two points and
	// typing a known length. It lives here, not in the PDF viewport, precisely so
	// that switching tabs cannot discard it.
	const state = {
		mode: "3d",
		models: [],
		model: null,
		tree: [],
		sheets: [],
		calibration: null, // { metres_per_unit, known_length_m, at }
		captured: [], // { description, quantity, unit, sheet }
	};

	/**
	 * Show one mode's cards. Visibility only -- no card is re-rendered, so every
	 * input the user has typed into survives.
	 */
	function setMode(mode) {
		const wanted = PANEL_MODES[mode] || PANEL_MODES["3d"];
		state.mode = mode;
		CARD_ORDER.forEach((id) => {
			cards[id].el.hidden = !wanted.includes(id);
		});
		// The split view shows calibration but not the full capture card, per the
		// spec's "condensed: scale only". A read-only summary is honest about being
		// a summary; silently omitting it would read as "not calibrated".
		if (wanted.includes("calibration")) {
			const condensed = !wanted.includes("capture");
			render_calibration(condensed);
		}
	}

	// -------------------------------------------------------------- models card
	function render_models() {
		if (!state.models.length) {
			cards.models.body.innerHTML = `<div class="bim-ws-empty">No models for this project yet. Upload an .ifc to a BIM Model record.</div>`;
			return;
		}
		const options = state.models
			.map(
				(m) =>
					`<option value="${esc(m.name)}"${m.name === state.model ? " selected" : ""}>${esc(
						m.model_name
					)} (${esc(m.conversion_status || "unknown")})</option>`
			)
			.join("");
		cards.models.body.innerHTML = `<select class="bim-ws-model">${options}</select>`;
		const select = cards.models.body.querySelector("select");
		select.onchange = () => {
			state.model = select.value;
			if (handlers.onModelChange) handlers.onModelChange(select.value);
		};
	}

	function setModels(rows, selected) {
		state.models = rows || [];
		state.model = selected || (state.models[0] && state.models[0].name) || null;
		render_models();
	}

	// ----------------------------------------------------------------- tree card
	function render_tree() {
		if (!state.tree.length) {
			cards.tree.body.innerHTML = `<div class="bim-ws-empty">No spatial tree in this model.</div>`;
			return;
		}
		// A `spine:` key marks a GROUPING node -- one of the 38 with no `localId` and
		// therefore no element behind it. Those are rendered as headers: no
		// `data-guid`, not focusable, and wired to no click handler. They used to look
		// and behave exactly like elements, so clicking one issued an element lookup
		// with a key that cannot match, and the panel answered "this element has no
		// issues" about something that is not an element -- which is indistinguishable
		// from a real element that genuinely has none.
		cards.tree.body.innerHTML = state.tree
			.map((r) => {
				const isSpine = String(r.id || "").startsWith("spine:");
				const indent = ((r.depth || 0) * 12) + 6;
				if (isSpine) {
					return `<div class="bim-ws-node bim-ws-node--group" role="heading" style="padding-left:${indent}px"><span class="bim-ws-dot bim-ws-dot--group"></span>${esc(r.name || r.id)}</div>`;
				}
				return `<div class="bim-ws-node" data-guid="${esc(r.id)}" role="treeitem" tabindex="0" style="padding-left:${indent}px"><span class="bim-ws-dot"></span>${esc(r.name || r.id)}</div>`;
			})
			.join("");
		cards.tree.body.querySelectorAll(".bim-ws-node[data-guid]").forEach((node) => {
			const activate = () => {
				cards.tree.body.querySelectorAll(".bim-ws-node.is-active").forEach((n) => n.classList.remove("is-active"));
				node.classList.add("is-active");
				if (handlers.onIsolate) handlers.onIsolate(node.dataset.guid);
			};
			node.onclick = activate;
			// The tree is the fastest way to isolate a floor, and a keyboard-only
			// user gets there with the mouse otherwise.
			node.onkeydown = (e) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					activate();
				}
			};
		});
	}

	function setTree(rows) {
		state.tree = rows || [];
		render_tree();
	}

	// ---------------------------------------------------------------- sheets card
	function render_sheets() {
		if (!state.sheets.length) {
			cards.sheets.body.innerHTML = `<div class="bim-ws-empty">No takeoff sheets yet.</div>`;
			return;
		}
		cards.sheets.body.innerHTML =
			state.sheets
				.map(
					(s) =>
						`<div class="bim-ws-sheet" data-sheet="${esc(s.name)}" role="button" tabindex="0">` +
						`<span>${esc(s.sheet_name || s.name)}</span>` +
						`<span class="bim-ws-sheet-meta">${esc(s.status || "")}</span></div>`
				)
				.join("") +
			`<button class="btn btn-xs btn-default bim-ws-new-sheet">+ New Sheet</button>`;
		cards.sheets.body.querySelectorAll(".bim-ws-sheet").forEach((node) => {
			const open = () => handlers.onSheetOpen && handlers.onSheetOpen(node.dataset.sheet);
			node.onclick = open;
			node.onkeydown = (e) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					open();
				}
			};
		});
		const add = cards.sheets.body.querySelector(".bim-ws-new-sheet");
		if (add) add.onclick = () => handlers.onNewSheet && handlers.onNewSheet();
	}

	function setSheets(rows) {
		state.sheets = rows || [];
		render_sheets();
	}

	// ----------------------------------------------------------- calibration card
	function render_calibration(condensed) {
		const c = state.calibration;
		if (!c) {
			cards.calibration.body.innerHTML = `<div class="bim-ws-hint">Not calibrated. Measure a known length, then calibrate.</div>`;
			return;
		}
		const summary = `<div class="bim-ws-cal-summary">Scale: <b>${c.metres_per_unit.toFixed(6)}</b> m/unit</div>`;
		if (condensed) {
			cards.calibration.body.innerHTML = summary;
			return;
		}
		cards.calibration.body.innerHTML =
			summary +
			`<div class="bim-ws-field"><label>Known length (m)</label>` +
			`<input class="bim-ws-known" type="number" step="0.001" value="${c.known_length_m}"></div>` +
			`<button class="btn btn-xs btn-default bim-ws-recalibrate">Recalibrate</button>`;
		const btn = cards.calibration.body.querySelector(".bim-ws-recalibrate");
		if (btn) {
			btn.onclick = () => {
				const input = cards.calibration.body.querySelector(".bim-ws-known");
				const value = parseFloat(input.value);
				if (!Number.isFinite(value) || value <= 0) {
					// Refused here rather than sent: a zero or negative known length
					// would produce an infinite or negative scale, and every later
					// area would be wrong by an order of magnitude.
					input.classList.add("bim-ws-invalid");
					return;
				}
				input.classList.remove("bim-ws-invalid");
				if (handlers.onCalibrate) handlers.onCalibrate(state.calibration.points, value);
			};
		}
	}

	/**
	 * Record a calibration. `points` is kept so the surveyor can re-derive the scale
	 * against a different known length without re-clicking in the viewport -- the
	 * clicks are the expensive part.
	 */
	function setCalibration(calibration) {
		if (calibration && calibration.points) {
			state.calibration = {
				metres_per_unit: calibration.metres_per_unit,
				known_length_m: calibration.known_length_m,
				points: calibration.points,
			};
		} else {
			state.calibration = calibration || null;
		}
		render_calibration(!PANEL_MODES[state.mode].includes("capture"));
	}

	// -------------------------------------------------------------- capture card
	function render_capture() {
		cards.capture.body.innerHTML =
			`<div class="bim-ws-field"><label>BOQ Item</label>` +
			`<input class="bim-ws-boq" type="text" placeholder="e.g. A-03 Concrete"></div>` +
			`<div class="bim-ws-field"><label>Description</label>` +
			`<input class="bim-ws-desc" type="text" placeholder="Slab F1"></div>` +
			`<div class="bim-ws-field"><label>Unit</label><select class="bim-ws-unit">` +
			["m2", "m3", "m", "no"]
				.map((u) => `<option value="${u}">${u}</option>`)
				.join("") +
			`</select></div>` +
			`<button class="btn btn-xs btn-primary bim-ws-capture">Capture Area</button>` +
			`<div class="bim-ws-hint bim-ws-capture-hint">Click points on the sheet, then capture.</div>`;
		cards.capture.body.querySelector(".bim-ws-capture").onclick = () => {
			if (handlers.onCapture) {
				handlers.onCapture({
					boq_item: cards.capture.body.querySelector(".bim-ws-boq").value.trim(),
					description: cards.capture.body.querySelector(".bim-ws-desc").value.trim(),
					unit: cards.capture.body.querySelector(".bim-ws-unit").value,
				});
			}
		};
	}

	function render_captured() {
		if (!state.captured.length) {
			cards.captured.body.innerHTML = `<div class="bim-ws-empty">Nothing captured yet.</div>`;
			return;
		}
		cards.captured.body.innerHTML = state.captured
			.map(
				(c, i) =>
					`<div class="bim-ws-captured" data-index="${i}"><span>✓</span>` +
					`<span>${esc(c.description)} = ${Number(c.quantity).toFixed(2)} ${esc(c.unit)}</span></div>`
			)
			.join("");
	}

	function addCaptured(entry) {
		state.captured.push(entry);
		render_captured();
	}

	// Build the two cards whose content does not depend on data.
	render_capture();
	render_captured();
	setMode("3d");

	return {
		setMode,
		setModels,
		setTree,
		setSheets,
		setCalibration,
		addCaptured,
		getState: () => JSON.parse(JSON.stringify(state)),
		cards,
	};
}
