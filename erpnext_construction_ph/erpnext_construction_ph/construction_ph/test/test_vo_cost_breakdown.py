# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-14 gate: the DUPA breakdown table exists and is bound to its parent.

`Construction Variation Order` declared a single `variation_amount` and nothing
else, so PH5-03's premise — a breakdown that reconciles to a total — had nothing
to reconcile *from*. This row adds the child table and the Table field, and
checks the two ways it can be wrong: the field not being a child table at all, and
the child rows drifting from the parent figure.
"""

import frappe
from frappe.tests import IntegrationTestCase


class TestVoCostBreakdown(IntegrationTestCase):
	"""The breakdown is a real child table and its lines sum to the parent."""

	def test_the_child_doctype_exists_and_is_a_child_table(self):
		self.assertTrue(frappe.db.exists("DocType", "VO Cost Breakdown"))
		self.assertTrue(
			frappe.get_meta("VO Cost Breakdown").istable,
			"without istable this becomes a standalone doctype and the Table field cannot use it",
		)

	def test_the_parent_carries_a_table_field_pointing_at_it(self):
		field = frappe.get_meta("Construction Variation Order").get_field("dupa_breakdown")
		self.assertIsNotNone(field, "the parent has no dupa_breakdown field")
		self.assertEqual(field.fieldtype, "Table")
		self.assertEqual(field.options, "VO Cost Breakdown")

	def test_a_breakdown_row_round_trips(self):
		"""All four fields survive a save and reload."""
		vo = self._vo()
		row = vo.dupa_breakdown[0]
		self.assertEqual(row.component, "Labor")
		self.assertEqual(row.quantity, 10.0)
		self.assertEqual(row.rate, 500.0)
		self.assertEqual(row.amount, 5000.00)

		vo.reload()
		self.assertEqual(vo.dupa_breakdown[0].amount, 5000.00)
		self.assertEqual(len(vo.dupa_breakdown), 2)

	def test_the_lines_sum_to_the_direct_cost_they_contain(self):
		"""Lines are the DIRECT cost; the parent figure is the inclusive total.

		They are deliberately different quantities (D-028), so the parent's guard
		does not compare them. What it must guarantee is that the lines add up to
		the direct cost they represent.
		"""
		vo = self._vo()
		summed = sum(float(r.amount) for r in vo.dupa_breakdown)
		self.assertAlmostEqual(summed, 6000.00, places=2)

	def test_the_amount_is_derived_not_typed(self):
		"""Changing quantity or rate moves the amount; typing it does not survive.

		The amount is read-only and recomputed, so a transcription error cannot
		enter a build-up that a contract variation is paid from.
		"""
		vo = self._vo()
		vo.dupa_breakdown[0].quantity = 20.0
		vo.variation_amount = 13000.00  # the inclusive total must cover the new direct cost
		vo.save()
		vo.reload()
		self.assertAlmostEqual(float(vo.dupa_breakdown[0].amount), 10000.00, places=2)

		# A hand-typed amount is overwritten rather than honoured.
		# A hand-typed amount does not survive: it is recomputed from qty x rate.
		vo.dupa_breakdown[0].amount = 999999.00
		vo.save()
		vo.reload()
		self.assertAlmostEqual(float(vo.dupa_breakdown[0].amount), 10000.00, places=2)

	def test_a_breakdown_that_disagrees_with_the_parent_is_refused(self):
		"""The gate's NEGATIVE: a child table that can drift is worse than none."""
		vo = self._vo()
		# Direct cost rises to 11,000 while variation_amount stays 6,000.
		vo.dupa_breakdown[0].quantity = 20.0
		with self.assertRaises(frappe.ValidationError) as caught:
			vo.save()
		self.assertIn("less than the direct cost", str(caught.exception))

	def test_a_hand_entered_line_amount_is_overwritten_not_refused(self):
		"""A typed amount does not survive; it is recomputed.

		An earlier version *rejected* a mismatch instead. That is undecidable: a
		stale derived amount left over from before an edit is indistinguishable
		from a deliberately typed one, so the check refused ordinary edits while
		proving nothing about intent. Deriving is simpler and safer.
		"""
		vo = self._vo()
		vo.dupa_breakdown[0].amount = 4999.00  # 10 x 500 is 5,000
		vo.save()
		vo.reload()
		self.assertAlmostEqual(float(vo.dupa_breakdown[0].amount), 5000.00, places=2)

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _any_project():
		"""A Construction Project, created if the site has none.

		This used to be `frappe.get_all("Construction Project", limit=1)[0]`,
		which is an **IndexError on a clean site** and silently depends on
		residue another test class happened to commit. Found by running CORE-01's
		own authored negative -- uninstall the app, reinstall it -- after which
		every Construction Project is gone and 11 tests failed. A test that only
		passes because something else left a row behind is not testing anything.
		"""
		import uuid

		# No borrowing (CORE-30). This helper was fixed once before -- its own docstring
		# records that `limit=1` used to raise IndexError on a clean site -- and the fix
		# removed the residue-sensitivity while leaving the dependence on residue.
		from erpnext_construction_ph.construction_ph.test import fixtures

		return fixtures.make_project("VO")
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = "Fixture Customer " + uuid.uuid4().hex[:6]
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer,
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		).insert(ignore_permissions=True)
		erp_name = "Fixture ERP " + uuid.uuid4().hex[:6]
		erp_project = frappe.get_doc(
			{"doctype": "Project", "project_name": erp_name, "company": company}
		).insert(ignore_permissions=True).name
		return frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": "Fixture Project " + uuid.uuid4().hex[:6],
				"erpnext_project": erp_project,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 1000000.00,
			}
		).insert(ignore_permissions=True).name

	def _vo(self):
		name = "CORE14 VO"
		existing = frappe.get_all("Construction Variation Order", filters={"vo_number": name}, pluck="name")
		if existing:
			frappe.delete_doc("Construction Variation Order", existing[0], force=True, ignore_permissions=True)
		project = self._any_project()
		return frappe.get_doc(
			{
				"doctype": "Construction Variation Order",
				"vo_number": name,
				"project": project,
				"vo_date": "2026-04-01",
				"vo_type": "Change Order",
				"cor": "CORE14-COR-001",
				"description": "CORE-14 breakdown fixture",
				"original_contract_amount": 1_000_000.00,
				"variation_amount": 6000.00,
				"status": "Draft",
				"dupa_breakdown": [
					{
						"component": "Labor",
						"basis": "DPWA schedule item, measured hours",
						"quantity": 10.0,
						"rate": 500.0,
					},
					{
						"component": "Material",
						"quantity": 10.0,
						"rate": 100.0,
					},
				],
			}
		).insert(ignore_permissions=True)
