# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Subcontractor claim. Invariant: 2% CWT auto-applied before submit."""

import frappe
from frappe.model.document import Document
from frappe.utils import flt

CWT_RATE = 2.0


class ConstructionSubcontractorClaim(Document):
	def validate(self):
		if self.is_new() or not self.subcon_cwt:
			self.subcon_cwt = CWT_RATE
		self.cwt_amount = flt(flt(self.claim_amount) * flt(self.subcon_cwt) / 100.0, 2)
		self.net_amount_payable = flt(
			flt(self.claim_amount) - flt(self.retention_amount) - self.cwt_amount, 2
		)

	def before_submit(self):
		# The rate is owned by the controller, not the user, once the claim is real.
		self.subcon_cwt = CWT_RATE
		self.db_set("subcon_cwt", CWT_RATE)
