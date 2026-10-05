# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-01-phase gate test for the BIR EIS export (GATES.md PH1-01).

The gate selects this by METHOD name -- `run-tests --test test_eis_export`
matches ``elem._testMethodName`` (D-014) -- so every assertion lives in the single
``test_eis_export`` method.

Lives in the module's ``test/`` folder, not beside a doctype, so Frappe does not
try to generate a dependency record set that collides with the gate site's
Fiscal Year.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import eis_export
from erpnext_construction_ph.construction_ph.test import fixtures

# Bare account_names; _account() resolves the abbr-suffixed document name.
INCOME_ACCOUNT = "Construction Contract Revenue - Progress Billings"
CWT_ACCOUNT = "Prepaid Tax - Creditable Withholding Tax 2307"
CWT_CATEGORY = "WC120 - 2% Contractors (Corporate)"
ATP_START = 5001
ATP_HEADROOM = 100


class TestEISExport(IntegrationTestCase):
	"""Export identity, consecutive ATP numbering, and the two refusals."""

	@staticmethod
	def _atp_end():
		"""A range end that always leaves room, whatever the site already holds.

		`allocate_atp` writes with `db_set`, which commits outside the test
		transaction, so the per-test rollback does not undo it. A fixed
		`ATP_END` therefore burned a permanent number on every run and, after
		100 runs, failed with "ATP range exhausted" -- a failure of the *site*,
		not of the code under test, in a gate already recorded as delivered.
		Deriving the end from the live maximum keeps the BIR-shaped range and
		makes the gate runnable indefinitely, with no deletes of submitted
		invoices.
		"""
		used = frappe.db.sql(
			"""
			SELECT COALESCE(MAX(custom_eis_atp_number), 0)
			FROM `tabSales Invoice`
			WHERE custom_eis_atp_number IS NOT NULL AND custom_eis_atp_number != 0
			"""
		)
		return max(int(used[0][0]) if used and used[0][0] else 0, ATP_START) + ATP_HEADROOM

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (D-012)")
		return company[0]

	def _profile(self, **overrides):
		"""Write the BIR EIS profile onto the company and return it."""
		values = {
			"custom_bir_registered_tin": "000-123-456-00000",
			"custom_bir_rdo_code": "0100",
			"custom_bir_business_style": "Professional",
			"custom_bir_qz_prefix": "QZ-00001",
			"custom_bir_atp_start": ATP_START,
			"custom_bir_atp_end": self._atp_end(),
		}
		values.update(overrides)
		frappe.db.set_value("Company", self._company(), values)
		return values

	def _account(self, account_name):
		"""Accounts are named "<account_name> - <abbr>", so link by field, not by
		constructing the name — the abbr is the part that is easy to get wrong."""
		name = frappe.db.get_value(
			"Account", {"account_name": account_name, "company": self._company()}, "name"
		)
		self.assertTrue(name, "gate site is missing GL account {0}".format(account_name))
		return name

	def _customer(self):
		name = "EIS Test Customer"
		if frappe.db.exists("Customer", name):
			return name
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
				"tax_withholding_category": CWT_CATEGORY,
			}
		).insert(ignore_permissions=True)
		return name

	def _item(self):
		code = "EIS-TEST-SERVICE"
		if frappe.db.exists("Item", code):
			return code
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": "EIS Test Service",
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
			}
		).insert(ignore_permissions=True)
		return code

	def _invoice(self, suffix, cwt=0.0, allocate=True):
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": self._customer(),
				"company": self._company(),
				"posting_date": "2026-09-28",
				"due_date": "2026-10-28",
				"currency": "PHP",
				"items": [
					{
						"item_code": self._item(),
						"qty": 1,
						"rate": 1000.0,
						"income_account": self._account(INCOME_ACCOUNT),
					}
				],
				"taxes": (
					[
						{
							"charge_type": "Actual",
							"account_head": self._account(CWT_ACCOUNT),
							"description": "Less: 2% Creditable Withholding Tax (BIR 2307)",
							"tax_amount": -abs(cwt),
							"custom_tax_withholding_category": CWT_CATEGORY,
						}
					]
					if cwt
					else []
				),
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		if allocate:
			eis_export.allocate_atp(doc.name)
		type(self)._made = tuple(getattr(type(self), "_made", ())) + (doc.name,)
		return doc

	_made = ()

	@classmethod
	def tearDownClass(cls):
		"""Reclaim only the invoices this class made.

		MEASURED: this module leaked +4 Sales Invoice per suite run -- it is the sole source,
		each invoice-creating module run in isolation (734 -> 738 here, +0 everywhere else).
		`export_eis` commits (eis_export.py:269), so these outlive the transaction.

		ROLL BACK FIRST. Without it this teardown takes the suite from failures=1 to
		failures=2, errors=7, because a bare commit also persists the half-written state the
		test transaction used to roll back -- surfacing as "Company Test Construction Co is
		missing required EIS profile fields". The rollback discards that state; the invoices
		survive it, since they were committed by the code under test; the deletes are then
		committed on their own. See ops/tmp-core37/EIS-LEAK-BLOCKED.md.
		"""
		try:
			frappe.db.rollback()
			gl_posting = fixtures.gl_posting_doctypes()
			for name in cls._made:
				if frappe.db.exists("Sales Invoice", name):
					fixtures.reclaim_document("Sales Invoice", name, gl_posting)
			frappe.db.commit()
		except Exception:  # noqa: BLE001
			# Never swallowed silently: that is how I concluded the reclaim "did not fire"
			# when it had simply printed to a stream the test runner discards.
			import sys
			import traceback

			print("TestEISExport: reclaim failed", file=sys.stderr)
			traceback.print_exc(file=sys.stderr)
			raise
		finally:
			cls._made = ()
			super().tearDownClass()

	def test_eis_export(self):
		profile = self._profile()

		# ---- EXPECT: the exported record matches the invoice it came from.
		first = self._invoice("A", cwt=20.0)
		record = eis_export.build_eis_record(first.name)
		header = record["header"]

		self.assertEqual(header["bis_doc_type"], "EIS")
		self.assertEqual(header["series"], profile["custom_bir_qz_prefix"])
		self.assertEqual(header["tin"], profile["custom_bir_registered_tin"])
		self.assertEqual(header["rdo_code"], profile["custom_bir_rdo_code"])
		self.assertEqual(header["business_style"], profile["custom_bir_business_style"])
		self.assertEqual(header["currency"], "PHP")
		self.assertEqual(flt(header["total_amount_due"]), flt(first.total))
		self.assertEqual(header["invoice_name"], first.name)

		# The ATP number must be inside the BIR-issued range, not just non-null.
		self.assertGreaterEqual(header["atp"], ATP_START)
		self.assertLessEqual(header["atp"], self._atp_end())

		# ---- EXPECT: line items carry an ATC and the tax amounts from the invoice.
		item_lines = [x for x in record["line_items"] if x.get("atc") == "VAT"]
		self.assertEqual(len(item_lines), 1, "expected one revenue line")
		self.assertEqual(flt(item_lines[0]["gross_amount"]), flt(first.items[0].amount))

		cwt_lines = [x for x in record["line_items"] if x.get("atc") == "WC120"]
		self.assertEqual(len(cwt_lines), 1, "the 2% CWT deduction must carry the WC120 ATC")
		self.assertEqual(flt(cwt_lines[0]["tax_amount"]), -20.0)

		# ---- EXPECT: a second invoice increments the series by exactly 1.
		second = self._invoice("B")
		second_header = eis_export.build_eis_record(second.name)["header"]
		self.assertEqual(
			second_header["atp"],
			header["atp"] + 1,
			"ATP numbers must be consecutive within the series",
		)
		self.assertEqual(second_header["series"], header["series"])

		# ---- EXPECT: the CSV carries one row per line, with the same ATP.
		exported = eis_export.export_eis(company=self._company())
		self.assertGreaterEqual(exported["count"], 2)
		self.assertIn(str(header["atp"]), exported["csv"])
		self.assertIn("WC120", exported["csv"])
		# Allocation is idempotent: a second export must not burn numbers.
		again = eis_export.export_eis(company=self._company())
		self.assertEqual(
			eis_export.build_eis_record(second.name)["header"]["atp"],
			second_header["atp"],
			"re-exporting must not re-issue an ATP number",
		)
		self.assertEqual(again["count"], exported["count"])
		# ---- D1: the dated form used to raise on every call. The filter list was
		# 3 elements ([from, "<=", to]) but frappe/database/query.py
		# apply_dict_filters unpacks ``operator, value = value`` -- exactly two --
		# so the dated form always raised ValueError. Only the undated form
		# survived. It must now run, the date filter must actually exclude
		# invoices outside the window, and it allocates ATP numbers (see report).
		# THE WINDOW IS DERIVED, NOT HARDCODED. This said from_date="2026-09-01",
		# to_date="2026-09-30" while the fixture invoices take their posting_date from
		# `nowdate()`. That is a TIME BOMB: the assertions below passed in September and
		# began failing in October, with an error naming allocation rather than the date
		# filter -- so it read as an idempotence problem and sent two agents after
		# `allocate_atp`'s db_set, which commits outside the test transaction and is the
		# reason the RANGE END is derived rather than fixed. Neither of those is the cause.
		#
		# THE WINDOW IS DERIVED FROM THE ROW UNDER TEST, NOT FROM `nowdate()`.
		#
		# This used to say from_date="2026-09-01", to_date="2026-09-30" while the fixture
		# invoices take their posting_date from the clock. That is a TIME BOMB: it passed in
		# September and began failing in October, and the error named ALLOCATION rather
		# than the date filter, so it read as an idempotence problem and sent two agents
		# after `allocate_atp`'s db_set -- a true statement about the code, and the wrong
		# explanation for this failure.
		#
		# Deriving from `nowdate()` would be better than the literal and still WRONG in the
		# same way: it assumes the fixture's posting_date is today. Reading the row back and
		# making the window exactly that day removes the assumption, so the window and the
		# invoice it is supposed to contain cannot drift apart.
		#
		# **A hardcoded date in a test is an expiry date, and a derived one is only a
		# smaller expiry date. Anchor to the row under test.**
		inside_window = self._invoice("E", allocate=False)
		anchor = frappe.db.get_value("Sales Invoice", inside_window.name, "posting_date")
		self.assertTrue(anchor, "the fixture invoice must carry a posting_date to anchor the window to")

		# One day outside, so "outside the window" is a fact about the boundary rather
		# than a month away.
		out_of_window = self._invoice("D", allocate=False)
		frappe.db.set_value(
			"Sales Invoice", out_of_window.name, "posting_date",
			frappe.utils.add_days(anchor, -1),
		)
		dated = eis_export.export_eis(
			company=self._company(),
			from_date=anchor,
			to_date=anchor,
		)
		self.assertGreaterEqual(dated["count"], 2)
		dated_names = {r["header"]["invoice_name"] for r in dated["records"]}
		# The out-of-window invoice must not appear in the dated export.
		self.assertNotIn(out_of_window.name, dated_names)
		# And it must not have been allocated by the dated call.
		self.assertFalse(
			frappe.db.get_value("Sales Invoice", out_of_window.name, "custom_eis_atp_number"),
			"the dated export must not allocate invoices outside its window",
		)
		# The inside-window invoice must have been allocated.
		self.assertTrue(
			frappe.db.get_value("Sales Invoice", inside_window.name, "custom_eis_atp_number"),
			"the dated export must allocate invoices inside its window",
		)

		# ---- NEGATIVE: an invoice with no allocated ATP cannot be exported.
		unallocated = self._invoice("C", allocate=False)
		self.assertRaises(
			frappe.ValidationError, eis_export.build_eis_record, unallocated.name
		)

		# ---- NEGATIVE: a company with no registered TIN is refused, not exported blank.
		self._profile(custom_bir_registered_tin="")
		self.assertRaises(frappe.ValidationError, eis_export.export_eis, company=self._company())

		# ---- MUTATION NOTE (not run here; run manually when the ATC logic changes):
		# point custom_tax_withholding_category at the WI120 category and re-export.
		# A correct implementation must then fail this gate, because the ATC on the
		# CWT line would be WI120 while the 2% corporate rate was applied.
