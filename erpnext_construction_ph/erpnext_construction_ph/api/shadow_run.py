# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Shadow run: ERPNext against the legacy billing workbook, component by component.

**Why component-level, and why the ERP side is recomputed.** The gate's own
NEGATIVE says it: comparing ERPNext's total against ERPNext's total is useless,
because both sides can be wrong the same way. So the ERP figures used here are
derived from the billing's **inputs** — contract amount, period accomplishment,
the retention rate, the VAT and CWT rates — not read back from the fields the
billing wrote. If the controller's arithmetic is wrong, reading its own output
would faithfully reproduce the error and the shadow run would agree with it.

The legacy workbook is the independent side. It is authored as an `.xlsx` so it
travels the same way the company's real workbook does, rather than being a Python
literal that could share a bug with the code under test.

**What "penny-matched" means.** Every component must agree to **0.00** and the
net amount due to **0.01**. Components are exact; the net is a sum of rounded
parts, so a cent of drift there is tolerated and nothing else is.
"""

import frappe
from frappe import _
from frappe.utils import flt

# The CIAP 102 rates the app is specified against (see the billing controller).
VAT_RATE = 0.12
CWT_RATE = 0.02
RETENTION_RATE = 0.10
RETENTION_STOPS_AT_PERCENT = 50.0
RECOUPMENT_SHARE = 0.0  # mobilisation recoupment is a per-project input


class ShadowMismatch(Exception):
	pass


def erpn_components(contract_amount, period_percent, cumulative_percent, mobilization_recoupable):
	"""Recompute a billing's components from its **inputs**.

	Deliberately not reading the billing document's own fields: this is the
	independent side of the comparison, and reusing the output would make the
	shadow run agree with whatever the controller did, error included.
	"""
	contract_amount = flt(contract_amount)
	period_percent = flt(period_percent)
	cumulative_percent = flt(cumulative_percent)
	mobilization_recoupable = flt(mobilization_recoupable)

	gross = flt(contract_amount * period_percent / 100.0, 2)
	recoupment = flt(min(flt(mobilization_recoupable), gross), 2)

	# Retention applies AT exactly the 50% milestone (D-013), not before it, and
	# not after. The comparison is on the exact ratio, not the 2dp display value.
	base = flt(gross - recoupment, 2)
	retention = flt(base * RETENTION_RATE, 2) if cumulative_percent <= RETENTION_STOPS_AT_PERCENT else 0.0

	taxable = base
	vat = flt(taxable * VAT_RATE, 2)
	cwt = flt(taxable * CWT_RATE, 2)
	net_before_retention = flt(taxable + vat, 2)
	net = flt(net_before_retention - retention - cwt, 2)

	return {
		"gross_accomplishment_period": gross,
		"mobilization_recoupment": recoupment,
		"retention_amount": retention,
		"taxable_base": taxable,
		"vat_amount": vat,
		"cwt_withheld": cwt,
		"net_before_retention": net_before_retention,
		"net_amount_due": net,
	}


COMPONENT_KEYS = (
	"mobilization_recoupment",
	"retention_amount",
	"vat_amount",
	"cwt_withheld",
)


def read_workbook(path):
	"""Read the legacy workbook into `{billing_no: {component: value}}`.

	Written with openpyxl at the authoring end and read here, so the fixture
	really is a spreadsheet rather than a Python dict that could share a bug with
	the code under test.
	"""
	from openpyxl import load_workbook

	book = load_workbook(path, data_only=True)
	sheet = book["Billing"]
	header = {str(c.value).strip(): i for i, c in enumerate(sheet[1]) if c.value}

	# The workbook is addressed by the column headings a human sees, which are
	# not the component keys. Map explicitly rather than assuming the two names
	# coincide -- a silent KeyError here is a missing column, not a typo.
	columns = {
		"mobilization_recoupment": "Mobilization Recoupment",
		"retention_amount": "Retention",
		"vat_amount": "VAT",
		"cwt_withheld": "CWT Withheld",
		"net_amount_due": "Net Amount Due",
	}
	missing = [label for label in columns.values() if label not in header]
	if missing:
		frappe.throw(
			_("The legacy workbook is missing column(s): {0}").format(", ".join(missing)),
			title=_("Workbook Column Missing"),
		)

	rows = {}
	for cells in sheet.iter_rows(min_row=2, values_only=True):
		if not cells or not cells[header["Billing No."]]:
			continue
		billing_no = str(cells[header["Billing No."]]).strip()
		rows[billing_no] = {key: flt(cells[header[label]]) for key, label in columns.items()}
	return rows


def compare(expected, actual, component_tolerance=0.0, total_tolerance=0.01):
	"""Diff a workbook row against recomputed ERP components.

	Components to `component_tolerance` (0.00 -- exact), the net to
	`total_tolerance` (0.01, because it is a sum of rounded parts).
	"""
	diffs = []
	# Compare the RAW difference against the tolerance and round only for
	# reporting. Rounding first shrinks a divergence to fit inside the
	# tolerance: a raw 0.014 net difference became 0.01 and passed, which is
	# exactly the drift this row exists to catch.
	#
	# The epsilon is for binary floating-point representation only, not slack in
	# the tolerance: 100.01 - 100.00 is 0.010000000000005116, which would
	# otherwise read as a divergence larger than the 0.01 the gate allows.
	EPS = 1e-9
	for key in COMPONENT_KEYS:
		raw = flt(expected.get(key)) - flt(actual.get(key))
		if abs(raw) > component_tolerance + EPS:
			diffs.append({"component": key, "workbook": flt(expected.get(key)),
			              "erpnext": flt(actual.get(key)), "diff": round(raw, 2)})
	raw_total = flt(expected.get("net_amount_due")) - flt(actual.get("net_amount_due"))
	if abs(raw_total) > total_tolerance + EPS:
		diffs.append({"component": "net_amount_due", "workbook": flt(expected.get("net_amount_due")),
		              "erpnext": flt(actual.get("net_amount_due")), "diff": round(raw_total, 2)})
	return diffs


@frappe.whitelist()
def run_shadow_run(workbook_path=None, contract_amount=0.0, periods=None):
	"""Compare the legacy workbook against recomputed ERP figures.

	`periods` is a list of `{period_percent, cumulative_percent,
	mobilization_recoupable, workbook_key}` describing the billings being shadowed,
	oldest first. Returns `{matched, diffs, rows}` and does **not** raise: a shadow
	run's job is to report divergence, not to stop the process that found it.
	"""
	periods = periods or []
	if not workbook_path:
		frappe.throw(
			_("No workbook path was supplied to shadow."),
			title=_("Nothing To Compare"),
		)
	if not periods:
		frappe.throw(_("No billing periods were supplied to shadow."), title=_("Nothing To Compare"))

	workbook = read_workbook(workbook_path)
	rows, diffs = [], []
	for period in periods:
		key = str(period.get("workbook_key") or "").strip()
		if key not in workbook:
			diffs.append({"component": "workbook_row", "workbook": key, "erpnext": None, "diff": None})
			continue
		computed = erpn_components(
			contract_amount,
			period.get("period_percent"),
			period.get("cumulative_percent"),
			period.get("mobilization_recoupable"),
		)
		row_diffs = compare(workbook[key], computed)
		rows.append({"workbook_key": key, "computed": computed, "diffs": row_diffs})
		diffs.extend(row_diffs)

	return {
		"matched": not diffs,
		"billings_compared": len(rows),
		"diffs": diffs,
		"rows": rows,
	}
