# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Variation order. Invariant: a linked COR is required before monetary approval."""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ConstructionVariationOrder(Document):
	def validate(self):
		# Derive the breakdown first: a line's amount must be quantity x rate
		# before the parent's figure can be checked against it. Calling this is
		# the whole point -- a guard that is written and never invoked is
		# indistinguishable from no guard, which is the same trap as the WBS
		# hook's `hasattr`.
		self._apply_breakdown()
		self.revised_contract_amount = flt(
			flt(self.original_contract_amount) + flt(self.variation_amount), 2
		)
		if self.status in ("Approved", "Executed") and not (self.cor or "").strip():
			frappe.throw(
				frappe._("A Change Order Request reference is required before monetary approval."),
				title=frappe._("COR Required"),
			)

	def _apply_breakdown(self):
		"""Derive every line's amount, and refuse a parent below its own direct cost.

		Each line's amount is **always** recomputed from quantity x rate and never
		compared to what was on the row. An earlier version rejected a mismatch,
		and that turned out to be undecidable: a stale derived amount left over
		from before an edit looks exactly like a deliberately typed one, so the
		check refused ordinary edits while proving nothing about intent. Deriving
		is both simpler and safer -- a typed amount simply does not survive.

		Two further points, both learned the hard way:
		- The lines are the **direct cost**; `variation_amount` is the **inclusive
		  total** after OCM, profit and VAT (D-028). Comparing them refused every
		  correctly-priced variation, because 9,800 never equals 7,000.
		- The full reconciliation to the total is `api/vo_docgen`'s job, where the
		  rates live, and is tested there.
		"""
		if not self.get("dupa_breakdown"):
			return
		direct = 0.0
		for row in self.dupa_breakdown:
			row.amount = flt(flt(row.quantity) * flt(row.rate), 2)
			direct = flt(direct + row.amount, 2)
		if flt(self.variation_amount) and flt(self.variation_amount) < direct - 0.005:
			frappe.throw(
				frappe._(
					"The variation amount {0} is less than the direct cost {1} its own "
					"breakdown contains. These cannot both be right."
				).format(flt(self.variation_amount), direct),
				title=frappe._("Variation Below Direct Cost"),
			)

	def on_update(self):
		if self.status == "Approved" and not self.approved_on:
			self.db_set("approved_by", frappe.session.user)
			self.db_set("approved_on", frappe.utils.now())
