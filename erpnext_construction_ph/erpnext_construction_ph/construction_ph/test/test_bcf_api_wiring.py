# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Wiring contracts for `api/bcf.py`: each published endpoint is actually published.

Deliberately a separate file from `test_bcf_api.py`. That one holds the *scoping*
contract -- who may read a topic, and that a foreign GUID is indistinguishable
from a nonexistent one. This one holds the *wiring* contract: that the endpoint
the viewer calls is reachable at all, and that a read returns what it claims to
return. The two overlap at `get_topic`, which is the reason to split them rather
than merge: a scoping failure and a wiring failure are different defects, and a
single file makes the distinction invisible in the report.

Three measured defects are pinned here.

**B1 -- `@frappe.whitelist()` sat above `_snapshot_file`.** The decorator is what
publishes an endpoint, and it landed on a private helper instead of on
`add_viewpoint`. So the one endpoint the BIM viewer calls was not published and
every "add viewpoint" died with Method Not Allowed, while a function that writes a
`File` from caller-supplied bytes and authorises nothing was the thing exposed.
The gate that refused it is `frappe.handler.execute_cmd`: it resolves the dotted
path, then calls `is_whitelisted(method)`, which is a membership test against
`frappe.whitelisted`. `frappe.call` is NOT that gate -- it resolves and invokes
with no whitelist check at all -- so a test written against `frappe.call` passes
whether or not the decorator is there, and proves nothing. These tests drive the
gate itself.

**B2 -- `get_topic` referenced an undefined `snapshot`.** It raised NameError on
every call that got past authorisation, so the one endpoint that reads a single
topic back could never return one.

**B3 -- `_scoped` filtered on `project` but never selected it.** The rows it
returns reported `project: None`, so no caller could tell which project a topic
belonged to -- and `_as_dict` forwarded that `None` to every consumer.
"""

import tempfile
import types

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bcf
from frappe.utils import get_attr

# The dotted path `bim_workspace.js:417` hands to `frappe.call`.
VIEWPOINT = "erpnext_construction_ph.api.bcf.add_viewpoint"
SNAPSHOT_HELPER = "erpnext_construction_ph.api.bcf._snapshot_file"

# `captureContext()` builds an object, which reaches the server as JSON, and
# `BCF Viewpoint.camera_vectors` is `Code`/JSON.
CAMERA = '{"eye": [1.0, 2.0, 3.0], "target": [0.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0]}'


def png_data_url():
	"""A real 1x1 PNG as the canvas hands it over.

	`store_snapshot` refuses anything that is not PNG or JPEG, so a plausible
	fake here would make this test assert against the validation rather than
	against the wiring it is here to prove.
	"""
	import base64

	return "data:image/png;base64," + base64.b64encode(
		base64.b64decode(
			b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
		)
	).decode()


class TestBcfWiring(IntegrationTestCase):
	"""B1: the endpoint the viewer calls is published. B2/B3: reads return what they claim."""

	def setUp(self):
		self.project = self._project("PH-B Wiring")
		self.model = self._bim_model(self.project)

	# ---- B1: the decorator landed on the wrong function ---------------------

	def test_add_viewpoint_is_published(self):
		"""The one endpoint the viewer calls must be whitelisted.

		`execute_cmd` gates on `is_whitelisted(method)`, and that is a membership
		test against `frappe.whitelisted` -- a set of function objects the decorator
		populates. Asserting the gate directly is what makes this fail when the
		decorator is on the wrong function; asserting via `frappe.call` would not,
		because `frappe.call` checks nothing.
		"""
		method = get_attr(VIEWPOINT)
		self.assertIn(
			method,
			frappe.whitelisted,
			f"{VIEWPOINT} is not whitelisted, so every add-viewpoint from the BIM "
			"viewer is refused with Method Not Allowed",
		)
		frappe.is_whitelisted(method)  # the gate the request path calls; must not throw

	def test_the_private_snapshot_writer_is_not_published(self):
		"""`_snapshot_file` is an implementation detail and must not be an endpoint.

		It writes a `File` from caller-supplied bytes, takes an arbitrary topic
		reference, and performs no authorisation of its own. Exposing it is the
		other half of the same mistake.
		"""
		method = get_attr(SNAPSHOT_HELPER)
		self.assertNotIn(
			method,
			frappe.whitelisted,
			"a private helper that writes files is exposed as a public endpoint",
		)
		with self.assertRaises(frappe.PermissionError):
			frappe.is_whitelisted(method)

	def test_add_viewpoint_is_reachable_over_the_route_the_viewer_uses(self):
		"""The whole `/api/method/` route, with the arguments the viewer sends.

		`issue_modal.js` stores the snapshot first and passes the resulting PATH to
		`add_viewpoint`, so this reproduces that order and that argument shape
		rather than inventing one.
		"""
		guid = bcf.create_topic(
			project=self.project, title="Duct clashes with beam", bim_model=self.model
		)["topic_guid"]
		stored = bcf.store_snapshot(project=self.project, payload=png_data_url())

		result = self._api_method(
			VIEWPOINT,
			{
				"topic_guid": guid,
				"project": self.project,
				"camera_vectors": CAMERA,
				"snapshot": stored["file_url"],
			},
		)
		self.assertTrue(result["ok"], "the viewer could not add a viewpoint over HTTP")
		self.assertEqual(result["snapshot_image"], stored["file_url"])
		self.assertEqual(bcf.jump(guid, self.project)["camera_vectors"], CAMERA)

	def test_an_unpublished_endpoint_is_refused_over_the_same_route(self):
		"""The gate is load-bearing in both directions, not just an allowlist entry.

		Drives the real route for the private helper and requires the refusal, so
		this cannot pass by accident if the gate is ever bypassed.
		"""
		with self.assertRaises(frappe.PermissionError):
			self._api_method(SNAPSHOT_HELPER, {"snapshot": "x", "topic_guid": "x"})

	# ---- B2: get_topic raises NameError on every call -----------------------

	def test_get_topic_returns_the_topic(self):
		"""A topic the caller may read comes back, rather than raising."""
		guid = bcf.create_topic(
			project=self.project, title="Rebar clash", status="Open", bim_model=self.model
		)["topic_guid"]

		topic = bcf.get_topic(guid, self.project)

		self.assertEqual(topic["topic_guid"], guid)
		self.assertEqual(topic["title"], "Rebar clash")
		self.assertEqual(topic["status"], "Open")
		self.assertEqual(topic["viewpoints"], [])

	def test_get_topic_returns_the_viewpoints_recorded_against_it(self):
		"""The second half of the endpoint: the viewpoints, not just the topic."""
		guid = bcf.create_topic(
			project=self.project, title="Rebar clash", bim_model=self.model
		)["topic_guid"]
		bcf.add_viewpoint(
			guid, self.project, CAMERA, comment="first look", snapshot=self._snapshot_path()
		)

		viewpoints = bcf.get_topic(guid, self.project)["viewpoints"]

		self.assertEqual(len(viewpoints), 1)
		self.assertEqual(viewpoints[0]["comment"], "first look")

	def test_get_topic_needs_no_snapshot_argument(self):
		"""Reading a topic must not depend on a snapshot being supplied.

		The undefined `snapshot` reference meant this raised `NameError` even
		with nothing to write, because the reference itself was the fault. Two
		positional arguments are the whole contract.
		"""
		guid = bcf.create_topic(
			project=self.project, title="No camera yet", bim_model=self.model
		)["topic_guid"]
		self.assertEqual(bcf.get_topic(guid, self.project)["topic_guid"], guid)

	# ---- B3: _scoped filters on project but did not select it ---------------

	def test_every_topic_names_the_project_it_belongs_to(self):
		"""`_scoped` filters on `project`, so the row it hands back must say so.

		`None` here is not a cosmetic gap: it is the field a consumer uses to
		attribute an issue to a project, and it was silently empty for every read
		in the module.
		"""
		guid = bcf.create_topic(
			project=self.project, title="Mine", bim_model=self.model
		)["topic_guid"]

		found = {t["topic_guid"]: t for t in bcf.get_topics(self.project)}

		self.assertIn(guid, found)
		self.assertEqual(
			found[guid]["project"],
			self.project,
			"a topic that filters under a project must report that project",
		)

	def test_no_listed_topic_reports_a_missing_project(self):
		"""Every row, not just the one this test created."""
		bcf.create_topic(project=self.project, title="Second", bim_model=self.model)
		for topic in bcf.get_topics(self.project):
			self.assertEqual(
				topic["project"], self.project, f"topic {topic['topic_guid']} lost its project"
			)

	def test_get_topic_also_reports_the_project(self):
		"""`_as_dict` forwards the field, so the single-topic read leaked the gap too."""
		guid = bcf.create_topic(
			project=self.project, title="Mine", bim_model=self.model
		)["topic_guid"]
		self.assertEqual(bcf.get_topic(guid, self.project)["project"], self.project)

	# ---- the rest of the module still answers -------------------------------

	def test_the_other_endpoints_still_answer(self):
		"""The three fixes must not have moved anything else.

		One pass over every remaining public endpoint in the file, so a regression
		shows up here rather than in a sibling's module.
		"""
		task = self._task(self.project)
		guid = bcf.create_topic(
			project=self.project,
			title="Everything",
			priority="High",
			status="Open",
			task=task,
			bim_model=self.model,
		)["topic_guid"]

		self.assertGreater(len(bcf.get_topics(self.project, status="Open")), 0)
		self.assertEqual(len(bcf.get_topics(self.project, status="Resolved")), 0)
		stored = bcf.store_snapshot(project=self.project, payload=png_data_url())
		self.assertTrue(stored["file_url"].endswith(".png"))
		self.assertGreater(stored["bytes"], 0)
		self.assertFalse(bcf.jump(guid, self.project)["has_viewpoint"])

		bcf.add_viewpoint(guid, self.project, CAMERA, snapshot=self._snapshot_path())

		jump = bcf.jump(guid, self.project)
		self.assertTrue(jump["has_viewpoint"])
		self.assertEqual(jump["task"], task)
		self.assertEqual(jump["project"], self.project)
		self.assertEqual(
			{t["topic_guid"] for t in bcf.task_viewpoints(task)}, {guid}
		)
		self.assertEqual(bcf.topic_viewpoints(guid, self.project)["topic_guid"], guid)
		self.assertEqual(
			bcf.topic_viewpoints(guid, self.project)["viewpoints"][0]["camera_vectors"],
			frappe.parse_json(CAMERA),
		)

	# ---- helpers ------------------------------------------------------------

	def _api_method(self, dotted_path, args, method="POST"):
		"""Resolve and gate a `/api/method/` request the way the web layer does.

		`IntegrationTestCase` tears `frappe.local.request` down and never puts it
		back, so the stub stands in for the browser's request. It carries the one
		attribute the route reads: the HTTP method, which decides whether the
		endpoint permits that verb.
		"""
		from frappe.handler import execute_cmd

		saved_form = frappe.local.form_dict
		had_request = hasattr(frappe.local, "request")
		saved_request = getattr(frappe.local, "request", None)
		frappe.local.form_dict = dict(args)
		frappe.local.request = types.SimpleNamespace(method=method)
		try:
			return execute_cmd(dotted_path)
		finally:
			frappe.local.form_dict = saved_form
			if had_request:
				frappe.local.request = saved_request
			else:
				delattr(frappe.local, "request")

	@staticmethod
	def _snapshot_path():
		"""A path, which is the shape the viewer sends and the export tooling passes."""
		import base64
		import os

		png = base64.b64decode(
			b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
		)
		handle, path = tempfile.mkstemp(suffix=".png")
		with os.fdopen(handle, "wb") as fh:
			fh.write(png)
		return path

	@staticmethod
	def _bim_model(project):
		name = f"{project} model"
		existing = frappe.get_all("BIM Model", filters={"model_name": name}, pluck="name")
		if existing:
			return existing[0]
		return frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": name,
				"project": project,
				"conversion_status": "Pending",
				"lod_level": "LOD200",
			}
		).insert(ignore_permissions=True).name

	@staticmethod
	def _task(project):
		erp = frappe.db.get_value("Construction Project", project, "erpnext_project")
		existing = frappe.get_all("Task", filters={"project": erp}, limit=1, pluck="name")
		if existing:
			return existing[0]
		return frappe.get_doc(
			{"doctype": "Task", "subject": "PH-B wiring task", "project": erp, "status": "Open"}
		).insert(ignore_permissions=True).name

	@staticmethod
	def _project(name):
		existing = frappe.get_all("Construction Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = f"{name} Customer"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
					"tax_id": "000-111-222-00000",
				}
			).insert(ignore_permissions=True)
		erp_name = f"{name} ERP"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		erp_project = (
			hits[0]
			if hits
			else frappe.get_doc(
				{"doctype": "Project", "project_name": erp_name, "company": company}
			).insert(ignore_permissions=True).name
		)
		return frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": name,
				"erpnext_project": erp_project,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 5_000_000.00,
			}
		).insert(ignore_permissions=True).name
