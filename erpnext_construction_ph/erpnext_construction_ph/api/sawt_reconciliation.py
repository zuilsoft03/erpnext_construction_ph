# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""SAWT reconciliation — sales-side creditable withholding tax.

The bookkeeper remits CWT that the GC withheld from its client. Before filing,
two figures have to agree:

- the **subledger**: the sum of ``cwt_withheld`` on submitted Construction
  Progress Billing documents in the period;
- the **general ledger**: the movement on the CWT asset account ("Prepaid Tax -
  Creditable Withholding Tax 2307", account 11050) for the same period.

Reconciling per document rather than only in total is the point. A total can be
right by accident — one billing short and another long — and a tax filing that
hides that is a filing nobody can defend. Every row here carries its own
variance, and GL movement that belongs to no billing in the period is reported as
unmatched rather than folded into the total.

Scope (docs/ROADMAP.md §Phase 1): this reconciles the *sales-side* CWT against
account 11050. The purchase-side EWT and the Form 2307 register are PH1-03.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_datetime, get_quarter_start

CWT_ASSET_ACCOUNT_NAME = "Prepaid Tax - Creditable Withholding Tax 2307"

# BIR withholding remittance forms: 0619-E is monthly, 1601-EQ is quarterly.
FORMS = {"monthly": "0619-E", "quarterly": "1601-EQ"}


def period_bounds(anchor, frequency="quarterly"):
	"""Return (start, end) for the monthly or quarterly period containing `anchor`."""
	anchor = get_datetime(anchor).date()
	if frequency == "monthly":
		start = anchor.replace(day=1)
		return start, add_months(start, 1)
	if frequency == "quarterly":
		start = get_quarter_start(anchor)
		return start, add_months(start, 3)
	frappe.throw(
		_("Unknown frequency {0}; use 'monthly' (0619-E) or 'quarterly' (1601-EQ).").format(frequency),
		title=_("SAWT Frequency"),
	)


def cwt_asset_account(company):
	"""The CWT asset account for a company, resolved by account_name.

	Accounts are named "<account_name> - <abbr>", so building the name by hand
	invites the same off-by-abbr bug the Phase 0 GL account guard had.
	"""
	name = frappe.db.get_value(
		"Account", {"account_name": CWT_ASSET_ACCOUNT_NAME, "company": company}, "name"
	)
	if not name:
		frappe.throw(
			_("Company {0} has no '{1}' account; run setup.accounts first.").format(
				company, CWT_ASSET_ACCOUNT_NAME
			),
			title=_("SAWT Unavailable"),
		)
	return name


def _gl_movement(account, start, end, voucher_no=None):
	"""Net debit-minus-credit on an account for a period (optionally one voucher)."""
	filters = {
		"account": account,
		"posting_date": ["between", [start, end]],
		"is_cancelled": 0,
	}
	if voucher_no:
		filters["voucher_no"] = voucher_no
	rows = frappe.get_all(
		"GL Entry",
		filters=filters,
		fields=["debit_in_account_currency", "credit_in_account_currency"],
	)
	return flt(sum(flt(r["debit_in_account_currency"]) - flt(r["credit_in_account_currency"]) for r in rows))


@frappe.whitelist()
def reconcile(period_start=None, period_end=None, company=None, frequency="quarterly", anchor=None):
	"""Reconcile billed CWT against the CWT asset account for a period.

	Returns per-billing rows with their own variance, the period totals, and any
	GL movement that no billing in the period accounts for.
	"""
	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		frappe.throw(_("No company selected."), title=_("SAWT Unavailable"))

	if not (period_start and period_end):
		if not anchor:
			frappe.throw(
				_("Give a period start and end, or an anchor date to derive them from."),
				title=_("SAWT Period"),
			)
		period_start, period_end = period_bounds(anchor, frequency)
	period_start = get_datetime(period_start).date()
	period_end = get_datetime(period_end).date()

	account = cwt_asset_account(company)

	billings = frappe.get_all(
		"Construction Progress Billing",
		filters={
			"company": company,
			"docstatus": 1,
			"posting_date": ["between", [period_start, period_end]],
		},
		fields=["name", "billing_number", "posting_date", "cwt_withheld", "sales_invoice"],
		order_by="posting_date asc, name asc",
	)

	rows = []
	matched_vouchers = set()
	for billing in billings:
		invoice = billing["sales_invoice"]
		# A billing with no Sales Invoice yet has been submitted but not posted by
		# the engine, so its subledger figure has no GL counterpart yet.
		gl_for_doc = _gl_movement(account, period_start, period_end, invoice) if invoice else None
		if invoice:
			matched_vouchers.add(invoice)
		subledger = flt(billing["cwt_withheld"])
		variance = flt(subledger - gl_for_doc) if gl_for_doc is not None else None
		rows.append(
			{
				"billing": billing["name"],
				"billing_number": billing["billing_number"],
				"posting_date": cstr_date(billing["posting_date"]),
				"sales_invoice": invoice,
				"billed_cwt": subledger,
				"gl_cwt": gl_for_doc,
				"variance": variance,
				"unposted": gl_for_doc is None,
			}
		)

	subledger_total = flt(sum(r["billed_cwt"] for r in rows))
	gl_total = _gl_movement(account, period_start, period_end)

	# GL movement in the period that no billing in the period explains. This is
	# what a billing dated outside the period looks like from the GL's side.
	unmatched = []
	for entry in frappe.get_all(
		"GL Entry",
		filters={
			"account": account,
			"posting_date": ["between", [period_start, period_end]],
			"is_cancelled": 0,
		},
		fields=["voucher_no", "voucher_type", "posting_date", "debit_in_account_currency", "credit_in_account_currency"],
	):
		if entry["voucher_no"] in matched_vouchers:
			continue
		unmatched.append(
			{
				"voucher_no": entry["voucher_no"],
				"voucher_type": entry["voucher_type"],
				"posting_date": cstr_date(entry["posting_date"]),
				"amount": flt(entry["debit_in_account_currency"]) - flt(entry["credit_in_account_currency"]),
			}
		)

	# A total that nets an unmatched entry away is exactly the absorption the
	# gate forbids, so the reported variance excludes it and the list is separate.
	unmatched_total = flt(sum(u["amount"] for u in unmatched), 2)
	# Rounded once and used for both the reported variance and the reconciled
	# flag, so the two can never disagree about whether the quarter is clean.
	delta = flt(subledger_total - gl_total, 2)

	return {
		"company": company,
		"form": FORMS[frequency],
		"frequency": frequency,
		"period_start": cstr_date(period_start),
		"period_end": cstr_date(period_end),
		"account": account,
		# Every peso figure is returned at 2dp. `flt(x)` with no precision argument
		# does NOT round, so an unrounded variance leaked values like
		# -499.99000000000524 into a 1601-EQ the bookkeeper reads. `delta` is rounded
		# once and drives both the reported variance and the reconciled flag, so a
		# sub-centavo residue cannot mark a clean quarter unreconciled.
		"billed_cwt_total": flt(subledger_total, 2),
		"gl_total": flt(gl_total, 2),
		"variance": delta,
		"unmatched_gl_total": flt(unmatched_total, 2),
		"billings": rows,
		"unmatched_gl": unmatched,
		"reconciled": delta == 0 and not unmatched,
		"csv": to_csv(rows),
	}


def cstr_date(value):
	return value.isoformat() if hasattr(value, "isoformat") else (value or "")


CSV_COLUMNS = (
	"billing_number",
	"posting_date",
	"sales_invoice",
	"billed_cwt",
	"gl_cwt",
	"variance",
)


def to_csv(rows):
	"""The per-document reconciliation, one line per billing."""
	import csv
	import io

	buf = io.StringIO()
	writer = csv.writer(buf, lineterminator="\n")
	writer.writerow(CSV_COLUMNS)
	for row in rows:
		writer.writerow(
			[
				row.get("billing_number", ""),
				row.get("posting_date", ""),
				row.get("sales_invoice", ""),
				row.get("billed_cwt", ""),
				"" if row.get("gl_cwt") is None else row.get("gl_cwt"),
				"" if row.get("variance") is None else row.get("variance"),
			]
		)
	return buf.getvalue()
