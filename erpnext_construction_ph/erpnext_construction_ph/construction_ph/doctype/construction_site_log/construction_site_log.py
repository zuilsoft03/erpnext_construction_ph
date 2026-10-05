# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Site log: concrete pour, weather delay, subcontractor headcount.

Invariants this controller owns, all of them about a field record being
reconcilable later:

1. **A pour with a volume must name its WBS Task.** Progress is reported
   against the WBS, so a pour with no task is invisible to the schedule. It is
   also the cheapest defect to introduce and the most expensive to find, because
   the crew's log looks complete.
2. **A weather delay with hours must give a reason.** An unexplained delay is
   not evidence of anything, and an unevidenced claim is worse than none.
3. **A pour item and a linked Stock Entry must agree.** If they disagree, the
   two records describe different pours and the reconciliation silently balances
   to the wrong number.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ConstructionSiteLog(Document):
	def validate(self):
		self._validate_pour_is_linked()
		self._validate_delay_has_a_reason()
		self._validate_stock_entry_agrees()

	def _validate_pour_is_linked(self):
		if flt(self.pour_volume) > 0 and not self.pour_task:
			frappe.throw(
				_("A concrete pour with a volume must be linked to a WBS Task ({0}).").format(
					frappe.bold(self.log_number or _("this log"))
				),
				title=_("Pour Not Linked To WBS"),
			)

	def _validate_delay_has_a_reason(self):
		if flt(self.delay_hours) > 0 and not (self.delay_reason or "").strip():
			frappe.throw(
				_("{0} hours of weather delay were recorded with no reason.").format(
					flt(self.delay_hours)
				),
				title=_("Delay Reason Required"),
			)

	def _validate_stock_entry_agrees(self):
		"""The linked Stock Entry must be a Material Issue for the same item.

		Checked rather than assumed: a Stock Entry can be drafted, can be
		submitted for a different item, or can be re-pointed after the log was
		written. Any of those makes the pair describe different pours.
		"""
		if not self.stock_entry or not self.pour_item:
			return
		entry = frappe.get_doc("Stock Entry", self.stock_entry)
		if entry.purpose != "Material Issue":
			frappe.throw(
				_("Stock Entry {0} is a {1}; a pour must be backed by a Material Issue.").format(
					self.stock_entry, entry.purpose
				),
				title=_("Wrong Stock Entry Purpose"),
			)
		# Stock Entry Detail names the item `item_code`; `row.item` does not
		# exist on that child row and raises rather than returning None.
		items = [row.item_code for row in entry.get("items", []) if row.item_code]
		if items and self.pour_item not in items:
			frappe.throw(
				_("Stock Entry {0} moves {1}, but this pour records {2}.").format(
					self.stock_entry, ", ".join(items), self.pour_item
				),
				title=_("Stock Entry Item Mismatch"),
			)
