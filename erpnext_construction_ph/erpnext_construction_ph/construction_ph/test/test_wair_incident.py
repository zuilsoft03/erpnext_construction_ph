# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-10 gate: the 24-hour LTI notification clock.

A statutory deadline is only useful if it cannot be moved by accident, so the
tests are weighted towards that: the deadline is stamped from `occurred_at`, it
survives an edit, and a future occurrence is refused rather than producing a
deadline later than the incident.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime

from erpnext_construction_ph.construction_ph.doctype.wair_incident.wair_incident import (
	NOTIFICATION_WINDOW_HOURS,
	mark_notified,
)


class TestWairIncident(IntegrationTestCase):
	"""The deadline is occurrence-anchored, immutable, and correctly overdue."""

	def test_the_deadline_is_occurrence_plus_24_hours(self):
		"""Hand-computed, not derived from the implementation."""
		incident = self._incident(hours_ago=2)
		expected = add_to_date(get_datetime(incident.occurred_at), hours=24)
		self.assertEqual(
			get_datetime(incident.notification_deadline).replace(microsecond=0),
			get_datetime(expected).replace(microsecond=0),
		)
		self.assertEqual(NOTIFICATION_WINDOW_HOURS, 24)

	def test_a_fresh_incident_is_pending_with_time_remaining(self):
		incident = self._incident(hours_ago=2)
		self.assertEqual(incident.notification_status, "Pending")
		# 2 hours elapsed of 24, so about 22 remain.
		self.assertGreater(incident.hours_remaining, 21.0)
		self.assertLess(incident.hours_remaining, 23.0)

	def test_an_incident_older_than_24_hours_is_overdue(self):
		"""The case the rule exists to catch: reported late must read as breached."""
		incident = self._incident(hours_ago=30)
		self.assertEqual(incident.notification_status, "Overdue")
		self.assertLess(incident.hours_remaining, 0)

	def test_an_edit_does_not_move_the_deadline(self):
		"""The load-bearing property. A statutory clock must be immutable.

		Deriving the deadline from `modified` would let a typo fix silently
		extend a notification window, and nothing in the record would show it.
		"""
		incident = self._incident(hours_ago=5)
		deadline = get_datetime(incident.notification_deadline)

		incident.reload()
		incident.remarks = "edited after the fact"
		incident.save()
		incident.reload()

		# Assert against the HAND-COMPUTED value, not against the value captured
		# before the edit. Comparing the deadline to itself proves only that two
		# reads agree: a mutation that recomputed it from `now` on every save was
		# green here, because both reads landed in the same second.
		self.assertEqual(
			get_datetime(incident.notification_deadline).replace(microsecond=0),
			get_datetime(add_to_date(get_datetime(incident.occurred_at), hours=24)).replace(microsecond=0),
			"an edit moved a statutory notification deadline away from occurred_at + 24h",
		)

	def test_a_future_occurrence_is_refused(self):
		"""A deadline later than the incident is nonsense."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self._incident(hours_ago=-3)
		self.assertIn("future", str(caught.exception).lower())

	def test_marking_notified_flips_the_status_and_keeps_the_deadline(self):
		incident = self._incident(hours_ago=2)
		deadline = get_datetime(incident.notification_deadline)
		result = mark_notified(incident.name, sti_reference="SIRC-2026-0001")
		self.assertEqual(result["notification_status"], "Notified")

		incident.reload()
		self.assertEqual(incident.sti_reference, "SIRC-2026-0001")
		self.assertTrue(incident.notified_at)
		self.assertEqual(
			get_datetime(incident.notification_deadline).replace(microsecond=0),
			deadline.replace(microsecond=0),
		)

	def test_the_project_name_is_denormalised_for_the_report(self):
		"""A report issued later must still name the project as it stood."""
		incident = self._incident(hours_ago=1)
		self.assertEqual(incident.project_name, "CORE10 Project")

	def test_a_classification_round_trips(self):
		"""The loss-time record the statutory report reads."""
		incident = self._incident(hours_ago=1, lti_classification="Temporary Total Disability")
		self.assertEqual(incident.lti_classification, "Temporary Total Disability")

	# ---- CORE-12: unclassified is NOT the same as "no lost time" -----------

	def test_unclassified_is_distinguishable_from_no_lost_time(self):
		"""The distinction the gate names, and the defect it prevents.

		Collapsing an empty classification into "No lost time" would let a
		report compute zero lost days for an incident nobody has assessed, and
		the filing would look complete. Empty means *not yet classified*.
		"""
		unclassified = self._incident(hours_ago=1)
		self.assertIn(unclassified.lti_classification, (None, ""))
		self.assertNotEqual(
			frappe.as_unicode(unclassified.lti_classification or "").strip(),
			"No lost time",
		)

		assessed = self._incident(hours_ago=1, lti_classification="No lost time")
		self.assertEqual(assessed.lti_classification, "No lost time")
		self.assertNotEqual(unclassified.lti_classification, assessed.lti_classification)

	def test_the_declared_options_separate_the_two_states(self):
		"""'No lost time' is a positive value; blank is the default.

		Checked against the declared JSON rather than the runtime meta, because
		`get_meta` merges Custom Fields and would pass on an option set that was
		never declared.
		"""
		import json
		import os

		# Derive the path from the module this test already imports, never from
		# `__file__` arithmetic. Path arithmetic has bitten this project six times;
		# the module's own location is layout-independent and cannot drift.
		from erpnext_construction_ph.construction_ph.doctype.wair_incident import (
			wair_incident as _module,
		)

		path = os.path.join(os.path.dirname(_module.__file__), "wair_incident.json")
		with open(path, encoding="utf-8") as handle:
			fields = {f["fieldname"]: f for f in json.load(handle)["fields"]}
		options = [o.strip() for o in fields["lti_classification"]["options"].splitlines()]
		self.assertIn("No lost time", options)
		self.assertIn("Fatality", options)
		# The leading blank is the "not classified yet" state.
		self.assertEqual(options[0], "")

	# ---- fixtures ----------------------------------------------------------

	def _incident(self, hours_ago, lti_classification=None):
		project = self._project()
		seq = len(frappe.get_all("WAIR Incident", pluck="name")) + 1
		return frappe.get_doc(
			{
				"doctype": "WAIR Incident",
				"incident_number": f"WAIR-{seq:04d}",
				"lti_classification": lti_classification,
				"project": project,
				"occurred_at": add_to_date(frappe.utils.now(), hours=-hours_ago),
				"incident_type": "Injury",
				"persons_involved": 2,
				"injured_persons": 1,
				"immediate_actions": "Area cordoned; first aid given.",
			}
		).insert(ignore_permissions=True)

	@staticmethod
	def _project():
		name = "CORE10 Project"
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
				"contract_amount": 1_000_000.00,
			}
		).insert(ignore_permissions=True).name
