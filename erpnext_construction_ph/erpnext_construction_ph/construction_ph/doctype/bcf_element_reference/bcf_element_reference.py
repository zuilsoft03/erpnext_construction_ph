# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""One IFC element a BCF topic is bound to.

A BCF issue means nothing without geometry: the topic says *what* is wrong and
this row says *which element*. The reference is stored as the typed
`IfcClass/GlobalId` path rather than a bare GUID, because a bare GUID says
nothing about discipline and two elements in different models can share one.

`element` points at the row in the model's GUID index. It is deliberately
optional: a package may name an element the model has not indexed, and dropping
the whole topic because of it would lose the issue. What must not happen is
serving that topic's pick as if it resolved -- `bcf.resolve_element` already
refuses an unindexed GUID rather than inventing a row.
"""

import frappe
from frappe.model.document import Document

# visinfo.xsd / markup.xsd IfcGuid: exactly 22 characters of [0-9,A-Za-z_$]
IFC_GUID_LENGTH = 22


class BCFElementReference(Document):
	def validate(self):
		global_id = (self.global_id or "").strip()
		if len(global_id) != IFC_GUID_LENGTH:
			frappe.throw(
				_("An IFC GlobalId is exactly {0} characters; got {1}.").format(
					IFC_GUID_LENGTH, global_id or "nothing"
				),
				title=_("Malformed GlobalId"),
			)
		self.global_id = global_id
		self.ifc_class = (self.ifc_class or "").strip() or None
