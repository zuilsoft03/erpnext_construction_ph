# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Runtime test for the CIAP 102 contract-amount lock.

`ConstructionProgressBilling.calculate_certification` opens with

    contract_amount = flt(self.contract_amount)
    if contract_amount <= 0:
        frappe.throw(_("Contract Amount must be greater than zero."))

and everything below it divides by that figure, so this is the guard that keeps a
certification from being certified against a contract worth nothing -- and it is
the only reason `cumulative_percent`, `retention_milestone_reached` and
`net_amount_due` mean anything.

The ledger recorded this control DONE and no runtime test had ever executed it.
These go through a real `insert()`, not a direct call to the controller: a
`frappe.get_doc(...).validate()` would exercise the arithmetic while proving
nothing about whether the lock sits on the path a user actually takes.

Expected figures are hand-computed from the class A invariant in
docs/ARCHITECTURE.md:82-88 for a PHP 10,000,000 contract certified at 10% of the
period with nothing recouped:

    gross_accomplishment_period = 10,000,000 x 10%          = 1,000,000.00
    taxable_base                = 1,000,000 - 0             = 1,000,000.00
    vat                         = 1,000,000 x 12%           =   120,000.00
    net_before_retention        = 1,120,000.00
    retention (10%, cum 10% <= 50% milestone)                =   112,000.00
    net_after_retention         = 1,008,000.00
    cwt (2% of taxable_base)                                  =    20,000.00
    net_amount_due              = 1,008,000 - 20,000         =   988,000.00

so a lock that refused everything would fail the fourth test below rather than
pass by refusing.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.construction_ph.test import fixtures

DT = "Construction Progress Billing"
REFUSAL = "Contract Amount must be greater than zero."
CONTRACT = 10_000_000.00


class TestContractAmountLockRuntime(IntegrationTestCase):
	"""A certification cannot be taken against a contract worth nothing."""

	def setUp(self):
		super().setUp()
		self.company = frappe.get_all("Company", limit=1, pluck="name")[0]
		self.assertTrue(self.company, "the site needs a Company (see D-012)")
		project = fixtures.make_project("CPBLK")
		self.project = frappe.db.get_value("Construction Project", project, "erpnext_project")
		self.customer = frappe.db.get_value("Construction Project", project, "client")

	def _billing(self, contract_amount, tag):
		return frappe.get_doc(
			{
				"doctype": DT,
				"billing_number": f"CPBLK-{tag}-{frappe.generate_hash(length=6)}",
				"project": self.project,
				"company": self.company,
				"client": self.customer,
				"posting_date": "2026-10-01",
				"billing_period_start": "2026-10-01",
				"billing_period_end": "2026-10-31",
				"contract_amount": contract_amount,
				"period_accomplishment_percent": 10.0,
				"cumulative_accomplishment": 1_000_000.00,
			}
		)

	def _assert_refused(self, doc):
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.insert(ignore_permissions=True)
		self.assertIn(REFUSAL, str(caught.exception))
		self.assertIsNone(
			frappe.db.exists(DT, {"billing_number": doc.billing_number}),
			"a refused insert must not leave a row behind",
		)

	# --- the lock ----------------------------------------------------------

	def test_a_zero_contract_amount_is_refused(self):
		self._assert_refused(self._billing(0, "ZERO"))

	def test_a_negative_contract_amount_is_refused(self):
		self._assert_refused(self._billing(-500.00, "NEG"))

	def test_a_billing_with_no_contract_amount_is_refused(self):
		"""`contract_amount` is not `reqd` in the JSON, so blank is reachable."""
		self._assert_refused(self._billing(None, "BLANK"))

	# --- and it is a lock, not a blanket refusal ---------------------------

	def test_a_real_certification_is_still_accepted_and_totals(self):
		doc = self._billing(CONTRACT, "OK")
		doc.insert(ignore_permissions=True)

		stored = frappe.db.get_value(
			DT,
			doc.name,
			["gross_accomplishment_period", "cumulative_percent", "net_amount_due"],
			as_dict=True,
		)
		self.assertEqual(flt(stored.gross_accomplishment_period, 2), 1_000_000.00)
		self.assertEqual(flt(stored.cumulative_percent, 4), 10.0)
		self.assertEqual(
			flt(stored.net_amount_due, 2),
			988_000.00,
			"the class A money invariant, hand-computed in this module's docstring",
		)

	def test_a_small_but_positive_contract_amount_is_accepted(self):
		"""The lock is on zero, not on size."""
		doc = self._billing(1.00, "SMALL")
		doc.insert(ignore_permissions=True)
		self.assertEqual(flt(doc.cumulative_percent, 4), 100_000_000.0)