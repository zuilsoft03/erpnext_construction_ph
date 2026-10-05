# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Subcontractor agreement.

Invariant: subcon_retention <= head_contract_retention.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ConstructionSubcontractorAgreement(Document):
	def validate(self):
		if flt(self.subcon_retention) > flt(self.head_contract_retention):
			frappe.throw(
				frappe._("Subcontractor retention ({0}%) may not exceed the head contract retention ({1}%).").format(
					self.subcon_retention, self.head_contract_retention
				),
				title=frappe._("Invalid Retention"),
			)
