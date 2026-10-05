// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The split-view sync engine: the 3D model above, the sheet below, and the link
// between them.
//
// THE SPEC'S PROMISE, AND WHAT IT CAN ACTUALLY DELIVER. The plan calls this the
// "killer differentiator", and the flow is: pick in 3D -> map the element to a region
// of the drawing -> highlight it -> compare the model's measurement with the takeoff.
// Two of those three steps had no data behind them when this was written:
//   * An element-to-drawing-region map does not exist. There is no field anywhere
//     that says "this wall is that polygon on that sheet".
//   * A per-element IFC area USED to not exist -- established in UI-5, where the
//     resolver had to return `None` and say so. IT NOW EXISTS. MEASURED: 1,502
//     elements carry a surface and footprint area, stored and server-confirmed, and
//     `area_summary` returns them classified by element type. This file's header is
//     corrected rather than left standing, because a header that says a capability is
//     absent when it is present stops the next person building the thing it describes.
//
// WHAT THE COMPARISON REFUSES, AND WHY IT NOW REFUSES ONE MORE THING. The module
// already refuses a missing measurement and a zero. It now also refuses a CLASS
// MISMATCH, because the live model carries 619 `IFCREINFORCINGBAR` at 0.16-0.23 m2
// each: diffing one against a takeoff line that never measured reinforcement yields a
// confident, entirely meaningless percentage. The arithmetic is fine and the two
// numbers are about different things -- which is the same category of error as
// printing `Δ 0.00%` because neither side had a number.
//
// So the engine is built COMPLETE and honest about the gap: the link registry is
// a real, populated structure; the comparison arithmetic is real and refuses to
// produce a number it cannot support; and an unlinked element says it is unlinked
// instead of highlighting whatever happens to be nearest.
//
// The rule that shapes `compare()` is the same one the Properties card is built
// on. A difference between two measurements is a serious claim in a QS tool, and
// printing `Δ 0.00%` because neither side had a number is worse than printing
// nothing: it looks like two independent measurements agreed.

const NOT_COMPARABLE = "Not comparable";

/**
 * Which family an element belongs to, mirroring `area_class` on the server.
 *
 * KEPT IN STEP WITH `api/bim_frag.area_class` DELIBERATELY. Two copies is a risk, and
 * the alternative -- importing server Python into a browser module -- is not possible;
 * the honest form of that risk is to say where the other copy is, so a change to one is
 * a prompt to change the other. The server's gate asserts its own list, so a drift
 * shows up there rather than as a quiet mis-classification here.
 */
const REINFORCEMENT_TYPES = ["IFCREINFORCINGBAR", "IFCREINFORCINGMESH", "IFCREBAR"];
const SURFACE_TYPES = [
	"IFCWALL", "IFCWALLSTANDARDCASE", "IFCCURTAINWALL", "IFCSLAB", "IFCSLABSTANDARDCASE",
	"IFCROOF", "IFCFOOTING", "IFCPLATE", "IFCCOLUMN", "IFCBEAM", "IFCDOOR", "IFCWINDOW",
	"IFCSTAIR", "IFCSTAIRFLIGHT", "IFCRAMP", "IFCRAILING", "IFCMEMBER",
	"IFCBUILDINGELEMENTPROXY",
];

export function areaClassOf(elementType) {
	const t = String(elementType || "").toUpperCase();
	if (REINFORCEMENT_TYPES.includes(t)) return "REINFORCEMENT";
	if (SURFACE_TYPES.includes(t)) return "SURFACE";
	// UNCLASSIFIED, never a default of SURFACE. A type nobody has listed must not
	// quietly join a surface comparison -- that is the bug this whole module exists
	// to refuse, and a permissive default would reintroduce it.
	return "UNCLASSIFIED";
}

/** What a link maps an element to: a sheet, a region, and optionally a measurement. */
export function createLinkRegistry() {
	const byGuid = new Map();

	return {
		/**
		 * Link an element GUID to a region. Replacing an existing link is explicit.
		 *
		 * `elementType` is kept so `compare()` can refuse a class mismatch. Without it a
		 * 0.16 m2 reinforcing bar diffs against a takeoff line that never measured
		 * reinforcement and reports a confident, meaningless percentage.
		 */
		link(guid, { sheet, region, area, label, elementType }) {
			if (!guid) return null;
			const entry = { guid, sheet: sheet || null, region: region || null };
			// `area` is stored ONLY if it is a real number. A region with no
			// measurement still links; it just cannot be compared.
			entry.area = Number.isFinite(Number(area)) && area !== null && area !== "" ? Number(area) : null;
			entry.label = label || null;
			// Always a class, never absent: UNCLASSIFIED is the honest answer for a
			// type nobody has listed, and it is what makes the comparison refuse rather
			// than guess.
			entry.element_class = areaClassOf(elementType);
			byGuid.set(guid, entry);
			return entry;
		},
		get: (guid) => byGuid.get(guid) || null,
		has: (guid) => byGuid.has(guid),
		all: () => [...byGuid.values()],
		size: () => byGuid.size,
		remove: (guid) => byGuid.delete(guid),
	};
}

/**
 * Compare two measurements, or say that they cannot be compared.
 *
 * `delta_percent` is signed: negative means the model measures LESS than the
 * takeoff, which is the direction a QS needs to see first. An absolute value here
 * would hide which side is short, and "0.56% out" does not say whether the
 * contractor is under- or over-measuring.
 */
export function compare(modelValue, takeoffValue, opts = {}) {
	// `Number(null)` is 0, and `Number("")` is 0, and `Number.isFinite(0)` is true --
	// so coercing first turned "no measurement" into a REAL ZERO and printed
	// "delta -100%". That is precisely the false measurement this whole module
	// exists to refuse, and it was in the refusal path itself. The absence of a
	// value is therefore checked BEFORE any coercion, and only then is what is
	// left coerced and tested for finiteness.
	const missing = (v) => v === null || v === undefined || v === "" || (typeof v === "number" && !Number.isFinite(v));
	const reasons = [];
	if (missing(modelValue)) reasons.push("the model has no measurement for this element");
	if (missing(takeoffValue)) reasons.push("the takeoff has no measurement for this region");

	const model = Number(modelValue);
	const takeoff = Number(takeoffValue);

	if (reasons.length) {
		return {
			comparable: false,
			reason: `${NOT_COMPARABLE}: ${reasons.join("; ")}.`,
		};
	}
	// A GENUINE zero on either side is degenerate, not a comparison. Dividing by a
	// zero takeoff is Infinity; comparing a zero model against a real takeoff gives
	// -100%, which reads as "the model is a hundred percent short" rather than
	// "one of these numbers is not a measurement". Symmetric on purpose: a 0 m2
	// element is as suspicious as a 0 m2 takeoff, and the takeoff branch already
	// refused for exactly that reason.
	// A CLASS MISMATCH IS NOT A COMPARISON, and this check comes BEFORE the arithmetic.
	// MEASURED: 619 of the 1,502 measured elements on the live model are
	// `IFCREINFORCINGBAR` at 0.16-0.23 m2 each. Diffing one of those against a takeoff
	// line that never measured reinforcement yields a confident, entirely meaningless
	// percentage -- the arithmetic is fine and the two numbers are about different
	// things. An UNKNOWN class on either side also refuses: the failure being prevented
	// is a confident wrong answer, not a missing one, so "I do not know what this is"
	// must not pass as agreement.
	const modelClass = opts.modelClass || null;
	const takeoffClass = opts.takeoffClass || null;
	if (modelClass && takeoffClass && modelClass !== takeoffClass) {
		return {
			comparable: false,
			model_class: modelClass,
			takeoff_class: takeoffClass,
			reason: `${NOT_COMPARABLE}: the element is ${modelClass} and the takeoff line is ${takeoffClass}. These measure different things, so a difference between them is not a discrepancy.`,
		};
	}
	if (opts.requireClass && (!modelClass || !takeoffClass)) {
		const unknown = !modelClass ? "the element" : "the takeoff line";
		return {
			comparable: false,
			model_class: modelClass,
			takeoff_class: takeoffClass,
			reason: `${NOT_COMPARABLE}: ${unknown} has no element class, so it cannot be confirmed to measure the same thing as the other side.`,
		};
	}

	if (takeawayZero(takeoff)) {
		return { comparable: false, reason: `${NOT_COMPARABLE}: the takeoff is zero.` };
	}
	if (takeawayZero(model)) {
		return { comparable: false, reason: `${NOT_COMPARABLE}: the model measures zero.` };
	}
	const delta = model - takeoff;
	return {
		comparable: true,
		model,
		takeoff,
		delta,
		delta_percent: (delta / takeoff) * 100,
		// A tolerance, because a difference of 0.0001% is a rounding artefact and
		// flagging it would train people to ignore the flag.
		within_tolerance: Math.abs((delta / takeoff) * 100) < 0.5,
	};
}

function takeawayZero(n) {
	return Math.abs(n) < Number.EPSILON;
}

/**
 * The sync engine: selection, and what each side should highlight.
 *
 * A toggle, not a mode. Turning sync off must leave both viewports exactly as they
 * are -- if disabling it cleared a highlight, the user would have to re-find what
 * they were looking at, and "off" would be destructive rather than inert.
 *
 * `resolve` is how a link is found. The DEFAULT reads the PERSISTED map --
 * `BIM Model GUID.drawing` / `drawing_page` / `drawing_region` / `mapped_area` --
 * because the gate for this row is explicit that those fields are the thing: "there
 * is nowhere to record which drawing region an element corresponds to ... the Sync
 * Selection assertion cannot pass without it." An in-memory registry alone is a
 * demo: it forgets on reload, so a highlight that worked once would not work for
 * the next person to open the page.
 */
/**
 * The default resolver: the persisted Element<->Drawing Map.
 *
 * `getMap` is supplied by the page, which has the server round trip; this keeps the
 * module free of `frappe` and testable in Node. `drawing_region` is "x,y,w,h" in
 * PDF user units -- stored, not recomputed, so the highlight does not move when the
 * sheet is recalibrated.
 *
 * EXPORTED so a test can reach the REAL function. It was module-private, and the
 * first persisted-path test worked around that by reimplementing the entry inline --
 * including the `element_class` line under test. That test could not have failed:
 * delete the line from this function and it still passed. A test that exercises a
 * copy of the code is a test of the copy.
 */
export function defaultResolve(guid) {
	const getMap = defaultResolve.getMap;
	if (typeof getMap !== "function") return null;
	const row = getMap(guid);
	if (!row || !row.drawing) return null;
	let region = null;
	if (row.drawing_region) {
		const parts = String(row.drawing_region).split(",").map((n) => Number(n.trim()));
		if (parts.length === 4 && parts.every(Number.isFinite)) {
			region = { x: parts[0], y: parts[1], w: parts[2], h: parts[3] };
		}
	}
	return {
		guid,
		sheet: row.drawing,
		page: row.drawing_page || null,
		region,
		// `mapped_area` is the SHEET's measurement. Null when unmapped, which is
		// what makes the comparison refuse rather than print a delta against
		// nothing.
		area: row.mapped_area === null || row.mapped_area === undefined ? null : Number(row.mapped_area),
		// The element's CLASS, so `compare()` can refuse a mismatch. Read from the
		// persisted row for the same reason the region is: a class supplied only in
		// memory forgets on reload, and a comparison that quietly stops refusing is
		// worse than one that never started.
		//
		// This line was MISSING while the registry path already set it, which made the
		// whole guard inert for persisted rows -- safe, because an undefined class
		// simply never mismatches, and useless, because it also never matches a
		// genuine SURFACE-vs-REINFORCEMENT case. `node --check` passes on a file that
		// is correct and incomplete, which is the whole difficulty.
		element_class: areaClassOf(row.element_type),
		persisted: true,
	};
}

export function createSyncEngine(options = {}) {
	const links = options.links || createLinkRegistry();
	const resolve = options.resolve || defaultResolve;
	// A persisted link, folded into the registry so `describe()` has one shape to
	// answer from whichever source produced it.
	const lookup = (guid) => {
		const inRegistry = links.get(guid);
		if (inRegistry) return inRegistry;
		return resolve(guid) || null;
	};
	const state = { enabled: false, selected: null, side: null };
	const listeners = [];

	function emit(reason) {
		listeners.forEach((fn) => fn({ ...state, reason, links }));
	}

	function select(guid, side) {
		// A selection is always recorded, even with sync off, so turning it on does
		// not need the user to click again.
		state.selected = guid || null;
		state.side = side || null;
		emit("select");
		return describe();
	}

	function setEnabled(on) {
		state.enabled = Boolean(on);
		emit("toggle");
		return state.enabled;
	}

	/** What the given side should highlight, given the current selection. */
	function describe() {
		const entry = state.selected ? lookup(state.selected) : null;
		return {
			enabled: state.enabled,
			selected: state.selected,
			side: state.side,
			// `null` means "highlight nothing", which is different from "highlight
			// everything" and from "highlight whatever was there before".
			other: state.enabled && entry ? { guid: state.selected, sheet: entry.sheet, region: entry.region } : null,
			linked: Boolean(entry),
			reason: entry ? null : state.selected
				? "This element is not linked to a region on any drawing yet."
				: "Nothing is selected.",
		};
	}

	return {
		links,
		select,
		setEnabled,
		describe,
		isEnabled: () => state.enabled,
		selected: () => state.selected,
		onChange: (fn) => {
			listeners.push(fn);
			return () => {
				const i = listeners.indexOf(fn);
				if (i >= 0) listeners.splice(i, 1);
			};
		},
		/**
		 * The comparison for the current selection, or why there is none.
		 * `takeoffClass` is the CALLER's, because only the takeoff knows what one of its
		 * lines was measuring. A class MISMATCH always refuses below -- that is the real
		 * hazard and it needs no opt-in. `requireClass` is OPT-IN, not forced: demanding
		 * a class from every caller broke the existing contract, where a linked element
		 * with a measurement on each side IS comparable, and a caller not yet wired to
		 * a takeoff cannot supply one. The stricter mode belongs to whoever knows both
		 * sides, not to the engine by default.
		 */
		comparison: (modelArea, opts = {}) => {
			const entry = state.selected ? lookup(state.selected) : null;
			if (!entry) {
				// TWO DIFFERENT STATES, and conflating them is the same defect as
				// collapsing "not measured" into "not present". MEASURED: with the
				// Element->Drawing Map empty, EVERY selected element is unlinked, and this
				// branch said "nothing is selected" for all of them -- a user who had just
				// clicked an element was told they had not. The map being empty is a fact
				// about the PROJECT and has to be reportable as such.
				return {
					comparable: false,
					linked: false,
					reason: state.selected
						? `${NOT_COMPARABLE}: this element is not linked to a region on any drawing yet, so there is nothing to compare it against.`
						: `${NOT_COMPARABLE}: nothing is selected.`,
				};
			}
			return compare(modelArea, entry.area, {
				modelClass: entry.element_class,
				takeoffClass: opts.takeoffClass,
				requireClass: Boolean(opts.requireClass),
			});
		},
	};
}
