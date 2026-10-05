# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Runtime test for the Takeoff Sheet quantity lock (`takeoff_sheet.py`).

The controller's stated invariant is "quantity is locked once the BOQ item is
linked", and `test_takeoff_studio` reaches it only as a side effect of
`capture_area`. Nothing exercised it as a document save, which is the path a
surveyor actually uses when they re-open a sheet and retype a figure.

The lock compares each incoming row against `get_doc_before_save()` by `idx`,
so the interesting cases are the ones around that key: a fresh row has no
counterpart and must be allowed, a row that survived a deletion must not become
editable by accident, and the refusal must leave the stored figure alone.

Two measured facts about this doctype, recorded here because they decide what
can be tested at all:

* `Construction BOQ Item.item_code` is `reqd: 1`, so every STORED row has an
  item code. The guard's `cstr_link(old.item_code)` scoping is therefore always
  true -- there is no reachable "unlinked row" to be lenient about. The lock
  covers every measured row, which is the outcome the invariant wants; the
  conditional is dead weight, not a hole.
* Appending after a deletion reuses an `idx` that is still taken (Frappe's
  `append` numbers rows `len(table) + 1`), and the save is then refused with a
  message naming a row that is not at fault. That is a usability defect in the
  delete-then-add path, not a hole in the lock: the test below proves the
  quantity is still protected either way.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.construction_ph.test import fixtures

LOCKED = "quantity is locked once the BOQ item is linked"


class TestTakeoffSheetRuntime(IntegrationTestCase):
	"""A measured quantity is refused on every save path that would restate it."""

	def setUp(self):
		super().setUp()
		self.project = fixtures.make_project("TSHRUN")
		self.unit = self._uom()
		self.boq = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": f"TSHRUN-{frappe.generate_hash(length=8)}",
				"project": self.project,
				"boq_date": "2026-01-01",
			}
		).insert(ignore_permissions=True).name
		self.sheet = self._sheet([self._row("A-01", 10.0), self._row("A-02", 5.0)])

	def _uom(self):
		"""A UOM this test owns, rather than one borrowed from the site."""
		name = f"TSHRUN-{frappe.generate_hash(length=8)}"
		return frappe.get_doc({"doctype": "UOM", "uom_name": name}).insert(
			ignore_permissions=True
		).name

	def _row(self, code, quantity):
		return {
			"item_code": code,
			"description": f"measured {code}",
			"unit": self.unit,
			"quantity": quantity,
			"direct_cost": 250.00,
		}

	def _sheet(self, rows):
		return frappe.get_doc(
			{
				"doctype": "Takeoff Sheet",
				"sheet_name": f"TSHRUN-{frappe.generate_hash(length=8)}",
				"project": self.project,
				"boq": self.boq,
				"status": "Draft",
				"takeoff_items": rows,
			}
		).insert(ignore_permissions=True).name

	def _stored(self, item_code):
		return flt(
			frappe.db.get_value(
				"Construction BOQ Item",
				{"parent": self.sheet, "item_code": item_code},
				"quantity",
			)
		)

	# --- the lock ----------------------------------------------------------

	def test_a_measured_quantity_cannot_be_restated(self):
		doc = frappe.get_doc("Takeoff Sheet", self.sheet)
		doc.takeoff_items[0].quantity = 999.0
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save(ignore_permissions=True)
		self.assertIn(LOCKED, str(caught.exception))
		self.assertIn("Row 1", str(caught.exception), "the refusal must name the row")
		self.assertEqual(self._stored("A-01"), 10.0, "a refused save must not restate the figure")

	def test_deleting_a_row_does_not_unlock_the_rows_below_it(self):
		"""The lock is keyed on `idx`, so a deletion must not hand anyone a keyhole."""
		doc = frappe.get_doc("Takeoff Sheet", self.sheet)
		del doc.takeoff_items[0]
		remaining = doc.takeoff_items[0]
		self.assertEqual(remaining.item_code, "A-02")
		remaining.quantity = 1234.0
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save(ignore_permissions=True)
		self.assertIn(LOCKED, str(caught.exception))
		self.assertEqual(self._stored("A-02"), 5.0)

	# --- the lock must not become a freeze ---------------------------------

	def test_an_unchanged_sheet_can_still_be_saved(self):
		doc = frappe.get_doc("Takeoff Sheet", self.sheet)
		doc.save(ignore_permissions=True)
		self.assertEqual(self._stored("A-01"), 10.0)
		self.assertEqual(self._stored("A-02"), 5.0)

	def test_a_freshly_measured_row_can_be_added(self):
		doc = frappe.get_doc("Takeoff Sheet", self.sheet)
		doc.append("takeoff_items", self._row("B-02", 42.0))
		doc.save(ignore_permissions=True)
		self.assertEqual(self._stored("B-02"), 42.0)
		self.assertEqual(len(doc.takeoff_items), 3)

	def test_a_description_change_still_saves(self):
		"""The lock is on the quantity, not on the row."""
		doc = frappe.get_doc("Takeoff Sheet", self.sheet)
		doc.takeoff_items[1].description = "measured on grid C, level 2"
		doc.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value(
				"Construction BOQ Item",
				{"parent": self.sheet, "item_code": "A-02"},
				"description",
			),
			"measured on grid C, level 2",
		)