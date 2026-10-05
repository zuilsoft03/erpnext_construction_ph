# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Takeoff sheet. Invariant: quantity is locked once the BOQ item is linked."""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class TakeoffSheet(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if not previous:
			return
		before = {row.idx: row for row in previous.takeoff_items}
		for row in self.takeoff_items:
			old = before.get(row.idx)
			if old and cstr_link(old.item_code) and flt(old.quantity) != flt(row.quantity):
				frappe.throw(
					frappe._("Row {0}: quantity is locked once the BOQ item is linked.").format(row.idx),
					title=frappe._("Takeoff Quantity Locked"),
				)


def cstr_link(item_code):
	return (item_code or "").strip()
