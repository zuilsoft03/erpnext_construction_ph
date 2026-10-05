// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The split view's consumer: what the two panes actually SHOW.
//
// WHY THIS EXISTS, AND WHY IT IS BUILT THE WAY IT IS. The split tab builds its DOM —
// a Sync Selection button, a hint, and two panes that were empty divs — and nothing
// ever rendered into them, so the tab was structurally present and unwired. The
// comparison engine beside this module is complete and class-guarded; it had no
// consumer.
//
// THE CONSTRAINT THAT SHAPES EVERYTHING: the Element->Drawing Map is EMPTY. MEASURED
// on 2,104 indexed rows, `drawing` 0 and `drawing_region` 0, while `drawing_page` and
// `mapped_area` are both 2,104. `defaultResolve` returns null unless `row.drawing` is
// set, so EVERY element is unlinked today.
//
// A pane that only rendered a comparison would therefore show 2,104 refusals and look
// broken. So the split view is built to be USEFUL IN THE STATE THE DATA IS IN:
//
//   * The model side is REAL. The measured area, its class and its basis exist, so
//     that half of the pane carries the number a quantity surveyor came for.
//   * The sheet side says what is missing, in one sentence, and does not apologise
//     for it. "Not linked to a region on any drawing yet" is a TRUE statement about
//     the project, and it is the same sentence on every element because it is true
//     on every element.
//   * When an element IS linked, the same two panes carry the comparison, and the
//     engine's refusal is shown verbatim rather than summarised away.
//
// Turning sync OFF must leave both viewports exactly as they are. That is the
// module's own rule (`createSyncEngine`'s comment says so) and it is honoured here:
// disabling changes a class and the button's state, and nothing else. A "clear
// highlights on disable" implementation would be destructive rather than inert, and
// the user would have to re-find whatever they were looking at.

const esc = (s) =>
	String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) =>
		({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
	);

/** The one sentence that is true of every unlinked element on today's data. */
export const UNLINKED_REASON =
	"Not linked to a region on any drawing yet. The element's area is measured; " +
	"the sheet it would be compared against is not.";

/**
 * Format an area for reading, by magnitude — the same rule the Properties card uses.
 * Duplicated rather than imported because this module is loaded independently of the
 * right panel, and a card that formats 3,569.543468157 m² is unreadable.
 */
export function formatArea(m2) {
	const n = Number(m2);
	if (!Number.isFinite(n)) return null;
	const digits = n === 0 ? 2 : Math.abs(n) >= 100 ? 1 : Math.abs(n) >= 1 ? 2 : 3;
	return n.toLocaleString("en-PH", { maximumFractionDigits: digits });
}

/**
 * Build the split view's consumer and bind it to the sync engine.
 *
 * @param {HTMLElement} root  the tab host containing .bim-split-toggle,
 *   .bim-split-hint and the two .bim-split-pane elements
 * @param {object} sync  the engine from `createSyncEngine()`
 * @param {(guid: string) => void} [onSeekPdf] called when the user asks to see the
 *   region on the sheet, so the page can act on it rather than this module guessing
 * @returns {{render: Function, setSelection: Function, isLinked: Function, destroy: Function}}
 */
export function createSplitView(root, sync, options = {}) {
	if (!root) return null;
	const onSeekPdf = typeof options.onSeekPdf === "function" ? options.onSeekPdf : null;

	const toggle = root.querySelector(".bim-split-toggle");
	const hint = root.querySelector(".bim-split-hint");
	const pane3d = root.querySelector('.bim-split-pane[data-pane="3d"]');
	const panePdf = root.querySelector('.bim-split-pane[data-pane="pdf"]');
	if (!pane3d || !panePdf) return null;

	let selected = null;
	let element = null;

	// The toggle is bound ONCE, and disabling is INERT: the flag and the button's own
	// state change, and the panes keep whatever they were showing. Clearing them would
	// be destructive, and "off" must mean off rather than wiped.
	if (toggle) {
		toggle.addEventListener("click", () => {
			const on = sync.setEnabled(!sync.isEnabled());
			toggle.classList.toggle("btn-primary", on);
			toggle.classList.toggle("btn-default", !on);
			toggle.textContent = on ? "Syncing" : "Sync Selection";
			toggle.setAttribute("aria-pressed", on ? "true" : "false");
			// Re-render so the panes state plainly whether sync is on, without
			// discarding the numbers.
			render();
		});
		toggle.setAttribute("aria-pressed", "false");
	}

	/** The model side: what we measured. Real, and present whether or not we are linked. */
	function renderModelSide() {
		if (!selected) {
			return `<div class="bim-split-label">3D Model</div>
				<div class="bim-ws-empty">Select an element in the model to see its measured area here.</div>`;
		}
		if (!element) {
			return `<div class="bim-split-label">3D Model</div>
				<div class="bim-ws-empty">That element is not in this model's index, so it has no properties.</div>`;
		}
		const area = formatArea(element.area);
		const cls = element.area_class || "UNCLASSIFIED";
		const name = element.element_name || element.element_type || element.element_guid;
		return (
			`<div class="bim-split-label">3D Model</div>` +
			`<div class="bim-split-measured">` +
			`<div class="bim-split-name">${esc(name)}</div>` +
			(area === null
				? `<div class="bim-ws-empty">No measured area for this element.</div>`
				: `<div class="bim-split-area">${esc(area)} m²</div>` +
				  `<div class="bim-split-class" data-class="${esc(cls)}">${esc(cls)}</div>`) +
			(element.ifc_id ? `<div class="bim-split-meta">IFC # ${esc(element.ifc_id)}</div>` : "") +
			`</div>`
		);
	}

	/**
	 * The sheet side: the comparison, or the honest reason there is not one.
	 *
	 * The engine's refusal is rendered VERBATIM, with its `reason`, rather than
	 * summarised into a generic "no data". The reason is where the project-specific
	 * truth lives -- "the element is REINFORCEMENT and the takeoff line is SURFACE,
	 * these measure different things" is a finding, and flattening it to "no
	 * comparison" would throw away the only thing the reader learned.
	 */
	function renderSheetSide() {
		if (!selected) {
			return `<div class="bim-split-label">PDF Plan</div>
				<div class="bim-ws-empty">The sheet this element would be compared against appears here.</div>`;
		}
		const c = sync.comparison(element ? element.area : null, {
			takeoffClass: options.takeoffClassFor ? options.takeoffClassFor(selected) : null,
			requireClass: false,
		});
		if (c && c.comparable) {
			const within = c.within_tolerance;
			return (
				`<div class="bim-split-label">PDF Plan</div>` +
				`<div class="bim-split-compare" data-within="${within}">` +
				`<div>Model <b>${esc(formatArea(c.model))} m²</b></div>` +
				`<div>Takeoff <b>${esc(formatArea(c.takeoff))} m²</b></div>` +
				`<div class="bim-split-delta">${c.delta_percent >= 0 ? "+" : ""}${c.delta_percent.toFixed(2)}%</div>` +
				`</div>`
			);
		}
		const reason = (c && c.reason) || UNLINKED_REASON;
		return (
			`<div class="bim-split-label">PDF Plan</div>` +
			`<div class="bim-ws-empty">${esc(reason)}</div>`
		);
	}

	function render() {
		pane3d.innerHTML = renderModelSide();
		panePdf.innerHTML = renderSheetSide();
		if (hint) {
			// The hint says what sync is DOING, not what it could do. A hint that
			// explains the feature teaches nobody anything at the moment they need it.
			hint.textContent = !selected
				? ""
				: sync.isEnabled()
				  ? "Selecting here highlights the same element on the sheet."
				  : "Sync is off. Turning it on does not change what is shown, only what follows a click.";
		}
	}

	// The engine announces every selection and toggle; re-rendering from that ONE
	// source means the panes cannot disagree with the engine about what is selected.
	const unsubscribe = typeof sync.onChange === "function" ? sync.onChange(() => render()) : null;

	return {
		render,
		/**
		 * Record the current selection. `element` is the resolved server payload, and
		 * is allowed to be null: a pick that did not resolve is a real state and the
		 * panes say so rather than showing a previous element's numbers.
		 */
		setSelection(guid, resolved) {
			selected = guid || null;
			element = resolved || null;
			sync.select(guid, "3d");
			render();
		},
		isLinked: () => Boolean(sync.describe().linked),
		seekPdf: onSeekPdf,
		destroy() {
			if (typeof unsubscribe === "function") unsubscribe();
		},
	};
}
