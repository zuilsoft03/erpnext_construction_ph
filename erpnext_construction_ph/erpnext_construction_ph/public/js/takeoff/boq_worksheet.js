// BOQ paste helpers for erpnext_construction_ph.
//
// Loaded on every Desk page via hooks.py app_include_js. This is the client-side
// half of the BOQ worksheet: it mirrors the column contract that
// api/boq_matrix.py enforces server-side, so a paste is validated *before* it is
// sent and the estimator sees which line is wrong instead of a server error.
//
// Per docs/ARCHITECTURE.md:60, Desk pages use frappe.call() — no direct SQL here.

frappe.provide("erpnext_construction_ph.boq_worksheet");

Object.assign(window.erpnext_construction_ph.boq_worksheet, {
	// Must stay in step with COLUMNS in api/boq_matrix.py.
	COLUMNS: [
		"pay_item_number",
		"item_code",
		"description",
		"unit",
		"quantity",
		"direct_cost",
		"ocm",
		"profit",
	],

	NUMERIC: ["quantity", "direct_cost", "ocm", "profit"],

	HEADER_TOKENS: [
		"pay item number",
		"pay item no",
		"pay item",
		"item code",
		"item no",
		"description",
		"unit",
		"quantity",
		"qty",
		"direct cost",
		"unit cost",
		"cost",
		"ocm",
		"profit",
	],

	/** Split pasted text into row objects. Never throws — problems land in .errors. */
	parse(text) {
		const lines = String(text || "")
			.replace(/\r\n/g, "\n")
			.replace(/\r/g, "\n")
			.split("\n")
			.filter((line) => line.trim().length);

		const errors = [];
		const rows = [];
		if (!lines.length) {
			return { rows, errors, headerSkipped: false };
		}

		const first = lines[0].split("\t").map((c) => c.trim().toLowerCase());
		const headerSkipped = first.some((cell) => this.HEADER_TOKENS.includes(cell));
		const body = headerSkipped ? lines.slice(1) : lines;

		body.forEach((line, offset) => {
			// Excel numbers carry thousands separators; the server strips them too.
			const lineNo = offset + (headerSkipped ? 2 : 1);
			const cells = line.split("\t").map((c) => c.trim());
			if (cells.length !== this.COLUMNS.length) {
				errors.push(`Line ${lineNo}: ${cells.length} columns, expected ${this.COLUMNS.length}.`);
				return;
			}
			const row = {};
			this.COLUMNS.forEach((col, i) => {
				row[col] = this.NUMERIC.includes(col) ? this.toNumber(cells[i], col, lineNo, errors) : cells[i];
			});
			if (!row.item_code) {
				errors.push(`Line ${lineNo}: no Item Code.`);
				return;
			}
			rows.push(row);
		});

		return { rows, errors, headerSkipped };
	},

	toNumber(raw, column, lineNo, errors) {
		const text = String(raw || "").replace(/,/g, "").trim();
		if (!text) {
			return 0;
		}
		const value = Number(text);
		if (Number.isNaN(value)) {
			errors.push(`Line ${lineNo}: ${column} is not a number (${raw}).`);
			return 0;
		}
		return value;
	},

	/** Mirror of the server formula in ConstructionBOQItem.validate(). */
	unitPrice(row) {
		const direct = Number(row.direct_cost) || 0;
		const ocm = Number(row.ocm) || 0;
		const profit = Number(row.profit) || 0;
		return frappe.utils.flt(direct * (1 + ocm / 100 + profit / 100), 2);
	},

	total(row) {
		return frappe.utils.flt((Number(row.quantity) || 0) * this.unitPrice(row), 2);
	},

	/** Render a preview table; bad lines are highlighted rather than dropped silently. */
	preview($el, text) {
		const parsed = this.parse(text);
		const $table = $("<table class='boq-worksheet-preview'></table>");
		const $head = $("<thead></thead>").append(
			$("<tr></tr>")
				.append($("<th>Pay Item</th>"))
				.append($("<th>Item Code</th>"))
				.append($("<th>Description</th>"))
				.append($("<th>Unit</th>"))
				.append($("<th>Qty</th>"))
				.append($("<th>Direct Cost</th>"))
				.append($("<th>OCM %</th>"))
				.append($("<th>Profit %</th>"))
				.append($("<th>Unit Price</th>"))
				.append($("<th>Total</th>"))
		);
		$table.append($head);

		const $body = $("<tbody></tbody>");
		parsed.rows.forEach((row) => {
			$body.append(
				$("<tr></tr>")
					.append($("<td></td>").text(row.pay_item_number))
					.append($("<td></td>").text(row.item_code))
					.append($("<td></td>").text(row.description))
					.append($("<td></td>").text(row.unit))
					.append($("<td></td>").text(row.quantity))
					.append($("<td></td>").text(row.direct_cost))
					.append($("<td></td>").text(row.ocm))
					.append($("<td></td>").text(row.profit))
					.append($("<td></td>").text(this.unitPrice(row)))
					.append($("<td></td>").text(this.total(row)))
			);
		});
		$table.append($body);

		$el.empty().append($table);

		if (parsed.errors.length) {
			$el.append(
				$("<div class='alert alert-warning mt-2'></div>").text(
					`${parsed.errors.length} line(s) will be refused: ${parsed.errors.join(" ")}`
				)
			);
		}
		return parsed;
	},
});
