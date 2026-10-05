# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BIR Electronic Invoicing System (EIS) export for Sales Invoices.

EIS is the BIR's electronic invoicing channel. A GC submits one record per Sales
Invoice, and the identity fields BIR validates are the *registered* TIN, the RDO
code, the business style, and the Authority-to-Print (ATP) number issued by BIR
within a QZ series range. The ATP number is the part that is easy to get wrong and
expensive to get wrong: the series must be consecutive and gap-free, because BIR
rejects a book with a hole in it.

Scope (docs/ROADMAP.md): this row builds the **export file**. Live eSubmit
submission is INT-002, backlog, because it needs BIR accreditation.

The company's EIS profile lives in Company custom fields rather than a bespoke
doctype, because D-016 pins module "Construction PH" at 13 DocTypes and the
CORE-03 gate counts them.
"""

import frappe
from frappe import _
from frappe.utils import cstr, flt, nowdate

# BIR doc type per ERPNext doctype. Sales Invoice is an e-Invoice (EIS);
# Purchase Invoice is an e-Book (EBK) -- the latter is PH1-03's scope.
BIS_DOCTYPE = {"Sales Invoice": "EIS"}

# ATC used for a plain output-VAT row, which carries no withholding category.
OUTPUT_VAT_ATC = "VAT"


@frappe.whitelist()
def company_profile(company=None):
	"""Return (and validate) the company's BIR EIS profile.

	Raises rather than exporting blanks: a record with an empty TIN is rejected by
	BIR, and a rejected book is a compliance problem, not a retryable error.
	"""
	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		frappe.throw(_("No company selected."), title=_("EIS Profile Incomplete"))

	profile = frappe.db.get_value(
		"Company",
		company,
		[
			"custom_bir_registered_tin",
			"custom_bir_rdo_code",
			"custom_bir_business_style",
			"custom_bir_qz_prefix",
			"custom_bir_atp_start",
			"custom_bir_atp_end",
			"company_name",
			"default_currency",
		],
		as_dict=True,
	)
	if not profile:
		frappe.throw(_("Company {0} not found.").format(company), title=_("EIS Profile Incomplete"))

	missing = [
		label
		for label, value in (
			(_("BIR Registered TIN"), profile.custom_bir_registered_tin),
			(_("RDO Code"), profile.custom_bir_rdo_code),
			(_("Business Style"), profile.custom_bir_business_style),
			(_("QZ Series Prefix"), profile.custom_bir_qz_prefix),
		)
		if not cstr(value).strip()
	]
	if missing:
		frappe.throw(
			_("Company {0} is missing required EIS profile fields: {1}").format(
				company, ", ".join(missing)
			),
			title=_("EIS Profile Incomplete"),
		)

	if flt(profile.custom_bir_atp_start) <= 0 or flt(profile.custom_bir_atp_end) <= 0:
		frappe.throw(
			_("Company {0} has no ATP range configured.").format(company),
			title=_("EIS Profile Incomplete"),
		)
	if flt(profile.custom_bir_atp_end) < flt(profile.custom_bir_atp_start):
		frappe.throw(
			_("Company {0}: ATP range end is before its start.").format(company),
			title=_("EIS Profile Incomplete"),
		)

	return profile


@frappe.whitelist()
def allocate_atp(sales_invoice):
	"""Assign the next consecutive ATP number to a Sales Invoice.

	Serialised on the Company row so two concurrent submissions cannot take the
	same number, and gap-free because the number is the previous maximum + 1.
	Idempotent: an invoice that already carries one is returned unchanged, so a
	re-submit or a re-run of the export never burns a number.
	"""
	if not frappe.db.exists("Sales Invoice", sales_invoice):
		frappe.throw(_("Sales Invoice {0} not found.").format(sales_invoice))

	doc = frappe.get_doc("Sales Invoice", sales_invoice)
	if doc.docstatus != 1:
		frappe.throw(
			_("Sales Invoice {0} is not submitted; ATP numbers are only issued on submission.").format(
				sales_invoice
			),
			title=_("Invoice Not Submitted"),
		)
	if doc.custom_eis_atp_number:
		return doc.custom_eis_atp_number

	profile = company_profile(doc.company)

	# Lock the company row for the rest of the transaction. Frappe commits once at
	# the end of the request, so the lock is held across the read-then-write.
	frappe.db.sql("SELECT name FROM `tabCompany` WHERE name = %s FOR UPDATE", doc.company)

	last_used = frappe.db.sql(
		"""
		SELECT MAX(custom_eis_atp_number)
		FROM `tabSales Invoice`
		WHERE company = %s
		  AND custom_eis_atp_number IS NOT NULL
		  AND custom_eis_atp_number != 0
		""",
		doc.company,
	)
	# The BIR-issued range is authoritative. A stray legacy value below the range
	# start (a pre-EIS book, or a row imported from another system) must not drag the
	# series below it, so the floor is the configured start, not zero.
	floor = flt(profile.custom_bir_atp_start) - 1
	last = flt(last_used[0][0]) if last_used else floor
	next_atp = int(max(last, floor)) + 1

	if next_atp > flt(profile.custom_bir_atp_end):
		frappe.throw(
			_("ATP range for {0} is exhausted (last issued {1}, range ends {2}).").format(
				doc.company, next_atp - 1, profile.custom_bir_atp_end
			),
			title=_("ATP Range Exhausted"),
		)

	doc.db_set("custom_eis_atp_number", next_atp)
	doc.db_set("custom_eis_qz", cstr(profile.custom_bir_qz_prefix))
	return next_atp


def _atc_for_tax_row(row):
	"""ATC for a Sales Taxes row: the withholding category's code, or plain VAT."""
	# The category is stamped by api/billing_engine.py on the deduction row.
	# Sales Taxes and Charges has no native field for it, so it is a custom one.
	category = cstr(row.get("custom_tax_withholding_category") or "").strip()
	if category:
		atc = frappe.db.get_value("Tax Withholding Category", category, "custom_atc_code")
		if not cstr(atc).strip():
			frappe.throw(
				_("Tax Withholding Category {0} has no BIR ATC code; cannot export.").format(category),
				title=_("EIS Export Blocked"),
			)
		return cstr(atc)
	return OUTPUT_VAT_ATC


@frappe.whitelist()
def build_eis_record(sales_invoice):
	"""Build the EIS record for one submitted Sales Invoice.

	Does not allocate: use allocate_atp() first, or call export_eis() which does
	both. Kept separate so the record shape can be asserted without touching the
	series.
	"""
	doc = frappe.get_doc("Sales Invoice", sales_invoice)
	if doc.docstatus != 1:
		frappe.throw(
			_("Sales Invoice {0} is not submitted.").format(sales_invoice), title=_("Invoice Not Submitted")
		)

	profile = company_profile(doc.company)
	atp = flt(doc.custom_eis_atp_number)
	if not atp:
		frappe.throw(
			_("Sales Invoice {0} has no ATP number; export blocked until it is allocated.").format(
				sales_invoice
			),
			title=_("EIS Export Blocked"),
		)

	lines = []
	for idx, item in enumerate(doc.items, start=1):
		lines.append(
			{
				"line_no": idx,
				"atc": OUTPUT_VAT_ATC,
				"description": item.description or item.item_code,
				"item_code": item.item_code,
				"revenue_account": item.income_account,
				"quantity": flt(item.qty),
				"unit_price": flt(item.rate),
				"gross_amount": flt(item.amount),
				"discount_amount": flt(item.discount_amount),
				"net_amount": flt(item.net_amount),
			}
		)

	for row in doc.taxes:
		lines.append(
			{
				"line_no": len(lines) + 1,
				"atc": _atc_for_tax_row(row),
				"description": row.description or row.charge_type,
				"revenue_account": row.account_head,
				"charge_type": row.charge_type,
				"tax_amount": flt(row.tax_amount),
			}
		)

	return {
		"header": {
			"bis_doc_type": BIS_DOCTYPE["Sales Invoice"],
			"registered_name": profile.company_name,
			"tin": cstr(profile.custom_bir_registered_tin).strip(),
			"rdo_code": cstr(profile.custom_bir_rdo_code).strip(),
			"business_style": cstr(profile.custom_bir_business_style).strip(),
			"series": cstr(doc.custom_eis_qz) or cstr(profile.custom_bir_qz_prefix).strip(),
			"atp": int(atp),
			"invoice_name": doc.name,
			"posting_date": cstr(doc.posting_date or nowdate()),
			"currency": doc.currency or profile.default_currency,
			"total_amount_due": flt(doc.total),
			"total_amount_paid": flt(doc.paid_amount),
			# EIS carries no separate void flag: docstatus 2 is the cancelled state.
			"is_void": 1 if doc.docstatus == 2 else 0,
		},
		"line_items": lines,
	}


@frappe.whitelist()
def export_eis(from_date=None, to_date=None, company=None):
	"""Allocate and export every submitted Sales Invoice in a period.

	Returns the records plus a CSV string, which is the file the bookkeeper
	uploads or hands to the eSubmit portal.
	"""
	company = company or frappe.defaults.get_user_default("Company")
	profile = company_profile(company)

	# docstatus 1 = submitted. Cancelled invoices (2) are excluded by that alone;
	# this ERPNext version has no separate is_void field.
	# Frappe's dict-filter unpacks ``operator, value = value`` -- exactly two
	# elements -- so the dated form must be ``["between", [from, to]]``. A bare
	# ``[from, "<=", to]`` is three elements and raises ValueError on every dated
	# call (frappe/database/query.py apply_dict_filters).
	filters = {"company": company, "docstatus": 1}
	if from_date or to_date:
		filters["posting_date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31"]]

	names = frappe.get_all("Sales Invoice", filters=filters, pluck="name", order_by="posting_date asc, name asc")

	records = []
	for name in names:
		allocate_atp(name)
		records.append(build_eis_record(name))

	frappe.db.commit()
	return {
		"company": company,
		"series": cstr(profile.custom_bir_qz_prefix).strip(),
		"atp_start": flt(profile.custom_bir_atp_start),
		"atp_end": flt(profile.custom_bir_atp_end),
		"count": len(records),
		"records": records,
		"csv": to_csv(records),
	}


CSV_COLUMNS = (
	"series",
	"atp",
	"invoice_name",
	"posting_date",
	"tin",
	"rdo_code",
	"business_style",
	"currency",
	"line_no",
	"atc",
	"description",
	"revenue_account",
	"quantity",
	"unit_price",
	"gross_amount",
	"tax_amount",
)


def to_csv(records):
	"""Flatten EIS records to the column-per-field CSV BIR accepts."""
	import csv
	import io

	buf = io.StringIO()
	writer = csv.writer(buf, lineterminator="\n")
	writer.writerow(CSV_COLUMNS)
	for record in records:
		header = record["header"]
		for line in record["line_items"]:
			writer.writerow(
				[
					header["series"],
					header["atp"],
					header["invoice_name"],
					header["posting_date"],
					header["tin"],
					header["rdo_code"],
					header["business_style"],
					header["currency"],
					line.get("line_no", ""),
					line.get("atc", ""),
					line.get("description", ""),
					line.get("revenue_account", ""),
					line.get("quantity", ""),
					line.get("unit_price", ""),
					line.get("gross_amount", ""),
					line.get("tax_amount", ""),
				]
			)
	return buf.getvalue()
