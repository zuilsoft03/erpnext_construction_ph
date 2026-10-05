"""End-to-end probe: does the pick -> issues path actually join?

Written while hunting for a defect, and kept because it exercises a join that
every other test reaches only in halves. `test_bim_geometry` covers the cache and
`test_bim_viewer_gate` covers authorisation, but nothing asserted the thing the
BIM Workspace page is FOR: that an element GUID the 3D viewer yields, the GUID the
server indexed, and the GUID a BCF topic's child row carries are the SAME string,
and that a topic's element rows survive `list_topics`.

Each half passing is not the join passing. A viewer that shows a wall with no
issues, while reporting honestly that there are none, is the failure this pins.
"""
import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase
from erpnext_construction_ph.api import bim_viewer, bim_convert

IFC = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION (('ViewDefinition[ReferenceView_V1.2]'), '2;1');
FILE_NAME ('probe.ifc', '2026-01-01T00:00:00', (), (), 'authored', $, '');
FILE_SCHEMA (('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('0cyJyJpgz269aC2ZMeIyS8',$,'Probe',$,$,$,$,$,$);
#2=IFCWALL('3bmyaIWCvEHufVx1c33vn0',$,'Wall A',$,$,$,$,$,$);
ENDSEC;
END-ISO-10303-21;
"""
# The GUID the 3D viewer would yield on clicking the wall above.
PICKED_GUID = "3bmyaIWCvEHufVx1c33vn0"


class TestBIMElementIssueJoin(IntegrationTestCase):
	def _model(self):
		# Its own project (CORE-30). It used to adopt whichever sorted first, and
		# skip when there was none, so the test silently stopped running.
		project = fixtures.make_project("JOIN")
		# A REAL attached file. `ingest` reads `source_file` and refuses a model
		# without one -- which is correct, and is why this fixture writes the IFC
		# to disk and attaches it rather than passing text in. A fixture that
		# bypasses the upload path would miss exactly the failure it is meant to
		# catch.
		saved = frappe.get_doc({
			"doctype": "File",
			"file_name": "probe.ifc",
			"attached_to_doctype": "BIM Model",
			"content": IFC,
			"attached_to_name": "probe.ifc",
		}).insert()
		model = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"Join {frappe.generate_hash(length=6)}",
			"project": project,
			"source_file": saved.name,
		}).insert()
		# Re-point the File at the model's real docname, which only exists after
		# the insert.
		frappe.db.set_value("File", saved.name, "attached_to_name", model.name)
		model.reload()
		return project, model

	def test_the_picked_guid_resolves_and_matches_the_bcf_child_row(self):
		project, model = self._model()

		# The PRODUCTION ingest path. An earlier version of this probe called
		# `index_guids` directly and then read the child table, found nothing, and
		# nearly reported it as a bug -- but `index_guids` mutates the in-memory
		# document and its CALLER saves. `ingest` is that caller. A hand-rolled
		# reimplementation of a real call path fails for reasons unrelated to what
		# is under test.
		bim_convert.ingest(
			model.name,
			converter=lambda _m, _p: "/files/probe.frag",
			enqueue=False,
		)

		# 1. The GUID the viewer yields resolves against the index.
		element = bim_convert.resolve_element(model.name, PICKED_GUID)
		self.assertEqual(element["element_guid"], PICKED_GUID)
		self.assertEqual(element["element_type"], "IFCWALL")
		self.assertEqual(element["element_name"], "Wall A")

		# 2. A BCF topic can point at that same indexed element.
		guid_rows = frappe.get_all(
			"BIM Model GUID", filters={"parent": model.name}, pluck="name"
		)
		self.assertTrue(guid_rows, "ingest created no BIM Model GUID rows")
		frappe.get_doc({
			"doctype": "BCF Topic", "topic_guid": "E2E-TOPIC-0000000001",
			"title": "Probe issue", "project": project, "bim_model": model.name,
			"status": "Open",
			"elements": [{"element": guid_rows[0], "ifc_class": "IFCWALL",
						  "global_id": PICKED_GUID}],
		}).insert()

		# 3. list_topics carries the child rows through, so the page can ask
		#    "which issues are on THIS element" without a second round trip.
		out = bim_viewer.list_topics(bim_model=model.name)
		self.assertTrue(out, "BUG: list_topics returned nothing for a topic that exists")
		topic = out[0]
		self.assertIn("elements", topic, "list_topics dropped the child rows")
		guids = [e.get("global_id") for e in topic["elements"]]
		self.assertIn(
			PICKED_GUID, guids,
			"the picked GUID is not among the topic's elements, so the viewer "
			"would show 'no issues' on an element that has one",
		)

	def test_a_topic_with_no_elements_reports_an_empty_list_not_a_missing_key(self):
		"""The page reads `t.elements`, so the key must exist even when empty.

		A missing key is an AttributeError in the browser and an empty panel; an
		empty list is the truthful answer.
		"""
		project, model = self._model()
		bim_convert.ingest(
			model.name,
			converter=lambda _m, _p: "/files/probe.frag",
			enqueue=False,
		)
		frappe.get_doc({
			"doctype": "BCF Topic", "topic_guid": "E2E-TOPIC-0000000002",
			"title": "No elements", "project": project, "bim_model": model.name,
			"status": "Open",
		}).insert()
		out = bim_viewer.list_topics(bim_model=model.name)
		self.assertTrue(out)
		self.assertIn("elements", out[0])
		self.assertEqual(out[0]["elements"], [])
