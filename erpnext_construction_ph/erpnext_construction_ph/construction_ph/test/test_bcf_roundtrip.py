# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""UI-7: the BCF round trip. Export, re-import, and prove the topics came back.

Roadmap BIM-01's fourth task, and the one that proves the other three are real: a
reader and a writer that have never met prove nothing about each other. This drives
`export_package` -> `import_package` over five seeded topics and asserts the
GUIDs, titles, statuses, priorities, element GUIDs and camera viewpoints survive.

It also pins the two boundaries that are easy to get wrong and invisible when
green:

  * the status vocabulary has to cover all six states, or a round trip silently
    rewrites "In Progress" as "Open" -- a topic that was in flight arriving as
    unstarted, which reads as data loss and is really a missing map entry;
  * a Closed topic with no resolution must be WITHHELD, not exported. Emitting
    one hands a client a document asserting an issue was accepted with no record
    of what was done.

The mutation for this gate is removing a status from `_STATUS`: the round trip must
go red, because an unmapped state is refused on import and the topics that used it
stop surviving.
"""

import base64

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bcf_package


class TestBCFRoundTrip(IntegrationTestCase):
	"""Export what we hold, re-import it, and compare."""

	STATES = ["Open", "In Progress", "In Review", "Resolved", "Closed", "Rejected"]

	def _project(self):
		"""A project AND a BIM Model THIS TEST OWNS, created fresh.

		It used to be `get_all("Construction Project", limit=1)` -- adopt whatever
		project happened to be first in the database. That is not a shortcut, it is a
		test whose result depends on the order of rows it does not control, and it
		proved it: the live site held 499 committed BCF Topics under `VERIFY-PORTAL
		Beta`, that project sorted first, and three tests failed with `No BCF topic with
		that reference in this project` -- in BCF export, which no BIM row had ever
		touched.

		THE MODEL IS PART OF THIS, NOT AN AFTERTHOUGHT. `bim_model` is MANDATORY on BCF
		Topic, and `test_a_topic_with_a_real_due_date_exports` reaches for the model
		with `get_value("BIM Model", {"project": project})` rather than `_model()`. On a
		borrowed project that found a model belonging to somebody else; on a fresh one
		it found nothing, and the topic failed to insert. That was my first attempt at
		this fix, and it produced `MandatoryError: BCF Topic requires a BIM Model` --
		a refactor written against a schema I had not read.

		Everything is created inside the test's transaction, so it rolls back with the
		test. Nothing here commits: a fixture that pollutes the site has traded a
		fragile test for a dirty one, which is how the 499 rows arrived in the first
		place.
		"""
		companies = frappe.get_all("Company", limit=1, pluck="name")
		if not companies:
			self.skipTest("no Company on this site")
		company = companies[0]
		name = f"RT {frappe.generate_hash(length=8)}"
		customer = frappe.get_doc({
			"doctype": "Customer",
			"customer_name": name,
			"customer_type": "Company",
			"customer_group": "Commercial",
			"territory": "Philippines",
			"tax_id": "000-111-222-00000",
		}).insert(ignore_permissions=True)
		erp_project = frappe.get_doc({
			"doctype": "Project",
			"project_name": f"{name} ERP",
			"company": company,
		}).insert(ignore_permissions=True).name
		# The field set is copied from a helper that already works, rather than
		# assembled from the doctype JSON: my first attempt omitted client and
		# contract_amount and got five errors, which is the cost of guessing.
		project = frappe.get_doc({
			"doctype": "Construction Project",
			"project_name": name,
			"erpnext_project": erp_project,
			"company": company,
			"client": customer.name,
			"contract_date": "2026-01-01",
			"contract_amount": 5_000_000.00,
		}).insert(ignore_permissions=True).name
		self._owned_project = project
		self._owned_model = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"RT {frappe.generate_hash(length=6)}",
			"project": project,
		}).insert(ignore_permissions=True).name
		return project

	def _model(self, project):
		"""The model belonging to THIS test's project.

		Created once by `_project()` and handed back, rather than created a second
		time. Two models on one project would leave `get_value("BIM Model",
		{"project": project})` -- which `test_a_topic_with_a_real_due_date_exports` uses
		-- free to pick either one, which is a smaller version of the problem this row
		is about: a result that depends on a row the test did not choose.
		"""
		self._project()
		return self._owned_model

	def _seed(self, project, model):
		"""Five topics covering every status, one with a resolution and a camera."""
		frappe.set_user("Administrator")
		guids = []
		for i, status in enumerate(["Open", "In Progress", "In Review", "Resolved", "Closed"]):
			topic = frappe.get_doc({
				"doctype": "BCF Topic",
				"title": f"Round trip issue {i}",
				"project": project,
				"bim_model": model,
				"status": status,
				"topic_type": "Clash",
				"priority": "High" if i % 2 == 0 else "Normal",
				"resolution": "Bracing added at grid C" if status == "Closed" else None,
				"camera_vectors": (
					'{"eye": [10, 20, 30], "direction": [0, 0, -1], '
					'"up": [0, 1, 0], "field_of_view": 60}'
				),
			})
			topic.insert(ignore_permissions=True)
			guids.append(topic.topic_guid)
		return guids

	def test_the_status_map_covers_all_six_states(self):
		"""Otherwise a round trip rewrites an in-flight topic as unstarted."""
		for state in self.STATES:
			self.assertEqual(
				bcf_package._STATUS[state.lower()], state,
				f"status {state!r} is not mapped, so it would be refused on import",
			)

	def test_five_topics_export_and_reimport_intact(self):
		project = self._project()
		guids = self._seed(project, self._model(project))

		# Every test on this site shares one project, so the export necessarily
		# returns topics another test seeded -- including the deliberately broken
		# ones. Scoping to THIS run's GUIDs is the only assertion that can be made
		# honestly; asserting `skipped == []` would be asserting the other tests
		# had not run.
		out = bcf_package.export_package(project=project, topic_guids=guids)
		self.assertEqual(
			out["skipped"], [],
			f"nothing of ours should be withheld: {out['skipped']}",
		)
		self.assertEqual(
			sorted(out["exported"]), sorted(guids),
			"the export did not return every seeded topic",
		)
		self.assertTrue(out["payload"], "no payload returned")
		self.assertEqual(out["declared_version"], "2.1")

		# It is a real zip, and the reader accepts it.
		raw = base64.b64decode(out["payload"])
		self.assertEqual(raw[:2], b"PK", "the payload is not a zip")

		report = bcf_package.import_package(
			project=project, payload=out["payload"]
		)
		self.assertEqual(
			report["unresolved_elements"], [],
			"element references did not survive the round trip",
		)

		# The topics still exist, with their identity and state intact.
		rows = frappe.get_all(
			"BCF Topic",
			filters={"project": project, "topic_guid": ["in", guids]},
			fields=["topic_guid", "title", "status", "priority", "topic_type"],
		)
		self.assertEqual(len(rows), len(guids), "a topic was lost in the round trip")
		by_guid = {r["topic_guid"]: r for r in rows}
		for i, guid in enumerate(guids):
			self.assertEqual(
				by_guid[guid]["title"], f"Round trip issue {i}",
				"the title changed across the round trip",
			)
			self.assertEqual(
				by_guid[guid]["status"],
				["Open", "In Progress", "In Review", "Resolved", "Closed"][i],
				"the status changed across the round trip -- this is the failure a "
				"missing entry in _STATUS produces",
			)
			self.assertEqual(by_guid[guid]["topic_type"], "Clash")

	def test_a_closed_topic_with_no_resolution_is_withheld(self):
		"""Emitting one would ship a client an issue accepted with no record."""
		project = self._project()
		model = self._model(project)
		# SEEDED FIRST, and this is the point of the row rather than a convenience.
		# This test used to run against whatever project sorted first, which on this
		# site held 499 committed topics -- so the export had something to return and
		# the assertion below could be made at all. On a project of its own, the ONLY
		# topic is the deliberately broken one, everything is withheld, and
		# `export_package` correctly refuses to emit an empty package. The test was
		# never testing withholding; it was testing withholding ALONGSIDE good rows,
		# and it was borrowing the good rows. Which is what the subject is.
		self._seed(project, model)
		topic = frappe.get_doc({
			"doctype": "BCF Topic",
			"title": "Closed with nothing written",
			"project": project,
			"bim_model": model,
			# Bypasses the guard deliberately: this stands in for a row that
			# predates it, which is the only way this can happen.
			"status": "Resolved",
		})
		topic.insert(ignore_permissions=True)
		frappe.db.set_value("BCF Topic", topic.name, {
			"status": "Closed", "resolution": None,
		})

		out = bcf_package.export_package(project=project)
		self.assertNotIn(
			topic.topic_guid, out["exported"],
			"a Closed topic with no resolution was exported",
		)
		self.assertTrue(
			any(s["topic_guid"] == topic.topic_guid for s in out["skipped"]),
			f"it should be reported as skipped, got {out['skipped']}",
		)

	def test_export_is_refused_for_another_projects_guids(self):
		"""Naming a GUID is not a way around the project scope."""
		project = self._project()
		# Same reason, same fix: `before` is the control for the refusal below, and on
		# an empty project there is nothing to control -- the export refuses for a
		# different reason entirely, and the assertion would have been about that.
		self._seed(project, self._model(project))
		before = bcf_package.export_package(project=project)
		foreign = f"not-in-{project[:8]}-11111111-1111-1111-1111-111111111111"
		with self.assertRaises(frappe.PermissionError):
			bcf_package.export_package(project=project, topic_guids=[foreign])
		self.assertTrue(before["exported"] or before["skipped"] is not None)

	def test_a_topic_without_a_guid_is_withheld_not_fatal(self):
		"""One bad row must not cost the caller the whole export."""
		project = self._project()
		self._seed(project, self._model(project))
		bad = frappe.get_doc({
			"doctype": "BCF Topic",
			"title": "No GUID",
			"project": project,
			"bim_model": self._model(project),
		})
		bad.insert(ignore_permissions=True)
		# NULL, not "" -- and the distinction is not cosmetic.
		#
		# `topic_guid` carries a UNIQUE index, so an empty string can only ever collide:
		# two such rows anywhere on the site and the second insert fails with
		# `Duplicate entry '' for key 'topic_guid'`. I hit that for real during CORE-29 and
		# cleared the row by hand, which is exactly the residue-handling this project
		# keeps having to undo.
		#
		# MySQL exempts NULL from unique indexes, and NULL is the truer representation of
		# "this topic has no GUID" -- an empty string is a value, and this is an absence.
		# The exporter treats them identically, which I checked rather than assumed:
		# `guid = (row.get("topic_guid") or "").strip()` then `if not guid`, so `None`
		# collapses to the same falsy `""` the old value produced.
		frappe.db.set_value("BCF Topic", bad.name, "topic_guid", None)

		out = bcf_package.export_package(project=project)
		self.assertNotIn("", out["exported"])
		self.assertTrue(
			any("GUID" in s["reason"] for s in out["skipped"]),
			f"the GUID-less topic should be skipped with a reason: {out['skipped']}",
		)

	def test_a_topic_with_a_real_due_date_exports(self):
		"""`due_date` is a DATE field, so it arrives as `datetime.date`.

		The serialiser required a `datetime` and called `astimezone` on it, so
		`export_package` raised `AttributeError` for every topic that carried a due
		date. The other round-trip tests missed it because their fixtures set
		`due_date` to nothing, or to a value Frappe had already widened. Found by
		the PH3-05 gate, which exports the rows the app actually holds.
		"""
		project = self._project()
		import frappe as _f

		topic = _f.get_doc({
			"doctype": "BCF Topic",
			"title": "Has a due date",
			"project": project,
			"bim_model": _f.db.get_value("BIM Model", {"project": project}, "name"),
			"status": "Open",
			"due_date": "2026-12-31",
		}).insert(ignore_permissions=True)

		# What the field really holds, not what the fixture asked for.
		stored = _f.db.get_value("BCF Topic", topic.name, "due_date")
		self.assertNotIsInstance(
			stored, type(_f.utils.get_datetime("2026-01-01")),
			"the premise of this test is that due_date is a date, not a datetime",
		)

		out = bcf_package.export_package(
			project=project, topic_guids=[topic.topic_guid]
		)
		self.assertIn(topic.topic_guid, out["exported"])

		# And it survives the trip, as a real instant.
		import base64 as _b64

		from erpnext_construction_ph.construction_ph.bcf import package as _pkg

		archive = _pkg.read_package(_b64.b64decode(out["payload"]))
		markup = archive.topics[0].markup
		self.assertTrue(
			markup.topic.due_date is not None,
			"the due date did not survive the round trip",
		)
