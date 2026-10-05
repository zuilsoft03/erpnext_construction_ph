# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BCF viewpoint. Invariant: snapshot_image is required on creation."""

import frappe
from frappe.model.document import Document
from frappe.utils import cstr


class BCFViewpoint(Document):
	def before_insert(self):
		if not cstr(self.snapshot_image).strip():
			frappe.throw(
				frappe._("A snapshot image is required when a viewpoint is created."),
				title=frappe._("Snapshot Required"),
			)
		import uuid

		self.viewpoint_guid = str(uuid.uuid4())
