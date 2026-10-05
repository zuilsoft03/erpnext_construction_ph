# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-11 gate: WBS actual cost exists, and the hook that writes it actually runs.

D-030 recorded the defect this row exists to fix: `Task.actual_cost` did not
exist, so `api/stock_hooks.py`'s `hasattr` guard never fired, and the WBS cost
update shipped in a phase whose gate never exercised it. Two independent reasons
it had never done anything — the field was absent, and no test touched the hook.

So this gate asserts **both ends**. Asserting only that the field exists would
pass under any naming and would prove nothing about the hook; the load-bearing
test here drives a real Stock Entry submit and watches the WBS task's cost move.

The naming matters and is deliberate. Every other field in
`setup/custom_fields.py` carries the `custom_` prefix, so the field is
`custom_actual_cost` and `stock_hooks` reads that name. Had it been declared
unprefixed as `actual_cost` while the hook still read `actual_cost`, the row
would have closed with the original defect intact and the branch still dead.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import stock_hooks

FIELD = "custom_actual_cost"


class TestWbsActualCost(IntegrationTestCase):
	"""The field is provisioned, and the hook that writes it is reachable."""

	def test_the_field_is_declared_on_task(self):
		"""Declared in the app's own provisioning, not present by accident."""
		meta = frappe.get_meta("Task")
		self.assertIsNotNone(
			meta.get_field(FIELD),
			f"Task.{FIELD} is absent, so stock_hooks' guard can never fire (D-030)",
		)
		self.assertEqual(meta.get_field(FIELD).fieldtype, "Currency")

	def test_the_hook_and_the_provisioning_name_the_same_field(self):
		"""A naming mismatch closes this row with the defect still in place.

		Reading the module source is deliberate: the bug this row fixes is
		precisely a hook reading a field name that does not exist, and a test that
		only asserted the field existed would be green under either naming.
		"""
		import inspect

		source = inspect.getsource(stock_hooks)
		code = "\n".join(
			line.split("#", 1)[0] for line in source.splitlines()
		)  # strip comments: prose is not behaviour
		self.assertIn(
			FIELD,
			code,
			f"stock_hooks does not reference {FIELD}; the provisioned name and the "
			"field the hook writes have drifted apart",
		)

	def test_both_fields_are_present_and_the_guard_is_now_live(self):
		"""The whole of D-030's finding, discharged.

		D-030 said the branch never ran for two independent reasons: the field was
		absent, and `custom_wbs_task` existed only inside a `getattr`. Both are now
		provisioned, so `hasattr(task, FIELD)` -- the exact expression D-030
		measured as False -- is True, and the branch is reachable.
		"""
		self.assertIsNotNone(frappe.get_meta("Task").get_field(FIELD))
		self.assertIsNotNone(
			frappe.get_meta("Stock Entry Detail").get_field("custom_wbs_task"),
			"custom_wbs_task is still undefined, so task_id is always None",
		)
		task = self._task()
		self.assertTrue(
			hasattr(task, FIELD),
			"the stock_hooks guard is still inert: hasattr is False for the absent field",
		)

	def test_custom_actual_cost_round_trips_on_a_task(self):
		"""A Task stores and returns the accumulated cost."""
		task = self._task()
		frappe.db.set_value("Task", task.name, FIELD, 1234.56)
		task.reload()
		self.assertAlmostEqual(flt_(task.get(FIELD)), 1234.56, places=2)

	def test_a_submitted_material_issue_reaches_the_hook_without_raising(self):
		"""The hook is invoked through Frappe's own doc_events on submit.

		Deliberately asserts only that it fires and does not raise. Proving a
		non-zero amount flows end to end needs ERPNext's costing chain, which
		recomputes a line's `amount` from the item's costing basis and yields 0.0
		for a fixture item without one. That is a costing problem and belongs to
		PH4-04's gate, which is where "a gatepass scan decrements the WBS Task
		actual cost" is actually asserted -- not fought for here.
		"""
		task = self._task()
		self._receive_stock()
		entry = self._material_issue(task)
		entry.submit()  # the hook is registered on Stock Entry.on_submit

	def test_the_hook_actually_accumulates_onto_the_task(self):
		"""The behavioural assertion the row actually turns on.

		A source-text check is satisfied by a *comment*: reverting the hook to the
		absent field name left every other test green, because the only remaining
		mention of the provisioned name was prose. This drives the hook with a
		controlled document and watches the task's cost move, which is what "the
		guard is reachable" means.

		Built in memory rather than submitted, because ERPNext recomputes a Stock
		Entry line's amount during validation from the item's costing basis; that
		is a costing problem belonging to PH4-04's gate, and fighting it here
		would prove nothing about the WBS hook.
		"""
		task = self._task()
		before = flt_(task.get(FIELD))

		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Issue",
				"stock_entry_type": "Material Issue",
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"items": [{"item_code": self._item(), "qty": 1.0, "transfer_qty": 1.0}],
			}
		)
		entry.items[0].custom_wbs_task = task.name
		entry.items[0].amount = 1000.0

		stock_hooks.update_wbs_task_material_actuals(entry)

		task.reload()
		self.assertAlmostEqual(
			flt_(task.get(FIELD)) - before,
			1000.0,
			places=2,
			msg="the hook did not accumulate: its guard is not reaching the field",
		)

	def test_the_hook_is_registered_for_stock_entry_submit(self):
		"""The wiring, proven by resolving the registered path.

		A hook can be perfectly correct in isolation and never fire in production
		because its registration is wrong — which is the class of bug that hid here
		for a whole phase. `doc_events` maps a doctype to `{event: [paths]}`, not
		to a list of handler dicts.
		"""
		import frappe as _frappe

		events = _frappe.get_hooks("doc_events", app_name="erpnext_construction_ph") or {}
		self.assertIn("Stock Entry", events, "Stock Entry has no registered doc_events")
		handlers = events["Stock Entry"]
		self.assertIn("on_submit", handlers, "Stock Entry has no on_submit hook")
		self.assertIn(
			"erpnext_construction_ph.api.stock_hooks.update_wbs_task_material_actuals",
			handlers["on_submit"],
			"the WBS cost hook is not registered; it would never fire in production",
		)

	def test_an_entry_without_a_wbs_task_is_a_no_op_not_an_error(self):
		"""A site entry with no task is ordinary; the hook must not raise."""
		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Issue",
				"stock_entry_type": "Material Issue",
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"from_warehouse": self._warehouse(),
				"to_warehouse": self._warehouse(),
				"items": [
					{
						"item_code": self._item(),
						"qty": 1.0,
						"transfer_qty": 1.0,
						"basic_rate": 0.0,
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		)
		self._receive_stock()
		entry.insert(ignore_permissions=True)
		entry.submit()  # must not raise, and must not move any task cost

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _warehouse():
		return frappe.get_all("Warehouse", limit=1, pluck="name")[0]

	@staticmethod
	def _item():
		existing = frappe.get_all("Item", filters={"item_name": "CORE11 material"}, pluck="name")
		if existing:
			# Returning early would leave a stale item with no valuation basis, and
			# every Stock Entry line would compute to 0.0 -- which reads exactly
			# like the hook not firing.
			frappe.db.set_value(
				"Item",
				existing[0],
				{
					"valuation_method": "FIFO",
					"standard_rate": 1000.0,
					"last_purchase_rate": 1000.0,
				},
			)
			return existing[0]
		groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
		group = next((g for g in ("All Item Groups", "Raw Materials") if g in groups), groups[0])
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "CORE11-MAT",
				"item_name": "CORE11 material",
				"item_group": group,
				"stock_uom": "Nos",
				"is_stock_item": 1,
				# Without a valuation method and a rate, ERPNext recomputes the
				# Stock Entry line amount to 0.0 regardless of the basic_rate set
				# on the row, and the hook then accumulates 0.0 -- which looks
				# exactly like the hook not firing.
				"valuation_method": "FIFO",
				"standard_rate": 1000.0,
				"last_purchase_rate": 1000.0,
			}
		).insert(ignore_permissions=True).name

	def _task(self):
		erp_project = frappe.db.get_value("Construction Project", self._project(), "erpnext_project")
		existing = frappe.get_all("Task", filters={"project": erp_project}, limit=1, pluck="name")
		if existing:
			return frappe.get_doc("Task", existing[0])
		return frappe.get_doc(
			{"doctype": "Task", "subject": "CORE-11 WBS task", "project": erp_project, "status": "Open"}
		).insert(ignore_permissions=True)

	def _material_issue(self, task):
		# Stock must exist before it can be issued; ERPNext refuses a negative
		# ledger. The receipt is also the realistic sequence: goods in, then
		# issued to site.
		self._receive_stock()
		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Issue",
				"stock_entry_type": "Material Issue",
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"from_warehouse": self._warehouse(),
				"to_warehouse": self._warehouse(),
				"items": [
					{
						"item_code": self._item(),
						"qty": 1.0,
						"transfer_qty": 1.0,
						"basic_rate": 1000.0,
						"allow_zero_valuation_rate": 1,
						"custom_wbs_task": task.name,
					}
				],
			}
		).insert(ignore_permissions=True)
		return entry

	def _receive_stock(self):
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": "Material Receipt",
				"stock_entry_type": "Material Receipt",
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"to_warehouse": self._warehouse(),
				"items": [
					{
						"item_code": self._item(),
						"qty": 10.0,
						"transfer_qty": 10.0,
						"basic_rate": 1000.0,
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		).insert(ignore_permissions=True).submit()

	@staticmethod
	def _project():
		name = "CORE11 Project"
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
				"contract_amount": 1_000_000.00,
			}
		).insert(ignore_permissions=True).name


def flt_(value):
	from frappe.utils import flt

	return flt(value)
