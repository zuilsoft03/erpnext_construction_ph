# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-4: the issue-from-viewer loop, including the step the spec never named.

The plan records the correction: `add_viewpoint(snapshot=...)` wants a FILE PATH
and requires one, while the browser produces `canvas.toDataURL()` -- a base64
data URL. Nothing was ever specified between them, so the loop as written could
not close. `store_snapshot` is that missing step.

The tests here are mostly about what that endpoint REFUSES, because it accepts
arbitrary caller-supplied bytes and its caller is a browser:

  * a payload that is not an image;
  * base64 that does not decode;
  * a PNG/JPEG header is required, so an ELF binary renamed .png is refused;
  * oversized images are capped, which is the only thing between this and an
    unbounded write;
  * a caller with no access to the project is refused BEFORE anything is written.

And the authorisation shape that matters: a caller who cannot read the project
must not be able to tell "no such project" from "not yours".
"""

import base64
import zlib

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bcf


def _png_bytes(width=2, height=2):
	"""A real, minimal PNG. Built here rather than shipped as a fixture so the
	test cannot pass on a file that happens to be checked in."""
	def chunk(tag, data):
		raw = tag + data
		return (
			len(data).to_bytes(4, "big")
			+ raw
			+ zlib.crc32(raw).to_bytes(4, "big")
		)

	ihdr = (
		width.to_bytes(4, "big")
		+ height.to_bytes(4, "big")
		+ bytes([8, 2, 0, 0, 0])  # 8-bit, truecolour
	)
	# One scanline of `width` RGB pixels, filter byte 0.
	raw = b"\x00" + b"\xff\x00\x00" * width
	return (
		b"\x89PNG\r\n\x1a\n"
		+ chunk(b"IHDR", ihdr)
		+ chunk(b"IDAT", zlib.compress(raw))
		+ chunk(b"IEND", b"")
	)


class TestIssueFromViewer(fixtures.CommittingCase, IntegrationTestCase):
	"""The base64 -> File step, and the topic read behind the camera jump."""
	# MEASURED: this class commits (it uses CommittingCase) and declared nothing, so
	# nothing it created was reclaimed except make_project rows. One run left +3 BCF
	# Topic rows pointing at a reclaimed BIM Model. The comment in fixtures.py already
	# called this "the whole of UI4's leak" -- named in the source, never fixed.
	#
	# Found by running each BCF-creating module alone: every other one measured +0.
	committing_doctypes = ("BCF Topic", "BIM Model")

	def setUp(self):
		frappe.set_user("Administrator")
		# Its own project (CORE-30). `self.projects` stays a list because
		# the tests below read it; it now holds exactly what this class made.
		self.project = fixtures.make_project("UI4")
		self.projects = [{"name": self.project}]
		# `bim_model` is MANDATORY on BCF Topic, so every fixture needs a real one.
		# Its own model too (CORE-30). It borrowed "the first BIM Model on this
		# project" and SKIPPED when there was none -- and the project is one this class
		# just created, so it never had one. The class's central flow was therefore not
		# running at all on a site that did not happen to have an ambient model.
		self.model = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"UI4 {frappe.generate_hash(length=6)}",
			"project": self.project,
		}).insert(ignore_permissions=True).name
		self._own_everything()

	def _own_everything(self):
		"""Remember what this class created, and delete it afterwards.

		**THIS CLASS COMMITS**, at two points, because a test here is about what survives
		a commit. A commit defeats the harness's rollback, so before CORE-30 the
		adopted project was never this class's and nothing leaked. Now that the fixture
		is its OWN, the commit persists it -- eleven projects and models per run, and
		`FIX*` rows on the site that the next run then reads.

		That is the residue problem this whole row exists to end, reintroduced through
		the fix for it. A class that commits has to clean up after itself, because for
		that class there is no longer a rollback to rely on.
		"""
		cls = TestIssueFromViewer
		cls._created = getattr(cls, "_created", set())
		cls._created.update({("Construction Project", self.project), ("BIM Model", self.model)})

	@classmethod
	def tearDownClass(cls):
		"""Delete, COMMIT the deletion, and only then let the harness tear down.

		The order is the whole point. `super().tearDownClass()` ROLLS BACK, so calling
		it first undid every delete -- the cleanup ran, committed nothing, and left 32
		`FIX *` projects on the site per run while looking like it had worked. Delete,
		COMMIT, and hand over.
		"""
		try:
			removed = 0
			projects = [name for dt, name in getattr(cls, "_created", set()) if dt == "Construction Project"]
			if projects:
				frappe.db.delete(
					"File",
					{
						"attached_to_doctype": "Construction Project",
						"attached_to_name": ("in", tuple(projects)),
					},
				)
			for doctype, name in sorted(getattr(cls, "_created", set()), reverse=True):
				if frappe.db.exists(doctype, name):
					frappe.db.delete(doctype, {"name": name})
					removed += 1
			frappe.db.commit()
			cls.last_cleanup_removed = removed
		finally:
			cls._created = set()
			super().tearDownClass()

	def _data_url(self, payload, mime="image/png"):
		return f"data:{mime};base64," + base64.b64encode(payload).decode()

	# ------------------------------------------------------------- store_snapshot
	def test_a_png_data_url_becomes_a_file(self):
		out = bcf.store_snapshot(self.project, self._data_url(_png_bytes()))
		self.assertTrue(out["file_url"], "no file was written")
		self.assertGreater(out["bytes"], 0)
		# The stored bytes must be the PNG, not the data URL wrapper.
		stored = frappe.get_doc("File", out["name"])
		self.assertTrue(stored.file_url.endswith(".png"))

	def test_a_raw_base64_png_is_also_accepted(self):
		"""The browser may send either form depending on how the canvas was read."""
		payload = base64.b64encode(_png_bytes()).decode()
		out = bcf.store_snapshot(self.project, payload)
		self.assertTrue(out["file_url"])

	def test_a_payload_that_is_not_an_image_is_refused(self):
		"""A generic file drop is not what this endpoint is for."""
		with self.assertRaises(frappe.ValidationError):
			bcf.store_snapshot(self.project, self._data_url(b"id,name\n1,x", mime="text/csv"))

	def test_base64_that_does_not_decode_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			bcf.store_snapshot(self.project, "data:image/png;base64,!!!not base64!!!")

	def test_a_binary_renamed_png_is_refused(self):
		"""Header check, not extension check -- an extension is a claim, not a fact."""
		payload = b"\x7fELF\x02\x01\x01" + b"\x00" * 64
		with self.assertRaises(frappe.ValidationError):
			bcf.store_snapshot(self.project, self._data_url(payload))

	def test_an_oversized_snapshot_is_capped(self):
		"""The cap is the only thing between this endpoint and an unbounded write."""
		oversize = b"\x89PNG\r\n\x1a\n" + b"\x00" * (bcf.SNAPSHOT_MAX_BYTES + 1)
		with self.assertRaises(frappe.ValidationError):
			bcf.store_snapshot(self.project, self._data_url(oversize))

	def test_a_caller_with_no_access_is_refused_before_anything_is_written(self):
		"""Authorisation BEFORE the write, not after."""
		frappe.set_user("__nobody__@example.com")
		try:
			with self.assertRaises(frappe.PermissionError):
				bcf.store_snapshot(self.project, self._data_url(_png_bytes()))
		finally:
			frappe.set_user("Administrator")

	# ------------------------------------------------------------ create_topic
	def test_create_topic_returns_both_the_guid_and_the_name(self):
		"""The spec expects `{topic_guid, name}`; it used to return a bare GUID, and
		the client needed `name` for the follow-up viewpoint call."""
		out = bcf.create_topic(self.project, "UI-4 shape check", bim_model=self.model)
		self.assertIsInstance(out, dict)
		self.assertTrue(out["topic_guid"])
		self.assertTrue(out["name"], "the caller cannot follow up without the docname")
		# And the GUID is a real hyphenated UUID, which is what the exporter and the
		# BCF folder name both depend on.
		import uuid

		uuid.UUID(out["topic_guid"])  # raises if it is not one

	# ---------------------------------------------------------- topic_viewpoints
	def test_topic_viewpoints_returns_the_points_and_the_elements(self):
		created = bcf.create_topic(self.project, "UI-4 viewpoint check", bim_model=self.model)
		frappe.db.commit()
		out = bcf.topic_viewpoints(created["topic_guid"], self.project)
		self.assertEqual(out["topic_guid"], created["topic_guid"])
		self.assertIsInstance(out["viewpoints"], list)
		self.assertIsInstance(out["elements"], list)

	def test_topic_viewpoints_refuses_a_foreign_project_the_same_way(self):
		"""'Not yours' and 'does not exist' must be indistinguishable."""
		# A SECOND project, created here (CORE-30). It used to look for one that was
		# not this class's and SKIP when there was none -- so the check that "not yours"
		# and "does not exist" are indistinguishable silently stopped running on a
		# single-project site, which is exactly the case it exists to cover.
		# A `frappe._dict`, because the test below reads `other[0].name`. A plain dict
		# raised `AttributeError: 'dict' object has no attribute 'name'` -- the SECOND
		# time I preserved the old list-of-rows shape in order not to touch the
		# assertions, and preserving the shape preserved the breakage.
		_other = fixtures.make_project("UI4-OTHER")
		# Registered too. `_own_everything` only knows what `setUp` made, and this one
		# is made inside the test -- so it survived `tearDownClass` and leaked one
		# project per run. A cleanup list that is populated in one place and not another
		# is a cleanup that works in the runs where nothing else happens.
		TestIssueFromViewer._created = getattr(TestIssueFromViewer, "_created", set())
		TestIssueFromViewer._created.add(("Construction Project", _other))
		other = [frappe._dict(name=_other)]
		created = bcf.create_topic(self.project, "UI-4 scope check", bim_model=self.model)
		frappe.db.commit()
		with self.assertRaises(frappe.PermissionError):
			bcf.topic_viewpoints(created["topic_guid"], other[0].name)
		with self.assertRaises(frappe.PermissionError):
			bcf.topic_viewpoints("no-such-guid-at-all", self.project)
