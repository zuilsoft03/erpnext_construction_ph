# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH3-03 gate: the BCF API, and the scoping that keeps it from being an oracle.

`BCF Topic.topic_guid` is a `Data` field, so a GUID resolves across the whole
installation unless every read is scoped. The gate's own NEGATIVE is that
cross-project GUID access is refused -- BCF data is client-visible, so an
unscoped read is a disclosure defect, not a missing feature.

The load-bearing property here is **indistinguishability**: a GUID that exists on
another project must produce the same refusal as a GUID that does not exist. If
they differ, the API becomes an oracle for guessing real references, and the
refusal is worthless.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bcf

CAMERA = '{"position": [1.0, 2.0, 3.0], "target": [0.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0]}'


class TestBcfApi(IntegrationTestCase):
	"""Topics, viewpoints, the camera jump, and the scoping that contains them."""

	def setUp(self):
		self.mine = self._project("PH3-03 Mine")
		self.theirs = self._project("PH3-03 Theirs")
		self.mine_model = self._bim_model(self.mine)
		self.their_model = self._bim_model(self.theirs)

	# ---- the happy path ----------------------------------------------------

	def test_a_topic_is_created_and_readable_within_its_project(self):
		guid = bcf.create_topic(self.mine, "Clash at grid C", priority="High", bim_model=self.mine_model)["topic_guid"]
		self.assertTrue(guid, "no topic reference returned")

		# Assert the topic is present, not that it is the ONLY one. An exact-list
		# assertion breaks the first time anything else on this project commits,
		# which is a fixture problem masquerading as a scoping failure.
		found = {t["topic_guid"]: t for t in bcf.get_topics(self.mine)}
		self.assertIn(guid, found)
		self.assertEqual(found[guid]["priority"], "High")

	def test_generated_guids_are_not_sequential(self):
		"""A predictable ID would make guessing the neighbour's trivial."""
		guids = {bcf.create_topic(self.mine, f"Topic {i}", bim_model=self.mine_model)["topic_guid"] for i in range(4)}
		self.assertEqual(len(guids), 4, "a GUID repeated")
		# A sequential scheme would share a long prefix; UUIDs do not.
		self.assertGreater(len({g[:8] for g in guids}), 1)

	def test_a_duplicate_reference_is_refused(self):
		guid = bcf.create_topic(self.mine, "First", bim_model=self.mine_model)["topic_guid"]
		with self.assertRaises(frappe.ValidationError):
			bcf.create_topic(self.mine, "Second", topic_guid=guid, bim_model=self.mine_model)["topic_guid"]

	def test_the_camera_jump_returns_the_latest_viewpoint_and_the_task(self):
		"""The jump: a reference in, a camera position and a WBS task out."""
		task = self._task(self.mine)
		guid = bcf.create_topic(self.mine, "Rebar clash", task=task, bim_model=self.mine_model)["topic_guid"]
		bcf.add_viewpoint(guid, self.mine, CAMERA, comment="first look", snapshot=self._snapshot())
		bcf.add_viewpoint(guid, self.mine, CAMERA, comment="after resolving", snapshot=self._snapshot())

		jump = bcf.jump(guid, self.mine)
		self.assertTrue(jump["has_viewpoint"])
		self.assertEqual(jump["task"], task)
		self.assertEqual(jump["camera_vectors"], CAMERA)

	def test_the_jump_is_bidirectional(self):
		"""A Task returns its topics' viewpoints -- the other direction."""
		task = self._task(self.mine)
		guid = bcf.create_topic(self.mine, "Both ways", task=task, bim_model=self.mine_model)["topic_guid"]
		bcf.add_viewpoint(guid, self.mine, CAMERA, snapshot=self._snapshot())

		found = {t["topic_guid"]: t for t in bcf.task_viewpoints(task)}
		self.assertIn(guid, found)
		self.assertEqual(found[guid]["viewpoints"][0]["camera_vectors"], CAMERA)

	def test_a_topic_with_no_viewpoint_says_so_rather_than_guessing(self):
		guid = bcf.create_topic(self.mine, "No camera yet", bim_model=self.mine_model)["topic_guid"]
		jump = bcf.jump(guid, self.mine)
		self.assertFalse(jump["has_viewpoint"])
		self.assertIsNone(jump["camera_vectors"])

	# ---- the NEGATIVE: cross-project access -------------------------------

	def test_a_guid_from_another_project_is_refused(self):
		guid = bcf.create_topic(self.theirs, "Their issue", bim_model=self.their_model)["topic_guid"]
		with self.assertRaises(frappe.PermissionError):
			bcf.get_topic(guid, self.mine)
		with self.assertRaises(frappe.PermissionError):
			bcf.jump(guid, self.mine)

	def test_a_foreign_guid_is_indistinguishable_from_one_that_does_not_exist(self):
		"""The property that makes the refusal worth anything.

		If a real-but-foreign GUID answers differently from a made-up one, the API
		becomes an oracle for discovering real references, and "access denied"
		proves the reference exists.
		"""
		real = bcf.create_topic(self.theirs, "Their issue", bim_model=self.their_model)["topic_guid"]

		messages = {}
		for label, reference in (("real-foreign", real), ("nonexistent", "00000000-0000-0000-0000-000000000000")):
			with self.assertRaises(frappe.PermissionError) as caught:
				bcf.get_topic(reference, self.mine)
			messages[label] = str(caught.exception)
		self.assertEqual(
			messages["real-foreign"],
			messages["nonexistent"],
			"a foreign GUID and a nonexistent one must be refused identically",
		)

	def test_topics_listing_never_leaks_another_project(self):
		mine_guid = bcf.create_topic(self.mine, "Mine", bim_model=self.mine_model)["topic_guid"]
		theirs_guid = bcf.create_topic(self.theirs, "Theirs", bim_model=self.their_model)["topic_guid"]
		listed = {t["topic_guid"] for t in bcf.get_topics(self.mine)}
		self.assertIn(mine_guid, listed)
		self.assertNotIn(theirs_guid, listed)

	def test_a_user_scoped_elsewhere_cannot_read_or_write(self):
		"""Scoping runs through the same gate the portal uses."""
		guid = bcf.create_topic(self.mine, "Mine", bim_model=self.mine_model)["topic_guid"]
		self._user_scoped_to("ph303-outsider@portal.test", self.theirs)

		previous = frappe.session.user
		frappe.set_user("ph303-outsider@portal.test")
		try:
			with self.assertRaises(frappe.PermissionError):
				bcf.get_topics(self.mine)
			with self.assertRaises(frappe.PermissionError):
				bcf.create_topic(self.mine, "Should not be allowed", bim_model=self.mine_model)["topic_guid"]
		finally:
			frappe.set_user(previous)

	def test_a_task_listing_hides_topics_the_caller_cannot_see(self):
		"""`task_viewpoints` is a listing, so foreign topics are omitted, not refused."""
		foreign_task = self._task(self.theirs)
		guid = bcf.create_topic(self.theirs, "Theirs", task=foreign_task, bim_model=self.their_model)["topic_guid"]
		bcf.add_viewpoint(guid, self.theirs, CAMERA, snapshot=self._snapshot())

		self._user_scoped_to("ph303-limited@portal.test", self.mine)
		previous = frappe.session.user
		frappe.set_user("ph303-limited@portal.test")
		try:
			self.assertEqual(bcf.task_viewpoints(foreign_task), [])
		finally:
			frappe.set_user(previous)

	# ---- fixtures ----------------------------------------------------------

	def _user_scoped_to(self, email, project):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
					"new_password": frappe.generate_hash(length=12),
				}
			).insert(ignore_permissions=True)
		erp = frappe.db.get_value("Construction Project", project, "erpnext_project")
		if not frappe.db.exists("User Permission", {"user": email, "for_value": erp}):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Project",
					"for_value": erp,
					"apply_to_all_doctypes": 1,
				}
			).insert(ignore_permissions=True)
		return email

	def _task(self, project):
		erp = frappe.db.get_value("Construction Project", project, "erpnext_project")
		existing = frappe.get_all("Task", filters={"project": erp}, limit=1, pluck="name")
		if existing:
			return existing[0]
		return frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "PH3-03 clash task",
				"project": erp,
				"status": "Open",
			}
		).insert(ignore_permissions=True).name

	@staticmethod
	def _snapshot():
		"""A real 1x1 PNG -- `snapshot_image` is mandatory on BCF Viewpoint."""
		import base64
		import os
		import tempfile

		png = base64.b64decode(
			b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
		)
		handle, path = tempfile.mkstemp(suffix=".png")
		with os.fdopen(handle, "wb") as fh:
			fh.write(png)
		return path

	@staticmethod
	def _bim_model(project):
		"""A BIM Model to hang topics off -- `bim_model` is mandatory on BCF Topic."""
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
		erp_project = hits[0] if hits else frappe.get_doc(
			{"doctype": "Project", "project_name": erp_name, "company": company}
		).insert(ignore_permissions=True).name
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
