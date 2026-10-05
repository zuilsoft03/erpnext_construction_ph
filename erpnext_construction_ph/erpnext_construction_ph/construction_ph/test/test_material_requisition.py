# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH4-04 gate: the requisition cap, and the gatepass that turns it into a cost.

Two separable properties, asserted separately so a failure says which broke:

- **the cap** — a requisition may not exceed the BOQ item's takeoff quantity,
  measured against the BOQ line and what earlier requisitions already drew;
- **the gatepass** — scanning issues the material, and submitting the resulting
  Stock Entry moves `Task.custom_actual_cost` via `api/stock_hooks`.

The second half is the assertion CORE-11 deferred here, so it is worth stating
that it goes through a real `submit()`, not a direct call to the hook.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import material_requisition as mr

# A BOQ line worth exactly 100 units. The cap tests read these literals.
TAKEOFF_QTY = 100.0
RATE = 1000.0


class TestMaterialRequisition(IntegrationTestCase):
	"""The cap is measured against the BOQ; the gatepass produces a WBS cost."""

	# ---- the cap -----------------------------------------------------------

	def test_a_requisition_within_the_takeoff_is_accepted(self):
		boq_item, project = self._boq_item(TAKEOFF_QTY)
		name = mr.raise_requisition(project, boq_item, qty=40.0, schedule_date="2027-01-01")
		req = frappe.get_doc("Material Request", name)
		self.assertEqual(len(req.items), 1)
		self.assertEqual(req.items[0].custom_boq_item, boq_item)
		self.assertEqual(flt(req.items[0].qty), 40.0)

	def test_a_requisition_above_the_takeoff_is_refused(self):
		"""The gate's NEGATIVE. The first of the two."""
		boq_item, project = self._boq_item(TAKEOFF_QTY)
		with self.assertRaises(mr.RequisitionError) as caught:
			mr.raise_requisition(project, boq_item, qty=TAKEOFF_QTY + 1, schedule_date="2027-01-01")
		self.assertIn("cannot exceed", str(caught.exception))

	def test_earlier_requisitions_reduce_the_headroom(self):
		"""The cap is cumulative, not per-requisition.

		Two requests of 60 each are individually inside a takeoff of 100, so a cap
		that only compared one request at a time would let 120 through — a phantom
		site order of exactly the kind blueprint 10.1 exists to prevent.
		"""
		boq_item, project = self._boq_item(TAKEOFF_QTY)
		mr.raise_requisition(project, boq_item, qty=60.0, schedule_date="2027-01-01")

		available, _item = mr.headroom(boq_item)
		self.assertAlmostEqual(available, 40.0, places=2)

		with self.assertRaises(mr.RequisitionError):
			mr.raise_requisition(project, boq_item, qty=60.0, schedule_date="2027-01-01")

	def test_a_request_exactly_at_the_takeoff_is_allowed(self):
		"""The boundary is inclusive; `> available` not `>= available`."""
		boq_item, project = self._boq_item(TAKEOFF_QTY)
		name = mr.raise_requisition(project, boq_item, qty=TAKEOFF_QTY, schedule_date="2027-01-01")
		self.assertTrue(name)

	def test_a_phantom_boq_item_is_refused(self):
		"""No BOQ line means no takeoff quantity, so nothing can be requested."""
		_project = self._boq_item(TAKEOFF_QTY)[1]
		with self.assertRaises(mr.RequisitionError) as caught:
			mr.raise_requisition(_project, "NO-SUCH-BOQ-ITEM", qty=1.0, schedule_date="2027-01-01")
		self.assertIn("does not exist", str(caught.exception))

	# ---- the gatepass ------------------------------------------------------

	def test_a_gatepass_scan_issues_stock_and_moves_the_wbs_cost(self):
		"""The assertion CORE-11 deferred to this row.

		Driven end to end: receive stock at a real rate, raise a requisition,
		scan it at the gate, and watch `Task.custom_actual_cost` move. The Stock
		Entry is submitted, so the hook fires through Frappe's own `doc_events`
		composition rather than being called directly.
		"""
		boq_item, project = self._boq_item(TAKEOFF_QTY)
		task = self._task(project)
		warehouse = self._warehouse()
		before = flt(task.get("custom_actual_cost"))

		mr.receive_into_store(boq_item, qty=50.0, warehouse=warehouse, rate=RATE)
		request = mr.raise_requisition(project, boq_item, qty=50.0, schedule_date="2027-01-01")
		entry_name = mr.gatepass_scan(request, warehouse, task=task.name)

		entry = frappe.get_doc("Stock Entry", entry_name)
		self.assertEqual(entry.purpose, "Material Issue")
		self.assertEqual(entry.docstatus, 1, "the gatepass entry was not submitted")

		task.reload()
		self.assertGreater(
			flt(task.get("custom_actual_cost")),
			before,
			"the gatepass did not move WBS actual cost; the stock hook did not fire",
		)

	# ---- fixtures ----------------------------------------------------------

	def _item(self, seq):
		"""A real Item code.

		`Construction BOQ Item.item_code` is Data, so any string validates there --
		but the requisition and both Stock Entries reference it as a Link to Item,
		so a BOQ line carrying a made-up code can never be issued against.
		"""
		code = f"PH404-MAT-{seq:03d}"
		if not frappe.db.exists("Item", code):
			groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
			group = next((g for g in ("All Item Groups", "Raw Materials") if g in groups), groups[0])
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": f"PH4-04 material {seq}",
					"item_group": group,
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"valuation_method": "FIFO",
					"standard_rate": RATE,
				}
			).insert(ignore_permissions=True)
		return code

	@staticmethod
	def _warehouse():
		return frappe.get_all("Warehouse", limit=1, pluck="name")[0]

	def _task(self, project):
		erp = frappe.db.get_value("Construction Project", project, "erpnext_project")
		existing = frappe.get_all("Task", filters={"project": erp}, limit=1, pluck="name")
		if existing:
			return frappe.get_doc("Task", existing[0])
		return frappe.get_doc(
			{"doctype": "Task", "subject": "PH4-04 WBS task", "project": erp, "status": "Open"}
		).insert(ignore_permissions=True)

	def _boq_item(self, qty):
		"""A BOQ line for this test, on a project and BOQ of its own."""
		name = "PH4-04 Project"
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
		project = frappe.db.get_value(
			"Construction Project", {"project_name": name}, "name"
		) or frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": name,
				"erpnext_project": erp_project,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 10_000_000.00,
			}
		).insert(ignore_permissions=True).name

		# A BOQ of its own per call, so each test gets an uncommitted takeoff and
		# the cumulative-headroom test is not polluted by its neighbours.
		seq = len(frappe.get_all("Construction BOQ", filters={"project": project}, pluck="name")) + 1
		boq = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": f"PH404-BOQ-{seq:04d}",
				"project": project,
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": f"{seq}.01",
						"item_code": self._item(seq),
						"description": "PH4-04 takeoff line",
						"unit": frappe.get_all("UOM", limit=1, pluck="name")[0],
						"quantity": qty,
						"direct_cost": 10.0,
						"ocm": 0.0,
						"profit": 0.0,
					}
				],
			}
		).insert(ignore_permissions=True)
		return boq.boq_items[0].name, project
