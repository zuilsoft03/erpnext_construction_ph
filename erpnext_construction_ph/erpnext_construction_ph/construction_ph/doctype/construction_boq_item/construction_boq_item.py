# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BOQ row. Invariant: unit_price = direct_cost x (1 + OCM + profit)."""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ConstructionBOQItem(Document):
	def validate(self):
		self.unit_price = flt(
			flt(self.direct_cost) * (1 + flt(self.ocm) / 100.0 + flt(self.profit) / 100.0), 2
		)
		self.total = flt(flt(self.quantity) * self.unit_price, 2)
