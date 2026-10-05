# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Document template. Invariant: template_html is immutable after first issuance."""

import frappe
from frappe.model.document import Document


class ConstructionDocumentTemplate(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if previous and previous.issued_on:
			if self.template_html != previous.template_html:
				frappe.throw(
					frappe._(
						"Template Body is immutable once the template has been issued. Create a new revision instead."
					),
					title=frappe._("Template Locked"),
				)

	def before_insert(self):
		# Stamped on the very first save. Stamping it only on a later transition
		# (as an on_update diff) left the lock disengaged for a brand-new template,
		# so its body stayed editable until somebody happened to save it twice.
		self.issued_on = frappe.utils.now()
