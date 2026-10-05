# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""WAIR incident record and the 24-hour LTI notification clock.

**The specification.** The employer must notify the LTI branch within 24 hours of
an incident. The clock runs from **when it happened**, not from when it was
reported — an incident buried for two days has already breached, and starting the
clock at report time would hide exactly the case the rule exists to catch.

**UNVERIFIED CITATION — do not repeat this downstream.** A previous draft of this
file named the governing instrument as "Workers' Compensation Emergency Cases,
Rule VIII, Book III of the Labour Code". That was written from memory and has
**not** been checked against the source text. It has been removed rather than
carried, on the same reasoning as D-029: a citation that looks authoritative and
is wrong is worse than no citation. Whoever implements the compliance filing
must confirm the instrument first.

**The deadline is stamped once and never recomputed from `modified`.** This is
the whole reason the field is `read_only` and `no_copy`. A deadline derived from
the last edit time is not a deadline: editing a typo would silently extend a
statutory notification window, and nobody reviewing the record would notice.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, flt, get_datetime, now

# The LTI notification window this app is configured for. The governing
# instrument is NOT named here because it has not been verified against the
# source text -- see the docstring above.
NOTIFICATION_WINDOW_HOURS = 24


class WAIRIncident(Document):
	def validate(self):
		self._refuse_a_future_occurrence()
		self._stamp_the_clock()

	def _refuse_a_future_occurrence(self):
		"""An incident cannot happen after it is reported.

		A future timestamp makes the deadline later than the incident, which is
		nonsense, and it is the same class of drift the TBM backdate guard
		catches -- one that would otherwise sit there looking valid.
		"""
		occurred = get_datetime(self.occurred_at)
		if occurred is None:
			frappe.throw(_("The incident occurrence time is required."), title=_("Invalid Occurrence"))
		drift_hours = (get_datetime(now()) - occurred).total_seconds() / 3600.0
		if drift_hours < 0:
			frappe.throw(
				_("The incident cannot be reported as occurring in the future ({0} ahead).").format(
					round(-drift_hours, 2)
				),
				title=_("Future Incident"),
			)

	def _stamp_the_clock(self):
		"""Set the deadline once, and report the remaining time.

		The deadline is written from `occurred_at` on first save and left alone
		afterwards. Recomputing it from `modified` would let an edit move a
		statutory deadline, which is the defect this comment exists to prevent.
		"""
		occurred = get_datetime(self.occurred_at)

		if not self.notification_deadline:
			self.notification_deadline = add_to_date(occurred, hours=NOTIFICATION_WINDOW_HOURS)
			if not self.reported_at:
				self.reported_at = now()

		deadline = get_datetime(self.notification_deadline)
		remaining = (deadline - get_datetime(now())).total_seconds() / 3600.0
		self.hours_remaining = round(remaining, 2)

		if self.notified_at:
			self.notification_status = "Notified"
		elif remaining < 0:
			self.notification_status = "Overdue"
		else:
			self.notification_status = "Pending"

		if not self.project_name:
			self.project_name = frappe.db.get_value("Construction Project", self.project, "project_name")


@frappe.whitelist()
def mark_notified(incident, sti_reference=None):
	"""Record that the LTI branch has been notified."""
	doc = frappe.get_doc("WAIR Incident", incident)
	doc.notified_at = now()
	if sti_reference:
		doc.sti_reference = sti_reference
	doc.save()
	return {
		"notification_status": doc.notification_status,
		"notified_at": str(doc.notified_at),
		"deadline": str(doc.notification_deadline),
	}
