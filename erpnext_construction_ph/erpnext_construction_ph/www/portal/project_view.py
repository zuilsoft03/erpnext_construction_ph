# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""`/portal/project-view` — the client-facing view of one project.

Authorisation is decided in `api/portal_data.get_portal_payload`; this module
only turns the answer into an HTTP status and a template context. Keeping the
decision in one place is what stops a template edit from leaking another
client's ledger.
"""

import frappe

no_cache = 1


def get_context(context):
	"""Build the portal context, or refuse.

	- not signed in            -> redirect to login (302)
	- signed in, not permitted -> 403
	- permitted                -> 200 with the project payload
	"""
	project = frappe.form_dict.get("project") or frappe.form_dict.get("project_name")

	if frappe.session.user in (None, "", "Guest"):
		frappe.local.flags.redirect_location = "/login?redirect-to=/portal/project-view"
		raise frappe.Redirect

	payload = frappe.get_attr("erpnext_construction_ph.api.portal_data.get_portal_payload")(
		project=project
	)

	if payload.get("denied"):
		if payload.get("reason") == "not_permitted":
			frappe.throw(
				frappe._("You do not have access to this project."),
				frappe.PermissionError,
			)
		# A missing project is indistinguishable from one the caller cannot see, so
		# the portal cannot be used to enumerate project names.
		frappe.throw(
			frappe._("That project does not exist, or you do not have access to it."),
			frappe.PermissionError,
		)

	context.project = payload["project"]
	context.company = payload["company"]
	context.contract_amount = payload["contract_amount"]
	context.totals = payload["totals"]
	context.billings = payload["billings"]
	context.available_projects = payload["available_projects"]
	# The S-curve. Assigned here as well as computed in the payload: a key the
	# payload returns but `get_context` never copies renders as an empty branch
	# in the template, which looks identical to "this project has no plan yet".
	context.evm = payload.get("evm") or {"available": False}
	context.bim = bim_context(context.project)
	# `templates/base.html` publishes this for every Desk page so Frappe's realtime
	# plugin can build its socket URL. This page extends `templates/web.html` and never
	# got it, so the URL came out as `http://<host>:undefined/...` and did not parse.
	# It belongs in the context rather than read as `frappe.socketio_port` in the
	# template: a template may only read names its controller assigns.
	# `frappe.conf`, NOT `frappe.socketio_port`: this version has no such module
	# attribute, and `boot.py` reads the same key off conf.
	context.socketio_port = frappe.conf.get("socketio_port") or 9000
	# REMOVED 2026-09-28: `context.boot = getattr(frappe, "boot", None)`. On Frappe
	# v17 `frappe.boot` is the MODULE `apps/frappe/frappe/boot.py`, not the boot
	# dict, so putting it in the Jinja context made the page unserialisable and
	# every render died with `TypeError: Object of type <class 'module'> ... is not
	# JSON serializable`. The template never referenced `boot`, so it was dead
	# context that also broke the page -- removed rather than corrected, because
	# there is nothing to correct it to.
	# No explicit commit. This controller is read-only -- it builds a context from
	# get_portal_payload, which only reads -- and Frappe commits at the end of the
	# request regardless. The line was vestigial, and it was actively harmful: any
	# test that calls get_context committed the *whole* test transaction, so
	# class-level fixtures escaped as real rows and later fixtures kept tripping
	# over them. Same non-hermetic class as the EIS ATP counter, reached through a
	# code path rather than a counter.
	return context


def bim_context(project):
    """The viewer's context for `project`, or a withheld payload.

    **Shared with the tests rather than reimplemented there.** An access-control
    decision that exists twice is an access-control decision that will drift,
    and the test would then be asserting against its own copy of the rule instead
    of the one the page runs.

    `bim` always carries an `available` key. The template branches on it, and a
    missing key renders as *undefined* -- which is exactly how this page 500'd
    before its controller was found at all.

    When withheld, the payload carries no model name, no conversion status and no
    `.frag` URL: a branch that said "restricted" against a branch that named a
    model would let a caller enumerate which projects have models.
    """
    from erpnext_construction_ph.api.portal_data import may_view_bim
    from erpnext_construction_ph.api import bim_viewer

    bim = {"available": False, "models": [], "model": None, "topics": []}
    if not may_view_bim(frappe.session.user, project):
        return bim
    models = [
        m
        for m in bim_viewer.list_models(project=project)
        if m.get("conversion_status") == "Ready"
    ]
    if models:
        bim = {"available": True, "models": models, "model": models[0]}

    # The clash LIST, which is the "delegate the geometry to Solibri" reading made
    # concrete: the partner tool detects the clashes, and the client needs to SEE
    # them. `list_topics` was already written, scoped and bounded, and tested -- and
    # nothing called it, so a client with a fully converted model was shown a viewer
    # surface and a list of nothing. The list needs no `.frag` to render, which is
    # what makes it deliverable now while the geometry stays delegated.
    #
    # Topics carry ISSUE TEXT and camera vectors, so this is the more sensitive of
    # the two payloads and it goes through the same `may_view_bim` gate above. It is
    # fetched from the caller's OWN entitled models rather than from `project`, so a
    # client never sees another project's issues by naming a different one.
    try:
        bim["topics"] = bim_viewer.list_topics() if models else []
    except Exception:
        # A clash list is additive. If it cannot be read the page still renders the
        # project rather than 500ing, and says so.
        bim["topics"] = []
    return bim
