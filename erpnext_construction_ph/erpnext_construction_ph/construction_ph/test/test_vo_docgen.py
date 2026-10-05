# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH5-03 gate: the DUPA build-up reconciles, additively, to the stated total.

Every expected figure below is **hand-computed** and written out in the fixture
comment, because a test that derives its expectations from the generator agrees
with it by construction. The fixture uses round numbers precisely so the
arithmetic can be checked on paper:

    labor      10 units x 500.00 =  5,000.00
    material   20 units x  50.00 =  1,000.00
    equipment   5 units x 200.00 =  1,000.00
    direct                         7,000.00
    OCM     15% of 7,000      =    1,050.00
    profit  10% of 7,000      =      700.00
    subtotal                     8,750.00
    VAT     12% of 8,750      =    1,050.00
    total                        9,800.00
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import vo_docgen

# Hand-computed. See the module docstring.
EXPECTED = {
	"direct": 7000.00,
	"ocm": 1050.00,
	"profit": 700.00,
	"subtotal": 8750.00,
	"vat": 1050.00,
	"total": 9800.00,
}


class TestVoDocgen(IntegrationTestCase):
	"""The additive build-up, its reconciliation, and the refusals."""

	def _components(self):
		return [
			{"component": "Labor", "quantity": 10.0, "rate": 500.00, "basis": "measured hours"},
			{"component": "Material", "quantity": 20.0, "rate": 50.00, "basis": "quotation"},
			{"component": "Equipment", "quantity": 5.0, "rate": 200.00, "basis": "plant hours"},
		]

	# ---- the arithmetic, against literals -----------------------------------

	def test_the_build_up_matches_hand_computed_figures(self):
		"""Each stage, against the numbers in this file's docstring."""
		result = vo_docgen.compute(self._components())
		for key, expected in EXPECTED.items():
			self.assertAlmostEqual(
				flt(result[key]), expected, places=2,
				msg=f"{key} is {result[key]}, hand-computed {expected}",
			)

	def test_the_rates_are_the_gate_rates(self):
		"""15 / 10 / 12, sourced to GATES.md PH5-03."""
		self.assertAlmostEqual(vo_docgen.DUPA_RATES["ocm"], 0.15, places=6)
		self.assertAlmostEqual(vo_docgen.DUPA_RATES["profit"], 0.10, places=6)
		self.assertAlmostEqual(vo_docgen.DUPA_RATES["vat"], 0.12, places=6)

	def test_the_build_up_reconciles_to_the_stated_total(self):
		"""The gate's core assertion, to 0.01."""
		result = vo_docgen.compute(self._components())
		self.assertTrue(vo_docgen.reconciles(result, EXPECTED["total"]))
		# Inside the stated 0.01 reconciles; outside it does not. Stated in
		# centavos so the boundary is unambiguous -- the previous phrasing called
		# a peso "inside 0.01", which is false and would mislead a later reader.
		self.assertTrue(
			vo_docgen.reconciles(result, EXPECTED["total"] + 0.005),
			"half a centavo is inside the stated 0.01",
		)
		self.assertFalse(
			vo_docgen.reconciles(result, EXPECTED["total"] + 1.00),
			"one peso is emphatically outside 0.01",
		)

	def test_the_build_up_is_additive_not_multiplicative(self):
		"""Pins the shape D-028 settled, which the two conventions disagree on.

		Multiplicative pricing of the same 7,000 direct cost would give
		7,000 x 1.15 x 1.10 = 8,855.00 before VAT. Asserting the additive figure is
		what stops someone "unifying" this with the BOQ Item's convention.
		"""
		result = vo_docgen.compute(self._components())
		multiplicative = flt(7000.00 * 1.15 * 1.10, 2)
		self.assertNotAlmostEqual(flt(result["subtotal"]), multiplicative, places=2)
		self.assertAlmostEqual(flt(result["subtotal"]), 8750.00, places=2)

	def test_each_line_carries_its_own_amount_and_basis(self):
		"""A reviewer asks "where did this come from" and gets an answer per line."""
		result = vo_docgen.compute(self._components())
		amounts = {line["component"]: line["amount"] for line in result["lines"]}
		self.assertEqual(amounts, {"Labor": 5000.00, "Material": 1000.00, "Equipment": 1000.00})
		self.assertTrue(all(line["basis"] for line in result["lines"]))

	# ---- refusals ----------------------------------------------------------

	def test_a_missing_rate_refuses_rather_than_being_zero(self):
		"""A rate that is absent must not become zero.

		A variation priced with a zero OCM looks like a real figure and is one
		nobody can check.
		"""
		original = vo_docgen.DUPA_RATES["ocm"]
		vo_docgen.DUPA_RATES["ocm"] = None
		try:
			with self.assertRaises(frappe.ValidationError) as caught:
				vo_docgen.compute(self._components())
			self.assertIn("No DUPA rate is configured", str(caught.exception))
		finally:
			vo_docgen.DUPA_RATES["ocm"] = original

	def test_an_unknown_component_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			vo_docgen.compute([{"component": "Subcontractor", "quantity": 1.0, "rate": 100.0}])
		self.assertIn("not a DUPA component", str(caught.exception))

	# ---- the generated document -------------------------------------------

	def test_the_generated_vo_carries_the_breakdown_and_the_total(self):
		"""The document the gate names, with its build-up attached."""
		project = self._project()
		name = vo_docgen.generate_variation_order(
			project,
			vo_type="Change Order",
			components=self._components(),
			cor="PH503-COR-001",
			description="PH5-03 fixture variation",
		)
		vo = frappe.get_doc("Construction Variation Order", name)

		self.assertAlmostEqual(flt(vo.variation_amount), EXPECTED["total"], places=2)
		self.assertEqual(len(vo.dupa_breakdown), 3)
		self.assertAlmostEqual(
			flt(sum(float(r.amount) for r in vo.dupa_breakdown)), 7000.00, places=2
		)
		self.assertEqual({r.component for r in vo.dupa_breakdown},
		                 {"Labor", "Material", "Equipment"})

	# ---- the COR guard, executed for the first time ------------------------

	def test_a_vo_cannot_exist_without_a_cor_reference(self):
		"""D-023 discharge, and it is a different finding than expected.

		The Phase 0 controller throws when a VO reaches Approved or Executed with
		no Change Order Request reference, and that guard has never been executed
		-- the CORE-03 gate only counted DocType rows. This is the row that finally
		runs it, and running it shows the guard is **unreachable**: `cor` is
		declared mandatory, so Frappe's own field validation refuses the insert
		first and the controller's message never appears.

		So the enforced property is the mandatoriness, and the controller check is
		defence-in-depth behind it. Asserting the controller's message here would
		be asserting something that never happens -- the same shape as the TBM
		"Date is required" branch, which `tbm_date`'s own mandatoriness makes
		unreachable.
		"""
		project = self._project()
		with self.assertRaises(frappe.MandatoryError) as caught:
			vo_docgen.generate_variation_order(
				project, vo_type="Change Order", components=self._components(),
				cor=None, description="no COR",
			)
		self.assertIn("cor", str(caught.exception))

	def test_approving_a_vo_with_a_cor_reference_succeeds(self):
		"""The positive half, so the negative above is not a blanket refusal."""
		project = self._project()
		name = vo_docgen.generate_variation_order(
			project, vo_type="Change Order", components=self._components(),
			cor="PH503-COR-003", description="approvable",
		)
		vo = frappe.get_doc("Construction Variation Order", name)
		vo.status = "Approved"
		vo.save()
		vo.reload()
		self.assertEqual(vo.status, "Approved")

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _project():
		name = "PH5-03 Project"
		existing = frappe.get_all("Construction Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = f"{name} Customer"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
				}
			).insert(ignore_permissions=True)
		erp_name = f"{name} ERP"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		erp_project = hits[0] if hits else frappe.get_doc(
			{"doctype": "Project", "project_name": erp_name, "company": company}
		).insert(ignore_permissions=True).name
		return frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": name,
				"erpnext_project": erp_project,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 20_000_000.00,
			}
		).insert(ignore_permissions=True).name
