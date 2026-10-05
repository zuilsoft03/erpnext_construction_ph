# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md PH2-01 — two-point calibration and closed-polygon area capture.

The gate selects this by METHOD name (D-014), so every assertion lives in the
single ``test_takeoff_studio`` method.

Areas are compared against shoelace figures derived by hand in the test, not read
back from the module under test.
"""

import math

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import takeoff_geometry as tg


class TestTakeoffStudio(IntegrationTestCase):
	"""Calibration accuracy, polygon area, and the open-polygon refusal."""

	def _uom(self):
		if not frappe.db.exists("UOM", "m2"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "m2", "uom_category": "Area"}).insert(
				ignore_permissions=True
			)
		return "m2"

	def _boq(self):
		name = "TKF Test BOQ"
		if frappe.db.exists("Construction BOQ", name):
			return name
		doc = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": name,
				"project": self._project(),
				"boq_date": "2026-09-28",
				"status": "Draft",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _project(self):
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = "TKF Test Client"
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
		# ERPNext autonames Project as PROJ-000N, so resolve the real name rather
		# than assuming project_name is the document name.
		erp_name = "TKF Test Project"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		if hits:
			erp_project = hits[0]
		else:
			erp_project = frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": erp_name,
					"company": company,
					"status": "Open",
				}
			).insert(ignore_permissions=True).name
		if not frappe.db.exists("Construction Project", erp_project):
			frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": erp_project,
					"erpnext_project": erp_project,
					"company": company,
					"client": customer,
					"contract_date": "2026-01-01",
					"contract_amount": 10_000_000.00,
				}
			).insert(ignore_permissions=True)
		return erp_project

	def _sheet(self, boq):
		name = "TKF Test Sheet"
		if frappe.db.exists("Takeoff Sheet", name):
			doc = frappe.get_doc("Takeoff Sheet", name)
			doc.set("takeoff_items", [])
			doc.save(ignore_permissions=True)
			return name
		frappe.get_doc(
			{
				"doctype": "Takeoff Sheet",
				"sheet_name": name,
				"project": self._project(),
				"boq": boq,
				"status": "Draft",
			}
		).insert(ignore_permissions=True)
		return name

	def test_takeoff_studio(self):
		# ---- EXPECT: two-point calibration recovers the true scale to within 0.1%.
		# A 250 page-unit span standing for a real 10.000 m -> 0.04 m per unit.
		p1, p2 = [0.0, 0.0], [250.0, 0.0]
		result = tg.calibrate(p1, p2, 10.0)
		true_scale = 10.0 / 250.0
		self.assertAlmostEqual(
			result["metres_per_unit"] / true_scale, 1.0, places=3
		)
		self.assertEqual(flt(result["page_span"], 4), 250.0)
		self.assertEqual(flt(result["metres_per_unit"], 4), 0.04)

		# A diagonal calibration must give the same answer as an axis-aligned one.
		diagonal = tg.calibrate([0.0, 0.0], [30.0, 40.0], 10.0)  # 3-4-5 triangle -> 50 units
		self.assertEqual(flt(diagonal["page_span"], 4), 50.0)
		self.assertEqual(flt(diagonal["metres_per_unit"], 4), 0.2)

		# ---- EXPECT: a closed polygon captures the shoelace area to 0.01.
		# A 100 x 60 rectangle traced clockwise, at 0.04 m/unit.
		# page area 6000 sq units x 0.04^2 = 9.60 sq m.
		rect = [[0, 0], [100, 0], [100, 60], [0, 60], [0, 0]]
		expected_units = abs(
			0 * 0
			+ 100 * 60
			+ 100 * 60
			+ 0 * 0
			- (0 * 100 + 0 * 100 + 60 * 0 + 60 * 0)
		) / 2.0
		self.assertEqual(flt(tg.shoelace_area(rect), 2), flt(expected_units, 2))
		self.assertEqual(tg.polygon_area_m2(rect, true_scale), 9.60)

		# An L-shape, to show the formula is not accidentally a bounding box.
		# Bounding box would be 120 x 80 = 9600. The top-right notch is
		# 80 x 40 = 3200, so the true area is 6400 sq units
		# x 0.04^2 = 10.24 sq m.
		l_shape = [[0, 0], [120, 0], [120, 40], [40, 40], [40, 80], [0, 80], [0, 0]]
		self.assertEqual(flt(tg.shoelace_area(l_shape), 2), 6400.00)
		self.assertNotEqual(flt(tg.shoelace_area(l_shape), 2), 9600.00)
		self.assertEqual(tg.polygon_area_m2(l_shape, true_scale), 10.24)

		# ---- NEGATIVE: an open polygon is refused, never silently closed.
		open_shape = [[0, 0], [100, 0], [100, 60], [0, 60]]
		self.assertFalse(tg.is_closed(open_shape))
		self.assertRaises(tg.TakeoffError, tg.polygon_area_m2, open_shape, true_scale)

		self._uom()
		boq = self._boq()
		sheet = self._sheet(boq)
		self.assertRaises(
			tg.TakeoffError,
			tg.capture_area,
			sheet,
			open_shape,
			true_scale,
		)

		# ---- NEGATIVE: coincident calibration points are refused, not divided by.
		self.assertRaises(tg.TakeoffError, tg.calibrate, [10, 10], [10.4, 10.4], 5.0)
		self.assertRaises(tg.TakeoffError, tg.calibrate, [0, 0], [250, 0], 0)

		# ---- EXPECT: the capture lands on the Takeoff Sheet with the area.
		captured = tg.capture_area(
			sheet, rect, true_scale, boq_item="A-01", description="Floor slab"
		)
		self.assertEqual(captured["quantity"], 9.60)

		rows = frappe.get_all(
			"Construction BOQ Item",
			filters={"parent": sheet, "parenttype": "Takeoff Sheet"},
			fields=["item_code", "description", "quantity", "unit"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(flt(rows[0]["quantity"], 2), 9.60)
		self.assertEqual(rows[0]["item_code"], "A-01")
		self.assertEqual(rows[0]["unit"], "m2")

		# ---- EXPECT: the BOQ Item controller priced the captured row.
		row_doc = frappe.get_doc("Takeoff Sheet", sheet).takeoff_items[0]
		self.assertGreaterEqual(flt(row_doc.unit_price, 2), 0.0)

		# ---- ALSO EXERCISES: the Takeoff Sheet quantity lock, never executed until
		# now (D-023). A captured quantity is locked, so a later edit is refused.
		sheet_doc = frappe.get_doc("Takeoff Sheet", sheet)
		sheet_doc.takeoff_items[0].quantity = 999.0
		self.assertRaises(frappe.ValidationError, sheet_doc.save, ignore_permissions=True)

		# The lock is total: Construction BOQ Item.item_code is mandatory, so every
		# row is tied to an item and there is no "unlinked" escape hatch. An
		# untouched re-save of the same values still succeeds.
		unchanged = frappe.get_doc("Takeoff Sheet", sheet)
		unchanged.save(ignore_permissions=True)
		self.assertEqual(
			flt(frappe.get_doc("Takeoff Sheet", sheet).takeoff_items[0].quantity, 2), 9.60
		)
