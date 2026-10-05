# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BIM model. Geometry lives on S3; only the converted .frag URL is stored here.

The GUID list is deferred to the Phase 3 BIM row: adding a dedicated child
doctype here would push the module past the 13-doctype count the CORE-03 gate
fixes (D-016).
"""

import frappe
from frappe.model.document import Document


class BIMModel(Document):
	def validate(self):
		if self.conversion_status == "Ready" and not (self.frag_url or "").strip():
			frappe.throw(
				frappe._("A converted .frag URL is required before a model can be marked Ready."),
				title=frappe._("Conversion Incomplete"),
			)
