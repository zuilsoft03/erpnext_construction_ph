# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH3-01 gate: the IFC ingest contract this app owns.

Scope, stated up front because the gate is partitioned (GATES.md PH3-01): this
suite proves what *this app* is responsible for -- recognising an IFC by
content, indexing GlobalIds, the status machine, the failure path, and the
storage invariant that geometry bytes never reach MariaDB. It does **not** claim
a `.frag` was encoded; that needs `@thatopen` and this bench has no `node`. The
converter is injected, so the app's own contract is tested for real.

Expected GUIDs are written out literally. Deriving them by calling the extractor
would make the test agree with the implementation by construction, which is the
whole failure mode this project keeps catching.
"""

import base64
import unittest

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bim_convert, bim_frag, bim_viewer

# An authored IFC. Four rooted entities, each with a hand-written 22-character
# GlobalId, plus two instances that are NOT rooted and must be skipped.
FIXTURE_IFC = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION (('ViewDefinition[ReferenceView_V1.2]'), '2;1');
FILE_NAME ('fixture.ifc', '2026-01-01T00:00:00', (), (), 'authored', $, '');
FILE_SCHEMA (('IFC4'));
ENDSEC;
DATA;
#1 = IFCPROJECT('B000000000000000000000', #20, 'Fixture Project', 'A test project', $, $, $, (#30), #21);
#2 = IFCSITE('M000000000000000000000', #20, 'Default Site', $, $, #21, $, $, .ELEMENT., $, $, $, $, #23);
#10 = IFCWALL('X000000000000000000000', #20, 'Wall A', 'North wall', $, #21, $, $, .SOLIDWALL.);
#11 = IFCWALLSTANDARDCASE('i000000000000000000000', #20, 'Wall B', 'South wall', $, #21, $, $, .SOLIDWALL.);
#12 = IFCSLAB('t000000000000000000000', #20, 'Slab 1', 'Ground slab', $, #21, $, $, .FLOOR.);
#20 = IFCLOCALPLACEMENT($, #22);
#21 = IFCAXIS2PLACEMENT3D($, $, $, $, $, $, #23);
#30 = IFCPERSON($, 'Tester', 'A', 'Person', $, $, $, $, $);
ENDSEC;
END-ISO-10303-21;
"""

# Rooted entities, in the order the file lists them. Hand-written, not derived.
EXPECTED_GUIDS = {
	"B000000000000000000000": "IFCPROJECT",
	"M000000000000000000000": "IFCSITE",
	"X000000000000000000000": "IFCWALL",
	"i000000000000000000000": "IFCWALLSTANDARDCASE",
	"t000000000000000000000": "IFCSLAB",
}

NOT_AN_IFC = b"\xff\xd8\xff\xe0JFIF-not-an-ifc-at-all"


class TestBimConvert(IntegrationTestCase):
	"""IFC validation, GUID indexing, the status machine, the storage invariant."""

	# ---- validation by content, not by name -------------------------------

	def test_recognises_an_ifc_by_content(self):
		self.assertTrue(bim_convert.is_ifc(FIXTURE_IFC.encode("utf-8")))

	def test_refuses_a_non_ifc_by_content(self):
		"""A JPEG renamed to `.ifc` is refused. The name is never consulted."""
		self.assertFalse(bim_convert.is_ifc(NOT_AN_IFC))
		self.assertFalse(bim_convert.is_ifc(b""))
		self.assertFalse(bim_convert.is_ifc(None))

	def test_refuses_a_step_file_with_a_non_ifc_schema(self):
		"""ISO-10303-21 alone is not enough -- FILE_SCHEMA must say IFC.

		STEP is the parent format; a CAD export can carry the right header and
		the wrong schema, and treating that as an IFC would fail much later with
		an unrelated message.
		"""
		not_ifc_schema = FIXTURE_IFC.replace("FILE_SCHEMA (('IFC4'));", "FILE_SCHEMA (('AUTOMOTIVE_DESIGN'));")
		self.assertFalse(bim_convert.is_ifc(not_ifc_schema.encode("utf-8")))

	# ---- GUID extraction ---------------------------------------------------

	def test_extracts_exactly_the_rooted_guids(self):
		"""Four walls/slabs/site/project, and nothing that is not rooted."""
		found = bim_convert.extract_guids(FIXTURE_IFC)
		got = {guid: entity for _id, guid, entity, _name in found}
		self.assertEqual(got, EXPECTED_GUIDS)
		self.assertEqual(len(found), len(EXPECTED_GUIDS))

	def test_skips_instances_with_no_guid(self):
		"""IFCLOCALPLACEMENT and IFCPERSON are not rooted and must not appear.

		Both carry a plain string at some position; a shape-only check would
		swallow one of them and produce a GUID that resolves to nothing.
		"""
		guids = {g for _i, g, _e, _n in bim_convert.extract_guids(FIXTURE_IFC)}
		self.assertNotIn("Tester", guids)
		for guid in guids:
			self.assertRegex(guid, r"^[0-9A-Za-z_$]{22}$")

	def test_guid_is_stored_exactly_as_the_file_wrote_it(self):
		"""No re-encoding: the join key for the viewer must round-trip."""
		found = bim_convert.extract_guids(FIXTURE_IFC)
		by_guid = {g: (i, e, n) for i, g, e, n in found}
		ifc_id, entity, name = by_guid["X000000000000000000000"]
		self.assertEqual(ifc_id, 10)
		self.assertEqual(entity, "IFCWALL")
		self.assertEqual(name, "Wall A")

	def test_splits_arguments_without_breaking_on_commas_in_strings(self):
		"""`IFCLABEL('a, b')` must not be split into two arguments."""
		guids = bim_convert.extract_guids(FIXTURE_IFC)
		self.assertEqual(len(guids), 5)
		with_comma = FIXTURE_IFC.replace("'Wall A'", "'Wall A, north'")
		names = {n for _i, _g, _e, n in bim_convert.extract_guids(with_comma)}
		self.assertIn("Wall A, north", names)

	# ---- the pipeline ------------------------------------------------------

	def test_ingest_reaches_ready_with_urls_and_indexed_guids(self):
		"""Happy path: Ready, a .frag URL, and the GUIDs in the child table."""
		model = self._model_with_ifc()
		result = bim_convert.ingest(
			model.name, converter=self._stub_converter("/files/model.frag"), enqueue=False
		)

		self.assertEqual(result.conversion_status, "Ready")
		self.assertEqual(result.frag_url, "/files/model.frag")
		self.assertTrue(result.converted_on)
		stored = {r.element_guid for r in result.model_guids}
		self.assertEqual(stored, set(EXPECTED_GUIDS))

	def test_a_failed_conversion_is_failed_and_never_ready(self):
		"""The negative the gate names: no URL, no Ready, reason recorded."""
		model = self._model_with_ifc()

		result = bim_convert.ingest(model.name, converter=self._failing_converter, enqueue=False)
		self.assertEqual(result.conversion_status, "Failed")
		self.assertFalse((result.frag_url or "").strip())
		self.assertIn("converter unavailable", result.remarks)

	def test_a_converter_returning_no_url_fails_rather_than_pretending(self):
		"""An empty .frag URL must not leave a model looking converted."""
		model = self._model_with_ifc()
		result = bim_convert.ingest(model.name, converter=lambda _m, _p: "   ", enqueue=False)
		self.assertEqual(result.conversion_status, "Failed")
		self.assertFalse((result.frag_url or "").strip())

	def test_a_non_ifc_is_refused_by_name(self):
		"""The error names the file, so the user knows which upload was wrong."""
		model = self._model_with_bytes(NOT_AN_IFC, "renamed-photo.ifc")
		result = bim_convert.ingest(
			model.name, converter=self._stub_converter("/files/model.frag"), enqueue=False
		)
		self.assertEqual(result.conversion_status, "Failed")
		self.assertIn("renamed-photo.ifc", result.remarks)
		self.assertIn("ISO-10303-21", result.remarks)

	def test_the_default_converter_fails_loudly(self):
		"""No converter here must be a named failure, never a silent success.

		The failure is pinned to the REAL wall, and to the absence of the two claims
		that were once wrong. This used to assert "no node runtime", which was true
		when written and stopped being true once node v24.12.0 was found under `nvm`
		in the frontend container -- a message that outlived its fact sends the next
		person after a runtime that has been sitting there for hours.
		"""
		model = self._model_with_ifc()
		result = bim_convert.ingest(model.name, enqueue=False)
		self.assertEqual(result.conversion_status, "Failed")

		remarks = result.remarks.lower()
		# The wall as measured: the fragments processor needs a WebGL context, which
		# an RQ worker in Node 24 does not have.
		self.assertIn("webgl", remarks)
		# And it must not blame the two things that are resolved and pinned. These two
		# negative assertions are what stop the message rotting back.
		self.assertIn("not a missing node runtime", remarks)
		self.assertIn("not a missing @thatopen package", remarks)

	# ---- the storage invariant --------------------------------------------

	def test_geometry_bytes_never_reach_mariadb(self):
		"""The IFC text is nowhere in the BIM Model row.

		The point of converting to `.frag` is that the heavy bytes live in object
	 storage. A single blob column on the model would defeat the entire row.
		"""
		model = self._model_with_ifc()
		result = bim_convert.ingest(
			model.name, converter=self._stub_converter("/files/model.frag"), enqueue=False
		)

		for field in frappe.get_meta("BIM Model").fields:
			value = result.get(field.fieldname)
			if isinstance(value, (bytes, bytearray)):
				self.fail(f"BIM Model.{field.fieldname} holds raw bytes")
			if isinstance(value, str):
				self.assertNotIn("IFCWALLSTANDARDCASE", value, f"IFC text leaked into {field.fieldname}")
		self.assertEqual(result.conversion_status, "Ready")
		# The identifiers, and only the identifiers, are persisted.
		self.assertEqual(len(result.model_guids), 5)
		for row in result.model_guids:
			self.assertLess(len(row.element_guid), 64)

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _failing_converter(_model, _path):
		"""A converter that cannot run here -- the default converter's situation."""
		raise bim_convert.ConversionError("converter unavailable in this environment")

	@staticmethod
	def _stub_converter(url):
		"""A converter that does not encode anything, and says so.

		It exists so the app's own contract can be tested without claiming a
		`.frag` was produced. It is called with the model and the source path.
		"""

		def _convert(_model, _path):
			return url

		return _convert

	def _any_project(self):
		"""A Construction Project, created if the site has none.

		This used to be a bare `frappe.db.get_value("Construction Project", ...)`,
		which returns None on a clean site -- and a BIM Model with no project then
		fails mandatory validation. Found by running CORE-01's own authored
		negative (uninstall, reinstall), after which every Construction Project is
		gone and 11 tests failed. Depending on a row another test committed is a
		fixture that passes without testing anything.
		"""
		import uuid

		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		existing = frappe.get_all("Construction Project", filters={"company": company}, pluck="name")
		if existing:
			return existing[0]
		customer = "Fixture Customer " + uuid.uuid4().hex[:6]
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer,
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		).insert(ignore_permissions=True)
		erp_project = frappe.get_doc(
			{"doctype": "Project", "project_name": "Fixture ERP " + uuid.uuid4().hex[:6], "company": company}
		).insert(ignore_permissions=True).name
		return frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": "Fixture Project " + uuid.uuid4().hex[:6],
				"erpnext_project": erp_project,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 1000000.00,
			}
		).insert(ignore_permissions=True).name

	def _model_with_bytes(self, payload, filename):
		import uuid

		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		project = self._any_project()
		model = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": f"{filename} {uuid.uuid4().hex[:8]}",
				"project": project,
				"lod_level": "LOD200",
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": filename,
				"attached_to_doctype": "BIM Model",
				"attached_to_name": model.name,
				"attached_to_field": "source_file",
				"content": payload,
			}
		).insert(ignore_permissions=True)
		model.reload()
		# A File row outlives the per-test rollback (it is written through to
		# disk), so it leaks into later classes and eventually breaks an unrelated
		# gate. Remove it explicitly rather than trusting the transaction.
		attached = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "BIM Model", "attached_to_name": model.name},
			pluck="name",
		)
		self.addCleanup(self._remove_files, attached)
		return model

	@staticmethod
	def _remove_files(names):
		for name in names or []:
			try:
				frappe.delete_doc("File", name, force=True, ignore_permissions=True)
			except Exception:
				pass
		# No commit: an explicit one here would push the whole test transaction out
		# as real rows, the same leak removed from project-view.py.

	def _model_with_ifc(self):
		return self._model_with_bytes(FIXTURE_IFC.encode("utf-8"), "authored-fixture.ifc")

	# ---- the ASYNC pipeline: what Phase 3 actually scopes -------------------

	def test_ingest_queues_rather_than_converting_inline(self):
		"""A model handed to `ingest` lands on `Queued`, not `Ready`.

		The old shape converted inline, so a 100MB model held a web worker for
		the length of the conversion -- which is the phase gate's own risk. This is
		what makes the pipeline asynchronous: the request returns before any
		encoding happens.
		"""
		enqueued = []
		original = frappe.enqueue

		def _capture(*args, **kwargs):
			enqueued.append((args, kwargs))

		frappe.enqueue = _capture
		try:
			model = self._model_with_ifc()
			result = bim_convert.ingest(model.name, converter=self._stub_converter("/files/model.frag"))
		finally:
			frappe.enqueue = original

		self.assertEqual(result.conversion_status, "Queued")
		self.assertFalse(result.frag_url, "a queued model must not carry a .frag URL")
		self.assertEqual(len(enqueued), 1, "the conversion was not handed to a worker")

	# ---- the worker itself -------------------------------------------------

	def test_the_worker_lands_a_queued_model_on_ready(self):
		"""**The boundary, asserted on both sides of it.**

		`ingest` must leave the model `Queued` with no `.frag` URL, and ONLY
		`convert_queued` may set `Ready`. Asserting only the end state would let
		an `ingest` that still converts inline pass this test -- which is the
		whole point of the change, so it is the one thing worth pinning.
		"""
		original = frappe.enqueue
		frappe.enqueue = lambda *a, **k: None          # queued, not run
		try:
			model = self._model_with_ifc()
			queued = bim_convert.ingest(
				model.name, converter=self._stub_converter("/files/model.frag")
			)
		finally:
			frappe.enqueue = original

		self.assertEqual(queued.conversion_status, "Queued")
		self.assertFalse(queued.frag_url, "ingest must not produce a .frag URL")
		self.assertFalse(queued.converted_on, "ingest must not stamp a conversion time")

		# Only now does the worker run, and only now may it say Ready.
		done = bim_convert.convert_queued(model.name, converter=self._stub_converter("/files/model.frag"))
		self.assertEqual(done.conversion_status, "Ready")
		self.assertEqual(done.frag_url, "/files/model.frag")
		self.assertTrue(done.converted_on)
		self.assertIn("elements indexed", done.remarks or "")

	def test_an_enqueue_failure_lands_on_failed_not_stranded(self):
		"""**The one failure the async split introduces.**

		The queue can be down, or the broker can refuse the job. Without handling
		it the model sits at `Queued` forever, which is this module's own
		contract violated in its worst form: a model that claims to be waiting
		for work that will never arrive, with nobody alerted.
		"""
		def _refuse(*_a, **_k):
			raise RuntimeError("queue is down")

		original = frappe.enqueue
		frappe.enqueue = _refuse
		try:
			model = self._model_with_ifc()
			result = bim_convert.ingest(
				model.name, converter=self._stub_converter("/files/model.frag")
			)
		finally:
			frappe.enqueue = original

		self.assertEqual(result.conversion_status, "Failed", "a model must not be stranded at Queued")
		self.assertFalse(result.frag_url)
		self.assertIn("queue is down", result.remarks or "")

	def test_a_worker_failure_lands_on_failed_not_ready(self):
		"""The async path must not be weaker than the sync one."""
		model = self._model_with_ifc()
		bim_convert.ingest(model.name, converter=self._failing_converter, enqueue=False)
		done = bim_convert.convert_queued(model.name, converter=self._failing_converter)
		self.assertEqual(done.conversion_status, "Failed")
		self.assertEqual(done.frag_url, "")

	def test_a_queued_model_is_visible_as_queued_not_pretending(self):
		"""A stranded `Queued` is a real state of an async pipeline.

		The queue can be down or a worker killed mid-job, and then a model sits
		at `Queued` with a stale `modified`. It must be readable as queued rather
		than looking converted -- the same class of bug as an empty ledger
		reading as "no billings yet".
		"""
		# The enqueue is CAPTURED rather than run: the point of this test is the
		# state a model is left in when no worker ever picks it up, and
		# `enqueue=False` runs the worker inline, which lands it on Ready.
		original = frappe.enqueue
		frappe.enqueue = lambda *a, **k: None
		try:
			model = self._model_with_ifc()
			queued = bim_convert.ingest(
				model.name, converter=self._stub_converter("/f.frag")
			)
		finally:
			frappe.enqueue = original

		self.assertEqual(queued.conversion_status, "Queued")
		self.assertFalse(queued.converted_on, "a queued model must not carry a conversion time")
		self.assertFalse(queued.frag_url, "a queued model must not carry a .frag URL")

	def test_the_worker_is_entered_with_the_model_name_alone(self):
		"""The enqueue passes the name, so a worker must run on a fresh document.

		Passing a serialised Document would carry a stale field set into the
		worker, which is how a `frag_url` cleared in the request comes back set.
		"""
		import inspect

		sig = inspect.getfullargspec(bim_convert.convert_queued).args
		self.assertEqual(sig[0], "model_name")


class TestFragStreamingForClients(fixtures.CommittingCase, IntegrationTestCase):
	# DECLARED, and the declaration is the fix rather than the base class.
	#
	# Being a `CommittingCase` was NOT enough: the default `committing_doctypes` is empty,
	# so the mixin snapshotted only the ambient doctypes, and this class's OWN projects,
	# customers, models and topics were never in the snapshot -- so `tearDownClass` had
	# nothing to reclaim. They come from this class's own `_make_project`, not from
	# `fixtures.make_project`, so they were not in `MADE_BY_TESTS` either. Two different
	# registries, and this class's rows were in neither.
	#
	# `File` is deliberately absent: it IS the geometry, and a File row must never be
	# reclaimed by another class.
	committing_doctypes = (
		"Construction Project", "Project", "Customer", "BCF Topic", "BIM Model",
	)
	"""`stream_frag` is the ONLY path that carries geometry off the server, so its
	refusal is the whole control.

	**Why this endpoint exists at all.** The portal's read-only viewer had a
	surface, a model identity and a role gate, and could not load geometry: the
	payload handed the browser `/private/files/bim-frag-SCHOOL-STR.frag`, and a
	`Construction Client` entitled to that very model got **403** on it. Frappe's
	`/private/files` authorisation requires READ on `File` and on the doctype the
	file hangs off, and a client role has neither. The 403 was FILE DELIVERY, not
	WebGL, and no amount of rendering work would have changed it.

	So the bytes come back through a whitelisted method that asks
	`_require_model_access` -- the same check `get_frag_url` and `store_frag` use --
	and these tests hold that one check shut.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext_construction_ph.api import bim_frag

		# IT BUILDS ITS OWN FIXTURES, which is the whole of CORE-30.
		#
		# It used to be `get_value("Construction Project", {"name": "VERIFY-PORTAL
		# Beta"})` plus a Ready model in it, plus a Ready model in some OTHER project.
		# Three ambient dependencies, and the second one had already broken: after I
		# removed nine orphaned models, `other_model` came back empty and
		# `test_a_caller_not_entitled_to_the_model_is_refused` -- the authorisation guard
		# that is the entire REASON this endpoint exists -- called `skipTest`. The suite
		# reported OK with its most important guard not running.
		#
		# A skip is the quietest false green there is. It reads as a pass, it survives
		# re-runs, and it disappears only when somebody reads the output closely. So the
		# fixtures are built here, and the two projects are genuinely different, which
		# is what makes the entitlement test mean anything.
		from erpnext_construction_ph.api import bim_frag

		# The bytes are assigned BEFORE the models, because `store_frag` consumes them.
		# My first version had the assignment last, so `_make_ready_model` reached for an
		# attribute that did not exist yet -- and the failure would have been an
		# AttributeError inside a fixture, which reads as a broken test rather than a
		# broken ordering.
		cls.frag_b64 = base64.b64encode(b"\x00\x01\x02fragment-bytes-for-the-test").decode()
		# A REQUEST, because `store_frag` builds a `/private/files/...` URL and reads
		# `request.host` to do it. The borrowed fixture had its fragment stored long ago,
		# so this path had never run inside a test and the omission was invisible until
		# the model was actually built here: `AttributeError: 'R' object has no attribute
		# 'host'`. The existing tests build their own `R` with only cookies and headers,
		# which is enough for the READ path and not for the WRITE path.
		cls._previous_request = getattr(frappe.local, "request", None)
		frappe.local.request = type("R", (), {"cookies": {}, "headers": {}, "host": "http://test.localhost"})()
		cls.project = cls._make_project("ALPHA")
		cls.other_project = cls._make_project("BETA")
		cls.model = cls._make_ready_model(cls.project, "ALPHA")
		cls.other_model = cls._make_ready_model(cls.other_project, "BETA")
		# COMMIT THE CLASS FIXTURES, because the harness rolls back between tests.
		#
		# MEASURED, in stages: `store_frag` works (the File row is present by file_url, by
		# file_name and attached to the model, the moment after it is called), the class
		# creates its own model and fragment, and `stream_frag` still cannot find the
		# fragment when the test runs. So the row exists at `setUpClass` and is gone by the
		# test body: the class-level rows are being rolled back before the first
		# assertion.
		#
		# I chased this through two wrong theories first -- that `ALWAYS_RECLAIMED` held
		# `File`, and then that a per-class `committing_doctypes` did. **Both were removed
		# and BOTH were real hazards**, but neither was the cause; and having ruled out the
		# first one I wrote it down as "disproven" while a second pathway was still open.
		# A theory is disproven when every mechanism for it is excluded.
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		try:
			models = getattr(cls, "_created_models", set())
			if models:
				frappe.db.delete(
					"File",
					{
						"attached_to_doctype": "BIM Model",
						"attached_to_name": ("in", tuple(models)),
					},
				)
				frappe.db.commit()
		finally:
			cls._created_models = set()
			super().tearDownClass()

	@classmethod
	def _make_project(cls, tag):
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			raise unittest.SkipTest("no Company on this site")
		company = company[0]
		name = f"CORE30 {tag} {frappe.generate_hash(length=6)}"
		customer = frappe.get_doc({
			"doctype": "Customer", "customer_name": name, "customer_type": "Company",
			"customer_group": "Commercial", "territory": "Philippines",
			"tax_id": "000-111-222-00000",
		}).insert(ignore_permissions=True)
		erp = frappe.get_doc({
			"doctype": "Project", "project_name": f"{name} ERP", "company": company,
		}).insert(ignore_permissions=True).name
		return frappe.get_doc({
			"doctype": "Construction Project", "project_name": name,
			"erpnext_project": erp, "company": company, "client": customer.name,
			"contract_date": "2026-01-01", "contract_amount": 5_000_000.00,
		}).insert(ignore_permissions=True).name

	@classmethod
	def _make_ready_model(cls, project, tag):
		"""A model in `Ready` with a REAL fragment behind it, via the supported path."""
		from erpnext_construction_ph.api import bim_frag

		model = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"CORE30 {tag} {frappe.generate_hash(length=6)}",
			"project": project,
		}).insert(ignore_permissions=True).name
		bim_frag.store_frag(model, cls.frag_b64)
		cls._created_models = getattr(cls, "_created_models", set())
		cls._created_models.add(model)
		return model

	def test_an_administrator_gets_the_bytes_and_not_a_url(self):
		"""The bytes are what the client needs; the File URL was never reachable."""
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()
		from erpnext_construction_ph.api import bim_frag

		bim_frag.stream_frag(self.model)
		content = frappe.local.response.get("filecontent")
		self.assertTrue(
			content, "stream_frag returned no filecontent, so the client gets an empty body"
		)
		self.assertEqual(frappe.local.response.get("type"), "download")

	def test_the_same_bytes_stored_twice_still_stream(self):
		"""THE CONTENT-DEDUPE BUG, pinned. This is the regression that matters.

		`stream_frag` used to find its File with

		    file_url LIKE %cache_path(model)

		But Frappe's `save_file` DEDUPES BY CONTENT: store identical bytes twice and it
		reuses the existing File row -- re-attaching it to the new model, updating
		`file_name` and `attached_to_*`, but LEAVING `file_url` as it was. `file_url` is
		retained from whichever row first stored these bytes, so it can disagree with the
		same row's own `file_name`. Both are model-name-derived, which is what makes this
		easy to miss: they look like the same kind of value and one is stale. The LIKE
		against `cache_path` can therefore never match:

		    file_name    'bim-frag-CORE30-ALPHA-d913be.frag'   <- model-derived, correct
		    file_url     '.../bim-frag-CORE30-ALPHA-b2beb9.frag'  <- retained from another row
		    MATCHES LIKE  False

		It failed SILENTLY — the row was present and correctly attached, the query
		returned nothing, and the error read "the cached fragment for this model is
		missing", which is false.

		Why this test rather than relying on the other one: the existing method passes
		only because the class happens to store the same bytes on every run, so it
		fires by luck of the fixture. **A test that only passes because of what a
		previous run left behind is not pinning the behaviour.** So this one makes the
		dedupe explicit — two models, byte-identical fragments, both must stream.

		It is a PRODUCTION bug, not a test one: it fires for any model whose bytes have
		been stored before, which is the ordinary case for a re-conversion.
		"""
		from erpnext_construction_ph.api import bim_frag

		frappe.set_user("Administrator")
		frappe.local.request = type(
			"R", (), {"cookies": {}, "headers": {}, "host": "http://test.localhost"}
		)()

		third = self._make_ready_model(self.project, "GAMMA")
		fourth = self._make_ready_model(self.project, "DELTA")

		# The premise, asserted PER MODEL rather than as a total.
		#
		# A total (`len(urls) == 2`) is also satisfied by one File on one model and none on
		# the other -- a different failure entirely, which the assertion would then report
		# as a lookup problem. Each model has to own its own row, or the two assertions
		# below prove nothing.
		#
		# Being exact about why this matters. The reused row KEEPS the `file_url` it was
		# first saved with, so two models given identical bytes end up sharing ONE url
		# while their `file_name`s -- derived from their own model names -- differ. That
		# divergence IS the bug, and the premise is only real if both halves hold.
		urls_by_model = {
			m: frappe.get_all(
				"File",
				filters={"attached_to_doctype": "BIM Model", "attached_to_name": m},
				pluck="file_url",
			)
			for m in (third, fourth)
		}
		for m, urls in urls_by_model.items():
			self.assertEqual(len(urls), 1, f"{m} must own exactly one File row")
		self.assertEqual(
			urls_by_model[third][0],
			urls_by_model[fourth][0],
			"the premise: identical bytes must leave both rows sharing ONE retained file_url",
		)

		for model in (third, fourth):
			frappe.local.response = frappe._dict()
			bim_frag.stream_frag(model)
			self.assertTrue(
				frappe.local.response.get("filecontent"),
				f"stream_frag served nothing for {model}, whose File was stored before",
			)

	def test_a_caller_not_entitled_to_the_model_is_refused(self):
		"""The lower bound, and the reason the endpoint exists at all.

		Without this clause the endpoint would serve ANY model's geometry to ANY
		session, which is a strictly worse version of the `/private/files` 403 it
		replaces.
		"""
		frappe.set_user("Administrator")
		# `host` is here because the previous test in this class left a request WITHOUT
		# one, and Frappe's download path reads `request.host` to build the filename.
		# Every test here shares `frappe.local`, so a request one of them installs is the
		# request the next one runs against -- which is the same class of coupling this
		# row exists to remove, one object wide.
		frappe.local.request = type("R", (), {"cookies": {}, "headers": {}, "host": "http://test.localhost"})()
		from erpnext_construction_ph.api import bim_frag
		from erpnext_construction_ph.api.portal_data import may_view_bim

		# A user with no entitlement to the OTHER model's project.
		outsider = "ph304-frag-outsider@portal.test"
		if not frappe.db.exists("User", outsider):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": outsider,
					"first_name": "Frag",
					"send_welcome_email": 0,
					"user_type": "Website User",
					"roles": [{"role": "Construction Client"}],
				}
			).insert(ignore_permissions=True)
		previous = frappe.session.user
		try:
			frappe.set_user(outsider)
			self.assertFalse(
				may_view_bim(outsider, frappe.db.get_value("BIM Model", self.other_model, "project")),
				"the fixture user is unexpectedly entitled; the test would prove nothing",
			)
			with self.assertRaises(frappe.PermissionError):
				bim_frag.stream_frag(self.other_model)
		finally:
			frappe.set_user(previous)

	def test_a_model_with_no_cache_is_refused_rather_than_served_empty(self):
		"""An empty 200 is the worst outcome: the viewer mounts and shows nothing."""
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()
		from erpnext_construction_ph.api import bim_frag

		# IT CREATES THE MODEL, and that is the whole of CORE-30.
		#
		# It used to be `get_value("BIM Model", {"conversion_status": "Pending"})` --
		# take whichever Pending model happened to be first. That made the result depend
		# on the site's contents, and it failed here for a reason that had nothing to do
		# with what it claims to test: it picked an ORPHANED model whose project had been
		# deleted, so `_require_model_access` raised `PermissionError` where the test
		# asserts `DoesNotExistError`. A refusal, but the wrong one -- and a test that
		# passes or fails on the residue of somebody else's run is not testing the guard.
		#
		# Nine such orphans existed. Deleting them made this class green, which is
		# exactly the mistake the row exists to prevent: a clean database is not a fix.
		empty = self._pending_model()
		with self.assertRaises(frappe.DoesNotExistError):
			bim_frag.stream_frag(empty)

	def _pending_model(self):
		"""A model THIS test created, in `Pending`, with no cached fragment.

		`Pending` with no `.frag` behind it is precisely the state under test, and it is
		produced here rather than found. Built on the class's own project, so
		`_require_model_access` passes and the ONLY refusal that can reach the assertion
		is the missing cache.
		"""
		if getattr(self, "_owned_pending", None):
			return self._owned_pending
		self._owned_pending = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"Pending {frappe.generate_hash(length=8)}",
			"project": self.project,
			"conversion_status": "Pending",
		}).insert(ignore_permissions=True).name
		return self._owned_pending


class TestFragmentCacheHonesty(IntegrationTestCase):
	"""A model whose .frag is gone must not keep reading as `Ready`.

	`bim_frag`'s own docstring names this as one of its three jobs -- "keep
	`conversion_status` honest, so a model with no cache reads as 'not yet converted'
	rather than 'Ready' with nothing behind it" -- and it was not doing it. The
	workspace's model DROPDOWN reads that field, so the two models on the gate site
	that sat at `Ready` with `file_size_bytes: 0` were offered to a user who then
	got a blank canvas and the message "The cached fragment is missing".
	"""

	def test_a_missing_cache_downgrades_ready_to_stale(self):
		frappe.set_user("Administrator")
		# It creates its own project rather than adopting whichever is first -- the
		# same defect CORE-30 removed from the class above. This one had not failed
		# YET, which is the dangerous part: it borrows a project whose state it does not
		# control, so it passes or fails on whatever the site happens to hold, and
		# "it has always been green" is not evidence that it is sound.
		project = self._own_project()

		model = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"HONESTY {frappe.generate_hash(length=6)}",
			"project": project,
		}).insert(ignore_permissions=True)
		# A stale frag_url with no file behind it -- exactly the state a cleaned cache
		# leaves behind.
		frappe.db.set_value(
			"BIM Model", model.name,
			{"frag_url": "/private/files/gone.frag", "conversion_status": "Ready",
			 "file_size_bytes": 0},
			update_modified=False,
		)
		# NO commit. A commit here defeats the harness's rollback, so this test left a
		# `Ready` model with no cache behind it on every run -- and PH3-09's gate
		# rejects precisely that. The gate was passing only because the leftovers were
		# deleted by hand, which made it pass once and go red on re-run.

		out = bim_frag.get_frag_url(model=model.name)
		self.assertFalse(out["available"], "a model with no cache reported itself available")
		self.assertEqual(out["reason"], "cache_missing")

		self.addCleanup(
			frappe.db.set_value, "BIM Model", model.name, "conversion_status", "Pending",
			update_modified=False,
		)
		self.addCleanup(frappe.delete_doc, "BIM Model", model.name, force=True,
                       ignore_permissions=True)

		after = frappe.db.get_value("BIM Model", model.name, "conversion_status")
		self.assertNotEqual(
			after, "Ready",
			"the status still says Ready with nothing behind it, so the dropdown "
			"offers a model the viewer cannot load",
		)

		# The DROPDOWN path, not just the probe path. `get_frag_url` correcting the
		# status is LAZY -- it only runs for a model somebody actually opens -- and
		# the workspace's model dropdown reads `conversion_status` straight off
		# `list_models` and never probes. A model nobody opened would keep saying
		# "Ready" with nothing behind it, which is the case the fix exists for.
		frappe.db.set_value("BIM Model", model.name, "conversion_status", "Ready",
                            update_modified=False)
		listed = {m["name"]: m for m in bim_viewer.list_models(project=project)}
		self.assertIn(model.name, listed, "the dropdown could not see the model at all")
		self.assertNotEqual(
			listed[model.name]["conversion_status"], "Ready",
			"the dropdown still shows Ready for a model with no fragment behind it, "
			"so the reconciliation only protects a model somebody happens to open",
		)

		# The correction must be a value the FIELD CAN REPRESENT. `frappe.db.set_value`
		# bypasses Select validation, so a status the options do not list would pass
		# every assertion above and still hand the dropdown something the field
		# cannot hold -- the same class of defect, one level down.
		options = (frappe.get_meta("BIM Model").get_options("conversion_status") or "").split("\n")
		self.assertIn(
			after, [o.strip() for o in options if o.strip()],
			f"{after!r} is not one of conversion_status's options {options}, so the "
			"dropdown is handed a value the field cannot represent",
		)

	def _own_project(self):
		"""A Construction Project THIS test created, for a model it also created."""
		if getattr(self, "_owned_project", None):
			return self._owned_project
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			self.skipTest("no Company on this site")
		company = company[0]
		name = f"HONESTY {frappe.generate_hash(length=8)}"
		customer = frappe.get_doc({
			"doctype": "Customer", "customer_name": name, "customer_type": "Company",
			"customer_group": "Commercial", "territory": "Philippines",
			"tax_id": "000-111-222-00000",
		}).insert(ignore_permissions=True)
		erp = frappe.get_doc({
			"doctype": "Project", "project_name": f"{name} ERP", "company": company,
		}).insert(ignore_permissions=True).name
		# The field set is copied from a helper that already works rather than assembled
		# from the doctype JSON: my first attempt omitted client and contract_amount and
		# MandatoryError named four fields.
		self._owned_project = frappe.get_doc({
			"doctype": "Construction Project",
			"project_name": name,
			"erpnext_project": erp,
			"company": company,
			"client": customer.name,
			"contract_date": "2026-01-01",
			"contract_amount": 5_000_000.00,
		}).insert(ignore_permissions=True).name
		return self._owned_project
