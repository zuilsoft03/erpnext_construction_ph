"""Normalise legacy `BCF Topic.topic_guid` values to the hyphenated UUID form.

**Found by writing the exporter, not by reasoning about it.** `bcf/writer.py`
refuses to name a topic folder anything but the topic's hyphenated UUID -- BCF 2.1's
folder name IS the GUID, and renaming it to something prettier "would produce a
package some tools read and others silently mis-file". So the export endpoint
raised `PackageError` on every pre-existing topic.

The cause: earlier revisions minted `uuid.uuid4().hex` (32 chars, no hyphens).
`uuid.UUID` accepts that form and `.hex` strips the hyphens, so these are the SAME
identifiers written non-canonically. `str(uuid.UUID(value))` restores the
canonical spelling without changing which UUID it is -- lossless, and safe to run
against data that other BCF tools have already recorded.

Rows that are neither 32-hex nor a parseable UUID are left alone and reported:
rewriting an unrecognised identifier would break the reference a vendor's own
tooling already holds, which is worse than a topic that will not export.
"""

import uuid

import frappe

HEX32 = 36 - 4  # the length of a hyphenated UUID's hex digits


def _normalise(value):
	"""The canonical spelling of `value`, or None if it is not a UUID at all."""
	if not value:
		return None
	text = str(value).strip()
	if len(text) == HEX32:
		# Only a true 32-hex string; anything else of that length is a guess.
		try:
			return str(uuid.UUID(hex=text))
		except (ValueError, AttributeError):
			return None
	try:
		return str(uuid.UUID(text))
	except (ValueError, AttributeError):
		return None


def execute():
	rows = frappe.db.sql(
		"select name, topic_guid from `tabBCF Topic` where topic_guid is not null",
		as_dict=True,
	)
	changed, unrecognised = [], []
	for row in rows:
		current = (row.topic_guid or "").strip()
		canonical = _normalise(current)
		if canonical is None:
			if current:
				unrecognised.append({"topic": row.name, "topic_guid": current})
			continue
		if canonical != current:
			frappe.db.set_value(
				"BCF Topic", row.name, "topic_guid", canonical, update_modified=False
			)
			changed.append({"topic": row.name, "was": current, "now": canonical})

	if changed:
		frappe.db.commit()
	print(f"bcf_topic_guid: {len(changed)} normalised, {len(unrecognised)} left alone")
	if unrecognised:
		print(f"  NOT a UUID, left untouched (a vendor tool may already reference it): {unrecognised}")
	return changed, unrecognised
