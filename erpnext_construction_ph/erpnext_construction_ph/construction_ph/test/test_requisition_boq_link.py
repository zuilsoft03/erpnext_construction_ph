# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-13 gate: the link a requisition's cap is measured against.

ERPNext's `Material Request Item` — the child table ERPNext actually names it;
`Material Request Detail` does not exist — had no link to a BOQ item, so the
"a requisition cannot exceed the takeoff quantity" cap in PH4-04 had nothing
measurable to compare against. A cap computed against the requisition's own
quantity would be a tautology.

The child doctype name is worth asserting, because getting it wrong fails as
`Could not find DocType: Material Request Detail` at provisioning time rather than
at the point the mistake matters.
"""

import frappe
from frappe.tests import IntegrationTestCase


class TestRequisitionBoqLink(IntegrationTestCase):
	"""`custom_boq_item` exists, round-trips, and points at a real BOQ Item."""

	def test_the_child_doctype_is_named_as_erpnext_names_it(self):
		"""Guards the name this row provisions against.

		The child table is `Material Request Item`. `Material Request Detail` does
		not exist in this ERPNext, and a field declared against it fails at
		provisioning with a message about the doctype rather than about the field.
		"""
		self.assertTrue(frappe.db.exists("DocType", "Material Request Item"))
		self.assertFalse(frappe.db.exists("DocType", "Material Request Detail"))
		options = frappe.get_meta("Material Request").get_field("items").options
		self.assertEqual(options, "Material Request Item")

	def test_the_field_is_provisioned_on_the_child_table(self):
		field = frappe.get_meta("Material Request Item").get_field("custom_boq_item")
		self.assertIsNotNone(
			field, "custom_boq_item is absent, so the requisition cap has nothing to measure"
		)
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Construction BOQ Item")

	def test_it_round_trips_on_a_material_request_item(self):
		"""A requisition line stores the BOQ item it draws against."""
		boq_item, request = self._request_with_boq_item()
		row = frappe.get_doc("Material Request Item", request.items[0].name)
		self.assertEqual(row.custom_boq_item, boq_item)

	def test_a_phantom_boq_item_is_refused(self):
		"""The negative the gate names.

		A cap measured against a BOQ item that does not exist would pass every
		requisition, so link validation is part of this row's correctness rather
		than a nicety.
		"""
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Material Request",
					"material_request_type": "Purchase",
					"company": frappe.get_all("Company", limit=1, pluck="name")[0],
					"items": [
						{
							"item_code": self._item(),
							"qty": 1.0,
							"uom": self._uom(),
							"schedule_date": "2026-02-01",
							"custom_boq_item": "NO-SUCH-BOQ-ITEM",
						}
					],
				}
			).insert(ignore_permissions=True)

	# ---- fixtures ----------------------------------------------------------

	def _request_with_boq_item(self):
		boq_item = self._boq_item()
		uom = frappe.db.get_value("Construction BOQ Item", boq_item, "unit") or self._uom()
		request = frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": "Purchase",
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"transaction_date": "2026-01-01",
				"items": [
					{
						"item_code": self._item(),
						"qty": 1.0,
						"uom": uom,
						# `schedule_date` is ERPNext's "Required By" and is mandatory on
						# the child row; without it the insert fails before the link this
						# row exists to exercise is ever reached.
						"schedule_date": "2026-02-01",
						"custom_boq_item": boq_item,
					}
				],
			}
		).insert(ignore_permissions=True)
		return boq_item, request

	@staticmethod
	def _uom():
		return frappe.get_all("UOM", limit=1, pluck="name")[0]

	@staticmethod
	def _item():
		existing = frappe.get_all("Item", filters={"item_name": "CORE13 material"}, pluck="name")
		if existing:
			return existing[0]
		groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
		group = next((g for g in ("All Item Groups", "Raw Materials") if g in groups), groups[0])
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "CORE13-MAT",
				"item_name": "CORE13 material",
				"item_group": group,
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _boq_item(cls, qty=100.0):
		"""A BOQ Item on a BOQ belonging to a project of our own."""
		name = "CORE13 Project"
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
				"contract_amount": 1_000_000.00,
			}
		).insert(ignore_permissions=True).name

		seq = len(frappe.get_all("Construction BOQ", filters={"project": project}, pluck="name")) + 1
		boq = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": f"CORE13-BOQ-{seq:04d}",
				"project": project,
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": "1.01",
						"item_code": "CORE13-BOQ-LINE",
						"description": "CORE-13 takeoff line",
						"unit": cls._uom(),
						"quantity": qty,
						"direct_cost": 10.0,
						"ocm": 0.0,
						"profit": 0.0,
					}
				],
			}
		).insert(ignore_permissions=True)
		return boq.boq_items[0].name
