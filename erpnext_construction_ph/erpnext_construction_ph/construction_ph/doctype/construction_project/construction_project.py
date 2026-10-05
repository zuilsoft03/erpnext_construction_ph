# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Construction project.

Invariant: contract_amount never changes after the first progress billing is
submitted against the project.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ConstructionProject(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if previous and flt(previous.contract_amount) != flt(self.contract_amount):
			if self._has_submitted_billing():
				frappe.throw(
					frappe._(
						"Contract Amount is locked: a progress billing has already been submitted for this project."
					),
					title=frappe._("Contract Amount Locked"),
				)

	def _has_submitted_billing(self):
		# Construction Progress Billing.project links to the *native* ERPNext
		# Project, not to this doctype -- this record wraps one through
		# erpnext_project. Matching on self.name would never find anything, so the
		# lock would be inert and the contract sum would stay editable forever.
		return bool(
			frappe.db.exists(
				"Construction Progress Billing",
				{"project": self.erpnext_project, "docstatus": 1},
			)
		)
