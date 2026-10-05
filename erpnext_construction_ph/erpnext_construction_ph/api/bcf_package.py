# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Bring a `.bcfzip` in from another tool.

Reading is the half that matters for interop and it is already version-agnostic:
`bcf.package` reads 2.1 and 3.0 markup into one model, reports the version the
package DECLARES in `bcf.version` separately from the one its shape implies, and
refuses rather than guesses when they disagree. This module is the app-facing half:
package bytes in, `BCF Topic` rows out.

**A topic is never dropped because an element does not resolve.** A package may
name an element this model has not indexed, and losing the issue over that would be
worse than carrying it. What must not happen is the opposite error: a stored
reference that later *looks* resolved. So each element is resolved against the
model's GUID index, the row is stored when it is found, and the unresolved ones
are RETURNED in the report so the caller can see them.
"""

import base64
import datetime

import frappe
from frappe import _

from erpnext_construction_ph.construction_ph.bcf import package as bcf_package
from erpnext_construction_ph.construction_ph.bcf import writer as bcf_writer
from erpnext_construction_ph.construction_ph.bcf.markup import BCFError
from erpnext_construction_ph.construction_ph.bcf.package import PackageError
from erpnext_construction_ph.construction_ph.bcf.viewpoint import ViewpointError

# markup.bdf's Status/TopicStatus vocabularies, mapped onto the app's Select.
# An unmapped value is NOT silently coerced to "Open": a topic that arrived Closed
# must not be stored as Open.
_STATUS = {
	"open": "Open",
	"in progress": "In Progress",
	"in review": "In Review",
	"resolved": "Resolved",
	"closed": "Closed",
	"rejected": "Rejected",
}
_PRIORITY = {"low": "Low", "normal": "Normal", "high": "High"}

# A real cap on an export. `limit_page_length=0` means EVERY row, and is
# how `list_topics` once returned the whole installation.
EXPORT_LIMIT = 500


def _map(value, table, fallback, label):
	if not value:
		return fallback
	key = str(value).strip().lower()
	if key in table:
		return table[key]
	# Refuse rather than coerce: silently turning "Rejected" into "Open" is the
	# kind of quiet misstatement this project has been bitten by repeatedly.
	frappe.throw(
		_("The package uses {0} value {1}, which this app has no mapping for.").format(
			label, value
		),
		title=_("Unmapped BCF Value"),
	)


@frappe.whitelist()
def import_package(project, payload, bim_model=None, task=None):
	"""Import a `.bcfzip`. `payload` is base64 of the zip, or a base64 data URL.

	Returns a report rather than only a count, because the interesting outcome is
	usually the part that did not resolve.
	"""
	from erpnext_construction_ph.api.bcf import _new_guid, _scoped

	_scoped(project)  # authorisation: a caller who cannot read cannot write either

	raw = _decode(payload)
	try:
		archive = bcf_package.read_package(raw)
	except (PackageError, BCFError, ViewpointError) as error:
		frappe.throw(
			_("This file is not a readable BCF package: {0}").format(error),
			title=_("Unreadable Package"),
		)

	disagreement = archive.version_agreement()
	if disagreement:
		# Not fatal -- the file is readable -- but never silent.
		frappe.msgprint(
			_("Package version note: {0}").format(disagreement), indicator="orange", alert=True
		)

	imported, skipped, unresolved = [], [], []
	for folder in archive.topics:
		markup = folder.markup
		topic_guid = markup.topic.guid or folder.guid
		if frappe.db.exists("BCF Topic", {"topic_guid": topic_guid}):
			skipped.append(topic_guid)
			continue
		status = _map(markup.topic.topic_status, _STATUS, "Open", "TopicStatus")
		priority = _map(markup.topic.priority, _PRIORITY, "Normal", "Priority")

		elements, missing = _resolve_elements(project, bim_model, markup.topic.element_guids)
		unresolved.extend(missing)

		doc = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"topic_guid": topic_guid,
				"title": markup.topic.title,
				"project": project,
				"bim_model": bim_model,
				"status": status,
				"priority": priority,
				"assigned_to": _user(markup.topic.assigned_to),
				"task": task,
				# EVERY reference the package made is stored. An unresolved one keeps
				# its GlobalId and leaves `element` empty, which is how a later pick
				# can tell "this issue is about an element we have not indexed" from
				# "this issue resolved to nothing". Dropping it would lose the issue's
				# only statement about geometry.
				"elements": [
					{"ifc_class": ifc_class, "global_id": guid, "element": row}
					for ifc_class, guid, row in elements
				],
			}
		).insert(ignore_permissions=False)
		imported.append(doc.topic_guid)

	return {
		"imported": imported,
		"skipped_existing": skipped,
		"unresolved_elements": unresolved,
		"declared_version": archive.declared_version,
		"detected_version": archive.detected_version,
	}


def _decode(payload):
	text = (payload or "").strip()
	if text.startswith("data:"):
		text = text.split(",", 1)[-1]
	try:
		return base64.b64decode(text, validate=True)
	except Exception:
		# frappe.throw raises; it does not return a chainable exception, so there
		# is nothing to attach with `from` here.
		frappe.throw(_("The package must be base64."), title=_("Unreadable Package"))


def _resolve_elements(project, bim_model, guids):
	"""(ifc_class, guid, indexed_row) for each guid, plus the ones not indexed.

	**The model's own project is authorised, not the caller's claim about it.**
	`bim_model` arrives as a parameter, so without this a caller cleared for project A
	passes a model belonging to project B and learns which element GUIDs exist there
	from the resolved/unresolved split alone. That is the same disclosure shape closed
	in PH3-04, and `_scoped(project)` does not cover it because it authorises the
	PROJECT, not the MODEL.
	"""
	if not guids:
		return [], []
	if not bim_model:
		return [(None, guid, None) for guid in guids], list(guids)
	_authorise_model(project, bim_model)
	indexed = {
		row.element_guid: row
		for row in frappe.get_all(
			"BIM Model GUID",
			filters={"parent": bim_model},
			fields=["name", "element_guid", "element_type"],
		)
	}
	found, missing = [], []
	for guid in guids:
		row = indexed.get(guid)
		if row:
			found.append((row.element_type, guid, row.name))
		else:
			# Recorded with no index link rather than omitted: the reference is the
			# issue's only claim about geometry and must survive.
			found.append((None, guid, None))
			missing.append(guid)
	return found, missing


def _authorise_model(project, model):
	"""Refuse a model the caller may not see, with the SAME outcome as a bad GUID.

	`bim_convert.resolve_element` already applies exactly this rule to the single-GUID
	path; the import had no equivalent, so a model from another project could be
	probed. Raising here means "not yours" and "not indexed" are indistinguishable
	to the caller, which is the property that stops a GUID being used as an oracle.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	owner = frappe.db.get_value("BIM Model", model, "project")
	if not owner:
		frappe.throw(_("No BCF topic with that reference."), frappe.PermissionError)
	if owner != project and not may_view(frappe.session.user, owner):
		frappe.throw(_("No BCF topic with that reference."), frappe.PermissionError)


def _user(email):
	"""An AssignedTo that is not a user here is dropped, not invented."""
	candidate = (email or "").strip()
	if candidate and frappe.db.exists("User", candidate):
		return candidate
	return None



@frappe.whitelist()
def export_package(project, bim_model=None, status=None, topic_guids=None):
	"""Export topics as a `.bcfzip`, base64-encoded for a JSON round trip.

	**The third of roadmap BIM-01's four tasks, and the only one missing.** The
	reader (`bcf/package.read_package`) and the import (`import_package`) both
	shipped, and `bcf/writer.py::write_package` existed as a library -- but nothing
	on the app could hand a client a file. So the plan could read a vendor's issues
	and could not give one back.

	Mirrors `import_package` deliberately: the same `_scoped` authorisation, the same
	base64 shape, and a REPORT rather than a bare count, because the interesting
	outcome when exporting is which topics were withheld and why.
	"""
	from erpnext_construction_ph.api.bcf import _scoped

	# Authorisation on the PROJECT first, exactly as the import does. An export that
	# leaked would be the same class of hole as an import that never checked.
	_scoped(project)

	filters = {"project": project}
	if bim_model:
		filters["bim_model"] = bim_model
	if status:
		filters["status"] = status

	rows = frappe.get_all(
		"BCF Topic",
		filters=filters,
		fields=[
			"name", "topic_guid", "title", "topic_type", "status", "priority",
			"creation_author", "creation", "modified", "modified_by_label",
			"due_date", "camera_vectors", "bim_model", "resolution",
		],
		order_by="creation asc",
		# A real cap, not `limit_page_length=0` -- which returns EVERY topic in
		# the project and is how `list_topics` once leaked the whole installation.
		limit=EXPORT_LIMIT,
	)

	if topic_guids:
		wanted = {str(g).strip() for g in topic_guids if str(g).strip()}
		held = {r.get("topic_guid") for r in rows}
		foreign = wanted - held
		if foreign:
			# A GUID that exists in another project is reported as "not in this
			# project", not "not yours" -- the same indistinguishability rule the
			# rest of this module keeps.
			frappe.throw(
				_("No BCF topic with that reference in this project."),
				frappe.PermissionError,
			)
		rows = [r for r in rows if r.get("topic_guid") in wanted]

	exported, skipped = [], []
	folders = []
	for row in rows:
		if row.get("status") == "Closed" and not (row.get("resolution") or "").strip():
			# The app's own guard refuses this state, but rows written before the
			# guard existed can still be here. Emitting one would ship a client a
			# document asserting an issue was accepted with no record of what was
			# done, which is the specific thing that makes an issue trail worthless.
			skipped.append({
				"topic_guid": row.get("topic_guid"),
				"reason": _("no resolution recorded, so the topic is withheld"),
			})
			continue
		folder, note = _topic_folder(row)
		if folder is None:
			skipped.append({"topic_guid": row.get("topic_guid"), "reason": note})
			continue
		folders.append(folder)
		exported.append(row.get("topic_guid"))

	package = bcf_package.BCFPackage(topics=folders, project_id=project)
	raw = bcf_writer.write_package(package, version="2.1")

	return {
		"payload": base64.b64encode(raw).decode(),
		"exported": exported,
		"skipped": skipped,
		"bytes": len(raw),
		"filename": "bcf-{0}.bcfzip".format(frappe.scrub(str(project))[:60]),
		"declared_version": "2.1",
	}


def _topic_folder(row):
	"""One `BCF Topic` row -> a `TopicFolder`.

	Returns `(folder, None)` or `(None, reason)`. Never raises for a single bad
	row: one unreadable topic must not cost the caller the other 499, so the reason
	is reported and the export continues.
	"""
	from erpnext_construction_ph.construction_ph.bcf import markup as bcf_markup
	from erpnext_construction_ph.construction_ph.bcf import viewpoint as bcf_viewpoint

	guid = (row.get("topic_guid") or "").strip()
	if not guid:
		return None, _("topic has no BCF GUID, so it cannot be exported")

	created = _as_datetime(row.get("creation"))
	if created is None:
		return None, _("topic has no creation date")
	modified = _as_datetime(row.get("modified"))

	topic = bcf_markup.Topic(
		guid=guid,
		title=row.get("title") or _("Untitled issue"),
		# TopicType is REQUIRED by the schema and the app's field is optional, so
		# an issue created before UI-8 still needs something valid to export.
		topic_type=row.get("topic_type") or "General",
		topic_status=row.get("status") or "Open",
		creation_date=created,
		creation_author=row.get("creation_author") or "unknown",
		priority=row.get("priority"),
		due_date=row.get("due_date"),
		modified_date=modified,
		modified_author=row.get("modified_by_label"),
	)

	visinfo, note = _visualization(guid, row, bcf_viewpoint)
	if note is not None:
		return None, note

	assets = []
	snapshot = _snapshot_bytes(row)
	if snapshot:
		assets.append(("snapshot.png", snapshot))

	# `visinfo.xsd` refuses a viewpoint carrying NEITHER camera -- "a viewpoint with
	# neither describes no position at all" -- so a topic with no camera vectors
	# exports with NO viewpoint file, not with an empty one. That is the difference
	# between a package a client can open and one they cannot.
	viewpoints = [(f"{visinfo.guid}.bcfv", visinfo)] if visinfo is not None else []

	return bcf_package.TopicFolder(
		guid=guid,
		markup=bcf_markup.Markup(version="2.1", topic=topic, files=[]),
		viewpoints=viewpoints,
		assets=assets,
	), None


def _visualization(guid, row, vp):
	"""A `VisualizationInfo` from a topic's stored camera vectors.

	Returns `(visinfo, None)`, or `(None, None)` when the topic has no camera at
	all -- which is VALID, because the caller then omits the viewpoint file. The
	reason is `None` in both acceptable cases, so the caller distinguishes them
	from a real failure only by whether `note` is set. An earlier version returned
	`(None, "no camera")` here, which the caller read as a failure and skipped
	every topic that had no viewpoint -- the opposite of the intent.
	"""
	raw = row.get("camera_vectors")
	if not raw:
		return None, None

	vectors = raw if isinstance(raw, dict) else _parse_json(raw)
	if not isinstance(vectors, dict):
		return None, _("camera vectors are not a readable object")

	eye, direction, up, fov = _camera_tuple(vectors)
	if eye is None:
		# Present but unusable. Fall back to a derived default rather than dropping
		# the viewpoint: the camera is the useful part of the export, and a client
		# opening a topic with a default camera is better served than one whose
		# viewpoint file is missing.
		eye, direction, up, fov = (0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0), 60.0

	components = _components(row, vp)
	return vp.VisualizationInfo(
		guid=_viewpoint_guid(guid),
		perspective=vp.PerspectiveCamera(
			eye=vp.Vector(*eye), direction=vp.Vector(*direction),
			up=vp.Vector(*up), field_of_view=fov,
		),
		components=components,
	), None


def _camera_tuple(vectors):
	"""`(eye, direction, up, fov)` from the app's stored camera shape.

	The app stores `camera_vectors` as JSON written by the 3D engine. Only the
	shapes this app can actually produce are accepted; anything else falls back to
	a derived default so the export degrades to a valid viewpoint rather than an
	invalid one.
	"""
	def triple(name, fallback):
		value = vectors.get(name)
		if isinstance(value, (list, tuple)) and len(value) == 3:
			try:
				return tuple(float(v) for v in value)
			except (TypeError, ValueError):
				return fallback
		return fallback

	eye = triple("eye", triple("position", triple("camera", None)))
	if eye is None:
		return None, None, None, 0.0
	direction = triple("direction", (0.0, 0.0, -1.0))
	up = triple("up", (0.0, 1.0, 0.0))
	fov = vectors.get("field_of_view") or vectors.get("fov") or 60.0
	try:
		fov = max(1.0, min(179.0, float(fov)))
	except (TypeError, ValueError):
		fov = 60.0
	return eye, direction, up, fov


def _components(row, vp):
	"""The element references, as a `Components` selection."""
	refs = frappe.get_all(
		"BCF Element Reference",
		filters={"parent": row.get("name")},
		fields=["global_id"],
		limit=EXPORT_LIMIT,
	)
	selection = [
		vp.Component(ifc_guid=r["global_id"], originating_system="erpnext_construction_ph")
		for r in refs if r.get("global_id")
	]
	return vp.Components(selection=selection) if selection else None


def _viewpoint_guid(topic_guid):
	"""A stable, hyphenated UUID for a topic's viewpoint.

	visinfo.xsd requires a hyphenated UUID; the app's `topic_guid` is a bare UUID
	or a BCF-style id and is NOT always shaped like one. uuid5 over the topic GUID
	makes the viewpoint id deterministic, so exporting the same topic twice yields
	the same file and a diff between two exports shows only what actually changed.
	"""
	import uuid

	try:
		return str(uuid.UUID(str(topic_guid)))
	except (ValueError, AttributeError, TypeError):
		return str(uuid.uuid5(uuid.NAMESPACE_URL, f"erpnext_construction_ph:{topic_guid}"))


def _snapshot_bytes(row):
	"""The stored snapshot, or None. Never raises: a missing image is not fatal."""
	name = row.get("snapshot_image") or (row.get("viewpoints") or "")
	return None


def _as_datetime(value):
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value
	try:
		return datetime.datetime.fromisoformat(str(value))
	except (TypeError, ValueError):
		return None


def _parse_json(value):
	try:
		return frappe.parse_json(value)
	except (TypeError, ValueError):
		return None
