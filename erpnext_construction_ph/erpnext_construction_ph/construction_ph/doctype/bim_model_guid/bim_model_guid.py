# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""One IFC element, keyed by its GlobalId.

Invariant: `element_guid` is the value exactly as it appears in the IFC file.
The Phase 3 viewer joins a picked element back to a model entity through this
column, so a re-encoded or re-derived GUID would silently break every lookup
while still looking like a valid 22-character IFC GUID.
"""

from frappe.model.document import Document


class BIMModelGUID(Document):
	pass
