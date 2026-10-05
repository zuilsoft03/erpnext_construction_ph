# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Subcontractor EWT remittance and the BIR Form 2307 register.

Scope (docs/ROADMAP.md §Phase 1, blueprint §2.2): when a GC pays a subcontractor it
withholds 2% creditable withholding tax (ATC WC120 corporate / WI120 individual),
issues the subcontractor a BIR Form 2307, and remits on Form 0619-E (monthly) or
1601-EQ (quarterly). This module owns the **capture** -- the register rows built
from submitted Purchase Invoices. PH5-04 only renders these rows to a PDF.

ERPNext v17 does not create withholding tax rows by itself. `TaxService`
(erpnext/accounts/services/taxes.py) only handles Taxes and Charges templates and
item tax templates; nothing maps a Tax Withholding Category onto a
`is_tax_withholding_account` row. So the app has to add the row itself, which is
what `apply_ewt_to_purchase_invoice` does.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, get_datetime, get_quarter_start

EWT_PAYABLE_ACCOUNT_NAME = "Expanded Withholding Tax Payable 1601-EQ"
EWT_RATE = 2.0

FORMS = {"monthly": "0619-E", "quarterly": "1601-EQ"}


def ewt_account(company, category):
	"""The account a Tax Withholding Category is bound to for this company."""
	for row in frappe.get_all(
		"Tax Withholding Account",
		filters={"parent": category},
		fields=["company", "account"],
	):
		if row["company"] == company:
			return row["account"]
	return None


def apply_ewt_to_purchase_invoice(doc, method=None):
	"""Append the 2% EWT deduction row to a Purchase Invoice, idempotently.

	Base is the net-of-VAT amount, per blueprint §2.2 ("Net Subcon Accomplishment
	(ex-VAT)"). Withholding is a deduction from what the GC pays, so the row is
	Deduct and the payable account is credited.
	"""
	# Not guarded on is_new(): frappe.get_doc({...}) does not set __islocal, so
	# is_new() is False for a document built from a dict and the row would never
	# be added. Idempotency comes from the existing-row check below instead.
	if doc.doctype != "Purchase Invoice" or doc.docstatus:
		return

	category = _purchase_category(doc)
	if not category:
		# A purchase against a construction project that carries no withholding
		# category is refused rather than silently paid gross. Letting it through
		# would mean paying a subcontractor with no 2% withheld and no 2307, which
		# is a compliance failure that only surfaces at remittance time.
		if _is_construction_purchase(doc):
			frappe.throw(
				_("Purchase Invoice {0} is against a construction project but neither the "
				  "item nor supplier {1} carries a Tax Withholding Category. Assign one "
				  "before submitting -- 2% CWT cannot be inferred.").format(
					doc.name, doc.supplier
				),
				title=_("EWT Category Required"),
			)
		return

	account = ewt_account(doc.company, category)
	if not account:
		frappe.throw(
			_("Tax Withholding Category {0} is not bound to an account for {1}.").format(
				category, doc.company
			),
			title=_("EWT Account Missing"),
		)

	# Already present (e.g. a copied invoice) -- do not stack a second deduction.
	for row in doc.taxes:
		if row.get("custom_tax_withholding_category") == category or row.get("is_tax_withholding_account"):
			return

	base = _net_of_vat_base(doc)
	amount = flt(base * EWT_RATE / 100.0, 2)
	if not amount:
		return

	doc.append(
		"taxes",
		{
			"charge_type": "Actual",
			"add_deduct_tax": "Deduct",
			"account_head": account,
			"description": _("2% Creditable Withholding Tax (BIR Form 2307)"),
			"tax_amount": amount,
			"category": "Total",
			# Give ERPNext the rate and the base and let it recompute the amount.
			# A flat tax_amount with rate 0 is recalculated to 0 on save, which
			# would leave the register and the GL disagreeing by the whole 2%.
			"rate": EWT_RATE,
			"base_tax_amount": base,
			"is_tax_withholding_account": 1,
			"cost_center": doc.items[0].get("cost_center") if doc.items else None,
			"custom_tax_withholding_category": category,
		},
	)


def _purchase_category(doc):
	"""The withholding category for this purchase, or None.

	Item-level wins over the supplier default, matching ERPNext's own resolution
	in stock/get_item_details.py:get_tax_withholding_category.
	"""
	for item in doc.items:
		category = item.get("purchase_tax_withholding_category") or frappe.db.get_value(
			"Item", item.get("item_code"), "purchase_tax_withholding_category"
		)
		if category:
			return category
	if doc.supplier:
		return frappe.db.get_value("Supplier", doc.supplier, "tax_withholding_category")
	return None


def _is_construction_purchase(doc):
	"""True when the invoice is against a construction project.

	Matches api/procurement_hooks.py, which keys the Purchase Order hook on the
	same link, so the PO and the PI agree about which purchases are in scope.
	"""
	project = doc.get("project")
	if not project:
		return False
	if frappe.db.exists("Construction Project", project):
		return True
	return bool(
		frappe.db.exists("Construction Project", {"erpnext_project": project})
	)


def _net_of_vat_base(doc):
	"""The ex-VAT accomplishment, i.e. the base the 2% is taken on.

	Computed from the item rows rather than from `doc.net_total`, and from
	qty x rate rather than from `item.amount`, because this runs in
	`before_validate` where *neither* total has been calculated yet -- both are
	computed later inside `validate`. An instrumented run returned base 0.00 and
	exited on the zero-amount branch for exactly that reason.

	Prefer the stored `amount` when present (it is authoritative once the document
	has been validated) and fall back to qty x rate before that.
	"""
	total = 0.0
	for item in doc.items:
		amount = flt(item.get("amount"))
		if not amount:
			amount = flt(item.get("qty")) * flt(item.get("rate")) - flt(item.get("discount_amount"))
		total += amount
	return flt(total, 2)


def finalise_ewt_on_purchase_invoice(doc, method=None):
	"""Re-assert the EWT figures after ERPNext's tax recomputation.

	A row appended in `before_validate` is rebuilt during `validate`: an
	instrumented run showed rate, base_tax_amount and tax_amount all stored as
	0.0, which would post nothing to account 21070 and leave the register
	disagreeing with the GL by the whole 2%. `before_submit` runs after that
	recomputation, so the figures set here are the ones the GL receives.
	"""
	if doc.doctype != "Purchase Invoice":
		return
	for row in doc.taxes:
		if not row.get("is_tax_withholding_account"):
			continue
		base = _net_of_vat_base(doc)
		row.rate = EWT_RATE
		row.base_tax_amount = base
		row.tax_amount = flt(base * EWT_RATE / 100.0, 2)
		row.total = row.tax_amount
		row.base_total = row.tax_amount
		row.dont_recompute_tax = 1


def post_ewt_journal(doc, method=None):
	"""Book the withheld EWT with a Journal Entry on submit.

	ERPNext composes a Purchase Invoice's GL from its items alone: a submitted
	invoice in this environment posts exactly Dr Cost of Goods Sold 500,000 and
	Cr Creditors 500,000, and the `Deduct` tax row is not picked up at all — not
	with `rate` 0, not with `dont_recompute_tax`, not when the figures are
	re-asserted in `before_submit`. Rather than keep fighting the tax engine, the
	withholding is booked the way a bookkeeper would: a journal that moves the
	amount out of the supplier payable and into the EWT payable (Account 21070).

	It is submitted, not merely inserted: a draft journal posts nothing, and an
	earlier run created the row correctly and still produced no GL entry on 21070.

	Idempotent by construction: the journal carries a deterministic
	`cheque_no` derived from the invoice, and an existing submitted one is left
	alone, so re-submitting or re-running never double-books.
	"""
	if doc.doctype != "Purchase Invoice":
		return
	ewt_rows = [r for r in doc.taxes if r.get("is_tax_withholding_account") and flt(r.tax_amount)]
	if not ewt_rows:
		return

	party_account = _supplier_payable_account(doc)
	if not party_account:
		frappe.log_error(
			title="Construction: EWT journal has no payable account",
			message="No supplier payable account for {0} on {1}; EWT not booked.".format(
				doc.supplier, doc.name
			),
		)
		return

	for row in ewt_rows:
		token = "EWT-{0}".format(doc.name)
		if frappe.db.exists("Journal Entry", {"cheque_no": token, "docstatus": 1}):
			continue
		amount = flt(abs(flt(row.tax_amount)), 2)
		if not amount:
			continue
		frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Bank Entry",
				"doc_type": "Bank Entry",
				"company": doc.company,
				"posting_date": doc.posting_date,
				# Same trap as api/billing_engine.py: ERPNext overwrites
				# posting_date with today unless set_posting_time is set
				# (erpnext/utilities/transaction_base.py:34). A journal dated today
				# would fall outside the SAWT/1601-EQ period it belongs to.
				"set_posting_time": 1,
				"posting_time": frappe.utils.nowtime(),
				"cheque_no": token,
				"cheque_date": doc.posting_date,
				"user_remark": "2% CWT withheld from {0} on {1} (BIR Form 2307)".format(
					doc.supplier, doc.name
				),
				"accounts": [
					{
						"account": party_account,
						"debit_in_account_currency": amount,
						"credit_in_account_currency": 0.0,
						"cost_center": doc.items[0].get("cost_center") if doc.items else None,
						# A journal row on a Payable account is rejected without the
						# party it clears.
						"party_type": "Supplier",
						"party": doc.supplier,
					},
					{
						"account": row.account_head,
						"debit_in_account_currency": 0.0,
						"credit_in_account_currency": amount,
						"cost_center": doc.items[0].get("cost_center") if doc.items else None,
					},
				],
			}
		).insert(ignore_permissions=True).submit()


def _supplier_payable_account(doc):
	"""The payable this invoice credits, so the withholding journal nets against it.

	Read from the invoice's own GL entries, which exist by the time `on_submit`
	runs. A Purchase Invoice in this environment has an empty `accounts` table,
	so reading it first finds nothing.
	"""
	for row in frappe.get_all(
		"GL Entry",
		filters={"voucher_no": doc.name, "voucher_type": "Purchase Invoice", "is_cancelled": 0},
		fields=["account", "credit_in_account_currency"],
		order_by="creation asc",
	):
		if flt(row["credit_in_account_currency"]):
			return row["account"]
	return None


@frappe.whitelist()
def register_row(purchase_invoice):
	"""One BIR Form 2307 capture row for a submitted Purchase Invoice."""
	doc = frappe.get_doc("Purchase Invoice", purchase_invoice)
	if doc.docstatus != 1:
		frappe.throw(
			_("Purchase Invoice {0} is not submitted.").format(purchase_invoice),
			title=_("Invoice Not Submitted"),
		)

	withholding = [
		row for row in doc.taxes if row.get("is_tax_withholding_account") or flt(row.get("tax_amount")) < 0
	]
	if not withholding:
		return None

	row = withholding[0]
	category = row.get("custom_tax_withholding_category")
	if not category:
		category = _purchase_category(doc)

	tin = frappe.db.get_value("Supplier", doc.supplier, "tax_id")
	if not (tin or "").strip():
		frappe.throw(
			_("Supplier {0} has no TIN; a Form 2307 cannot be issued without one.").format(doc.supplier),
			title=_("2307 Capture Blocked"),
		)

	atc = frappe.db.get_value("Tax Withholding Category", category, "custom_atc_code") if category else None
	if not atc:
		frappe.throw(
			_("Purchase Invoice {0} withholds tax with no ATC code; set one on {1}.").format(
				purchase_invoice, category
			),
			title=_("2307 Capture Blocked"),
		)

	return {
		"purchase_invoice": doc.name,
		"posting_date": str(doc.posting_date or ""),
		"payee": doc.supplier,
		"payee_name": frappe.db.get_value("Supplier", doc.supplier, "supplier_name"),
		"tin": tin,
		"atc": atc,
		"tax_withholding_category": category,
		"base_net_of_vat": _net_of_vat_base(doc),
		"amount_withheld": flt(abs(flt(row.tax_amount)), 2),
		"currency": doc.currency,
	}


@frappe.whitelist()
def reconcile(period_start=None, period_end=None, company=None, frequency="quarterly", anchor=None):
	"""Register total for a period against the EWT payable (account 21070)."""
	company = company or frappe.defaults.get_user_default("Company")
	if not (period_start and period_end):
		if not anchor:
			frappe.throw(
				_("Give a period start and end, or an anchor date."), title=_("EWT Period")
			)
		anchor = get_datetime(anchor).date()
		if frequency == "monthly":
			period_start, period_end = anchor.replace(day=1), add_months(anchor.replace(day=1), 1)
		else:
			period_start = get_quarter_start(anchor)
			period_end = add_months(period_start, 3)
	period_start = get_datetime(period_start).date()
	period_end = get_datetime(period_end).date()

	rows = []
	for name in frappe.get_all(
		"Purchase Invoice",
		filters={"company": company, "docstatus": 1, "posting_date": ["between", [period_start, period_end]]},
		pluck="name",
		order_by="posting_date asc, name asc",
	):
		row = register_row(name)
		if row:
			rows.append(row)

	register_total = flt(sum(r["amount_withheld"] for r in rows))
	account = frappe.db.get_value(
		"Account", {"account_name": EWT_PAYABLE_ACCOUNT_NAME, "company": company}, "name"
	)
	gl_total = None
	if account:
		entries = frappe.get_all(
			"GL Entry",
			filters={
				"account": account,
				"posting_date": ["between", [period_start, period_end]],
				"is_cancelled": 0,
			},
			fields=["credit_in_account_currency", "debit_in_account_currency"],
		)
		gl_total = flt(
			sum(flt(e["credit_in_account_currency"]) - flt(e["debit_in_account_currency"]) for e in entries)
		)

	return {
		"company": company,
		"form": FORMS[frequency],
		"frequency": frequency,
		"period_start": str(period_start),
		"period_end": str(period_end),
		"account": account,
		"register_total": register_total,
		"gl_total": gl_total,
		"variance": flt(register_total - gl_total) if gl_total is not None else None,
		"rows": rows,
		"reconciled": flt(register_total - gl_total) == 0 if gl_total is not None else None,
		"csv": to_csv(rows),
	}


CSV_COLUMNS = (
	"purchase_invoice",
	"posting_date",
	"payee_name",
	"tin",
	"atc",
	"base_net_of_vat",
	"amount_withheld",
	"currency",
)


def to_csv(rows):
	import csv
	import io

	buf = io.StringIO()
	writer = csv.writer(buf, lineterminator="\n")
	writer.writerow(CSV_COLUMNS)
	for row in rows:
		writer.writerow([row.get(c, "") for c in CSV_COLUMNS])
	return buf.getvalue()
