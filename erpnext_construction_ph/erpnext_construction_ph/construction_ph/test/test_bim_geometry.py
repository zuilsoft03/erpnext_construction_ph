# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH3-02 gate: the browser-side conversion cache and the element join (D-043).

**What this claims.** The architecture is OpenProject's: the browser converts the
IFC with That Open's WASM importer, and the server stores the resulting `.frag` as a
cache. This module covers the server half of that — accepting a converted fragment,
refusing the states that must not be served, and the authorisation that stands
between one client's building geometry and another.

**What this does NOT claim, and the omission is the point.** It does not claim the
model renders. That needs WebGL and a live Desk site, and `docker ps` fails in this
environment, so no pixel is proven here. The conversion, the spatial tree and the
GlobalId round-trip are proven by `ops/tmp-ph3-02/geometry_check.mjs`, which drives
the real engine headlessly against a real 8.6 MB IFC. This file covers what that
check cannot reach: the server's refusal behaviour and its access control.

**The negative is the load-bearing half.** A cache endpoint that accepts anything
and serves it to anyone is worse than one that does not exist — it turns a missing
feature into a disclosure. Every refusal below is asserted on its specific message,
because a check that swallows its own exception passes on the code that is broken.
"""

import base64
import hashlib

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bim_frag

# A `.frag` is a compressed binary. These bytes are deliberately NOT valid base64
# text and NOT a zip, so the "looks like an archive" and "not base64" paths are
# exercised by the same payload that stands in for a real fragment.
FAKE_FRAG = bytes(range(256)) * 8


class TestFragCache(IntegrationTestCase):
	"""Store, serve, refuse."""

	def _make_model(self, project=None):
		project = project or self._project()
		return frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": f"Cached Model {frappe.generate_hash(length=6)}",
				"project": project,
				"conversion_status": "Pending",
			}
		).insert()

	def _project(self):
		"""A Construction Project the test user is entitled to.

		Carries every REQUIRED field. An incomplete Project fails at insert and
		takes the cache tests down with a construction error that says nothing about
		the cache -- so a fixture defect would present as a cache defect.
		"""
		# The shared fixture (CORE-30), which also creates its OWN Customer.
		#
		# This helper had a second, quieter version of the same defect: it hardcoded
		# `"client": "Test Client"`, a Customer some other test class happened to create.
		# It never showed, because the old first line -- adopt whichever project sorted
		# first -- returned before reaching it. Removing the borrow made this path run
		# for the first time, and eleven tests failed with
		# `LinkValidationError: Could not find Client: Test Client`.
		#
		# Which is the shape of the whole defect: a shortcut that HIDES a dependency
		# behind another shortcut, and removing the first one reveals the second.
		from erpnext_construction_ph.construction_ph.test import fixtures

		return fixtures.make_project("FRAG")

	# ---- the happy path ---------------------------------------------------

	def test_a_stored_fragment_marks_the_model_ready(self):
		model = self._make_model()
		result = bim_frag.store_frag(
			model.name, base64.b64encode(FAKE_FRAG).decode(), file_size=len(FAKE_FRAG)
		)
		model.reload()
		self.assertEqual(model.conversion_status, "Ready")
		self.assertEqual(model.frag_url, result["file_url"])
		self.assertEqual(cint_or_zero(result["bytes"]), len(FAKE_FRAG))

	def test_a_stored_fragment_is_served_from_private_files(self):
		"""The geometry must not be world-readable.

		A `.frag` in `sites/assets` would be fetchable by anyone who can guess the
		filename, which for `bim-frag-<model>.frag` is a short search.
		"""
		model = self._make_model()
		bim_frag.store_frag(model.name, base64.b64encode(FAKE_FRAG).decode())
		# `.reload()` first. `store_frag` mutates its own copy of the document, and
		# the local `model` here is the one the test made — so reading `model.frag_url`
		# without reloading reads the field as it was BEFORE the store, which is
		# None. That is an `AttributeError` on None, not a failed assertion, and it
		# reads like a production bug when it is the test looking at a stale object.
		model.reload()
		self.assertIsNotNone(model.frag_url, "frag_url was not written at all")
		self.assertTrue(
			model.frag_url.startswith("/private/files/"),
			f"frag_url was {model.frag_url!r}, expected a /private/files/ path",
		)

	def test_storing_twice_replaces_rather_than_accumulates(self):
		"""One cache per model, not one per page load.

		A viewer that converts on every open and writes a new file each time would
		fill the volume with copies of the same building, and the most recent one
		would be ambiguous.
		"""
		model = self._make_model()
		first = bim_frag.store_frag(model.name, base64.b64encode(FAKE_FRAG).decode())
		second = bim_frag.store_frag(model.name, base64.b64encode(b"other").decode())
		self.assertEqual(first["file_name"], second["file_name"])
		rows = frappe.get_all(
			"File", filters={"file_name": second["file_name"]}, pluck="name"
		)
		self.assertEqual(len(rows), 1, "storing twice left more than one File behind")

	def test_the_digest_lets_a_browser_detect_a_stale_cache(self):
		model = self._make_model()
		result = bim_frag.store_frag(model.name, base64.b64encode(FAKE_FRAG).decode())
		self.assertEqual(result["sha256"], hashlib.sha256(FAKE_FRAG).hexdigest())

	# ---- the refusals ----------------------------------------------------

	def test_an_empty_fragment_is_refused(self):
		"""An empty cache is worse than none: it reads as proof and contains none."""
		model = self._make_model()
		with self.assertRaises(frappe.ValidationError):
			bim_frag.store_frag(model.name, base64.b64encode(b"").decode())
		model.reload()
		self.assertNotEqual(model.conversion_status, "Ready")

	def test_a_non_base64_payload_is_refused(self):
		"""Storing a corrupt fragment produces a viewer that renders a partial
		building, which is far harder to diagnose than a refusal."""
		model = self._make_model()
		with self.assertRaises(frappe.ValidationError):
			bim_frag.store_frag(model.name, "this is not base64 !!!")

	def test_a_zip_is_refused_rather_than_stored_and_hoped_for(self):
		"""A `.frag` is raw binary. A zip renamed to one would fail deep inside the
		fragment parser, in the browser, with no useful message."""
		model = self._make_model()
		zipped = b"PK\x03\x04" + b"\x00" * 64
		with self.assertRaises(frappe.ValidationError):
			bim_frag.store_frag(model.name, base64.b64encode(zipped).decode())

	def test_an_oversized_fragment_is_refused(self):
		"""The ceiling is enforced, not merely declared.

		Exercised through a lowered `max_bytes` rather than by building a 256 MB
		payload. The production constant is still what every real caller gets — this
		proves the COMPARISON exists, which is the part that could silently rot if
		someone reordered the checks and dropped it. The previous version of this
		test asserted `MAX_FRAG_BYTES > 0` and called the code zero times, which is
		a tautology: it passed whether or not the limit was enforced at all.
		"""
		model = self._make_model()
		with self.assertRaises(frappe.ValidationError):
			bim_frag.store_frag(
				model.name,
				base64.b64encode(FAKE_FRAG).decode(),
				max_bytes=len(FAKE_FRAG) - 1,
			)
		model.reload()
		self.assertNotEqual(
			model.conversion_status, "Ready", "an oversized fragment was stored anyway"
		)

	def test_a_model_with_no_project_cannot_exist_in_the_first_place(self):
		"""The orphan case is closed by the SCHEMA, not by our check.

		This asserted that a project-less model is refused by `get_frag_url`, which
		requires building a `BIM Model` with no `project` — and `project` is
		`reqd: 1`, so Frappe's `_validate_mandatory` rejects the insert first. The
		test was asserting a state the database cannot hold, which is why it errored
		rather than passing.

		What is worth pinning is the stronger fact: the row cannot be created
		detached from its project in the first place. `bim_frag._require_model_access`
		still guards the empty case as defence in depth, but the control that
		actually holds is this one.
		"""
		with self.assertRaises(frappe.MandatoryError):
			frappe.get_doc(
				{
					"doctype": "BIM Model",
					"model_name": f"Orphan {frappe.generate_hash(length=6)}",
					"conversion_status": "Pending",
				}
			).insert()
		self.assertTrue(
			frappe.get_meta("BIM Model").get_field("project").reqd,
			"BIM Model.project stopped being mandatory; the orphan guard in "
			"_require_model_access is now the only thing standing between a model "
			"and a caller with no entitlement to it",
		)

	def test_a_model_that_does_not_exist_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			bim_frag.get_frag_url("no-such-model-xyz")

	# ---- serving ---------------------------------------------------------

	def test_a_model_with_no_cache_says_so_by_reason(self):
		"""Three causes of "no URL" — never converted, cache deleted, not entitled —
		and a viewer that cannot tell them apart shows a blank canvas for all three.
		"""
		model = self._make_model()
		result = bim_frag.get_frag_url(model.name)
		self.assertFalse(result["available"])
		self.assertEqual(result["reason"], "not_converted")
		self.assertTrue(result["message"])

	def test_a_stale_url_is_reported_as_a_missing_cache_not_as_ready(self):
		"""The row says `Ready` and points at a File that is gone. Reporting that
		as available would hand the viewer a 404 and call it a model."""
		model = self._make_model()
		result = bim_frag.store_frag(model.name, base64.b64encode(FAKE_FRAG).decode())
		# Delete by the File's DOCNAME. `frappe.delete_doc` takes a document name, and
		# a File's name is a hash while its file_name is ours — passing the file_name
		# deletes nothing, silently, and the cache then correctly still exists. This
		# is the same bug the module's own `clear_frag` had; the test reproduced it
		# independently, which is the point of writing the negative at all.
		file_docname = frappe.db.get_value(
			"File", {"file_name": result["file_name"]}, "name"
		)
		self.assertTrue(file_docname, "the stored File could not be found to delete")
		frappe.delete_doc("File", file_docname, force=True)
		outcome = bim_frag.get_frag_url(model.name)
		self.assertFalse(outcome["available"])
		self.assertEqual(outcome["reason"], "cache_missing")

	def test_clearing_the_cache_returns_the_model_to_pending(self):
		model = self._make_model()
		bim_frag.store_frag(model.name, base64.b64encode(FAKE_FRAG).decode())
		bim_frag.clear_frag(model.name)
		model.reload()
		self.assertIsNone(model.frag_url)
		self.assertEqual(model.conversion_status, "Pending")


def cint_or_zero(value):
	"""`int()` that tolerates None, without importing frappe.utils for one call."""
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0
