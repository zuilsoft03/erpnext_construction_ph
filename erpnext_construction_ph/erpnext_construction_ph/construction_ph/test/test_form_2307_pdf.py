# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH5-04 gate: the 2307 renders, and its figures are the register's.

Two halves, asserted separately (D-031's split):

- **the payload** — payee, TIN, ATC-bearing withholding rows, the net-of-VAT
  base, and 2% withheld. This is the half that carries the tax consequence and it
  is asserted as structured data, so no viewer is needed.
- **the PDF** — a thin render over that payload, proven by asserting real
  `%PDF-` bytes. How it *looks* is a visual claim and is not made.

The base is net of VAT because withholding charged on the VAT-inclusive amount
overstates the credit by exactly the VAT, and the supplier's Form 1701 would then
disagree with the 2307 it was credited from. That is the arithmetic this row
exists to get right, so it is asserted against a hand-computed literal.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import form_2307

# The supplies are 108,035.71 NET of VAT (ERPNext adds VAT on top of the item
# amount, so the item net is already net), and 2% of that is 2,160.71 withheld.
# Hand-computed here so the test is not simply asking the implementation what it
# already decided. The earlier version derived the base by dividing a
# VAT-inclusive figure by 1.12, which does not match how ERPNext reports a line.
NET_SUPPLIES = 108_035.71
EXPECTED_BASE = 108_035.71
EXPECTED_WITHHELD = 2_160.71


class TestForm2307Pdf(IntegrationTestCase):
	"""The certificate's figures, and that it renders at all."""

	# ---- the payload --------------------------------------------------------

	def test_the_certificate_carries_payee_tin_and_series(self):
		cert = form_2307.build_certificate(self._invoice())
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		# The payee is the invoice's counterparty -- the party who can credit the
		# tax. Asserting it against the company profile would lock in the
		# inversion, which is the most expensive kind of green on a tax document.
		self.assertEqual(cert["payee"], "PH5-04 Customer")
		self.assertNotEqual(
			cert["payee"], cert["withholding_agent"],
			"the payee must not be the withholding agent; that inverts the form",
		)
		self.assertEqual(cert["withholding_agent"], frappe.db.get_value("Company", company, "company_name"))
		self.assertTrue(cert["withholding_agent_tin"], "the agent's TIN is required on a 2307")
		self.assertTrue(cert["atp"], "no ATP number on the certificate")

	def test_the_base_is_net_of_vat(self):
		"""108,035.71, not 121,000.00.

		Withholding charged on the VAT-inclusive amount overstates the credit by
		exactly the VAT.
		"""
		cert = form_2307.build_certificate(self._invoice())
		self.assertAlmostEqual(
			flt(cert["base_net_of_vat"]), EXPECTED_BASE, places=2,
			msg="the 2307 base must be the net-of-VAT supplies",
		)

	def test_two_percent_is_withheld_on_the_base(self):
		cert = form_2307.build_certificate(self._invoice())
		self.assertAlmostEqual(flt(cert["amount_withheld"]), EXPECTED_WITHHELD, places=2)
		self.assertAlmostEqual(
			flt(cert["expected_withholding"]), EXPECTED_WITHHELD, places=2,
			msg="2% of the net-of-VAT base must equal the withheld amount",
		)
		self.assertAlmostEqual(flt(cert["withholding_rate"]), 0.02, places=6)

	def test_the_certificate_total_matches_the_register_row(self):
		"""The gate's cross-check: the 2307 and the EIS register must agree.

		The certificate is built FROM the register, so this asserts the two do not
		drift -- which is the whole reason this row exists. Two answers to "what
		was withheld" is the failure a 2307 is meant to prevent.
		"""
		from erpnext_construction_ph.api import eis_export

		invoice = self._invoice()
		cert = form_2307.build_certificate(invoice)
		record = eis_export.build_eis_record(invoice)

		from_register = flt(
			sum(abs(flt(l.get("tax_amount"))) for l in record["line_items"]
				if "2307" in str(l.get("revenue_account")) or "Withholding" in str(l.get("revenue_account")))
		)
		self.assertAlmostEqual(flt(cert["amount_withheld"]), from_register, places=2)

	def test_an_invoice_with_no_withholding_refuses(self):
		"""A 2307 with nothing withheld is not a 2307."""
		with self.assertRaises(frappe.ValidationError) as caught:
			form_2307.build_certificate(self._plain_invoice())
		self.assertIn("no creditable-withholding line", str(caught.exception))

	# ---- the render ---------------------------------------------------------

	def test_it_renders_to_real_pdf_bytes(self):
		"""A thin render over the payload, proven on its own terms.

		Frappe's default renderer: weasyprint is absent from this bench and
		forcing wkhtmltopdf fails (D-031).
		"""
		payload = form_2307.render_certificate(self._invoice())
		self.assertTrue(payload.startswith(b"%PDF-"), "the render is not a PDF")
		self.assertGreater(len(payload), 1500)

	def test_the_rendered_file_is_a_well_formed_pdf(self):
		"""A PDF is not a zip.

		The structural check here is the PDF's own shape, not the OOXML package
		check used for the .docx row: `%PDF-` header, a `%%EOF` trailer, and an
		`xref` table. Opening it as a zip was the .docx assertion copied across and
		it could not have passed on a PDF at all.
		"""
		payload = form_2307.render_certificate(self._invoice())
		self.assertTrue(payload.startswith(b"%PDF-"), "missing the PDF header")
		self.assertIn(b"%%EOF", payload[-2048:], "missing the PDF EOF trailer")
		self.assertIn(b"xref", payload, "missing the xref table")

	def test_the_html_names_the_payee_tin_and_amounts(self):
		"""What is asserted is in the HTML that becomes the PDF.

		Asserting only that bytes came back would pass with a blank page, which
		is the "looks like something, measures nothing" trap in document form.
		"""
		cert = form_2307.build_certificate(self._invoice())
		html = form_2307.certificate_html(cert)
		self.assertIn(cert["payee"], html)
		self.assertIn(cert["tin"], html)
		self.assertIn("{:,.2f}".format(EXPECTED_BASE), html)
		self.assertIn("{:,.2f}".format(EXPECTED_WITHHELD), html)
		self.assertIn("2307", html)

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _company():
		return frappe.get_all("Company", limit=1, pluck="name")[0]

	def _invoice(self, with_cwt=True):
		"""A submitted Sales Invoice, with CWT when asked for."""
		seq = len(frappe.get_all("Sales Invoice", pluck="name")) + 1
		company = self._company()
		customer = "PH5-04 Customer"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
					"tax_id": "000-999-888-00000",
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Customer", customer, "tax_id", "000-999-888-00000")

		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": customer,
				"company": company,
				# Relative to today: `posting_date` is forced to now on insert
				# (the set_posting_time trap, ops/STATE.md), so a fixed past
				# date would put due_date before it.
				"posting_date": frappe.utils.nowdate(),
				"due_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
				"currency": "PHP",
				"items": [
					{
						"item_code": self._item(),
						"qty": 1.0,
						"rate": NET_SUPPLIES,
						"income_account": "Construction Contract Revenue - Progress Billings - TCC",
					}
				],
				"taxes": (
					[
						{
							"charge_type": "Actual",
							"account_head": "Prepaid Tax - Creditable Withholding Tax 2307 - TCC",
							"description": "Less: 2% Creditable Withholding Tax (BIR 2307)",
							"tax_amount": -EXPECTED_WITHHELD,
							"custom_tax_withholding_category": self._cwt_category(),
						}
					]
					if with_cwt
					else []
				),
			}
		).insert(ignore_permissions=True)
		doc.submit()
		from erpnext_construction_ph.api import eis_export

		eis_export.allocate_atp(doc.name)
		return doc.name

	def _plain_invoice(self):
		return self._invoice(with_cwt=False)

	@staticmethod
	def _cwt_category():
		"""A 2% contractors category that exists on this site.

		Named by search rather than hard-coded: the abbr suffix is site-specific,
		and a wrong name fails link validation on a field the certificate does not
		even read.
		"""
		from frappe.utils import cstr

		for name in frappe.get_all("Tax Withholding Category", pluck="name"):
			if "WC120" in cstr(name) or "2%" in cstr(name):
				return name
		hits = frappe.get_all("Tax Withholding Category", pluck="name")
		if hits:
			return hits[0]
		return frappe.get_doc(
			{"doctype": "Tax Withholding Category", "title": "WC120 - 2% Contractors (Corporate)"}
		).insert(ignore_permissions=True).name

	@staticmethod
	def _item():
		existing = frappe.get_all("Item", filters={"item_name": "PH5-04 supply"}, pluck="name")
		if existing:
			return existing[0]
		groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
		group = next((g for g in ("All Item Groups", "Raw Materials") if g in groups), groups[0])
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "PH504-SUP",
				"item_name": "PH5-04 supply",
				"item_group": group,
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		).insert(ignore_permissions=True).name
