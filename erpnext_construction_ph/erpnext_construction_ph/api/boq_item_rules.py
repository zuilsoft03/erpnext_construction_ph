# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Starter pay-item rule set for imported BOQ descriptions.

**This is not the DPWH pay-item catalogue.** It is a small, hand-authored keyword
table covering the handful of items a residential/light-commercial BOQ actually
uses. It exists so an imported legacy sheet carries *a* pay item reference, not so
it carries *the authoritative* one. Two consequences a caller must design for:

- **Coverage is limited.** A description outside this table is reported as
  unmapped, never guessed at.
- **False positives are reachable.** This table has already produced one real
  mis-map: "unskilled labor" matched the "skilled labor" rule and would have
  priced common labour at the skilled rate. Keyword rules over free text are
  inherently fallible, and a wrong pay item propagates into every billing derived
  from the BOQ baseline.

Before any BOQ leaves draft, its pay items should be reconciled against the real
DPWH schedule. Replacing this table with an authoritative source is the honest fix
and is tracked as backlog, not done here.

Matching is deliberately conservative: an unrecognised description returns None so
the caller can report it. A wrong pay item is worse than a missing one, because it
prices against the wrong rate with no trace.

The table lives in code rather than as a doctype on purpose: D-016 pins module
"Construction PH" at 13 DocTypes because the CORE-03 gate counts them.
"""

import re

import frappe

# Ordered most-specific first: "reinforcing steel" must win over a bare "steel".
# (pay_item_code, keywords)
CATALOG = [
	("C-30", ("c-30", "concrete", "ready mixed", "ready-mixed", "rcd")),
	("A-405", ("reinforcing steel", "rebar", "steel bar", "a-405", "bars")),
	("B-15", ("formwork", "plywood", "b-15", "shuttering")),
	("B-11", ("b-11", "lumber", "plank", "wood")),
	# Order is the guard. Plain substring matching finds "skilled labor" inside
	# "unskilled labor", so the more specific rule must be tested first. A
	# word-boundary regex would also prevent it, but it breaks plurals -- a bare
	# (?!\w) stops "steel bars" matching "steel bar", which is what most real
	# BOQ sheets say. One mechanism, and it is the one a reader can reason about
	# by reading the table order.
	("A-902", ("a-902", "unskilled labor", "unskilled labour", "common labor", "common labour")),
	("A-901", ("a-901", "minimum wages", "skilled labor", "skilled labour")),
	("E-1010", ("plumbing", "pipe", "sanitary")),
	("E-2010", ("electrical", "conduit", "wiring")),
	("A-1110", ("a-1110", "equipment", "rentals")),
]

# Cheap normalisation for matching, not for display.
_PUNCT = re.compile(r"[^a-z0-9\- ]+")


def normalise(text):
	"""Lower-case, collapse punctuation and whitespace, for keyword matching."""
	clean = _PUNCT.sub(" ", (text or "").lower())
	return re.sub(r"\s+", " ", clean).strip()


def match_pay_item(description, item_code=None):
	"""Return (pay_item_code, matched_keyword) or (None, None).

	An explicit `item_code` wins when it already looks like a pay item, so a sheet
	that was catalogued once does not get re-guessed from its description.
	"""
	explicit = normalise(item_code)
	if explicit and re.match(r"^[a-z]-\d+$", explicit):
		return explicit.upper(), None

	haystack = normalise(description)
	if not haystack:
		return None, None

	for code, keywords in CATALOG:
		for keyword in keywords:
			if keyword in haystack:
				return code, keyword

	return None, None


@frappe.whitelist()
def classify_descriptions(rows):
	"""Classify a list of {description, item_code} dicts.

	Returns the rows annotated with `pay_item` and `matched_on`, plus the ones
	nothing matched so the caller can report them rather than drop them.
	"""
	classified = []
	unmatched = []
	for index, row in enumerate(rows, start=1):
		description = row.get("description") if isinstance(row, dict) else None
		item_code = row.get("item_code") if isinstance(row, dict) else None
		code, keyword = match_pay_item(description, item_code)
		entry = dict(row) if isinstance(row, dict) else {"description": description}
		entry["pay_item"] = code
		entry["matched_on"] = keyword
		classified.append(entry)
		if not code:
			unmatched.append({"row": index, "description": description})

	return {"rows": classified, "unmatched": unmatched}
