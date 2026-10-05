# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""buildingSMART BCF API: topics, viewpoints, and the camera jump between them.

**The disclosure risk this module exists to contain.** `BCF Topic.topic_guid` is a
`Data` field, not a Link. A lookup by GUID alone therefore resolves across the
whole installation -- any client that guesses or leaks a GUID could read another
project's issues, their photographs and their camera positions. So **every read
here is scoped by project**, and a GUID that exists but belongs to another
project is refused exactly as a GUID that does not exist is: the two answers must
be indistinguishable, or the API becomes an oracle for guessing real GUIDs.

Scoping is applied in one place, `_scoped`, so a new caller cannot forget it.

**The camera jump is bidirectional**, and is why a topic is linked to a WBS Task:
`task_viewpoints` gives a Task its camera positions, and `topic_viewpoints` gives
a topic's. Without the Task link one direction could not exist at all.
"""

import uuid

import frappe
from frappe import _


def _new_guid():
	"""A BCF topic/viewpoint GUID.

	BCF identifies topics by UUID, so this is one -- generated, not sequenced. A
	sequential ID would make "guess the neighbour's GUID" trivial.
	"""
	return str(uuid.uuid4())


def _scoped(project):
	"""BCF Topics for `project`, or a refusal.

	Every read path starts here. There is deliberately no "get a topic by GUID
	anywhere in the installation" helper, because one is exactly the hole.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	if not project:
		frappe.throw(_("A project is required to read BCF topics."), title=_("Project Required"))
	if not may_view(frappe.session.user, project):
		# The same refusal a caller gets for a GUID that does not exist.
		frappe.throw(_("No BCF topic with that reference."), frappe.PermissionError)
	return frappe.get_all(
		"BCF Topic",
		filters={"project": project},
		fields=["name", "topic_guid", "title", "status", "priority", "bim_model", "task", "project"],
		order_by="creation desc",
	)


def _one(topic_guid, project):
	"""One topic by GUID, scoped. Refuses rather than 404s on a foreign GUID."""
	rows = [r for r in _scoped(project) if r.topic_guid == topic_guid]
	if not rows:
		frappe.throw(_("No BCF topic with that reference."), frappe.PermissionError)
	return rows[0]


@frappe.whitelist()
def get_topics(project, status=None):
	"""Topics for `project`, optionally filtered by status."""
	rows = _scoped(project)
	if status:
		rows = [r for r in rows if r.status == status]
	return [_as_dict(r) for r in rows]


@frappe.whitelist()
def get_topic(topic_guid, project):
	"""One topic with its viewpoints."""
	row = _one(topic_guid, project)
	doc = frappe.get_doc("BCF Topic", row.name)
	out = _as_dict(row)
	out["viewpoints"] = [
		{
			"viewpoint_guid": v.viewpoint_guid,
			"comment": v.comment,
			"camera_vectors": v.camera_vectors,
		}
		for v in doc.viewpoints
	]
	return out


@frappe.whitelist()
def create_topic(project, title, topic_guid=None, status="Open", priority="Normal", bim_model=None, task=None):
	"""Create a topic. The GUID is generated unless the caller supplies one."""
	_scoped(project)  # authorisation: a caller who cannot read cannot write either

	guid = (topic_guid or "").strip() or _new_guid()
	if frappe.db.exists("BCF Topic", {"topic_guid": guid}):
		frappe.throw(
			_("A BCF topic with reference {0} already exists.").format(guid),
			title=_("Duplicate Reference"),
		)

	doc = frappe.get_doc(
		{
			"doctype": "BCF Topic",
			"topic_guid": guid,
			"title": title,
			"project": project,
			"status": status,
			"priority": priority,
			"bim_model": bim_model,
			"task": task,
		}
	).insert(ignore_permissions=False)
	# The spec expects `{topic_guid, name}` back (viewport:482) and this returned a
	# bare GUID. The client needs `name` for the follow-up `add_viewpoint` call and
	# for the row it optimistically adds to the issue list, and a bare GUID left the
	# viewer re-querying for it -- one extra round trip whose only purpose was to
	# recover a value the insert already knew.
	return {"topic_guid": doc.topic_guid, "name": doc.name}


# The snapshot arrives from the browser as a data URL. The API used to demand a FILE
# PATH, which made the issue-from-viewer loop unimplementable as specified: the canvas
# produces one shape and the endpoint demanded another, and nothing anywhere named a
# step that bridged them. So the bridge lives here, where the bytes are.
def _snapshot_file(snapshot, topic_guid):
	"""Accept a data URL or a path; return a stored path, or a reason there is none.

	A data URL is written into the site's private files and its path returned, because
	BCF Viewpoint stores a PATH. A caller that genuinely has a path on disk still
	passes, because the export tooling and the tests do that -- so both shapes are
	accepted rather than one being declared correct.
	"""
	if not snapshot:
		return {"ok": False, "reason": "no snapshot was captured"}

	if not str(snapshot).startswith("data:"):
		# A path, or something pretending to be one. It is kept as given, and whether it
		# exists is the caller's business -- this endpoint does not read the filesystem
		# for a path it was handed.
		return {"ok": True, "path": snapshot, "written": False}

	# "data:image/png;base64,XXXX" -> ("image/png", "XXXX")
	header, _, payload = str(snapshot).partition(",")
	if not payload:
		return {"ok": False, "reason": "the snapshot data URL carries no data"}
	mime = header[5:].split(";")[0] or "image/png"
	if not payload.strip():
		return {"ok": False, "reason": "the snapshot data URL is empty"}

	ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
	name = f"bcf-snapshot-{topic_guid}-{frappe.generate_hash(length=10)}.{ext}"
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": name,
			"attached_to_doctype": "BCF Topic",
			"attached_to_name": topic_guid,
			"content": payload,
			"decode": True,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return {"ok": True, "path": f"/private/files/{name}", "written": True}


@frappe.whitelist()
def add_viewpoint(topic_guid, project, camera_vectors, comment=None, viewpoint_guid=None, snapshot=None):
	"""Attach a camera position to a topic.

	`snapshot` is **required**, and it is a data URL from `canvas.toDataURL()` -- not a
	file path, which is what this used to demand and what made the flow unimplementable
	as specified. BCF Viewpoint declares `snapshot_image` mandatory, and a viewpoint with
	a camera but no photograph is not a viewpoint; it is half a record that reads as
	complete. A path is still accepted, because the export tooling passes one.

	Returns the viewpoint guid, or the reason there was no snapshot -- a half record is
	worse than a refusal, and a refusal the caller can report is better than an
	exception from a required field.
	"""
	row = _one(topic_guid, project)
	# The bridge the spec never named: the canvas hands over base64 and the field
	# stores a path, so the bytes are written HERE rather than demanded of a caller
	# that has no way to write one.
	shot = _snapshot_file(snapshot, row.topic_guid)
	if not shot["ok"]:
		return {"ok": False, "reason": shot["reason"]}
	doc = frappe.get_doc("BCF Topic", row.name)
	doc.append(
		"viewpoints",
		{
			"viewpoint_guid": (viewpoint_guid or "").strip() or _new_guid(),
			"comment": comment or "",
			"camera_vectors": camera_vectors,
			"snapshot_image": shot["path"],
		},
	)
	doc.save()
	return {
		"ok": True,
		"viewpoint_guid": doc.viewpoints[-1].viewpoint_guid,
		"snapshot_image": shot["path"],
		"snapshot_written": shot["written"],
	}


@frappe.whitelist()
def task_viewpoints(task):
	"""Camera positions for a WBS Task -- the Task -> BCF direction of the jump.

	Scoped through the topic's project rather than trusting the task, so a task
	cannot be used to reach a topic on a project the caller cannot see.
	"""
	rows = frappe.get_all(
		"BCF Topic",
		filters={"task": task},
		fields=["name", "topic_guid", "project", "title"],
	)
	from erpnext_construction_ph.api.portal_data import may_view

	out = []
	for row in rows:
		if not may_view(frappe.session.user, row.project):
			continue  # invisible, not refused: this is a listing, not a lookup
		doc = frappe.get_doc("BCF Topic", row.name)
		out.append(
			{
				"topic_guid": row.topic_guid,
				"title": row.title,
				"viewpoints": [
					{"viewpoint_guid": v.viewpoint_guid, "camera_vectors": v.camera_vectors}
					for v in doc.viewpoints
				],
			}
		)
	return out


@frappe.whitelist()
def jump(topic_guid, project):
	"""The camera jump: a topic reference in, a camera position and WBS task out."""
	row = _one(topic_guid, project)
	doc = frappe.get_doc("BCF Topic", row.name)
	latest = doc.viewpoints[-1] if doc.viewpoints else None
	return {
		"topic_guid": row.topic_guid,
		"project": row.project,
		"task": row.task,
		"bim_model": row.bim_model,
		"camera_vectors": latest.camera_vectors if latest else None,
		"viewpoint_guid": latest.viewpoint_guid if latest else None,
		"has_viewpoint": latest is not None,
	}


def _as_dict(row):
	return {
		"topic_guid": row.topic_guid,
		"title": row.title,
		"status": row.status,
		"priority": row.priority,
		"project": getattr(row, "project", None),
		"bim_model": row.bim_model,
		"task": row.task,
	}


@frappe.whitelist()
def store_snapshot(project, payload, filename="snapshot.png"):
	"""Write a base64 data URL to a File and return its path.

	**This step exists because the spec's own description of it does not work.**
	`add_viewpoint(snapshot=...)` takes a FILE PATH and is required (BCF Viewpoint
	declares `snapshot_image` mandatory), while the browser produces
	`canvas.toDataURL()` -- a base64 data URL. Nothing between them was ever named,
	so the loop as specified could not close: the caller had a data URL and the
	server wanted a path.

	Decoded here rather than in the client so the image is validated, size-capped
	and stored exactly the way every other attachment in this app is. A snapshot is
	arbitrary caller-supplied bytes, and the size cap is the only thing between this
	endpoint and an unbounded write.

	Authorised exactly as the rest of the module: `_scoped(project)` first, so a
	caller with no access to the project is refused before anything is written.
	"""
	import base64
	import binascii
	import os

	_scoped(project)  # authorisation BEFORE any write, not after

	text = (payload or "").strip()
	if text.startswith("data:"):
		# `data:image/png;base64,....` -- take the part after the comma, and only
		# accept an image, so this cannot be used as a generic file drop.
		# NOT named `_`. `header, _, text = text.partition(",")` shadows Frappe's
		# translation function with the separator string, and every `_("...")` after
		# it in this function then raises "'str' object is not callable" -- which is
		# only reachable on the error paths, so the happy path looked fine.
		header, separator, text = text.partition(",")
		if not separator:
			frappe.throw(_("The snapshot data URL is malformed."), title=_("Unreadable Snapshot"))
		if "image/" not in header:
			frappe.throw(_("The snapshot must be an image."), title=_("Not An Image"))
	if not text:
		frappe.throw(_("The snapshot was empty."), title=_("No Snapshot"))

	try:
		raw = base64.b64decode(text, validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("The snapshot is not valid base64."), title=_("Unreadable Snapshot"))

	if len(raw) > SNAPSHOT_MAX_BYTES:
		frappe.throw(
			_("The snapshot is {0} KB, over the {1} KB limit.").format(
				len(raw) // 1024, SNAPSHOT_MAX_BYTES // 1024
			),
			title=_("Snapshot Too Large"),
		)
	if not raw.startswith(b"\x89PNG") and not raw.startswith(b"\xff\xd8"):
		frappe.throw(
			_("The snapshot is not a PNG or a JPEG."),
			title=_("Unreadable Snapshot"),
		)

	name = os.path.basename(filename or "snapshot.png")
	if not name.lower().endswith((".png", ".jpg", ".jpeg")):
		name += ".png"

	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": name,
			"content": raw,
			"attached_to_doctype": "Construction Project",
			"attached_to_name": project,
			"attached_to_field": "preview_image",
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return {"file_url": file.file_url, "name": file.name, "bytes": len(raw)}


# A screenshot of a 4K viewport is a few hundred KB; 8 MB is far past anything a
# browser produces and low enough that a caller cannot use this to fill the disk.
SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024


@frappe.whitelist()
def topic_viewpoints(topic_guid, project):
	"""One topic's viewpoints, for the camera jump.

	Authorised through `_one(topic_guid, project)`, which routes `_scoped(project)`
	and REFUSES a GUID from another project rather than returning an empty list --
	so "not yours" and "does not exist" are the same answer, as everywhere else in
	this module.
	"""
	row = _one(topic_guid, project)
	doc = frappe.get_doc("BCF Topic", row["name"])
	refs = frappe.get_all(
		"BCF Element Reference",
		filters={"parent": row["name"]},
		fields=["global_id", "element", "ifc_class"],
		ignore_permissions=True,
	)
	viewpoints = []
	for vp in doc.viewpoints:
		raw = vp.get("camera_vectors")
		vectors = raw
		if isinstance(raw, str):
			try:
				vectors = frappe.parse_json(raw)
			except (TypeError, ValueError):
				vectors = {}
		viewpoints.append({
			"viewpoint_guid": vp.get("viewpoint_guid"),
			"comment": vp.get("comment") or "",
			"camera_vectors": vectors if isinstance(vectors, dict) else {},
			"snapshot_image": vp.get("snapshot_image") or "",
		})
	return {"topic_guid": topic_guid, "viewpoints": viewpoints, "elements": refs}
