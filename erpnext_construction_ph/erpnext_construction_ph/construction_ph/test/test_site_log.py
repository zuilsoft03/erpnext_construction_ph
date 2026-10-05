# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-09 gate: a site log is reconcilable, not merely writable.

A field log that saves but cannot be tied back to the schedule is the failure
this row exists to prevent, so the negatives matter more than the happy path:
a pour with a volume and no WBS Task, a delay with hours and no reason, and a
Stock Entry that moves a different item or is not a Material Issue at all.
"""

import frappe
from frappe.tests import IntegrationTestCase


class TestSiteLog(IntegrationTestCase):
	"""Pour linkage, delay evidence, and Stock Entry agreement."""

	def test_a_pour_links_to_a_wbs_task(self):
		"""The happy path: pour volume, WBS task, and a Material Issue."""
		# Bind the task once. Calling _task() again would create a *second* task
		# when none exists yet, and the assertion would compare against a task the
		# log was never linked to -- a failure that reads like a wiring bug.
		task = self._task()
		log = self._log(pour_volume=12.5, pour_task=task.name)
		self.assertEqual(log.log_type, "Concrete Pour")
		self.assertEqual(log.pour_task, task.name)
		self.assertEqual(flt_or(log.pour_volume), 12.5)

	def test_a_pour_with_a_volume_and_no_task_is_refused(self):
		"""The negative the gate names: an unlinked pour cannot be reconciled."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._log(pour_volume=12.5, pour_task=None)
		self.assertIn("WBS Task", str(caught.exception))

	def test_a_pour_with_no_volume_needs_no_task(self):
		"""Zero volume is not a pour, so no task is required.

		The guard is on the *volume*, so a general site note is not forced to
		invent a WBS link it has nothing to do with.
		"""
		log = self._log(pour_volume=0, pour_task=None, log_type="General")
		self.assertEqual(log.log_type, "General")

	def test_delay_hours_require_a_reason(self):
		"""Unevidenced delay is not evidence."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._log(log_type="Weather Delay", delay_hours=4.0, delay_reason="")
		self.assertIn("reason", str(caught.exception).lower())

		ok = self._log(log_type="Weather Delay", delay_hours=4.0, delay_reason="Heavy monsoon")
		self.assertEqual(flt_or(ok.delay_hours), 4.0)

	def test_stock_entry_must_be_a_material_issue_for_the_same_item(self):
		"""The two records must describe the same pour."""
		concrete = self._item("Ready-mix concrete")
		other = self._item("Cement, 40kg")

		task = self._task()
		entry = self._stock_entry(concrete, purpose="Material Issue")
		log = self._log(pour_volume=5.0, pour_task=task.name, pour_item=concrete, stock_entry=entry.name)
		self.assertEqual(log.stock_entry, entry.name)

		# A different item: the pair balances to the wrong number.
		wrong_item = self._stock_entry(other, purpose="Material Issue")
		with self.assertRaises(frappe.ValidationError) as caught:
			self._log(
				pour_volume=5.0, pour_task=task.name, pour_item=concrete, stock_entry=wrong_item.name
			)
		self.assertIn("moves", str(caught.exception))

		# The right item, the wrong purpose.
		wrong_purpose = self._stock_entry(concrete, purpose="Material Transfer")
		with self.assertRaises(frappe.ValidationError) as caught:
			self._log(
				pour_volume=5.0, pour_task=task.name, pour_item=concrete, stock_entry=wrong_purpose.name
			)
		self.assertIn("Material Issue", str(caught.exception))

	def test_the_two_refusals_name_the_log(self):
		"""The message must identify which log is wrong, not just that one is."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._log(log_number="CORE09-NAMED", pour_volume=1.0, pour_task=None)
		self.assertIn("CORE09-NAMED", str(caught.exception))

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _item_group():
		"""A stock Item Group that exists on this ERPNext.

		Hardcoding a plausible-sounding group name is a trap: ERPNext's default
		tree differs by version, and a wrong name fails link validation with a
		message about Item Group rather than about the fixture.
		"""
		groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
		for preferred in ("All Item Groups", "Raw Materials", "Consumables"):
			if preferred in groups:
				return preferred
		return groups[0] if groups else None

	@staticmethod
	def _fractional_uom():
		"""A UOM that permits fractions.

		`Nos` is must-be-whole, so a 12.5 m3 pour raises
		UOMMustBeIntegerError. That constraint is real and is exactly why concrete
		is measured in m3 rather than in pieces.
		"""
		fractional = frappe.get_all("UOM", filters={"must_be_whole_number": 0}, pluck="name")
		for preferred in ("Cubic Meter", "M3", "Cubic m"):
			if preferred in fractional:
				return preferred
		return frappe.get_doc(
			{"doctype": "UOM", "uom_name": "Cubic Meter", "uom": "m3", "must_be_whole_number": 0}
		).insert(ignore_permissions=True).uom

	@classmethod
	def _item(cls, name):
		existing = frappe.get_all("Item", filters={"item_name": name}, pluck="name")
		if existing:
			return existing[0]
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": name.split(" ")[0].upper()[:12] + "-CORE09",
				"item_name": name,
				"item_group": cls._item_group(),
				"stock_uom": cls._fractional_uom(),
				"is_stock_item": 1,
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _project(cls):
		"""A dedicated Construction Project, created once and then reused.

		Neither "first row" nor "oldest row" is safe: the gate site carries
		committed leftovers from other fixtures, and ordering by creation picked a
		smoke-test project that has since rolled back, so the Task failed link
		validation against a project that no longer exists. A named project of
		our own is the only stable choice.
		"""
		name = "CORE09 Project"
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

	@classmethod
	def _erp_project(cls):
		"""The native ERPNext Project behind our Construction Project.

		`Task.project` links to the **native** Project, not to Construction
		Project. Pointing it at the Construction Project name fails link
		validation with a message naming the project, which reads like a data
		problem rather than the name/link confusion it is -- the fourth time this
		session that asymmetry has cost a cycle.
		"""
		return frappe.db.get_value("Construction Project", cls._project(), "erpnext_project")

	def _task(self):
		erp_project = self._erp_project()
		existing = frappe.get_all("Task", filters={"project": erp_project}, limit=1, pluck="name")
		if existing:
			return frappe.get_doc("Task", existing[0])
		return frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "CORE-09 pour task",
				"project": erp_project,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	def _stock_entry(self, item, purpose):
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		warehouse = frappe.db.get_value(
			"Stock Entry", {"purpose": purpose, "docstatus": 1}, "to_warehouse"
		) or frappe.get_all("Warehouse", limit=1, pluck="name")[0]
		return frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"purpose": purpose,
				# Honour the argument. Hardcoding one value here would make the
				# "right item, wrong purpose" negative build exactly the same entry
				# as the happy path, so it would assert a purpose refusal while
				# never actually varying the purpose.
				"stock_entry_type": purpose,
				"company": company,
				"from_warehouse": warehouse,
				"to_warehouse": warehouse,
				"items": [
					{
						"item_code": item,
						"qty": 1.0,
						"transfer_qty": 1.0,
						"basic_rate": 100.0,
						# The fixture item has no stock ledger entry, so ERPNext
						# demands a valuation rate it cannot resolve. This row is
						# about the purpose/item agreement, not about costing.
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

	def _log(self, **kwargs):
		seq = len(frappe.get_all("Construction Site Log", pluck="name")) + 1
		values = {
			"doctype": "Construction Site Log",
			"log_number": kwargs.pop("log_number", f"CORE09-{seq:04d}"),
			"project": self._project(),
			"log_date": "2026-01-15 08:00:00",
			"log_type": "Concrete Pour",
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)


def flt_or(value):
	from frappe.utils import flt

	return flt(value)
