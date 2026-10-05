# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md PH1-03 — subcontractor EWT and the BIR Form 2307 register.

The gate selects this by METHOD name (D-014), so every assertion lives in the
single ``test_form_2307_register`` method.

This is the first row to exercise a Purchase Invoice end to end, which is how
PH1-02's two class A defects in the submit path were found (D-025). The invoices
here are built and submitted for real, so the EWT tax row, its GL posting and the
register all come from the same source rather than from fabricated rows.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import ebook_export

CWT_CORPORATE = "WC120 - 2% Contractors (Corporate)"
CWT_INDIVIDUAL = "WI120 - 2% Contractors (Individual)"
EWT_PAYABLE = "Expanded Withholding Tax Payable 1601-EQ"


class TestForm2307Register(IntegrationTestCase):
	"""2% EWT withheld from a subcontractor, captured for BIR Form 2307."""

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (D-012)")
		return company[0]

	def _supplier(self, name, tin, category):
		existing = frappe.get_all("Supplier", filters={"supplier_name": name}, pluck="name")
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": "All Supplier Groups",
				"supplier_type": "Company",
				"tax_id": tin,
				"tax_withholding_category": category,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _item(self):
		existing = frappe.get_all("Item", filters={"item_code": "P1-REGISTER-ITEM"}, pluck="name")
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "P1-REGISTER-ITEM",
				"item_name": "P1 Register Item",
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_purchase_item": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _customer(self):
		name = "P1 Register Client"
		hits = frappe.get_all("Customer", filters={"customer_name": name}, pluck="name")
		if hits:
			return hits[0]
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		).insert(ignore_permissions=True).name

	def _construction_project(self):
		"""An ERPNext Project wrapped by a Construction Project, for the block case."""
		erp_name = "P1 Construction Project"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		if hits:
			erp_project = hits[0]
		else:
			erp_project = frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": erp_name,
					"company": self._company(),
					"status": "Open",
				}
			).insert(ignore_permissions=True).name
		if not frappe.db.exists("Construction Project", erp_name):
			frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": erp_name,
					"erpnext_project": erp_project,
					"company": self._company(),
					"client": self._customer(),
					"contract_date": "2026-01-01",
					"contract_amount": 5_000_000.00,
				}
			).insert(ignore_permissions=True)
		return erp_project

	def _invoice(self, supplier, number, qty=10, rate=50_000.0, project=None):
		company = self._company()
		warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
		doc = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"supplier": supplier,
				"company": company,
				"posting_date": "2026-02-15",
				# ERPNext overwrites posting_date with today unless set_posting_time
				# is set (erpnext/utilities/transaction_base.py:34). Without this
				# the invoice, its GL and the EWT journal all land outside the
				# quarter under test. Same trap api/billing_engine.py hits on the
				# Sales Invoice side (D-025).
				"set_posting_time": 1,
				"posting_time": "09:00:00",
				"bill_date": "2026-02-10",
				"currency": "PHP",
				"project": project,
				"items": [
					{
						"item_code": self._item(),
						"qty": qty,
						"rate": rate,
						"received_qty": qty,
						"warehouse": warehouse,
					}
				],
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	def test_form_2307_register(self):
		company = self._company()
		corporate = self._supplier("P1 Register Sub Corp", "000-111-222-00000", CWT_CORPORATE)
		individual = self._supplier("P1 Register Sub Individual", "000-333-444-00000", CWT_INDIVIDUAL)

		# ---- EXPECT: one 2307 register row per submitted PI, carrying the payee
		# TIN, the ATC, the net-of-VAT base and the 2% withheld.
		# 10 x 50,000 = 500,000 base; 2% = 10,000.
		first = self._invoice(corporate, "CORP-1")
		row = ebook_export.register_row(first.name)
		self.assertIsNotNone(row, "a PI with an EWT category must produce a register row")
		self.assertEqual(row["tin"], "000-111-222-00000")
		self.assertEqual(row["atc"], "WC120", "a corporate payee is WC120")
		self.assertEqual(flt(row["base_net_of_vat"]), 500_000.00)
		self.assertEqual(flt(row["amount_withheld"]), 10_000.00)
		self.assertEqual(row["payee"], corporate)

		# The register and the document must come from the same source.
		withheld_rows = [t for t in first.taxes if t.get("is_tax_withholding_account")]
		self.assertEqual(len(withheld_rows), 1, "exactly one EWT deduction row per PI")
		self.assertEqual(flt(withheld_rows[0].tax_amount), 10_000.00)
		self.assertTrue(
			frappe.db.exists(
				"Account",
				{"name": withheld_rows[0].account_head, "root_type": "Liability"},
			),
			"the EWT row must point at a liability account (21070)",
		)

		# The EWT must reach account 21070, or the register and the books disagree.
		self.assertTrue(
			frappe.db.exists(
				"GL Entry",
				{
					"account": frappe.db.get_value(
						"Account", {"account_name": EWT_PAYABLE, "company": company}, "name"
					),
					"voucher_type": "Journal Entry",
					"is_cancelled": 0,
				},
			),
			"the EWT journal must post to account 21070",
		)

		# ---- EXPECT: an individual payee carries WI120 at the same rate.
		second = self._invoice(individual, "IND-1", qty=4, rate=25_000.0)
		row2 = ebook_export.register_row(second.name)
		self.assertEqual(row2["atc"], "WI120", "an individual payee is WI120")
		self.assertEqual(flt(row2["amount_withheld"]), 2_000.00)  # 100,000 x 2%

		# ---- EXPECT: the period register total equals the EWT payable movement.
		result = ebook_export.reconcile("2026-01-01", "2026-03-31", company=company)
		self.assertEqual(result["form"], "1601-EQ")
		self.assertEqual(flt(result["register_total"]), 12_000.00)
		self.assertEqual(flt(result["gl_total"]), 12_000.00)
		self.assertEqual(flt(result["variance"]), 0.00)
		self.assertTrue(result["reconciled"])
		self.assertEqual(len(result["rows"]), 2)
		self.assertIn("WC120", result["csv"])
		self.assertIn("WI120", result["csv"])

		# ---- NEGATIVE: a supplier with no TIN cannot be captured. A 2307 without
		# a payee TIN is not fileable, so the capture is refused rather than
		# emitted with a blank field.
		no_tin = self._supplier("P1 Register Sub NoTIN", "", CWT_CORPORATE)
		blank = self._invoice(no_tin, "NOTIN-1")
		self.assertRaises(frappe.ValidationError, ebook_export.register_row, blank.name)

		# ---- NEGATIVE: a PI with no withholding category produces no register row
		# rather than a row with a blank ATC.
		no_category = self._supplier("P1 Register Sub Plain", "000-555-666-00000", None)
		plain = self._invoice(no_category, "PLAIN-1")
		self.assertIsNone(
			ebook_export.register_row(plain.name),
			"a PI with no EWT category must not enter the 2307 register",
		)

		# ---- NEGATIVE: a construction-project purchase with no withholding category
		# is refused outright. Paying a subcontractor gross, with no 2% withheld and
		# no 2307, is a compliance failure that only surfaces at remittance time.
		# The refusal happens on insert, because the EWT row is built in
		# before_validate -- so the whole invoice must fail rather than save gross.
		self.assertRaises(
			frappe.ValidationError,
			self._invoice,
			no_category,
			"PLAIN-2",
			project=self._construction_project(),
		)

		# ---- MUTATION NOTE (run manually when the ATC logic changes): point the
		# category at WB080 and re-capture. A correct implementation must then fail
		# this gate, because a passing register on the wrong ATC is
		# indistinguishable from a correct one.
