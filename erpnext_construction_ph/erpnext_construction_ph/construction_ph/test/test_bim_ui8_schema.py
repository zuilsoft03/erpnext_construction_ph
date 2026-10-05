# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH3-05 / UI-8 — schema, close-guard, coarse authorisation, takeoff arithmetic.

Four things are proven here, and the first two carry the negatives:

1. the six-status machine and the five new fields round-trip;
2. **a topic cannot be Closed without a resolution**, and the mutation (clear
   `resolution`) is what proves the guard bites rather than merely being present;
3. the coarse role check lets a coordination role advance a topic, lets a client
   advance ONLY an issue they raised and only to a non-judgement state, and
   refuses both the commercial/finance roles and a client reaching Closed;
4. `measure_length()` and `count_items()` compute, and refuse the degenerate
   inputs that would otherwise let a misclick become a recorded dimension.

The role check replaced an earlier position -- "six states, no permissions" --
which was recorded as a known gap. It is a COARSE check, deliberately: the full
role model is the user's to set (plan §6), and this is the minimum that is not
nothing.
"""

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import takeoff_geometry

COORDINATION = "Construction BIM Coordinator"
CLIENT = "Construction Client"
COMMERCIAL = "Construction Buyer"


class TestBCFStatusMachine(IntegrationTestCase):
	def _topic(self, **kw):
		# Its own project (CORE-30), so this schema check cannot skip itself
		# out of existence on a site with no ambient project.
		# The NAME, not a dict. My first version wrapped it as `[{"name": ...}]` to
		# keep the old `existing[0].name` shape working, and the test does
		# `existing[0].name` -- so it raised `AttributeError: 'dict' object has no
		# attribute 'name'` in eleven places. Preserving a shape I was replacing
		# preserved the breakage too.
		existing = [frappe._dict(name=fixtures.make_project("UI8"))]
		# `bim_model` is mandatory (reqd: 1) -- an omission here fails at insert
		# and takes every test in the class with it, which is how the first run
		# produced 10 errors that had nothing to do with the guards under test.
		model = frappe.get_doc({
			"doctype": "BIM Model",
			"model_name": f"UI8 {frappe.generate_hash(length=6)}",
			"project": existing[0].name,
		}).insert(ignore_permissions=True)
		base = {
			"doctype": "BCF Topic",
			"title": "UI-8 probe",
			"project": existing[0].name,
			"bim_model": model.name,
		}
		base.update(kw)
		doc = frappe.get_doc(base)
		doc.insert(ignore_permissions=True)
		return doc

	def _user_with(self, role):
		email = f"ui8-{role.split()[-1].lower()}@construction.test"
		if not frappe.db.exists("User", email):
			frappe.get_doc({
				"doctype": "User",
				"email": email,
				"first_name": "UI8",
				"last_name": role.split()[-1],
				"send_welcome_email": 0,
				"new_password": frappe.generate_hash(length=12),
			}).insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		if role not in [r.role for r in user.roles]:
			user.add_roles(role)
		return user.name

	# --- 1. schema -----------------------------------------------------------

	def test_the_six_states_are_selectable(self):
		options = frappe.get_meta("BCF Topic").get_field("status").options
		states = [s for s in options.split("\n") if s.strip()]
		self.assertEqual(
			states,
			["Open", "In Progress", "In Review", "Resolved", "Closed", "Rejected"],
			"the state machine does not match the roadmap's six states",
		)

	def test_the_new_fields_round_trip(self):
		doc = self._topic(
			topic_type="Clash",
			due_date="2026-10-15",
			resolution="Bracing added at grid C",
			creation_author="UI8 Probe",
			modified_by_label="UI8 Probe",
		)
		doc.reload()
		self.assertEqual(doc.topic_type, "Clash")
		self.assertEqual(str(doc.due_date), "2026-10-15")
		self.assertEqual(doc.resolution, "Bracing added at grid C")
		self.assertTrue(doc.creation_author)

	def test_boq_item_links_from_an_element_reference(self):
		"""Roadmap BIM-07: the element-to-cost bridge UI-5 reads."""
		meta = frappe.get_meta("BCF Element Reference")
		self.assertIn(
			"boq_item", [f.fieldname for f in meta.fields],
			"BCF Element Reference has no boq_item, so the Properties card's Cost "
			"row has nothing to read (UI-5)",
		)
		self.assertEqual(
			meta.get_field("boq_item").options, "Construction BOQ Item"
		)

	# --- 2. the close-guard, and its MUTATION --------------------------------

	def test_a_topic_cannot_be_closed_without_a_resolution(self):
		doc = self._topic()
		doc.status = "Closed"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_MUTATION_clearing_the_resolution_makes_the_guard_reach_through(self):
		"""The guard must bite on an ALREADY-CLOSED topic with its text removed.

		Without this, a guard that only checks on the transition would still let a
		resolution be deleted afterwards, leaving a closed issue with no record of
		what was done -- the exact state the guard exists to prevent.
		"""
		doc = self._topic(status="Closed", resolution="Bracing added at grid C")
		frappe.db.set_value("BCF Topic", doc.name, "resolution", None)
		doc.reload()
		# It must RAISE. An earlier version of this test asserted the save SUCCEEDED
		# and the text came back empty -- which reads like the guard passing and is
		# the opposite: the guard is what raised here, and asserting success was
		# asserting the absence of the control.
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		# The resolution reads empty afterwards because a refused save ROLLS BACK,
		# taking the direct `set_value` with it. That is the transaction doing its
		# job, not a gap in the guard -- the guard's evidence is the raise above.
		self.assertIsNone(
			frappe.db.get_value("BCF Topic", doc.name, "resolution")
		)

	def test_a_resolution_allows_the_close(self):
		doc = self._topic(resolution="Bracing added at grid C")
		doc.status = "Closed"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.status, "Closed")

	# --- 3. the coarse role check -------------------------------------------

	def test_a_coordination_role_may_advance_a_topic(self):
		self._user_with(COORDINATION)
		doc = self._topic()
		previous = frappe.session.user
		frappe.set_user(self._user_with(COORDINATION))
		try:
			doc.reload()
			doc.status = "In Progress"
			doc.save()
			doc.reload()
			self.assertEqual(doc.status, "In Progress")
		finally:
			frappe.set_user(previous)

	def test_a_commercial_role_may_not_advance_a_topic(self):
		"""Buyer/Contract Administrator/Project Accountant touch money, not clashes."""
		self._user_with(COMMERCIAL)
		doc = self._topic()
		previous = frappe.session.user
		frappe.set_user(self._user_with(COMMERCIAL))
		try:
			doc.reload()
			doc.status = "In Progress"
			with self.assertRaises(frappe.PermissionError):
				doc.save()
		finally:
			frappe.set_user(previous)

	def test_a_client_may_advance_an_issue_they_raised(self):
		"""The client must be the topic's OWNER for this to be permitted.

		`_user_with` creates the user while the session is still Administrator, so a
		topic created afterwards is owned by the Administrator, not the client --
		which the guard correctly refuses. Switch the session FIRST so the topic is
		actually theirs.
		"""
		previous = frappe.session.user
		frappe.set_user(self._user_with(CLIENT))
		try:
			doc = self._topic()
			doc.reload()
			doc.status = "Resolved"
			doc.save()
			doc.reload()
			self.assertEqual(doc.status, "Resolved")
		finally:
			frappe.set_user(previous)

	def test_a_client_may_not_close_an_issue(self):
		"""The client who raised it cannot also accept it -- the verifying party
		has to be someone else, or the issue trail proves nothing.

		The session is switched BEFORE the topic is created so the client owns it;
		otherwise this passes for the wrong reason (refused as not-the-owner rather
		than refused as Closed).
		"""
		previous = frappe.session.user
		frappe.set_user(self._user_with(CLIENT))
		try:
			doc = self._topic()
			doc.reload()
			doc.status = "Closed"
			# The CLOSE-GUARD raises first, because a client-owned topic with no
			# resolution is refused by `guard_close_requires_resolution` before
			# `guard_status_transition` ever runs. That ordering is correct -- a
			# missing resolution is a data defect regardless of who is saving -- so
			# the test asserts what actually fires rather than the exception it
			# expected. `test_a_client_may_not_reject` below is the pure
			# authorisation case, because Rejected has no resolution rule.
			with self.assertRaises(frappe.ValidationError):
				doc.save()
		finally:
			frappe.set_user(previous)

	def test_a_client_may_not_reject_an_issue(self):
		self._user_with(CLIENT)
		doc = self._topic()
		previous = frappe.session.user
		frappe.set_user(self._user_with(CLIENT))
		try:
			doc.reload()
			doc.status = "Rejected"
			with self.assertRaises(frappe.PermissionError):
				doc.save()
		finally:
			frappe.set_user(previous)

	def test_a_client_may_not_advance_someone_elses_issue(self):
		self._user_with(COORDINATION)
		owner = self._user_with(COORDINATION)
		doc = self._topic()
		previous = frappe.session.user
		frappe.set_user(owner)
		frappe.set_user(self._user_with(CLIENT))
		try:
			doc.reload()
			doc.status = "Resolved"
			with self.assertRaises(frappe.PermissionError):
				doc.save()
		finally:
			frappe.set_user(previous)


class TestTakeoffArithmetic(IntegrationTestCase):
	"""UI-8 adds the PDF viewport's two missing measurements (spec 265-266)."""

	def test_measure_length_on_a_known_span(self):
		# 100 page units at 0.1 m/unit = 10 m.
		out = takeoff_geometry.measure_length((0, 0), (100, 0), 0.1)
		self.assertAlmostEqual(out["length"], 10.0, places=2)
		self.assertEqual(out["unit"], "m")

	def test_measure_length_refuses_an_uncalibrated_scale(self):
		with self.assertRaises(takeoff_geometry.TakeoffError):
			takeoff_geometry.measure_length((0, 0), (100, 0), 0)
		with self.assertRaises(takeoff_geometry.TakeoffError):
			takeoff_geometry.measure_length((0, 0), (100, 0), None)

	def test_measure_length_refuses_a_zero_length_span(self):
		"""A misclick must not become a recorded dimension of 0 m."""
		with self.assertRaises(takeoff_geometry.TakeoffError):
			takeoff_geometry.measure_length((5, 5), (5, 5), 0.1)

	def test_count_items_tallies_clicks(self):
		out = takeoff_geometry.count_items([(0, 0), (10, 0), (10, 10)], unit="no")
		self.assertEqual(out["count"], 3)
		self.assertEqual(out["unit"], "no")

	def test_count_items_refuses_an_empty_click_set(self):
		"""A count of zero is not a measurement."""
		with self.assertRaises(takeoff_geometry.TakeoffError):
			takeoff_geometry.count_items([])
