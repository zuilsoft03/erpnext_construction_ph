# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-02 gate tests for the CIAP Document 102 progress billing.

The gate selects this by METHOD name (`bench run-tests --test test_progress_billing`
matches ``elem._testMethodName``), so every gate assertion lives in the single
``test_progress_billing`` method below.

These live in the module's ``test/`` folder rather than beside the doctype on
purpose: a test inside ``doctype/<x>/`` makes Frappe lazily generate a full
dependency record set for the doctype, which collides with the Fiscal Year the
gate site already has. This test creates the Project and Customer it needs, so
it declares no generated-record dependencies.

Expected figures are hand-computed from the class A invariant in
docs/ARCHITECTURE.md:82-87 rather than recomputed by calling the code under
test, so a change to the formula cannot silently redefine "correct".
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

CONTRACT_AMOUNT = 10_000_000.00


class TestConstructionProgressBilling(IntegrationTestCase):
	"""Certification arithmetic and the CIAP 102 50% retention milestone."""

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (see D-012)")
		return company[0]

	def _customer(self, suffix):
		"""Return the Customer's real name — autoname is not guaranteed to equal customer_name."""
		customer_name = f"CPB Test Customer {suffix}"
		existing = frappe.get_all("Customer", filters={"customer_name": customer_name}, pluck="name")
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"customer_type": "Company",
				# leaf records: "All Customer Groups" / "All Territories" are groups
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _project(self, suffix):
		"""Return the Project's real name — ERPNext appends the company abbreviation."""
		project_name = f"CPB Test Project {suffix}"
		existing = frappe.get_all("Project", filters={"project_name": project_name}, pluck="name")
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": project_name,
				"company": self._company(),
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _bill(self, suffix, period_percent, recoupment, cumulative):
		doc = frappe.get_doc(
			{
				"doctype": "Construction Progress Billing",
				"billing_number": f"CPB-{suffix}",
				"project": self._project(suffix),
				"company": self._company(),
				"client": self._customer(suffix),
				"posting_date": "2026-09-28",
				"billing_period_start": "2026-09-01",
				"billing_period_end": "2026-09-30",
				"contract_amount": CONTRACT_AMOUNT,
				"period_accomplishment_percent": period_percent,
				"mobilization_recoupment": recoupment,
				"cumulative_accomplishment": cumulative,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def _assert_pesos(self, actual, expected, label):
		"""2-decimal-place match, and the stored value really is 2dp."""
		self.assertEqual(flt(actual), flt(expected, 2), f"{label} mismatch")
		self.assertEqual(flt(actual), round(flt(actual), 2), f"{label} is not rounded to 2dp")

	def test_progress_billing(self):
		# --- EXPECT: penny-match against the CIAP 102 formula, below the milestone.
		# contract 10,000,000 x 20% = gross 2,000,000
		# less 500,000 recoupment = taxable base 1,500,000
		# VAT  1,500,000 x 0.12 = 180,000   -> net before retention 1,680,000
		# ret  1,680,000 x 0.10 = 168,000
		# CWT  1,500,000 x 0.02 =  30,000
		# due  1,500,000 - 168,000 + 180,000 - 30,000 = 1,482,000
		first = self._bill("2026-001", 20, 500_000.00, 2_000_000.00)

		self._assert_pesos(first.gross_accomplishment_period, 2_000_000.00, "gross_accomplishment_period")
		self._assert_pesos(first.cumulative_percent, 20.00, "cumulative_percent")
		self._assert_pesos(first.taxable_base, 1_500_000.00, "taxable_base")
		self._assert_pesos(first.vat_amount, 180_000.00, "vat_amount")
		self._assert_pesos(first.net_before_retention, 1_680_000.00, "net_before_retention")
		self._assert_pesos(first.retention_amount, 168_000.00, "retention_amount")
		self._assert_pesos(first.cwt_withheld, 30_000.00, "cwt_withheld")
		self._assert_pesos(first.net_amount_due, 1_482_000.00, "net_amount_due")

		# --- EXPECT: second billing past the 50% milestone drops retention.
		# contract 10,000,000 x 40% = gross 4,000,000, no recoupment
		# VAT  480,000 -> net before retention 4,480,000; cum 60% > 50% so retention 0
		# CWT  4,000,000 x 0.02 = 80,000; due 4,400,000
		second = self._bill("2026-002", 40, 0.00, 6_000_000.00)

		self.assertGreater(second.cumulative_percent, 50.0, "fixture must sit past the milestone")
		self._assert_pesos(second.cumulative_percent, 60.00, "cumulative_percent")
		self._assert_pesos(second.net_before_retention, 4_480_000.00, "net_before_retention")
		self._assert_pesos(second.retention_amount, 0.00, "retention_amount past 50%")
		self._assert_pesos(second.cwt_withheld, 80_000.00, "cwt_withheld")
		self._assert_pesos(second.net_amount_due, 4_400_000.00, "net_amount_due")

		# --- NEGATIVE 1: at or below 50%, retention is exactly 10% of
		# net_before_retention. cum 5,000,000 / 10,000,000 = exactly 50.00%.
		boundary = self._bill("2026-003", 50, 0.00, 5_000_000.00)

		self._assert_pesos(boundary.cumulative_percent, 50.00, "cumulative_percent at milestone")
		self.assertLessEqual(boundary.cumulative_percent, 50.0, "fixture must sit at the milestone")
		self.assertEqual(
			int(boundary.retention_milestone_reached),
			0,
			"retention_milestone_reached must stay clear AT the milestone (gate: <=50% retains)",
		)
		self._assert_pesos(
			boundary.retention_amount,
			flt(boundary.net_before_retention * 0.10, 2),
			"retention_amount at 50%",
		)
		self._assert_pesos(boundary.retention_amount, 560_000.00, "retention_amount at 50%")
		self._assert_pesos(boundary.net_amount_due, 4_940_000.00, "net_amount_due at 50%")

		# --- NEGATIVE 2: the milestone test uses the EXACT ratio, and that exact
		# ratio is what gets stored. 5,000,001 / 10,000,000 x 100 = 50.00001%,
		# which is past the milestone, so retention must be zero.
		# period 0.0001% -> gross 10.00; VAT 1.20; CWT 0.20; due 11.00.
		past = self._bill("2026-004", 0.0001, 0.00, 5_000_001.00)

		# Assert against a value re-read from the database, not the in-memory doc:
		# the whole point is that the persisted figure a reviewer reads is the same
		# number the milestone decision used. Asserted in-memory it would pass even
		# if the value were rounded away on the way to MariaDB.
		stored_pct = frappe.db.get_value("Construction Progress Billing", past.name, "cumulative_percent")
		self.assertEqual(
			flt(stored_pct, 5),
			50.00001,
			"cumulative_percent must persist the exact ratio, not a rounded display value",
		)
		self.assertGreater(flt(stored_pct), 50.0, "stored percent must clear the milestone")
		self.assertEqual(
			int(past.retention_milestone_reached),
			1,
			"retention_milestone_reached must be set just past 50%",
		)
		self._assert_pesos(past.retention_amount, 0.00, "retention_amount just past 50%")
		self.assertEqual(flt(past.retention_amount), 0.0, "retention must be a true zero")
		self._assert_pesos(past.net_amount_due, 11.00, "net_amount_due just past 50%")

