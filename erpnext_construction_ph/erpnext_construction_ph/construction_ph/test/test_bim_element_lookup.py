# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH3-02 gate: the server-side half of the BIM workspace element lookup.

**What this claims, and what it does not.** The row is a WebGL viewer where a
user *clicks* an element and the answer comes back. The click needs a `.frag`, and
**that is not claimed here**. This paragraph used to add "and a browser" and to say
`@thatopen` is absent from this bench -- **both false as of 2026-09-28.** A browser
is reachable and capable (`typeof Worker` is a function and
`canvas.getContext("webgl2")` returns a context on ANGLE/NVIDIA RTX 4070 Ti --
`ops/tmp-ph3-01/webgl-probe.json`), and `@thatopen/components@3.4.8` installs in 5s
and is now pinned in `package.json`. The real gap is the one PH3-01 records: no
model reaches `conversion_status = 'Ready'` because no `.frag` converter exists, so
nothing has geometry to click. What is claimed is the part that must be right
for the click to mean anything: given an element GUID carried by the geometry,
the server resolves it against the model's index, and a GUID that is not indexed
does not resolve.

That is the whole substance. A viewer whose lookup returns a plausible row for an
unknown GUID will display an element the model has never heard of, and that
reads as a real pick — so the negative is the load-bearing half.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bim_convert

IFC = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION (('ViewDefinition[ReferenceView_V1.2]'), '2;1');
FILE_NAME ('fixture.ifc', '2026-01-01T00:00:00', (), (), 'authored', $, '');
FILE_SCHEMA (('IFC4'));
ENDSEC;
DATA;
#10 = IFCWALL('2CdEfGhIjKlMnOpQrStUvw', #20, 'North wall', $, #21, $, $, .SOLIDWALL.);
#20 = IFCLOCALPLACEMENT($, #22);
ENDSEC;
END-ISO-10303-21;
"""
KNOWN_GUID = "2CdEfGhIjKlMnOpQrStUvw"
UNKNOWN_GUID = "0zzz0000000000000000"


class TestBimElementLookup(IntegrationTestCase):
	"""An element GUID resolves against the model index, or it does not."""

	def setUp(self):
		self.model = self._model_with_index()

	# ---- the resolution ----------------------------------------------------

	def test_an_indexed_element_guid_resolves_to_its_row(self):
		"""The server-side answer to a click."""
		found = bim_convert.resolve_element(self.model, KNOWN_GUID)
		self.assertEqual(found["element_guid"], KNOWN_GUID)
		self.assertEqual(found["element_type"], "IFCWALL")
		self.assertEqual(found["element_name"], "North wall")
		self.assertEqual(found["model"], self.model)

	def test_listing_the_index_reports_the_element(self):
		"""The workspace page's element list reads the same table."""
		guids = [e["element_guid"] for e in bim_convert.list_elements(self.model)]
		self.assertIn(KNOWN_GUID, guids)

	def test_the_index_actually_came_from_the_ifc(self):
		"""Guards the fixture: an empty index would make the resolve tests vacuous."""
		rows = frappe.get_all("BIM Model GUID", filters={"parent": self.model}, pluck="element_guid")
		self.assertIn(KNOWN_GUID, rows, "the index is empty; the resolve assertions prove nothing")

	# ---- the negative: an unknown GUID must not resolve --------------------

	def test_a_guid_that_is_not_indexed_does_not_resolve(self):
		"""The load-bearing half.

		A viewer that cannot tell "not in the index" from "nothing indexed yet"
		will show an element the model has never heard of, and that reads as a
		real pick.

		THE MECHANISM CHANGED, THE INTENT DID NOT. This used to assert that a
		`ValidationError` is raised, and it did -- which meant the workspace's
		not-in-index branch was UNREACHABLE: the exception produced an HTTP 500,
		`frappe.call`'s success callback never fired, and the page rendered nothing
		where a sentence explaining the disagreement should have been. The state is
		now RETURNED with `not_indexed` set, which is what lets the page name it.

		So the assertion is on the same property the old one protected -- a
		not-indexed guid must not come back looking like a resolved element -- and
		additionally on the flag, because the flag is the thing the page now
		branches on and the thing a future change could quietly drop.
		"""
		result = bim_convert.resolve_element(self.model, UNKNOWN_GUID)
		self.assertTrue(
			result.get("not_indexed"),
			"an unindexed guid must be flagged, not resolved silently",
		)
		self.assertIn("not in this model's index", result.get("reason", ""))
		# The load-bearing half: nothing that would render as a real element.
		for field in ("element_type", "ifc_id", "area", "footprint"):
			self.assertIsNone(
				result.get(field),
				"an unindexed element must not carry %s; that is what makes it read as resolved" % field,
			)
		self.assertNotIn("unknown", result)

	def test_a_guid_from_another_model_does_not_resolve(self):
		"""The index is per model: a GUID valid elsewhere must not resolve here."""
		other = self._model_with_index(name="PH3-02 other model", guid="9Wwx0000000000000000")
		# Same mechanism change as the test above, same reason: a not-indexed guid is a
		# reportable state, not an exception, and the page needs a payload to report it.
		crossed = bim_convert.resolve_element(other, KNOWN_GUID)
		self.assertTrue(
			crossed.get("not_indexed"),
			"a GUID valid in another model must not resolve here",
		)
		self.assertIsNone(crossed.get("element_type"))
		# ...and the original still resolves in its own model.
		self.assertEqual(
			bim_convert.resolve_element(self.model, KNOWN_GUID)["element_guid"], KNOWN_GUID
		)

	def test_an_unknown_model_is_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			bim_convert.resolve_element("NO-SUCH-MODEL", KNOWN_GUID)

	def test_a_user_outside_the_project_cannot_resolve(self):
		"""BCF and the model are both client-visible; same scoping applies."""
		from erpnext_construction_ph.api.portal_data import may_view

		project = frappe.db.get_value("BIM Model", self.model, "project")
		frappe.get_doc(
			{
				"doctype": "User",
				"email": "ph302-outsider@portal.test",
				"first_name": "ph302",
				"enabled": 1,
				"user_type": "System User",
				"send_welcome_email": 0,
				"new_password": frappe.generate_hash(length=12),
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": "ph302-outsider@portal.test",
				"allow": "Project",
				"for_value": frappe.db.get_value("Construction Project", project, "erpnext_project"),
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)

		other = self._other_project_model()
		previous = frappe.session.user
		frappe.set_user("ph302-outsider@portal.test")
		try:
			with self.assertRaises(frappe.PermissionError):
				bim_convert.resolve_element(other, KNOWN_GUID)
		finally:
			frappe.set_user(previous)

	# ---- fixtures ----------------------------------------------------------

	def _model_with_index(self, name="PH3-02 model", guid=KNOWN_GUID):
		"""A BIM Model whose index was built by running the real ingest path.

		`ingest` reads the IFC from an **attached File**, not a local path, so the
		fixture attaches one. Building the index through the real path rather than
		hand-writing rows is the point: a hand-written index would let a broken
		extractor pass.
		"""
		existing = frappe.get_all("BIM Model", filters={"model_name": name}, pluck="name")
		if existing:
			doc = frappe.get_doc("BIM Model", existing[0])
			if frappe.db.count("BIM Model GUID", {"parent": doc.name}):
				return doc.name
		else:
			company = frappe.get_all("Company", limit=1, pluck="name")[0]
			doc = frappe.get_doc(
				{
					"doctype": "BIM Model",
					"model_name": name,
					"project": self._project_for(company, name),
					"conversion_status": "Pending",
					"lod_level": "LOD200",
				}
			).insert(ignore_permissions=True)

		text = IFC if guid == KNOWN_GUID else IFC.replace(KNOWN_GUID, guid)
		self._attach_ifc(doc.name, text)
		bim_convert.ingest(doc.name, converter=lambda _m, _p: "/files/model.frag", enqueue=False)
		doc.reload()
		if doc.conversion_status != "Ready":
			doc.db_set("conversion_status", "Ready")
		# No commit. A commit inside setUp writes the whole test transaction to
		# the gate site -- that is how the ATP series, the EVM fixtures and the
		# BCF topics all became committed residue earlier in this session. The
		# model and its index roll back with the test like everything else.
		return doc.name

	@staticmethod
	def _attach_ifc(model, text):
		# `model` is the NAME, not the Document: it is used as a filter value, and
		# frappe rejects a Document there.
		"""Attach the IFC the way a real upload would, as a File on the model."""
		already = frappe.get_all(
			"File", filters={"attached_to_doctype": "BIM Model", "attached_to_name": model}, pluck="name"
		)
		if already:
			return
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "fixture.ifc",
				"attached_to_doctype": "BIM Model",
				"attached_to_name": model,
				"attached_to_field": "source_file",
				"content": text.encode("utf-8"),
			}
		).insert(ignore_permissions=True)

	def _other_project_model(self):
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		project = self._project_for(company, "PH3-02 other project")
		doc = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": "PH3-02 other project model",
				"project": project,
				# Ready requires a frag_url -- the Phase 0 controller refuses
				# otherwise, which is the guard PH2-05 exercised.
				"conversion_status": "Ready",
				"frag_url": "/files/other.frag",
				"lod_level": "LOD200",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _project_for(self, company, name):
		existing = frappe.get_all("Construction Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]
		customer = f"{name} Customer"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
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
				"contract_amount": 3_000_000.00,
			}
		).insert(ignore_permissions=True).name

	# ---- the schema invariant, which is what CORE-08's gate is about -------

	def test_bim_model_guid_is_a_child_table(self):
		"""**This assertion is the reason CORE-08's negative bites.**

		CORE-08's gate says "`istable` must be 1. Without it Frappe builds
		`BIM Model GUID` as a standalone doctype, the `model_guids` Table field
		cannot accept it". Measured: flipping `istable` to 0 left the whole suite
		green -- 188 tests OK -- because every test here reaches the GUID rows
		through an already-created table, so the schema change is invisible to
		them. A stated negative that no test can fail is a comment, not a gate.

		So the invariant is asserted directly. The mutation that follows is the
		proof: with this test in place, `istable = 0` fails here.
		"""
		self.assertEqual(
			frappe.get_meta("BIM Model GUID").istable, 1,
			"BIM Model GUID must be a child table; a standalone doctype means "
			"model_guids silently stops being a Table field",
		)

	def test_the_guid_table_is_reachable_as_a_child_of_bim_model(self):
		"""And it is genuinely wired to `model_guids`, not merely flagged."""
		field = frappe.get_meta("BIM Model").get_field("model_guids")
		self.assertEqual(field.fieldtype, "Table")
		self.assertEqual(field.options, "BIM Model GUID")
