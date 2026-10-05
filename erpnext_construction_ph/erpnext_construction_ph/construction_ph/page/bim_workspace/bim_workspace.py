# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Controller for the BIM Workspace Desk Page.

**This file's existence is the reason the page works at all**, and that is not
obvious. Frappe's `set_pymodule` locates a Page's controller by MODULE NAME, using
`get_module_path(module) + "." + name`. A controller named `bim-workspace.py` for
the route `bim-workspace` is therefore never found: the route name is hyphenated,
the module is underscored, and the lookup joins them as written. `get_context` then
never runs, and a Desk Page with no `get_context` renders with `this.root` set to
the page container but none of the context the JS expects -- an empty page, no
console error, and nothing in the logs naming the cause.

That is not a hypothetical. PH2-04 lost a full session to exactly this shape on
`project-view`, where the controller was `project-view.py`. The file here is
`bim_workspace.py`, matching the Page record `bim_workspace` (the ROUTE is
`bim-workspace`; the RECORD is underscored). Do not rename it to match the route.
"""

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	"""Context for the workspace.

	Deliberately minimal. The three viewports each call their own whitelisted
	endpoint with the project as a parameter -- `bim_viewer.list_models`,
	`bim_frag.get_frag_url`, `bcf.list_topics`, `bim_convert.resolve_element` --
	because those endpoints authorise the caller themselves. Pre-loading data here
	instead would put a data path in front of them that has no authorisation at all,
	which is the class of hole PH3-04 was opened to close.

	What belongs here is what the browser cannot work out: which project this
	workspace is for, and whether this user may open it at all. A Desk Page that
	never checks renders an empty shell to a user who then sees a permission error
	from an endpoint three calls later, with the real fault nowhere in view.
	"""
	context.no_cache = 1

	project = frappe.form_dict.get("project") or _default_project()
	if not project:
		context.workspace = {"project": None, "models": [], "can_open": False}
		return context

	if not _may_open(project):
		# Refused here rather than at the first data call, so the user gets one clear
		# message instead of a shell that fills with a permission error per panel.
		frappe.throw(
			_("You do not have access to the BIM workspace for {0}.").format(project),
			frappe.PermissionError,
		)

	context.workspace = {
		"project": project,
		"can_open": True,
		# The modes the JS builds. Kept in sync with `MODES` in viewport_tabs.js;
		# the JS is the source of truth and this is for anything server-rendered.
		"modes": [
			{"id": "3d", "label": _("3D Model")},
			{"id": "pdf", "label": _("PDF Takeoff")},
			{"id": "split", "label": _("Split View")},
		],
	}
	return context


def _may_open(project):
	"""Whether this user may open the workspace for `project`."""
	from erpnext_construction_ph.api.portal_data import may_view

	return may_view(frappe.session.user, project)


def _default_project():
	"""The project to open when the URL does not name one.

	Only a project the user can ACTUALLY see is returned. Falling back to "the first
	Construction Project" would hand a coordinator a workspace for someone else's
	project -- empty of their data, but enough to confirm it exists.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	for name in frappe.get_all("Construction Project", pluck="name", limit=20):
		if may_view(frappe.session.user, name):
			return name
	return None
