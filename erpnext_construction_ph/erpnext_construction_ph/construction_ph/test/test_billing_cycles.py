# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md PH1-04 — 10 CIAP Document 102 billing cycles, penny-matched.

The gate selects this by METHOD name (D-014), so every assertion lives in the
single ``test_billing_cycles`` method.

The expected figures are re-derived here from the class A invariant in
docs/ARCHITECTURE.md:82-87 rather than read back from the controller, and the
first cycle and the milestone cycle are additionally pinned as literals so the
check cannot be satisfied by a formula that merely agrees with itself:

    taxable_base = gross - recoupment
    vat          = taxable_base x 0.12
    net_before   = taxable_base + vat
    retention    = net_before x 0.10 while cumulative <= 50%, else 0.00
    cwt          = taxable_base x 0.02
    net_due      = taxable_base - retention + vat - cwt

The table deliberately walks cumulative accomplishment through 5%, 13%, 23%,
35%, **50%**, 59%, 70%, 83%, 90%, 100% so the CIAP 102 retention milestone is
crossed and the exact-50% boundary is asserted, and the first three cycles
carry the 15% mobilisation recoupment.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api.billing_engine import output_vat_account

CONTRACT = 50_000_000.00
ADVANCE_RATIO = 0.15

# (billing_number, period_accomplishment_pct, recoupment, cumulative_accomplishment)
CYCLES = [
	("CYC-01", 5.0, 375_000.00, 2_500_000.00),
	("CYC-02", 8.0, 600_000.00, 6_500_000.00),
	("CYC-03", 10.0, 750_000.00, 11_500_000.00),
	("CYC-04", 12.0, 0.00, 17_500_000.00),
	("CYC-05", 15.0, 0.00, 25_000_000.00),  # cumulative lands exactly on 50.00%
	("CYC-06", 9.0, 0.00, 29_500_000.00),   # first cycle past the milestone
	("CYC-07", 11.0, 0.00, 35_000_000.00),
	("CYC-08", 13.0, 0.00, 41_500_000.00),
	("CYC-09", 7.0, 0.00, 45_000_000.00),
	("CYC-10", 10.0, 0.00, 50_000_000.00),
]

# Pinned so a controller that merely echoes its own formula cannot pass.
PINNED = {
	# CYC-01: gross 2,500,000; less 375,000 = 2,125,000; VAT 255,000;
	# net_before 2,380,000; retention 238,000; CWT 42,500;
	# due = 2,125,000 - 238,000 + 255,000 - 42,500 = 2,099,500.
	"CYC-01": {"gross": 2_500_000.00, "taxable": 2_125_000.00, "vat": 255_000.00,
			  "retention": 238_000.00, "cwt": 42_500.00, "due": 2_099_500.00},
	# CYC-05: cumulative exactly 50.00% -> retention still withheld.
	# gross 7,500,000; VAT 900,000; net_before 8,400,000; retention 840,000; CWT 150,000;
	# due = 7,500,000 - 840,000 + 900,000 - 150,000 = 7,410,000.
	"CYC-05": {"gross": 7_500_000.00, "taxable": 7_500_000.00, "vat": 900_000.00,
			  "retention": 840_000.00, "cwt": 150_000.00, "due": 7_410_000.00},
	# CYC-06: cumulative 59.00% -> retention stopped.
	# gross 4,500,000; VAT 540,000; net_before 5,040,000; retention 0.00; CWT 90,000;
	# due = 4,500,000 - 0 + 540,000 - 90,000 = 4,950,000.
	"CYC-06": {"gross": 4_500_000.00, "taxable": 4_500_000.00, "vat": 540_000.00,
			  "retention": 0.00, "cwt": 90_000.00, "due": 4_950_000.00},
}


class TestBillingCycles(IntegrationTestCase):
	"""Ten CIAP 102 cycles, each matching the invariant to the centavo."""

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (D-012)")
		return company[0]

	def _customer(self):
		name = "CYC Client"
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

	def _project(self):
		"""The ERPNext Project the billings link to, plus its Construction Project."""
		erp_name = "CYC Project"
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
					"contract_amount": CONTRACT,
				}
			).insert(ignore_permissions=True)
		return erp_project

	@staticmethod
	def _expected(pct, recoup, cumulative):
		"""The invariant, re-derived independently of the controller."""
		gross = flt(CONTRACT * pct / 100.0, 2)
		taxable = flt(gross - recoup, 2)
		vat = flt(taxable * 0.12, 2)
		net_before = flt(taxable + vat, 2)
		over = flt(cumulative) / CONTRACT * 100.0 > 50.0
		retention = 0.0 if over else flt(net_before * 0.10, 2)
		cwt = flt(taxable * 0.02, 2)
		due = flt(taxable - retention + vat - cwt, 2)
		return {
			"gross": gross,
			"taxable": taxable,
			"vat": vat,
			"net_before": net_before,
			"retention": retention,
			"cwt": cwt,
			"due": due,
		}

	def test_billing_cycles(self):
		company = self._company()
		vat_account = output_vat_account(company)
		self.assertTrue(vat_account, "the output VAT account must resolve, or VAT is omitted")

		project = self._project()
		client = self._customer()

		results = []
		for number, pct, recoup, cumulative in CYCLES:
			doc = frappe.get_doc(
				{
					"doctype": "Construction Progress Billing",
					"billing_number": number,
					"project": project,
					"company": company,
					"client": client,
					"posting_date": "2026-03-31",
					"billing_period_start": "2026-01-01",
					"billing_period_end": "2026-03-31",
					"contract_amount": CONTRACT,
					"period_accomplishment_percent": pct,
					"mobilization_recoupment": recoup,
					"cumulative_accomplishment": cumulative,
				}
			)
			doc.insert(ignore_permissions=True)
			doc.submit()
			results.append((doc, self._expected(pct, recoup, cumulative)))

		# ---- EXPECT: every cycle matches the invariant to 0.01.
		for doc, want in results:
			number = doc.billing_number
			self._assert_pesos(doc.gross_accomplishment_period, want["gross"], f"{number} gross")
			self._assert_pesos(doc.taxable_base, want["taxable"], f"{number} taxable base")
			self._assert_pesos(doc.vat_amount, want["vat"], f"{number} VAT")
			self._assert_pesos(doc.net_before_retention, want["net_before"], f"{number} net before retention")
			self._assert_pesos(doc.retention_amount, want["retention"], f"{number} retention")
			self._assert_pesos(doc.cwt_withheld, want["cwt"], f"{number} CWT")
			self._assert_pesos(doc.net_amount_due, want["due"], f"{number} net amount due")

		# ---- EXPECT: the pinned cycles match their hand-computed literals.
		for number, pinned in PINNED.items():
			doc = next(d for d, _ in results if d.billing_number == number)
			for field, value in pinned.items():
				attr = {
					"gross": "gross_accomplishment_period",
					"taxable": "taxable_base",
					"vat": "vat_amount",
					"retention": "retention_amount",
					"cwt": "cwt_withheld",
					"due": "net_amount_due",
				}[field]
				self._assert_pesos(getattr(doc, attr), value, f"{number} pinned {field}")

		# ---- EXPECT: retention is withheld up to and including 50%, and stops after.
		at_milestone = next(d for d, _ in results if d.billing_number == "CYC-05")
		past_milestone = next(d for d, _ in results if d.billing_number == "CYC-06")
		self._assert_pesos(at_milestone.cumulative_percent, 50.00, "CYC-05 cumulative percent")
		self._assert_pesos(at_milestone.retention_amount, 840_000.00, "CYC-05 retains at exactly 50%")
		self.assertEqual(int(at_milestone.retention_milestone_reached), 0)
		self._assert_pesos(past_milestone.cumulative_percent, 59.00, "CYC-06 cumulative percent")
		self._assert_pesos(past_milestone.retention_amount, 0.00, "CYC-06 retention past 50%")
		self.assertEqual(int(past_milestone.retention_milestone_reached), 1)

		# Every cycle after the milestone retains nothing.
		for doc, _ in results:
			if doc.billing_number > "CYC-05":
				self.assertEqual(
					flt(doc.retention_amount), 0.0, f"{doc.billing_number} must not retain past 50%"
				)

		# ---- EXPECT: the 15% mobilisation advance is recouped in the early cycles.
		first, second, third = results[0][0], results[1][0], results[2][0]
		for doc, pct in ((first, 5.0), (second, 8.0), (third, 10.0)):
			expected_recoup = flt(CONTRACT * pct / 100.0 * ADVANCE_RATIO, 2)
			self._assert_pesos(doc.mobilization_recoupment, expected_recoup, f"{doc.billing_number} recoupment")
		# and not afterwards
		self._assert_pesos(results[3][0].mobilization_recoupment, 0.00, "CYC-04 recoupment")

		# ---- EXPECT: the milestone change is reflected in cumulative percent.
		self._assert_pesos(results[-1][0].cumulative_percent, 100.00, "final cumulative percent")

		# ---- NEGATIVE: the contract amount is locked once a billing is submitted.
		# This is the first row that can exercise that guard; it never fired
		# before, and D-025 found it was matching against the wrong name entirely.
		# Construction Project autonames from its own project_name, which is NOT
		# the ERPNext Project name the billings link to.
		construction_project = frappe.get_doc("Construction Project", "CYC Project")
		construction_project.contract_amount = CONTRACT + 1_000_000.00
		self.assertRaises(
			frappe.ValidationError, construction_project.save, ignore_permissions=True
		)

	def _assert_pesos(self, actual, expected, label):
		self.assertEqual(
			flt(actual), flt(expected, 2), f"{label}: {actual} != {expected}"
		)
