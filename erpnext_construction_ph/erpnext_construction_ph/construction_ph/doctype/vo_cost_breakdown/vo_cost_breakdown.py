# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""One line of a Variation Order's DUPA build-up.

`amount` is derived from quantity × rate by the parent's controller rather than
typed, so every line is computed the same way and a transcription error cannot
enter the build-up that a contract variation is paid from.
"""

from frappe.model.document import Document
from frappe.utils import flt


class VOCostBreakdown(Document):
	pass
