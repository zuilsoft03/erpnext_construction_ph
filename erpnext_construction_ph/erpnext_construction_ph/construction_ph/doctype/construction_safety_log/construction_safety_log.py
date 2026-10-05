# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Safety log. Invariant: tbm_date cannot be backdated by more than 24 hours."""

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now

MAX_BACKDATE_HOURS = 24


class ConstructionSafetyLog(Document):
	def validate(self):
		meeting = get_datetime(self.tbm_date)
		if meeting is None:
			frappe.throw(
				frappe._("Tool Box Meeting Date is required."), title=frappe._("Invalid Meeting Date")
			)
		# frappe.utils.now() is a formatted string; compare datetimes, not str - dt.
		drift_hours = (get_datetime(now()) - meeting).total_seconds() / 3600.0
		if drift_hours > MAX_BACKDATE_HOURS:
			frappe.throw(
				frappe._(
					"Tool Box Meeting Date is more than {0} hours in the past (DOLE D.O. 13)."
				).format(MAX_BACKDATE_HOURS),
				title=frappe._("Backdated Safety Log"),
			)
