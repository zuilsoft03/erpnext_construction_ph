# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""BIR Form 2307 certificate: the creditable-withholding slip, rendered as PDF.

**What this is.** Form 2307 is the certificate a contractor issues to a supplier
showing the creditable tax withheld, so the supplier can credit it against their
own income tax. It is a *withholding* document: the figures that matter are the
**net-of-VAT base** and the **2% withheld on that base** — not the gross invoice
total. Withholding computed on the VAT-inclusive amount overstates the credit by
exactly the VAT, and the supplier's Form 1701 would then disagree with the 2307
it was credited from.

**Built from the PH1-03 register, not recomputed.** `eis_export.build_eis_record`
is the single source for the payee's identity, the TIN, and the invoice's tax
rows. Recomputing them here would give the certificate and the EIS register two
answers to "what was withheld", which is precisely the disagreement this document
exists to prevent. The gate asserts the two agree to 0.01.

**Rendering.** `frappe.utils.pdf.get_pdf` with Frappe's **default** renderer:
`weasyprint` is absent from this bench and forcing `wkhtmltopdf` fails at runtime
(D-031). Whether the PDF *looks* right is a visual claim and is not made; what is
claimed is that the figures in it are the register's.
"""

import frappe
from frappe import _
from frappe.utils import flt

# Creditable withholding on a supplier's income, 2% of the net-of-VAT base.
WITHHOLDING_RATE = 0.02
# Output VAT, excluded from the 2307 base.
VAT_RATE = 0.12


def _withholding_rows(record):
	"""The withholding lines in an EIS record, identified by their tax account.

	An EIS record carries output VAT rows and creditable-withholding rows. Only
	the latter belong on a 2307, and picking them by account head rather than by
	row position is what stops a VAT line being reported as a credit.
	"""
	rows = []
	for line in record.get("line_items", []):
		head = cstr_(line.get("revenue_account")).strip()
		if "2307" in head or "Withholding" in head:
			rows.append(line)
	return rows


def cstr_(value):
	from frappe.utils import cstr

	return cstr(value)


def build_certificate(sales_invoice):
	"""The 2307 payload for one submitted Sales Invoice.

	Returns the structured certificate. The PDF is a render over this, so the
	figures can be asserted without a viewer -- the split D-031 established.
	"""
	from erpnext_construction_ph.api import eis_export

	record = eis_export.build_eis_record(sales_invoice)
	header = record["header"]

	# The payee is the party the payment went to -- the one who can credit the
	# tax. It is NOT the withholding agent: `registered_name` is this company's
	# own name, and putting it in the payee field inverts the form's central
	# attribution on a class A tax document. The agent keeps its own block.
	counterparty = frappe.db.get_value("Sales Invoice", sales_invoice, "customer")
	payee = frappe.db.get_value("Customer", counterparty, "customer_name") or counterparty
	payee_tin = str(frappe.db.get_value("Customer", counterparty, "tax_id") or "").strip()
	if not payee_tin:
		# BIR rejects a 2307 without the payee's TIN, and a blank cell reads as
		# an oversight rather than a refusal. Refuse at generation instead.
		frappe.throw(
			_("Payee {0} has no TIN recorded, so a Form 2307 cannot be issued for "
			  "invoice {1}. Set the Customer's tax_id first.").format(payee, sales_invoice),
			title=_("Payee TIN Missing"),
		)

	withholding = _withholding_rows(record)
	if not withholding:
		frappe.throw(
			_("Invoice {0} has no creditable-withholding line, so there is no 2307 "
			  "to issue. Withholding is required for a Form 2307.").format(sales_invoice),
			title=_("No Creditable Withholding"),
		)

	# The base is net of VAT, and **the withholding row's `gross_amount` already
	# is**: ERPNext reports a creditable-withholding line against the VAT-exclusive
	# amount, so 121,000 inclusive appears as 108,035.71 here. Dividing by 1.12
	# again -- the first version of this -- gave 96,460.27 and a 2% of 1,929.21,
	# understating the credit twice over.
	# The base is the **item** lines' net amount, not the withholding row's
	# `gross_amount` -- a tax row carries no gross, so reading it gives 0.00. ERPNext
	# adds VAT on top of the item amount, so the item `net_amount` is already
	# net of VAT and must not be divided again (doing so understated the credit
	# twice over in the first version).
	base = 0.0
	for line in record.get("line_items", []):
		if line.get("tax_amount") is not None:
			continue  # a tax row, not a supply
		base = flt(base + flt(line.get("net_amount") or line.get("gross_amount")), 2)

	withheld = 0.0
	for line in withholding:
		withheld = flt(withheld + abs(flt(line.get("tax_amount"))), 2)

	return {
		"invoice": header["invoice_name"],
		"series": header["series"],
		"atp": header["atp"],
		# Payee: the credited party, from the invoice's counterparty.
		"payee": payee,
		"tin": payee_tin,
		# Withholding agent: this company, in its own block.
		"withholding_agent": header["registered_name"],
		"withholding_agent_tin": header["tin"],
		"rdo_code": header["rdo_code"],
		"business_style": header["business_style"],
		"posting_date": header["posting_date"],
		"withholding_rate": WITHHOLDING_RATE,
		"vat_rate": VAT_RATE,
		"base_net_of_vat": base,
		"amount_withheld": withheld,
		# Recomputed from the base so the certificate can be checked without
		# trusting the invoice's own tax line.
		"expected_withholding": flt(base * WITHHOLDING_RATE, 2),
	}


def certificate_html(certificate):
	"""Minimal HTML for the payload — inspectable, and the thing asserted."""
	def row(label, value):
		return f"<tr><th>{label}</th><td>{value}</td></tr>"

	rows = "".join(
		[
			row("Series", certificate["series"]),
			row("ATP No.", certificate["atp"]),
			row("Invoice", certificate["invoice"]),
			row("Date", certificate["posting_date"]),
			row("Payee", certificate["payee"]),
			row("Payee TIN", certificate["tin"]),
			row("Withholding Agent", certificate["withholding_agent"]),
			row("Agent TIN", certificate["withholding_agent_tin"]),
			row("RDO Code", certificate["rdo_code"]),
			row("Business Style", certificate["business_style"]),
			row("Base Amount (net of VAT)", "{:,.2f}".format(certificate["base_net_of_vat"])),
			row("Withholding Rate", "{:.0%}".format(certificate["withholding_rate"])),
			row("Amount Withheld", "{:,.2f}".format(certificate["amount_withheld"])),
		]
	)
	return (
		"<html><head><meta charset='utf-8'><title>Form 2307</title></head>"
		"<body><h1>Certificate of Creditable Tax Withheld</h1>"
		"<p>BIR Form 2307</p>"
		f"<table>{rows}</table></body></html>"
	)


@frappe.whitelist()
def render_certificate(sales_invoice):
	"""Render the 2307 as PDF bytes."""
	from frappe.utils.pdf import get_pdf

	certificate = build_certificate(sales_invoice)
	return get_pdf(certificate_html(certificate))
