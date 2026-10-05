"""Tests for the area classification and the summary it feeds.

The point of `area_summary` is that a flat total cannot be read as a surface
quantity, and the thing worth pinning down is the classification decision itself --
because a wrong classification is worse than an absent one: it looks authoritative
and silently moves reinforcement into a surface takeoff.

So these assert behaviour a reader depends on, not wiring:
  - a reinforcing bar is REINFORCEMENT, not SURFACE
  - an unrecognised type is UNCLASSIFIED rather than guessed into a total
  - the flat total is never returned without the classification beside it
  - both shares are present, because they do not move together
"""

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_construction_ph.api.bim_frag import area_class, area_summary


def uuid_suffix(n=15):
	"""A short unique run, so every document a test inserts is its own.

	Shared rather than duplicated in each test so a GUID is built the same way
	everywhere, and 15 hex after an 8-character prefix is the 22 characters an IFC
	GlobalId requires.
	"""
	return uuid.uuid4().hex[:n].upper()



class TestAreaClassification(FrappeTestCase):
	def test_reinforcement_is_not_a_surface(self):
		"""A rebar bar has a surface area but is not a built surface."""
		self.assertEqual(area_class("IFCREINFORCINGBAR"), "REINFORCEMENT")
		self.assertEqual(area_class("IFCREINFORCINGMESH"), "REINFORCEMENT")

	def test_surfaces_classify_as_surfaces(self):
		for t in ("IFCWALL", "IFCSLAB", "IFCBEAM", "IFCCOLUMN", "IFCWINDOW", "IFCDOOR"):
			self.assertEqual(area_class(t), "SURFACE", t)

	def test_unrecognised_type_is_not_guessed(self):
		"""An unknown type must not silently join a total.

		A pattern like 'anything with BAR in it' would reclassify a type nobody has
		seen, and a wrong classification is worse than an absent one because it looks
		authoritative. UNCLASSIFIED forces a decision.
		"""
		self.assertEqual(area_class("IFCSOMETHINGNEW"), "UNCLASSIFIED")
		self.assertEqual(area_class(""), "UNCLASSIFIED")
		self.assertEqual(area_class(None), "UNCLASSIFIED")

	def test_case_does_not_decide(self):
		"""A type name differing only in case is the same type."""
		self.assertEqual(area_class("ifcreinforcingbar"), "REINFORCEMENT")
		self.assertEqual(area_class("IfcSlab"), "SURFACE")

	def test_a_type_is_never_both_classes(self):
		for t in ("IFCREINFORCINGBAR", "IFCSLAB", "IFCWALL"):
			self.assertIn(area_class(t), ("REINFORCEMENT", "SURFACE", "UNCLASSIFIED"))
			self.assertNotEqual(area_class(t), "REINFORCEMENT,SURFACE")

	def test_summary_never_returns_a_bare_total(self):
		"""The flat figure must not travel without its classification.

		This is the whole reason the endpoint exists: a bare 22,338 m2 is
		arithmetically right and mixes reinforcement with finished surfaces.
		"""
		model = self._model_with_a_bar_and_a_slab()
		s = area_summary(model.name)

		self.assertIn("all_types_surface_m2", s)
		self.assertTrue(s.get("note"), "a flat total must carry the warning that qualifies it")
		# Every type is classified, and the per-class totals are present.
		for t in s["by_type"]:
			self.assertIn(t["class"], ("REINFORCEMENT", "SURFACE", "UNCLASSIFIED"))
		# This class uses NEITHER `fixtures.make_project` NOR `CommittingCase`, so nothing
		s = area_summary(model.name)

		flat = s["all_types_surface_m2"]
		surface = s["by_class"]["SURFACE"]["surface_m2"]
		reinf = s["by_class"]["REINFORCEMENT"]["surface_m2"]

		self.assertGreater(flat, surface, "the flat total should exceed the surface total")
		self.assertAlmostEqual(flat, surface + reinf, places=1)

	def test_both_shares_are_reported(self):
		"""Rows and area move independently; a reader shown one draws the wrong
		conclusion from it. Rebar is the case that proves it: 41% of the rows and
		1.6% of the area."""
		model = self._model_with_a_bar_and_a_slab()
		s = area_summary(model.name)
		for t in s["by_type"]:
			self.assertIn("share_of_rows", t)
			self.assertIn("share_of_surface", t)

	def test_an_unclassified_type_is_excluded_from_the_surface_subtotal(self):
		"""An unrecognised type must not quietly join the surface total.

		This is the behaviour that matters, and it is a different assertion from
		`area_class` returning UNCLASSIFIED. The next fragment will carry a type nobody
		has listed -- a door, a window, a curtain wall -- and if the summary defaults it
		into SURFACE it reproduces exactly the bug this endpoint exists to prevent: a
		number that looks authoritative and is quietly wrong. So the unknown type's
		area must be absent from the surface subtotal and present as a decision owed.
		"""
		model = self._model_with_a_bar_and_a_slab()
		frappe.get_doc(
			{
				"doctype": "BIM Model GUID",
				"parent": model.name,
				"parenttype": "BIM Model",
				"parentfield": "indexed_guids",
				# A type deliberately not in the list.
				"element_guid": "TESTODD" + uuid_suffix(),
				"element_type": "IFCSOMETHINGUNLISTED",
				"geom_surface_area": 50.0,
				"geom_footprint_area": 50.0,
			}
		).insert(ignore_permissions=True)

		s = area_summary(model.name)

		# It is reported, as a decision still owed.
		self.assertIn("UNCLASSIFIED", s["by_class"])
		self.assertEqual(s["by_class"]["UNCLASSIFIED"]["rows"], 1)
		self.assertEqual(s["by_class"]["UNCLASSIFIED"]["surface_m2"], 50.0)

		# And it is NOT in the surface subtotal, which is the whole point.
		self.assertEqual(s["by_class"]["SURFACE"]["surface_m2"], 99.0)
		self.assertEqual(s["by_class"]["SURFACE"]["rows"], 1)

		# The three classes together account for everything, with nothing lost.
		self.assertAlmostEqual(
			s["by_class"]["SURFACE"]["surface_m2"]
			+ s["by_class"]["REINFORCEMENT"]["surface_m2"]
			+ s["by_class"]["UNCLASSIFIED"]["surface_m2"],
			s["all_types_surface_m2"],
			places=1,
		)


	def _model_with_a_bar_and_a_slab(self):
		"""A tiny model holding one reinforcing bar and one slab.

		Deliberately not a fixture copied from a real fragment: what is being tested
		is the classification arithmetic, and that needs two known values, not 1,502.

		The model name carries a fresh suffix per test. A fixed name made every test
		that needs documents pass on the FIRST suite run and fail on the second with a
		duplicate -- a false green that then turns red for reasons that have nothing to
		do with the code under test.

		The GUIDs are 22 characters, because an IFC GlobalId is 22 and the field
		validates it. A short synthetic GUID would trade a duplicate error for a length
		error and teach nothing about the code.
		"""
		import uuid
		# BIM Model requires a project. Reusing one per test keeps the model unique
		# while the project stays cheap, and a project is shared state so it is
		# created only if absent.
		project = self._project("AREA-CLASS-TEST")


		suffix = uuid.uuid4().hex[:15].upper()
		model = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": "AREA-CLASS-TEST-" + suffix[:8],
				"frag_file": "/private/files/area_class_test_" + suffix[:8] + ".frag",
				"project": project,
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "BIM Model GUID",
				"parent": model.name,
				"parenttype": "BIM Model",
				"parentfield": "indexed_guids",
				# "TESTBAR" + 15 = 22 characters, an IFC GlobalId length.
				"element_guid": "TESTBAR" + suffix,
				"element_type": "IFCREINFORCINGBAR",
				"geom_surface_area": 1.0,
				"geom_footprint_area": 1.0,
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "BIM Model GUID",
				"parent": model.name,
				"parenttype": "BIM Model",
				"parentfield": "indexed_guids",
				# "TESTSLAB" + 14 = 22 characters.
				"element_guid": "TESTSLAB" + suffix[:14],
				"element_type": "IFCSLAB",
				"geom_surface_area": 99.0,
				"geom_footprint_area": 99.0,
			}
		).insert(ignore_permissions=True)
		return model

	@staticmethod
	def _project(name):
		"""A Construction Project, which is what BIM Model.project links to.

		Checked on the field's own `options` rather than assumed from a sibling: in
		this app several `project` fields link to the NATIVE Project while this one
		links to Construction Project, and guessing from the neighbours has been wrong
		before. Reused across tests, so it is created only when absent.
		"""
		existing = frappe.get_all("Construction Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]

		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = name + " Customer"
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

		erp_name = name + " ERP"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		erp_project = (
			hits[0]
			if hits
			else frappe.get_doc(
				{"doctype": "Project", "project_name": erp_name, "company": company}
			)
			.insert(ignore_permissions=True)
			.name
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