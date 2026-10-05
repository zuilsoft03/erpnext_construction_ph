# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BOQ. Invariant: Draft -> Approved -> Frozen; rows editable only while Draft."""

import frappe
from frappe.model.document import Document
from frappe.utils import flt

ALLOWED_TRANSITIONS = {"Draft": {"Approved"}, "Approved": {"Frozen"}, "Frozen": set()}


class ConstructionBOQ(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if previous and previous.status != "Draft":
			self._assert_rows_unchanged(previous)
		# A parent's validate() runs before its children are validated, so the rows
		# still carry their incoming values here. Validate them first, otherwise
		# total_amount is summed from stale zeros.
		for row in self.boq_items:
			row.run_method("validate")
		self.total_amount = flt(sum(flt(row.total) for row in self.boq_items), 2)

	def on_update(self):
		previous = self.get_doc_before_save()
		if previous and previous.status != self.status:
			if self.status not in ALLOWED_TRANSITIONS.get(previous.status, set()):
				frappe.throw(
					frappe._("A Bill of Quantities cannot move from {0} to {1}.").format(
						previous.status, self.status
					),
					title=frappe._("Invalid BOQ Status"),
				)
		if self.status == "Approved" and not self.approved_on:
			self.db_set("approved_on", frappe.utils.now())
		if self.status == "Frozen" and not self.frozen_on:
			self.db_set("frozen_on", frappe.utils.now())

	def _assert_rows_unchanged(self, previous):
		"""Once the BOQ leaves Draft the priced rows are frozen."""
		before = {row.idx: row for row in previous.boq_items}
		for row in self.boq_items:
			old = before.get(row.idx)
			if not old:
				continue
			if flt(old.quantity) != flt(row.quantity) or flt(old.direct_cost) != flt(row.direct_cost):
				frappe.throw(
					frappe._("Row {0}: quantity and direct cost are locked once the BOQ leaves Draft.").format(
						row.idx
					),
					title=frappe._("BOQ Locked"),
				)
