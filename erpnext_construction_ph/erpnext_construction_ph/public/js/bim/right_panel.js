// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The right panel: Properties, Issues, Viewpoints, and — in PDF mode — the
// takeoffs captured so far.
//
// THE ONE RULE THAT SHAPES THIS FILE: a value the database does not have is
// rendered as "not measured", never as a number. In a quantity-surveying tool the
// Properties card sits next to a bill, and `0` is a claim about the BUILDING
// ("this wall encloses no area") while "not measured" is a claim about the DATA
// ("nobody has measured it"). They are different sentences, and only one of them
// is true when the field is empty. So `null`/`undefined`/absent all render the
// same way, and the only way to get a `0` on screen is for the server to have
// sent a real `0`.
//
// The resolver lists which keys it could not fill, so the panel can say so in the
// user's terms rather than leaving four blank rows that look like a bug.

const esc = (s) =>
	String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const NOT_MEASURED = "Not measured";

/** The Properties card's rows, in the spec's order. Exported because the row
  SET is part of the contract: the panel has eight rows, and that is a fact a test
  should be able to assert rather than infer from a rendering. */
export const PROPERTY_ROWS = [
	// These two OPT IN to the em-dash. An IFC may name a spatial node or may not, and
	// that is a fact about the model, not a measurement that failed. Every other row
	// keeps the default, because a blank where a quantity belongs is the one
	// rendering a QS must never meet.
	{ key: "element_name", label: "Name", absent: "—" },
	{ key: "element_type", label: "Type", absent: "—" },
	{ key: "element_guid", label: "GlobalId" },
	{ key: "ifc_id", label: "IFC #" },
	// `format: "area"` because a bare float is unreadable in a quantity-surveying
	// tool: the measured value renders as "3569.543468157 m2", which is a figure no
	// one reads and every one of them rounds differently. The precision is chosen by
	// MAGNITUDE, not fixed -- a slab wants one decimal and a 0.16 m2 reinforcing bar
	// wants three, and a single fixed precision is wrong at one end or the other.
	{ key: "area", label: "Area", format: "area" },
	// The class travels with the number, so 0.23 m2 of reinforcement is never read
	// beside 16,464 m2 of slab as if the two were the same kind of thing.
	{ key: "area_class", label: "Area class", format: "area_class" },
	{ key: "boq_item", label: "BOQ Item", format: "boq" },
	{ key: "cost", label: "Cost", format: "money" },
	{ key: "progress_percent", label: "Progress", format: "progress" },
];

/** Format one value, or say plainly that it is not known. */
export function formatValue(row, value, unknown) {
	// THE DEFAULT IS "Not measured", and a row must OPT IN to "not present".
	//
	// A first attempt made absence universal -- any null rendered an em-dash -- and
	// it broke `test_every_unknown_value_renders_as_not_measured`, which was right to.
	// The rule that test protects is real: a quantity surveyor must never see a blank
	// where a measurement belongs, because a blank reads as "nothing to report" and
	// "not measured" reads as "we looked and there is none". Those are different
	// answers and only one of them is safe to guess at.
	//
	// So the distinction is PER ROW, and it is earned. An IFC may name a spatial node
	// or may not, and "—" is the honest rendering of that. An area, a cost or a
	// progress figure is a measurement, and for those the default stands.
	if (value === null || value === undefined || value === "") {
		return row.absent || NOT_MEASURED;
	}
	if (unknown && unknown.includes(row.key)) return NOT_MEASURED;
	switch (row.format) {
		case "area": {
			const n = Number(value);
			if (!Number.isFinite(n)) return String(value);
			// Precision by MAGNITUDE, not fixed. A slab and a reinforcing bar differ by
			// four orders of magnitude on this model -- 16,464 m2 against 0.16 -- so one
			// fixed precision is either noise on the big ones or a rounding error on
			// the small ones. A QS needs both readable and exact enough to total, and a
			// total of rounded figures is not the same number as a rounded total.
			const digits = n === 0 ? 2 : Math.abs(n) >= 100 ? 1 : Math.abs(n) >= 1 ? 2 : 3;
			// The unit is appended HERE, not from the row. The row used to carry
			// `unit: " m2"` and renderProperties had a separate branch that appended it to
			// `String(value)` -- which won, printed the raw float, and made this whole
			// formatter dead code for the one field that most needs it. ONE path formats.
			return n.toLocaleString("en-PH", { maximumFractionDigits: digits }) + (row.unit || " m2");
		}
		case "area_class": {
			// Plain, not prettified. The whole reason for measuring per type was that
			// reinforcement and surfaces are not interchangeable, and a friendly label
			// here would undo that at the point the user reads the number.
			return esc(String(value));
		}
		case "money":
			return typeof value === "number" ? `₱${value.toLocaleString("en-PH", { maximumFractionDigits: 2 })}` : String(value);
		case "progress": {
			const n = Number(value);
			if (!Number.isFinite(n)) return String(value);
			// A bare percentage with a bar, so a glance reads as progress rather
			// than as a number someone has to interpret.
			const width = Math.min(100, Math.max(0, n));
			return `<span class="bim-progress">${n}%<span class="bim-progress-bar"><i style="width:${width}%"></i></span></span>`;
		}
		case "boq":
			if (typeof value === "object") {
				const label = value.description || value.item_code || "";
				const code = value.item_code ? `<code>${esc(value.item_code)}</code> ` : "";
				return code + esc(label);
			}
			return esc(String(value));
		default:
			return typeof value === "number" ? String(value) : esc(String(value));
	}
}

export function createRightPanel(root, handlers = {}) {
	root.innerHTML = `
		<div class="bim-rp-card" data-card="properties">
			<div class="bim-rp-label">Properties</div>
			<div class="bim-rp-body" data-body="properties"></div>
		</div>
		<div class="bim-rp-card" data-card="issues">
			<div class="bim-rp-label">Issues (BCF)</div>
			<div class="bim-rp-body" data-body="issues"></div>
		</div>
		<div class="bim-rp-card" data-card="viewpoints">
			<div class="bim-rp-label">Viewpoints</div>
			<div class="bim-rp-body" data-body="viewpoints"></div>
		</div>
		<div class="bim-rp-card" data-card="takeoffs" hidden>
			<div class="bim-rp-label">Captured Takeoffs</div>
			<div class="bim-rp-body" data-body="takeoffs"></div>
		</div>`;

	const body = (id) => root.querySelector(`[data-body="${id}"]`);
	const card = (id) => root.querySelector(`[data-card="${id}"]`);

	function showCard(id, on) {
		const el = card(id);
		if (el) el.hidden = !on;
	}

	// ------------------------------------------------------------- properties
	/**
	 * Render the properties card.
	 *
	 * `notice` is what makes the NOT-IN-INDEX case say so. Without it, clicking an
	 * element the server has no row for fell through to the `null` branch and rendered
	 * "Select an element to see its properties" -- which is a DIFFERENT claim, not just
	 * a vaguer one: an element WAS selected, the server simply has no record of it. A
	 * user reading that concludes they never clicked anything, and clicks again.
	 */
	function renderProperties(element, notice) {
		const box = body("properties");
		if (!element) {
			box.innerHTML = `<div class="bim-rp-empty">${esc(
				notice || "Select an element to see its properties."
			)}</div>`;
			return;
		}
		const unknown = element.unknown || [];
		const rows = PROPERTY_ROWS.map(
			(row) => `<tr>
				<td class="bim-rp-key">${esc(row.label)}</td>
				<td class="bim-rp-val${unknown.includes(row.key) ? " is-unknown" : ""}">${
					row.unit && typeof element[row.key] === "number"
						? `${esc(String(element[row.key]))}${row.unit}`
						: formatValue(row, element[row.key], unknown)
				}</td>
			</tr>`
		).join("");
		box.innerHTML = `<table class="bim-rp-table">${rows}</table>`;
	}

	// ----------------------------------------------------------------- issues
	function renderIssues(topics, selectedGuid) {
		const box = body("issues");
		const mine = (topics || []).filter((t) =>
			selectedGuid ? (t.elements || []).some((e) => e.global_id === selectedGuid) : true
		);
		if (!mine.length) {
			box.innerHTML = `<div class="bim-rp-empty">${
				selectedGuid ? "No issues on this element." : "No issues on this model."
			}</div>`;
			return;
		}
		box.innerHTML =
			mine
				.map(
					(t) => `<div class="bim-rp-issue" data-topic="${esc(t.topic_guid || t.name)}" role="button" tabindex="0">
						<span class="bim-rp-issue-title">${esc(t.title || "")}</span>
						<span class="bim-rp-issue-meta">${esc(
							[t.priority, t.status, t.topic_type].filter(Boolean).join(" · ")
						)}</span>
					</div>`
				)
				.join("") +
			`<button class="btn btn-xs btn-primary bim-rp-new">+ New Issue on Selection</button>`;

		const open = (node) => () => handlers.onIssue && handlers.onIssue(node.dataset.topic);
		box.querySelectorAll(".bim-rp-issue").forEach((node) => {
			node.onclick = open(node);
			node.onkeydown = (e) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					open(node)();
				}
			};
		});
		const add = box.querySelector(".bim-rp-new");
		if (add) add.onclick = () => handlers.onNewIssue && handlers.onNewIssue();
	}

	// ------------------------------------------------------------- viewpoints
	function renderViewpoints(items) {
		const box = body("viewpoints");
		if (!items || !items.length) {
			box.innerHTML = `<div class="bim-rp-empty">No viewpoints yet. Raise an issue to record one.</div>`;
			return;
		}
		box.innerHTML = items
			.map(
				(v, i) => `<div class="bim-rp-vp" data-index="${i}" role="button" tabindex="0">
					${v.snapshot_image
						? `<img alt="Viewpoint ${i + 1}" src="${esc(v.snapshot_image)}">`
						: `<div class="bim-rp-vp-blank">${i + 1}</div>`}
					${v.comment ? `<span class="bim-rp-vp-note">${esc(v.comment)}</span>` : ""}
				</div>`
			)
			.join("");
		box.querySelectorAll(".bim-rp-vp").forEach((node) => {
			const jump = () => handlers.onViewpoint && handlers.onViewpoint(Number(node.dataset.index));
			node.onclick = jump;
			node.onkeydown = (e) => {
				if (e.key === "Enter" || e.key === " ") {
					e.preventDefault();
					jump();
				}
			};
		});
	}

	// ---------------------------------------------------------------- takeoffs
	function renderTakeoffs(rows) {
		const box = body("takeoffs");
		if (!rows || !rows.length) {
			box.innerHTML = `<div class="bim-rp-empty">Nothing captured yet.</div>`;
			return;
		}
		box.innerHTML = rows
			.map(
				(r, i) => `<div class="bim-rp-tk" data-index="${i}">
					<span>${esc(r.description || "")}</span>
					<span class="bim-rp-tk-qty">${Number(r.quantity).toFixed(2)} ${esc(r.unit || "")}</span>
				</div>`
			)
			.join("");
	}

	/** The takeoffs card only exists in PDF mode, per the spec. */
	function setMode(mode) {
		showCard("takeoffs", mode === "pdf");
	}

	return {
		renderProperties,
		renderIssues,
		renderViewpoints,
		renderTakeoffs,
		setMode,
		element: root,
	};
}
