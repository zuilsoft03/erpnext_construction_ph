# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md PH2-03 — whole-`.xlsx` BOQ import with DPWH pay-item mapping.

The gate selects this by METHOD name (D-014), so every assertion lives in the
single ``test_boq_file_import`` method.

The fixture is a real workbook written by openpyxl, with a title block above the
table, because that is what legacy sheets look like and the importer must find the
header rather than assume row 1.
"""

import os
import tempfile

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import boq_item_rules, boq_matrix

# Must line up one-for-one with the header written by _workbook():
# pay item no, item code, description, unit, qty, direct cost, ocm, profit.
ROWS = [
	("1.01", "IT-C30", "C-30 Concrete, 3000 psi", "m3", 120, 4500.00, 10, 5),
	("1.02", "IT-RBAR", "Reinforcing Steel Bar, 16mm", "kg", 8500, 62.40, 8, 7),
	("2.01", "IT-FORM", "Plywood Formwork for Slab", "m2", 640, 1180.00, 12, 5),
	("3.01", "IT-LAB", "Unskilled Labor", "ls", 1, 85000.00, 5, 10),
]

# (item code, expected pay item, expected unit price)
EXPECTED = [
	("IT-C30", "C-30", 5175.00),  # 4500 x 1.15
	("IT-RBAR", "A-405", 71.76),  # 62.40 x 1.15
	("IT-FORM", "B-15", 1380.60),  # 1180 x 1.17
	("IT-LAB", "A-902", 97750.00),  # 85000 x 1.15
]


class TestBoqFileImport(IntegrationTestCase):
	"""Every data row lands, priced, with a pay item; unmapped rows are reported."""

	def _workbook(self, rows, header_row=4, extra=()):
		"""Write a realistic legacy sheet: a title block, then a table."""
		import openpyxl

		book = openpyxl.Workbook()
		sheet = book.active
		sheet.title = "BOQ"
		sheet.append(["ABC Construction Co."])
		sheet.append(["Bill of Quantities"])
		sheet.append([])
		sheet.append(["Pay Item No", "Item Code", "Description", "Unit", "Qty", "Direct Cost", "OCM", "Profit"])
		for row in rows:
			sheet.append(list(row))
		for extra_row in extra:
			sheet.append(list(extra_row))

		handle, path = tempfile.mkstemp(suffix=".xlsx", prefix="boq_")
		os.close(handle)
		book.save(path)
		return path

	def _boq(self):
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = "BOQ File Import Client"
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
		# ERPNext autonames Project as PROJ-000N; erpnext_project is a Link and
		# needs that real name, not the project_name.
		erp_name = "BOQ File Import Project"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		if hits:
			erp = hits[0]
		else:
			erp = frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": erp_name,
					"company": company,
					"status": "Open",
				}
			).insert(ignore_permissions=True).name
		if not frappe.db.exists("Construction Project", erp_name):
			frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": erp_name,
					"erpnext_project": erp,
					"company": company,
					"client": customer,
					"contract_date": "2026-01-01",
					"contract_amount": 5_000_000.00,
				}
			).insert(ignore_permissions=True)

		name = "BOQ File Import BOQ"
		if frappe.db.exists("Construction BOQ", name):
			frappe.delete_doc("Construction BOQ", name, force=True, ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": name,
				"project": erp_name,
				"boq_date": "2026-09-28",
				"status": "Draft",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def test_boq_file_import(self):
		boq = self._boq()
		path = self._workbook(ROWS)
		try:
			# ---- EXPECT: every data row lands, priced, with a pay item.
			result = boq_matrix.import_boq_file(boq, path)
			self.assertEqual(result["imported"], len(ROWS))
			self.assertEqual(result["unmapped"], [])

			persisted = frappe.get_all(
				"Construction BOQ Item",
				filters={"parent": boq, "parenttype": "Construction BOQ"},
				fields=["item_code", "pay_item_number", "unit_price", "total", "quantity", "boq"],
				order_by="idx asc",
			)
			self.assertEqual(len(persisted), len(ROWS))
			by_code = {r["item_code"]: r for r in persisted}
			for item_code, pay_item, unit_price in EXPECTED:
				row = by_code.get(item_code)
				self.assertIsNotNone(row, f"{item_code} did not land")
				self.assertEqual(row["pay_item_number"], pay_item)
				self.assertEqual(flt(row["unit_price"], 2), unit_price)
				self.assertEqual(row["boq"], boq)

			# The importer found the header below the title block, not at row 1.
			self.assertEqual(frappe.db.count("Construction BOQ Item", {"parent": boq}), len(ROWS))

			# ---- EXPECT: the catalog is keyword-driven and order-sensitive.
			self.assertEqual(boq_item_rules.match_pay_item("C-30 Concrete, 3000 psi")[0], "C-30")
			self.assertEqual(boq_item_rules.match_pay_item("Reinforcing Steel Bar, 16mm")[0], "A-405")
			self.assertEqual(boq_item_rules.match_pay_item("plywood formwork")[0], "B-15")
			# A typed pay item is trusted rather than re-guessed from the text.
			self.assertEqual(boq_item_rules.match_pay_item("anything", "c-99")[0], "C-99")
			# Plurals still match: a real BOQ says "steel bars", and a bare word
			# boundary would stop "steel bar" from matching it.
			self.assertEqual(
				boq_item_rules.match_pay_item("Reinforcing Steel Bars, 16mm")[0], "A-405"
			)
			self.assertEqual(
				boq_item_rules.match_pay_item("Formwork plywood sheets")[0], "B-15"
			)
			# Unknown text is reported, not bucketed.
			self.assertEqual(boq_item_rules.match_pay_item("mystery item xyz")[0], None)

			# ---- NEGATIVE: a sheet with an unmappable description is refused
			# outright, so a short BOQ cannot pass as a clean import.
			bad_path = self._workbook(
				ROWS,
				header_row=4,
				extra=[("9.99", "IT-ODD", "Quantum Entangler Housing", "ls", 1, 100.0, 0, 0)],
			)
			try:
				with self.assertRaises(frappe.ValidationError) as ctx:
					boq_matrix.import_boq_file(boq, bad_path)
				self.assertIn("map to no DPWH pay item", str(ctx.exception))

				# The refusal must not have half-imported on top of the good rows.
				self.assertEqual(
					frappe.db.count("Construction BOQ Item", {"parent": boq}), len(ROWS)
				)

				# ... unless the caller explicitly accepts unmapped rows, in which
				# case they land and are reported back.
				permissive = boq_matrix.import_boq_file(boq, bad_path, allow_unmapped=True)
				self.assertEqual(permissive["imported"], len(ROWS) + 1)
				self.assertEqual(len(permissive["unmapped"]), 1)
				self.assertIn("Quantum Entangler", str(permissive["unmapped"][0]["description"]))
			finally:
				os.remove(bad_path)
		finally:
			os.remove(path)
