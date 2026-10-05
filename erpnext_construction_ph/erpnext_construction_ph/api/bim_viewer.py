# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Read-only API behind the BIM/BCF Desk surface (public/js/bim/bim_viewer.js).

Returns only inventory and issue data. Geometry stays on S3 and is fetched by the
browser once the Phase 3 engine is in place (D-009); nothing here streams or
transcodes a model, and no model bytes are ever stored in MariaDB.
"""

import frappe

# Bounded, because a topic carries issue text and camera vectors: an unbounded
# list is both a disclosure and a portal-sized denial of service.
TOPIC_LIMIT = 200

MODEL_FIELDS = (
	"name",
	# `project` is returned so a caller can see WHAT it was granted, and so the
	# scoping is observable: without it the response is an inventory whose
	# provenance is invisible, and a test asserting "no cross-project rows" has
	# nothing to assert on. It is the field the authorisation is expressed in.
	"project",
	"model_name",
	"conversion_status",
	"lod_level",
	"file_size_bytes",
	"frag_url",
	"converted_on",
	"creation",
)

TOPIC_FIELDS = (
	"name",
	# `bim_model` and `project` are returned so a caller can see WHAT it was
	# granted -- and so the scoping is observable. Without them the cross-project
	# enumeration test passed even with this method's filter REMOVED, because
	# `row.get("bim_model")` was None on every row and the assertion was
	# comparing None against a name. A leak test needs the field it asserts on to
	# actually be in the response.
	"bim_model",
	"project",
	"title",
	"status",
	"priority",
	"camera_vectors",
	"assigned_to",
)

# The element GUIDs a topic covers live in the `elements` CHILD TABLE
# (`BCF Element Reference.global_id`), not in a column on `tabBCF Topic`. They are
# therefore NOT in TOPIC_FIELDS: putting a Table field into a `get_all` field list
# makes Frappe emit `SELECT ..., elements FROM tabBCF Topic`, and a child table is
# not a column -- MariaDB answers 1054 Unknown column and the method 500s.
# `list_topics` fetches them in a second, properly scoped query and attaches them.
ELEMENT_REF_FIELDS = ("parent", "element", "ifc_class", "global_id")


@frappe.whitelist()
def list_models(project=None):
	"""Models for a project the caller may view AS A CLIENT.

	**Was unscoped, which made the page's role gate decorative.** With no
	authorisation check, any session holder could call this directly and
	enumerate every model in the installation -- `project` was a filter, not a
	permission. The endpoint and the embed now enforce the same rule (PH3-04),
	and the mutation that removes THIS check fails a test the page cannot catch.

	`project=None` therefore returns only projects the caller is entitled to, and
	never a cross-project inventory. An internal Desk role alone gets nothing:
	`may_view_bim` requires a client-facing role, not just `READ_ROLES`.
	"""
	from erpnext_construction_ph.api.portal_data import entitled_projects

	# `ignore_permissions=True` deliberately: the scoping below is OUR filter, so
	# this endpoint's behaviour must not change when Frappe's implicit User
	# Permission layer is on or off. Relying on that layer is how the previous
	# version looked protected while carrying no filter of its own.
	# ONE mechanism for both paths. The caller-named `project` is an
	# INTERSECTION with the entitled set, not a filter that replaces it -- asking
	# for a project by name must never be a way to bypass entitlement, and two
	# paths using two different predicates is how they drift apart and one of
	# them quietly stops checking.
	entitled = entitled_projects(frappe.session.user)
	if not entitled:
		return []
	if project:
		if project not in entitled:
			return []
		filters = {"project": project}
	else:
		filters = {"project": ["in", entitled]}

	# This second comment used to say the flag was "deliberately NOT set here",
	# directly above a call that passed it -- and a neighbouring comment in the same
	# function said the opposite, correctly. Two comments, one flag, opposite
	# claims: whichever a reader believed, the other would make them wrong. The
	# neighbouring one is the true account, so the stale paragraph is gone rather
	# than rewritten. The history it carried is real but belongs to the ledger, not
	# to the line it misdescribed -- the 403 that tempted an earlier edit was never
	# this function's authorisation, and the flag is here for the reason the
	# neighbouring comment gives: the entitlement check is OURS, and it must not
	# vary with Frappe's User Permission layer being on or off.
	models = frappe.get_all(
		"BIM Model",
		filters=filters,
		fields=list(MODEL_FIELDS),
		order_by="creation desc",
		limit_page_length=0,
		ignore_permissions=True,
	)

	# RECONCILE, here, where the DROPDOWN's data is assembled.
	#
	# `get_frag_url` also downgrades a `Ready` model whose cache has gone, but that
	# is a LAZY correction: it only happens for a model somebody actually opened.
	# The dropdown reads `conversion_status` straight off this function and never
	# probes, so a model nobody has opened keeps saying "Ready" with nothing behind
	# it -- which is precisely the case the correction exists for. Measured on the
	# gate site: five models sat at `Ready` with `file_size_bytes: 0` and
	# `get_frag_url` answering `cache_missing`, and the dropdown offered every one.
	#
	# Best effort, like the correction in `get_frag_url`: this is a read path, and a
	# read-only replica must still get its list.
	try:
		from erpnext_construction_ph.api.bim_frag import cache_path, FRAG_DOCTYPE

		for model in models:
			if model.get("conversion_status") != "Ready" or not model.get("frag_url"):
				continue
			if frappe.db.exists(FRAG_DOCTYPE, {"file_name": cache_path(model["name"])}):
				continue
			frappe.db.set_value(
				"BIM Model", model["name"], "conversion_status", "Stale", update_modified=False
			)
			model["conversion_status"] = "Stale"
	except Exception:
		frappe.log_error(
			title="Could not reconcile a stale fragment cache",
			message=frappe.get_traceback(),
		)

	return models


@frappe.whitelist()
def list_topics(bim_model=None, status=None):
	"""BCF issues for a model the caller may view as a client.

	**Was the identical hole to `list_models`, and worse.** Whitelisted, no
	authorisation, and with no model named it returned *every BCF Topic in the
	installation* with `limit_page_length=0` -- no limit at all. BCF topics carry
	issue TEXT and camera vectors, not just an inventory row, so this leaked more
	than the model list did. The overlay calls exactly this method, so closing
	`list_models` alone left the embed's own second call wide open.

	The model is resolved to its project and `may_view_bim` decides; with no model
	named, the result is scoped to the models on projects the caller is entitled
	to. Unbounded was also wrong on its own terms: a client with one project does
	not need every issue ever filed, and a limit is what stops this becoming a
	denial-of-service on the portal as well as a disclosure.
	"""
	from erpnext_construction_ph.api.portal_data import entitled_projects

	# Nothing to initialise by hand: the flag is not set, and that is deliberate.
	# `frappe.set_user(frappe.session.user)` used to sit here, and it belongs to
	# `login()` -- in a handler it REPLACES `local.session.sid` with the username and
	# empties `local.session.data`, so the request succeeded and the NEXT one came
	# back as a Guest. See ops/JOURNAL/2026-09-29-PH3-10.md.
	filters = {}
	if status:
		filters["status"] = status

	entitled = entitled_projects(frappe.session.user)
	if not entitled:
		return []
	if bim_model:
		# Resolved to its project and INTERSECTED with the entitled set: naming a
		# model must not be a way to bypass entitlement.
		project = frappe.db.get_value("BIM Model", bim_model, "project")
		if not project or project not in entitled:
			return []
		filters["bim_model"] = bim_model
	else:
		models = frappe.get_all(
			"BIM Model", filters={"project": ["in", entitled]}, pluck="name"
		)
		if not models:
			return []
		filters["bim_model"] = ["in", models]

	# `ignore_permissions=True` for the same reason as `list_models`: the scoping
	# above is ours, and must not vary with the framework's implicit layer.
	topics = frappe.get_all(
		"BCF Topic",
		filters=filters,
		fields=list(TOPIC_FIELDS),
		order_by="creation desc",
		limit_page_length=TOPIC_LIMIT,
		ignore_permissions=True,
	)

	# The element GUIDs each topic covers, so a viewer that picked an element can ask
	# "which issues are on THIS one" rather than showing every issue on the model.
	#
	# A second query because `elements` is a CHILD TABLE, not a column: including it
	# in the field list above would generate `SELECT ..., elements FROM
	# tabBCF Topic` and fail with MariaDB 1054. Scoped to `parent IN` the topics just
	# returned, so it cannot widen the result set -- the same reasoning as the
	# entitlement intersection above. `ignore_permissions` for the same reason: the
	# scoping here is ours and must not vary with the framework's implicit layer.
	if topics:
		refs = frappe.get_all(
			"BCF Element Reference",
			filters={"parent": ["in", [t.name for t in topics]]},
			fields=list(ELEMENT_REF_FIELDS),
			ignore_permissions=True,
		)
		by_parent = {}
		for ref in refs:
			by_parent.setdefault(ref.parent, []).append(
				{"element": ref.element, "ifc_class": ref.ifc_class, "global_id": ref.global_id}
			)
		for topic in topics:
			topic["elements"] = by_parent.get(topic.name, [])

	# The overlay needs a screen position; topics without camera vectors still
	# belong in the list, so fall back to the centre rather than dropping them.
	for topic in topics:
		vectors = _camera_vectors(topic.get("camera_vectors"))
		topic["screen_x"] = vectors.get("screen_x", 50)
		topic["screen_y"] = vectors.get("screen_y", 50)

	return topics


def _camera_vectors(raw):
	"""BCF camera vectors are stored as JSON on the topic."""
	if not raw:
		return {}
	if isinstance(raw, dict):
		return raw
	try:
		parsed = frappe.parse_json(raw)
	except (ValueError, TypeError):
		return {}
	return parsed if isinstance(parsed, dict) else {}


@frappe.whitelist()
def default_project():
	"""The project this user's workspace opens on, or None.

	**The page had no project at all.** `bim_workspace.js` called `list_models`
	with `args: { project: this.project }` and `this.project` was never assigned
	anywhere, so the request carried `project: undefined`, the server answered
	400, and the page reported "No BIM models for this project yet" -- which is
	false, and is indistinguishable from a project that genuinely has none.

	Why not just have the JS read the URL? It should, and it does: `?project=` is
	checked first, so a coordinator can open a specific project. But a Desk Page
	has no project in its route, and the Desk's own `get_context` output is not
	reachable from the page script, so a route with no parameter still needs a
	server answer. This is that answer, and it is authorised exactly the way the
	rest of the module is: it returns a project this user may actually view, and
	None otherwise. It never falls back to "the first project" -- that would hand
	a coordinator someone else's job, which is enough to confirm it exists.
	"""
	from erpnext_construction_ph.api.portal_data import entitled_projects

	entitled = entitled_projects(frappe.session.user)
	return entitled[0] if entitled else None


@frappe.whitelist()
def schedule_for_project(project):
	"""BOQ lines with planned dates, and the element links that reach them.

	Scoped like everything else in this module: `entitled_projects` decides what
	the caller may see, and an unentitled caller gets an EMPTY answer rather than
	a refusal, because "this project has no schedule" and "you may not look" are
	different things to say and only one of them is true.

	The links come from `BCF Element Reference.boq_item`, which is the ONLY
	element-to-BOQ edge in this app. The response says how many links there were,
	so the overlay can state its coverage instead of letting a viewer assume the
	whole model is scheduled.
	"""
	from erpnext_construction_ph.api.portal_data import entitled_projects

	entitled = entitled_projects(frappe.session.user)
	if project not in entitled:
		return {"items": [], "links": [], "coverage": {"linked": 0, "unlinked": 0}, "permitted": False}

	boqs = frappe.get_all(
		"Construction BOQ",
		filters={"project": project},
		pluck="name",
		ignore_permissions=True,
	)
	items = []
	if boqs:
		items = frappe.get_all(
			"Construction BOQ Item",
			filters={"boq": ["in", boqs]},
			fields=[
				"name", "item_code", "description", "unit",
				"planned_start", "planned_end", "actual_percent_complete",
			],
			ignore_permissions=True,
			limit=2000,
		)
	by_name = {i["name"]: i for i in items}

	refs = frappe.get_all(
		"BCF Element Reference",
		filters={"boq_item": ["in", list(by_name)]} if by_name else {"boq_item": ["is", "not set"]},
		fields=["global_id", "boq_item"],
		ignore_permissions=True,
		limit=5000,
	)
	links = [r for r in refs if r.get("boq_item") in by_name]
	linked_guids = {r["global_id"] for r in links}
	return {
		"items": list(by_name.values()),
		"links": links,
		"coverage": {"linked": len(linked_guids), "unlinked": max(0, len(linked_guids) - len(links))},
		"permitted": True,
	}
