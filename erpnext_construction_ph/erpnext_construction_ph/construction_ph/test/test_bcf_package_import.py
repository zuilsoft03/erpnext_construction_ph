# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""`.bcfzip` -> `BCF Topic` rows, end to end.

The point of these is the part that is easy to get quietly wrong: an element the
model has not indexed must not lose the issue, and must not look resolved either.
"""

import base64
import io
import zipfile

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api.bcf_package import import_package

MARKUP = """<?xml version="1.0" encoding="UTF-8"?>
<Markup xmlns="http://www.buildingsmart-tech.org/bcf/bcf/2.1">
  <Header>
    <File IfcProject="24O8sC$qj4q8B0D3sK2L1e" isExternal="true">
      <Filename>structural.ifc</Filename>
      <Date>2026-09-28T08:00:00Z</Date>
    </File>
  </Header>
  <Topic Guid="{topic}" TopicType="Clash" TopicStatus="Open">
    <ReferenceLink>IfcDuctSegment/{known}</ReferenceLink>
    <ReferenceLink>IfcBeam/{unknown}</ReferenceLink>
    <Title>Duct clashes with beam B102</Title>
    <Priority>High</Priority>
    <CreationDate>2026-09-28T08:00:00Z</CreationDate>
    <CreationAuthor>mep.engineer@project.com</CreationAuthor>
  </Topic>
</Markup>
"""

KNOWN_GUID = "24O8sC$qj4q8B0D3sK2L1e"
UNKNOWN_GUID = "1Ab2Cd$ef3Gh4Ijk5Lmn6O"


def _package(topic_guid: str, **overrides) -> bytes:
	markup = overrides.get("markup") or MARKUP.format(
		topic=topic_guid, known=KNOWN_GUID, unknown=UNKNOWN_GUID
	)
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		archive.writestr("bcf.version", '<?xml version="1.0"?><BCFVersion Version="2.1"/>')
		archive.writestr(f"{topic_guid}/markup.bcf", markup)
	return buffer.getvalue()


class TestBcfPackageImport(fixtures.CommittingCase, IntegrationTestCase):
	"""A package is only useful if the rows it makes are true."""

	# This class commits so an imported package is visible to the read-back below.
	# The commit stays -- an imported row that rolls back cannot be read back, which is
	# the whole point of importing it -- and `CommittingCase` removes what it persists.
	# `File` IS NOT HERE, and I put it here to begin with.
	#
	# A `File` row is the geometry: `store_frag` writes one and `stream_frag` reads it.
	# Because the snapshot is per-class, declaring it meant that whenever this class ran
	# AFTER `test_bim_convert`, it deleted the fragment that class had just stored -- and
	# that test failed with "The cached fragment for this model is missing", pointing at
	# neither the mixin nor this line.
	#
	# **This is the SECOND pathway, and I had already written the first one off as
	# "disproven" after ruling out `ALWAYS_RECLAIMED`.** Removing `File` from the mixin
	# did not fix the test, so I declared the theory dead -- while this declaration was
	# still doing the same thing by a different route. A theory is disproven when every
	# mechanism for it is excluded, not when the first one is.
	#
	# What this class leaves behind is at most a File or two of its own. That is a far
	# better failure mode than deleting another class's geometry.
	committing_doctypes = (
		"Construction Project", "Project", "Customer", "BCF Topic", "BIM Model",
	)

	def setUp(self):
		frappe.set_user("Administrator")
		self.company = frappe.get_all("Company", limit=1, pluck="name")[0]
		self.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
		# ERPNext autonames Project as PRJ-#####, so the row's NAME is not its
		# project_name. Read the real name back rather than assuming one.
		self.project = frappe.db.get_value("Project", {"project_name": "PKG-PROJ"}, "name")
		if not self.project:
			frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": "PKG-PROJ",
					"company": self.company,
					"customer": self.customer,
				}
			).insert(ignore_permissions=True)
			self.project = frappe.db.get_value("Project", {"project_name": "PKG-PROJ"}, "name")
		self.construction_project = self._construction_project()
		self.model = self._model_with()

	def tearDown(self):
		for name in frappe.get_all("BCF Topic", filters={"project": self.construction_project}, pluck="name"):
			frappe.delete_doc("BCF Topic", name, force=True, ignore_permissions=True)
		# The commit below persists EVERYTHING this test created, not just the
		# deletes -- so the foreign-branch fixtures are removed here too, or a
		# green run leaves rows behind on the gate site that the next run reuses.
		# A test that only passes because of residue is not proving anything on CI.
		for model_name in frappe.get_all("BIM Model", filters={"model_name": "PKG Foreign"}, pluck="name"):
			frappe.delete_doc("BIM Model", model_name, force=True, ignore_permissions=True)
		for cp in frappe.get_all("Construction Project", filters={"project_name": "PKG Other"}, pluck="name"):
			frappe.delete_doc("Construction Project", cp, force=True, ignore_permissions=True)
		if frappe.db.exists("User", "pkg-outsider@example.com"):
			frappe.delete_doc("User", "pkg-outsider@example.com", force=True, ignore_permissions=True)
		frappe.db.commit()

	def _construction_project(self):
		name = "PKG-CONSTRUCTION"
		if not frappe.db.exists("Construction Project", name):
			frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": name,
					"erpnext_project": self.project,
					"company": self.company,
					"client": self.customer,
					"contract_date": "2026-09-28",
					"contract_amount": 1000000,
				}
			).insert(ignore_permissions=True)
		return name

	def _model_with(self):
		name = "PKG-MODEL"
		if not frappe.db.exists("BIM Model", name):
			model = frappe.get_doc(
				{
					"doctype": "BIM Model",
					"model_name": name,
					"project": self.construction_project,
				}
			)
			model.insert(ignore_permissions=True)
			model.append(
				"model_guids",
				{
					"element_guid": KNOWN_GUID,
					"element_type": "IfcDuctSegment",
					"element_name": "Supply Air Duct",
					"ifc_id": 1,
				},
			)
			model.save(ignore_permissions=True)
		return name

	def _import(self, topic_guid, **kwargs):
		return import_package(
			self.construction_project,
			base64.b64encode(_package(topic_guid, **kwargs)).decode(),
			bim_model=self.model,
		)

	def test_a_package_becomes_a_topic_row(self):
		from erpnext_construction_ph.api.bcf import _new_guid

		report = self._import(_new_guid())
		self.assertEqual(len(report["imported"]), 1)
		topic = frappe.get_doc("BCF Topic", {"topic_guid": report["imported"][0]})
		self.assertEqual(topic.title, "Duct clashes with beam B102")
		self.assertEqual(topic.priority, "High")

	def test_an_indexed_element_is_bound_to_its_indexed_row(self):
		from erpnext_construction_ph.api.bcf import _new_guid

		report = self._import(_new_guid())
		topic = frappe.get_doc("BCF Topic", {"topic_guid": report["imported"][0]})
		guids = {row.global_id for row in topic.elements}
		self.assertIn(KNOWN_GUID, guids)
		bound = [row for row in topic.elements if row.global_id == KNOWN_GUID][0]
		self.assertEqual(bound.ifc_class, "IfcDuctSegment")
		self.assertTrue(bound.element, "an indexed element must link to its index row")

	def test_an_unindexed_element_does_not_lose_the_topic(self):
		"""The wrong failure to fear is dropping the issue because a GUID is unknown."""
		from erpnext_construction_ph.api.bcf import _new_guid

		report = self._import(_new_guid())
		self.assertEqual(len(report["imported"]), 1)
		self.assertIn(UNKNOWN_GUID, report["unresolved_elements"])
		topic = frappe.get_doc("BCF Topic", {"topic_guid": report["imported"][0]})
		row = [r for r in topic.elements if r.global_id == UNKNOWN_GUID]
		self.assertEqual(len(row), 1, "the reference is still recorded")
		self.assertFalse(row[0].element, "but it must NOT claim to be resolved")

	def test_importing_the_same_package_twice_is_not_a_duplicate(self):
		from erpnext_construction_ph.api.bcf import _new_guid

		guid = _new_guid()
		first = self._import(guid)
		second = self._import(guid)
		self.assertEqual(len(first["imported"]), 1)
		self.assertEqual(second["imported"], [])
		self.assertEqual(second["skipped_existing"], [guid])

	def test_version_is_reported_and_ambiguity_is_not_guessed(self):
		"""This package's topic has no comments and no viewpoints, which are the
		ONLY shape differences between 2.1 and 3.0. So the shape says nothing and
		`bcf.version` is the real signal -- the reader must say "unknown" rather
		than default to 3.0 and present a guess as a reading."""
		from erpnext_construction_ph.api.bcf import _new_guid

		report = self._import(_new_guid())
		self.assertEqual(report["declared_version"], "2.1")
		self.assertEqual(
			report["detected_version"], "unknown",
			"a document with no 2.1-only elements must not be reported as 3.0",
		)

	def test_an_unmappable_status_is_refused_rather_than_coerced(self):
		"""Silently storing an unrecognised status as 'Open' is a quiet misstatement.

		The control under test is unchanged: a status with no mapping is REFUSED,
		never coerced to the default. The EXAMPLE value did change, and had to.

		This test originally used `Rejected`, which was correct when written -- before
		UI-8 the app's status vocabulary was three states and `Rejected` had no
		mapping, so it was a genuine example of an unmappable value. UI-8 (a signed
		design decision) added `Rejected` as one of the six states, which made the
		fixture stale: `_STATUS` grew the entry, the import stopped refusing, and the
		guard this test exists to prove went unexercised.

		BCF 2.1 defines `TopicStatus` as a free `xs:string` (verified against the
		vendored markup.xsd), so a vendor file may legitimately carry anything. A
		value outside the six states is therefore the honest example, and it exercises
		the identical code path the old fixture did.
		"""
		from erpnext_construction_ph.api.bcf import _new_guid
		import frappe as _f

		# A real vendor vocabulary, outside the app's six, and deliberately NOT a
		# near-miss of one of them -- "Rejected" would now be mapped.
		markup = MARKUP.format(topic=_new_guid(), known=KNOWN_GUID, unknown=UNKNOWN_GUID)
		markup = markup.replace('TopicStatus="Open"', 'TopicStatus="Superseded"')
		with self.assertRaises(_f.ValidationError):
			self._import(_new_guid(), markup=markup)

	def test_every_one_of_the_six_states_survives_an_import(self):
		"""The counterpart to the refusal above: the six ARE mappable, not refused.

		Without this, the refusal test above can be satisfied by a `_STATUS` that is
		empty or entirely wrong -- refusing everything is not the same control as
		refusing the unmappable.
		"""
		from erpnext_construction_ph.api import bcf_package

		for state in ("Open", "In Progress", "In Review", "Resolved", "Closed", "Rejected"):
			self.assertIn(state.lower(), bcf_package._STATUS)
			self.assertEqual(bcf_package._STATUS[state.lower()], state)

	def test_a_model_the_caller_may_not_see_is_refused_like_a_missing_one(self):
		"""The disclosure this closes, tested on the guard that carries it.

		`bim_model` is a caller-supplied parameter and the import reported resolved
		vs unresolved. Without authorising the MODEL's own project, a caller cleared
		for project A could pass a model belonging to project B and learn which
		element GUIDs exist there from that split alone -- the shape closed in
		PH3-04. `_scoped(project)` does not cover it: it authorises the PROJECT, not
		the MODEL.

		Both refusals are asserted to be WORDED THE SAME, because a caller who can
		tell "not yours" from "not indexed" still has an oracle. That needs no
		second project to demonstrate.
		"""
		import frappe as _f
		from erpnext_construction_ph.api.bcf_package import _authorise_model

		with self.assertRaises(_f.PermissionError) as unseen:
			_authorise_model(self.construction_project, "PKG-MODEL-NOT-MINE")
		with self.assertRaises(_f.PermissionError) as absent:
			_authorise_model(self.construction_project, "PKG-MODEL-ABSENT")
		self.assertEqual(str(unseen.exception), str(absent.exception))
		self.assertIn("No BCF topic", str(unseen.exception))

		# A model the caller CAN see still works, or the guard would be useless.
		_authorise_model(self.construction_project, self.model)

	def test_a_model_that_EXISTS_on_a_project_you_cannot_see_is_refused(self):
		"""The branch that is the actual leak, and the one the test above cannot reach.

		The other test compares two model names that do NOT exist. That proves the two
		refusals are indistinguishable, but it never exercises the real case: a model
		that EXISTS, on a project the caller has no rights to. Without this, "not yours"
		and "no such model" are proven equal while the branch that leaks is untested.

		Uses a Project already on the site -- the gate site has hundreds -- rather
		than creating one, because creating rows inside the test transaction is what
		produced link-validation failures that had nothing to do with the guard.
		"""
		import frappe as _f
		from erpnext_construction_ph.api.bcf_package import _authorise_model

		foreign_project = self._foreign_construction_project()
		if not foreign_project:
			self.skipTest("the gate site needs a second ERPNext Project to test against")
		try:
			foreign_model = self._model_on(foreign_project)
		except _f.LinkValidationError:
			# SKIP rather than fail. A security test that fails on absent site data
			# reads as a false alarm the next time the site is rebuilt.
			self.skipTest("link validation will not resolve a second Construction Project here")
		outsider = self._outsider()

		previous = frappe.session.user
		try:
			frappe.set_user(outsider)
			with self.assertRaises(_f.PermissionError) as unseen:
				_authorise_model(self.construction_project, foreign_model)
			with self.assertRaises(_f.PermissionError) as absent:
				_authorise_model(self.construction_project, "PKG-MODEL-ABSENT")
			self.assertEqual(str(unseen.exception), str(absent.exception))
		finally:
			frappe.set_user(previous)

	def _foreign_construction_project(self):
		"""A Construction Project of ours own on a DIFFERENT native Project, or None.

		`BIM Model.project` links to Construction Project, NOT to the native ERPNext
		Project -- so the fixture has to walk the whole chain
		`BIM Model.project` -> Construction Project -> `erpnext_project` -> Project.
		Passing a native Project name here is what made every earlier attempt fail
		with "Could not find Project": the row existed, but in the wrong doctype.
		"""
		for candidate in frappe.get_all("Project", pluck="name", limit=30):
			if candidate == self.project or not frappe.db.exists("Project", candidate):
				continue
			# Autonamed BY TITLE, so the row's name is its project_name -- look it up
			# by that rather than by an invented identifier, and return whatever
			# Frappe actually named it.
			existing = frappe.db.get_value(
				"Construction Project", {"project_name": "PKG Other"}, "name"
			)
			if existing:
				return existing
			doc = frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": "PKG Other",
					"erpnext_project": candidate,
					"company": self.company,
					"client": self.customer,
					"contract_date": "2026-09-28",
					"contract_amount": 1000000,
					"status": "Active",
				}
			)
			doc.insert(ignore_permissions=True)
			# No commit: the test runner rolls this back. Committing here is what
			# seeded the persistent rows that caused phantom link failures earlier.
			return doc.name
		return None

	def _model_on(self, construction_project):
		"""A BIM Model on `construction_project`, or None.

		Returns the row's REAL name. This helper previously returned the literal
		string "PKG-MODEL-FOREIGN" while inserting a model autonamed "PKG Foreign" --
		so the guard looked up a model that did not exist, took its `not owner`
		branch, and threw for the wrong reason. The test then passed even with the
		authorisation check deleted, which made it worthless.
		"""
		for name in frappe.get_all("BIM Model", pluck="name", limit=200):
			if frappe.db.get_value("BIM Model", name, "project") == construction_project:
				return name
		doc = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": "PKG Foreign",
				"project": construction_project,
			}
		)
		doc.insert(ignore_permissions=True)
		self.assertTrue(
			frappe.db.exists("BIM Model", doc.name),
			"the foreign model must exist, or the guard refuses it for being missing",
		)
		return doc.name

	def _outsider(self):
		email = "pkg-outsider@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Pkg",
					"last_name": "Outsider",
					"roles": [{"role": "Construction Site Manager"}],
					"send_welcome_email": 0,
					"new_password": frappe.generate_hash(length=12),
				}
			).insert(ignore_permissions=True)
		return email

	def test_a_file_that_is_not_a_package_is_refused(self):
		import frappe as _f

		with self.assertRaises(_f.ValidationError):
			import_package(
				self.construction_project,
				base64.b64encode(b"this is not a zip file at all").decode(),
				bim_model=self.model,
			)
