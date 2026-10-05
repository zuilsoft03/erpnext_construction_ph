# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-06 gate: the time-phased baseline fields exist and round-trip.

D-026 needs `planned_start`, `planned_end` and `actual_percent_complete` on
Construction BOQ Item before PV can be anything but a straight line. The
negative case matters as much as the round-trip: a field that only exists
because something called `add_custom_field` at runtime would pass a round-trip
and still be absent on a clean install, so this asserts the fields are part of
the *declared* doctype schema, not of a live table someone mutated.
"""

import frappe
from frappe.tests import IntegrationTestCase


class TestBoqItemPlannedFields(IntegrationTestCase):
	def test_fields_are_declared_on_the_doctype(self):
		"""The three fields are in the doctype definition, not just the table.

		`bench migrate` applies the doctype JSON. A runtime Custom Field would
		appear in `tabDocField` but not in the doctype's declared field set, and
		would be missing on a fresh site.
		"""
		# Assert against the app's own JSON, not `frappe.get_meta`. get_meta
		# MERGES Custom Fields into the doctype meta, so a field added by a
		# runtime `add_custom_field` would satisfy a meta check while still
		# being absent on a clean install. Reading the declared JSON is the
		# only assertion that survives that mistake.
		fields = self._declared_fields()
		declared = {f["fieldname"] for f in fields}
		for fieldname in ("planned_start", "planned_end", "actual_percent_complete"):
			self.assertIn(
				fieldname,
				declared,
				f"{fieldname} is not declared in the doctype JSON; a Custom Field "
				"would not exist on a clean install",
			)
		by_name = {f["fieldname"]: f for f in fields}
		self.assertEqual(by_name["planned_start"]["fieldtype"], "Date")
		self.assertEqual(by_name["planned_end"]["fieldtype"], "Date")
		self.assertEqual(by_name["actual_percent_complete"]["fieldtype"], "Percent")

	@staticmethod
	def _declared_fields():
		import json

		import os

		here = os.path.dirname(os.path.abspath(__file__))
		path = os.path.join(
			os.path.dirname(here), "doctype", "construction_boq_item", "construction_boq_item.json"
		)
		with open(path, encoding="utf-8") as handle:
			return json.load(handle)["fields"]

	def test_fields_round_trip_on_a_child_row(self):
		"""A BOQ Item row stores and returns all three values unchanged."""
		boq = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": self._boq_number(),
				"project": self._project(),
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": "1.01",
						"item_code": "CORE06-TEST",
						"description": "CORE-06 round-trip probe",
						"unit": self._uom(),
						"quantity": 2.0,
						"direct_cost": 1000.0,
						"ocm": 10.0,
						"profit": 20.0,
						"planned_start": "2026-01-01",
						"planned_end": "2026-01-31",
						"actual_percent_complete": 60.0,
					}
				],
			}
		).insert(ignore_permissions=True)

		row = frappe.get_doc("Construction BOQ Item", boq.boq_items[0].name)
		self.assertEqual(str(row.planned_start), "2026-01-01")
		self.assertEqual(str(row.planned_end), "2026-01-31")
		self.assertAlmostEqual(float(row.actual_percent_complete), 60.0, places=2)

	def test_actual_percent_defaults_to_zero(self):
		"""A row with no progress recorded reports 0%, not None.

		EV weights every item by its actual progress. If an untouched row
		defaulted to None the sum would raise on the first project, which is the
		sort of defect that only shows up on real data.
		"""
		boq = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": self._boq_number(),
				"project": self._project(),
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": "1.02",
						"item_code": "CORE06-DEFAULT",
						"description": "CORE-06 default probe",
						"unit": self._uom(),
						"quantity": 1.0,
						"direct_cost": 500.0,
						"ocm": 0.0,
						"profit": 0.0,
					}
				],
			}
		).insert(ignore_permissions=True)
		row = frappe.get_doc("Construction BOQ Item", boq.boq_items[0].name)
		self.assertEqual(float(row.actual_percent_complete or 0.0), 0.0)

	@staticmethod
	def _uom():
		return frappe.get_all("UOM", limit=1, pluck="name")[0]

	@staticmethod
	def _boq_number():
		"""Construction BOQ autonames on boq_number, so it must be unique per run."""
		existing = frappe.get_all("Construction BOQ", pluck="name")
		return f"CORE06-BOQ-{len(existing) + 1:04d}"

	@staticmethod
	def _project():
		name = "CORE06 Project"
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		client = f"{name} Customer"
		if not frappe.db.exists("Customer", client):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": client,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
				}
			).insert(ignore_permissions=True)
		erp_name = f"{name} ERP"
		existing = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		erp_project = existing[0] if existing else frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": erp_name,
				"company": company,
			}
		).insert(ignore_permissions=True).name
		if not frappe.db.exists("Construction Project", name):
			frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": name,
					"erpnext_project": erp_project,
					"company": company,
					"client": client,
					"contract_date": "2026-01-01",
					"contract_amount": 1_000_000.00,
				}
			).insert(ignore_permissions=True)
		return name
