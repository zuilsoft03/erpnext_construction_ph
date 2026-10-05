# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-0's server half: the Desk Page controller actually resolves and refuses.

The browser half of UI-0 -- tabs, no remount, camera preserved -- is driven in
`test_ui0_viewport_tabs.py` against a DOM, because that is what it is: DOM and
state behaviour with no server in it.

This covers what is left: that `get_context` is REACHABLE, and that it does the two
things only the server can do.

Reachability is not a formality. Frappe locates a Page's controller by module name
(`get_module_path(module) + "." + name`), so a file named after the hyphenated ROUTE
is never found, `get_context` never runs, and the page renders empty with no error
anywhere. That is not hypothetical: PH2-04 lost a session to it on `project-view`.
So the first assertion here is that the module IMPORTS under the name Frappe will
ask for -- if the file is renamed, this fails immediately and says why.

The second is the refusal: a user with no access to a project must be refused HERE,
once, with a clear message -- not handed a shell that fills with one permission
error per panel three calls later.
"""

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase


class TestWorkspaceController(IntegrationTestCase):
	"""`bim_workspace.py` -- the file whose name must match the Page record."""

	# Frappe's `TemplatePage.set_pymodule` builds the controller's module name from
	# the TEMPLATE's path, not from the Page record's name and not from the route:
	#
	#     pymodule_path = dirname(template) + basename(template).replace("-","_") + ".py"
	#     pymodule_name = app + "." + that_path.replace("/", ".")[:-3]
	#
	# Read out of frappe/website/page_renderers/template_page.py:129-146. So the
	# controller must sit BESIDE the page in `page/<name>/<name>.py`. An earlier
	# version of this test used `get_module("bim_workspace")`, which imports a
	# top-level module and therefore never tested the real lookup at all.
	CONTROLLER = "erpnext_construction_ph.construction_ph.page.bim_workspace.bim_workspace"

	def test_the_controller_imports_under_the_name_frappe_asks_for(self):
		"""The PH2-04 failure shape, asserted rather than assumed absent."""
		import importlib
		import os

		import frappe

		module = importlib.import_module(self.CONTROLLER)
		self.assertTrue(
			hasattr(module, "get_context"),
			"the controller imported but has no get_context -- the page would render "
			"empty with no console error, which is exactly the PH2-04 failure",
		)
		# And the file must exist exactly where `set_pymodule` looks for it, which
		# is the difference between "imports in a test" and "is loaded by the page".
		expected = os.path.join(
			frappe.get_app_path("erpnext_construction_ph"),
			"construction_ph", "page", "bim_workspace", "bim_workspace.py",
		)
		self.assertTrue(
			os.path.exists(expected),
			f"the controller is not at the path set_pymodule builds: {expected}",
		)

	def test_get_context_refuses_a_project_the_caller_may_not_see(self):
		"""One clear refusal here beats a permission error per panel later."""
		from erpnext_construction_ph.construction_ph.page.bim_workspace import (
			bim_workspace as controller,
		)

		# Its own project (CORE-30). This is the workspace controller's gate,
		# and it used to skip rather than run without one to borrow.
		project = fixtures.make_project("UI0")

		# A user with no role at all is the honest "may not see" case, and does not
		# depend on how roles happen to be provisioned on this site.
		frappe.set_user("__nobody__@example.com")
		saved = frappe.local.form_dict
		try:
			self.assertFalse(
				controller._may_open(project),
				"a user with no roles was allowed into the workspace",
			)
			# `get_context` reads the REQUEST's form_dict, not a `form_dict` key on the
			# context it is handed -- which is how Frappe pages actually work, and
			# which the first version of this test got wrong, so the refusal never
			# fired and the test failed for a reason that had nothing to do with
			# authorisation.
			frappe.local.form_dict = frappe._dict(project=project)
			with self.assertRaises(frappe.PermissionError):
				controller.get_context(frappe._dict())
		finally:
			frappe.local.form_dict = saved
			frappe.set_user("Administrator")

	def test_get_context_gives_an_administrator_the_workspace(self):
		"""And the allowed path really does produce a usable context."""
		from erpnext_construction_ph.construction_ph.page.bim_workspace import (
			bim_workspace as controller,
		)

		frappe.set_user("Administrator")
		saved = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict()
		try:
			context = controller.get_context(frappe._dict())
		finally:
			frappe.local.form_dict = saved
		workspace = context.workspace
		self.assertTrue(workspace["can_open"])
		self.assertTrue(workspace["project"], "no project resolved for an administrator")
		self.assertEqual(
			[m["id"] for m in workspace["modes"]],
			["3d", "pdf", "split"],
			"the server's mode list must match MODES in viewport_tabs.js",
		)

	def test_the_default_project_is_one_the_caller_can_see(self):
		"""Falling back to 'the first project' would confirm another job's work."""
		from erpnext_construction_ph.construction_ph.page.bim_workspace import (
			bim_workspace as controller,
		)

		frappe.set_user("__nobody__@example.com")
		try:
			self.assertIsNone(
				controller._default_project(),
				"a user with no roles was handed a project to open",
			)
		finally:
			frappe.set_user("Administrator")

	def test_the_page_routes_to_the_controller(self):
		"""The served page bundle must actually name this page."""
		self.assertTrue(
			frappe.get_all("Page", filters={"name": "bim_workspace"}, limit=1),
			"the bim_workspace Page record is missing, so the route renders nothing",
		)
