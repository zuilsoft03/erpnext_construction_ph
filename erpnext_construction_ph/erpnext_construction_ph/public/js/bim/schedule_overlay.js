// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The 4D schedule overlay: a WBS timeline driving what the model looks like on a
// given day.
//
// WHAT THE DATA ACTUALLY IS, because it decides what this can honestly claim.
// An element's schedule comes from its BOQ line, and the ONLY link from an IFC
// element to a BOQ line in this app is `BCF Element Reference.boq_item`. So the
// overlay can schedule the elements somebody has already raised an issue against,
// and nothing else. It does NOT guess for the rest, and it does not colour an
// unscheduled element a plausible grey -- an element shown as "complete" because
// nothing said otherwise is worse than one shown as unscheduled, because the first
// is a claim about the building.
//
// The arithmetic lives in `scheduleState` as a pure function. It is the part with
// off-by-one and boundary bugs, and it is the part that has to be testable without
// a viewer, a date picker and a WebGL context.

/** The states an element can be in on a given day. */
export const STATES = {
	UNSCHEDULED: "unscheduled",
	FUTURE: "future",
	ACTIVE: "active",
	COMPLETE: "complete",
};

const DAY = 24 * 60 * 60 * 1000;

/** Parse a date to epoch millis, or null. Accepts ISO strings and Date objects. */
export function toTime(value) {
	if (value === null || value === undefined || value === "") return null;
	if (value instanceof Date) return Number.isFinite(value.getTime()) ? value.getTime() : null;
	if (typeof value === "number") return Number.isFinite(value) ? value : null;
	const t = Date.parse(String(value));
	return Number.isFinite(t) ? t : null;
}

/**
 * Where one BOQ line stands on a given day.
 *
 * The boundaries are inclusive at both ends of a day, because a task planned to
 * start on the 3rd is being worked on during the 3rd -- an exclusive end would show
 * a one-day task as never active, and a task as complete the moment it starts.
 *
 * `actual_percent_complete` is reported but never used to decide the state. A line
 * the database says is 100% done but whose plan has not started is a real
 * discrepancy, and the overlay's job is to SHOW that, not to resolve it silently.
 */
export function scheduleState(item, at) {
	const when = toTime(at);
	if (when === null) return { state: STATES.UNSCHEDULED, reason: "no date given" };

	const start = toTime(item && item.planned_start);
	const end = toTime(item && item.planned_end);
	if (start === null || end === null) {
		return { state: STATES.UNSCHEDULED, reason: "the line has no planned dates" };
	}
	if (end < start) {
		// Refused rather than normalised. Two dates the wrong way round is a data
		// error, and swapping them silently would draw a task that runs backwards.
		return { state: STATES.UNSCHEDULED, reason: "the planned end is before the planned start" };
	}

	const actual = Number(item.actual_percent_complete);
	const hasActual = Number.isFinite(actual);
	const out = { actual_percent_complete: hasActual ? actual : null };

	if (when < start) {
		return { ...out, state: STATES.FUTURE, fraction: 0 };
	}
	if (when > end) {
		// Past the planned end. Still checked for `behind`: a line whose window has
		// closed at 20% is the single most useful thing this overlay shows, and
		// returning a bare COMPLETE made a late task indistinguishable from a
		// finished one. The state is still COMPLETE -- the PLAN is complete -- and
		// the flag is what tells the viewer not to believe it.
		const late = hasActual && actual < 100 ? { behind: true } : {};
		return { ...out, ...late, state: STATES.COMPLETE, fraction: 1 };
	}

	// Inside the window. The fraction is over the whole span, not the elapsed
	// fraction of today, so a scrub bar reads as a plan.
	const span = end - start;
	const fraction = span === 0 ? 1 : (when - start) / span;
	const state = {
		...out,
		state: STATES.ACTIVE,
		fraction: Math.min(1, Math.max(0, fraction)),
	};
	// The one thing this overlay exists to surface: a line that is finished, or
	// part-finished, outside the window it was planned for.
	if (hasActual && actual >= 100 && when < end) {
		state.ahead = true;
	}
	if (hasActual && actual < 100 && when >= end) {
		state.behind = true;
	}
	return state;
}

/** The span a set of items occupies, so a scrubber can cover all of them. */
export function scheduleSpan(items) {
	const starts = [];
	const ends = [];
	(items || []).forEach((i) => {
		const s = toTime(i && i.planned_start);
		const e = toTime(i && i.planned_end);
		if (s !== null) starts.push(s);
		if (e !== null) ends.push(e);
	});
	if (!starts.length || !ends.length) return null;
	return { from: Math.min(...starts), to: Math.max(...ends) };
}

/**
 * The overlay: a scrub bar and a legend, over the model.
 *
 * @param {HTMLElement} host
 * @param {object} handlers { onScrub, getEngine }
 */
export function createScheduleOverlay(host, handlers = {}) {
	let items = [];
	let byGuid = new Map(); // global_id -> BOQ item
	let span = null;
	let at = null;

	host.innerHTML = `
		<div class="bim-sched" hidden>
			<div class="bim-sched-head">
				<span class="bim-sched-label">4D Schedule</span>
				<span class="bim-sched-date"></span>
			</div>
			<input class="bim-sched-scrub" type="range" min="0" max="1000" value="0" step="1"
				aria-label="Schedule date">
			<div class="bim-sched-legend">
				<span class="bim-sched-key" data-state="future">Not started</span>
				<span class="bim-sched-key" data-state="active">In progress</span>
				<span class="bim-sched-key" data-state="complete">Complete</span>
				<span class="bim-sched-key" data-state="unscheduled">Unscheduled</span>
			</div>
			<div class="bim-sched-coverage"></div>
		</div>`;

	const root = host.querySelector(".bim-sched");
	const scrub = host.querySelector(".bim-sched-scrub");
	const dateEl = host.querySelector(".bim-sched-date");
	const coverageEl = host.querySelector(".bim-sched-coverage");

	/**
	 * Load the mapping. `links` are `{global_id, boq_item}` rows.
	 *
	 * The coverage line is the honest part: it says how much of the model this
	 * overlay can actually speak for, so a viewer who assumes "the whole model is
	 * scheduled" is corrected on screen rather than in a document.
	 */
	function load(items_, links) {
		items = items_ || [];
		byGuid = new Map();
		(links || []).forEach((l) => {
			if (l && l.global_id && l.boq_item) byGuid.set(l.global_id, l.boq_item);
		});
		span = scheduleSpan(items);
		root.hidden = false;

		if (!span) {
			scrub.hidden = true;
			dateEl.textContent = "No planned dates on this BOQ";
			coverageEl.textContent = "";
			return { scheduled: 0, total: 0 };
		}
		scrub.hidden = false;
		setAt(span.from);
		return coverage();
	}

	function setAt(time) {
		at = toTime(time);
		if (!span) return;
		const total = Math.max(1, span.to - span.from);
		scrub.value = String(Math.round(((at - span.from) / total) * 1000));
		dateEl.textContent = new Date(at).toISOString().slice(0, 10);
		apply();
	}

	function scrubTo(value) {
		if (!span) return;
		const total = Math.max(1, span.to - span.from);
		setAt(span.from + (Number(value) / 1000) * total);
	}

	/** Per-state counts, and how much of the model has no schedule at all. */
	function coverage() {
		const states = { future: 0, active: 0, complete: 0, unscheduled: 0 };
		items.forEach((item) => {
			states[scheduleState(item, at).state] += 1;
		});
		const total = items.length;
		const scheduled = total - states.unscheduled;
		coverageEl.textContent = total
			? `${scheduled} of ${total} BOQ lines scheduled on this date`
			: "";
		return { states, total, scheduled };
	}

	function apply() {
		const counts = coverage();
		if (handlers.onScrub) handlers.onScrub({ at, counts, schedule: (item) => scheduleState(item, at) });
	}

	scrub.addEventListener("input", (e) => scrubTo(e.target.value));

	return {
		load,
		setAt,
		scrubTo,
		coverage,
		at: () => at,
		span: () => span,
		element: host,
		root,
	};
}
