# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH4-03 gate: the statutory report payload, and the PDF render over it.

Split deliberately (D-031). The payload is the statutory substance and is
asserted as structured data; the PDF is a thin render over it, proven separately
by asserting real `%PDF-` bytes. A renderer quirk must not be able to cost the
row its substantive content.

Expected values are hand-written literals. Nothing here is derived by calling
the module under test.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date

from erpnext_construction_ph.api import wair

# A fixture table, NOT a statutory claim. Real figures are unsourced (D-029).
FIXTURE_DAYS = {"Temporary Total Disability": 7, "No lost time": 0}


class TestWairReport(IntegrationTestCase):
	"""Report payload, both refusals, and the render."""

	def setUp(self):
		wair.LTI_DAYS_BY_CLASSIFICATION.update(FIXTURE_DAYS)
		self.addCleanup(wair.LTI_DAYS_BY_CLASSIFICATION.clear)

	# ---- the payload -------------------------------------------------------

	def test_the_report_carries_the_statutory_substance(self):
		"""Payee, times, classification, LTI days, deadline status — as literals."""
		incident = self._incident(hours_ago=2, classification="Temporary Total Disability")
		report = wair.build_report(incident.name)

		self.assertFalse(report["refused"])
		self.assertEqual(report["incident_number"], incident.incident_number)
		self.assertEqual(report["payee"], "PH4-03 Project")
		self.assertEqual(report["lti_classification"], "Temporary Total Disability")
		self.assertEqual(report["lti_days"], 7)
		self.assertEqual(report["persons_involved"], 3)
		self.assertEqual(report["injured_persons"], 1)
		self.assertEqual(report["fatalities"], 0)
		self.assertEqual(report["notification_status"], "Pending")
		self.assertEqual(report["location"], "Level 2 Grid A-C")

	def test_lti_days_for_returns_none_not_zero_when_unknown(self):
		"""None means 'cannot be determined'; zero is a finding.

		Conflating them is the whole defect the gate's NEGATIVE names.
		"""
		self.assertIsNone(wair.lti_days_for(""))
		self.assertIsNone(wair.lti_days_for(None))
		self.assertIsNone(wair.lti_days_for("Fatality"))  # not in the table
		self.assertEqual(wair.lti_days_for("No lost time"), 0)  # a real zero

	# ---- refusal 1: no classification --------------------------------------

	def test_an_unclassified_incident_is_refused_not_defaulted_to_zero(self):
		"""The gate's NEGATIVE, first half."""
		incident = self._incident(hours_ago=1, classification=None)
		report = wair.build_report(incident.name)

		self.assertTrue(report["refused"])
		self.assertIn("no LTI classification", report["reason"])
		self.assertNotIn("lti_days", report)
		self.assertNotIn("lti_days", report.get("reason", "") and {} or {})

	def test_the_two_refusals_are_independent(self):
		"""Proves the unclassified refusal is a deliberate check, not a side effect.

		While the classification table is empty, an unclassified incident and a
		classified one are refused for different reasons -- and a test that only
		ever runs against the empty table cannot tell them apart. That matters
		because the day someone populates the table with real figures, an
		unclassified incident must STILL be refused rather than quietly computing
		zero lost days.

		So: with the table emptied, a *classified* incident must still be refused
		(and for the table, not the classification).
		"""
		incident = self._incident(hours_ago=2, classification="Temporary Total Disability")
		wair.LTI_DAYS_BY_CLASSIFICATION.clear()
		report = wair.build_report(incident.name)

		self.assertTrue(report["refused"], "a classified incident passed with no table")
		self.assertIn("No lost-day figure is configured", report["reason"])
		self.assertNotIn("no LTI classification", report["reason"])

		# And with the table restored, the same incident is no longer refused --
		# so the two refusals really are different gates.
		wair.LTI_DAYS_BY_CLASSIFICATION.update(FIXTURE_DAYS)
		self.assertFalse(wair.build_report(incident.name)["refused"])

	# ---- refusal 2: past the deadline --------------------------------------

	def test_a_report_requested_after_the_deadline_is_refused(self):
		"""The gate's NEGATIVE, second half.

		An artefact bearing a timestamp suggests the deadline was met when it
		was not, so the report is not issued at all.
		"""
		incident = self._incident(hours_ago=30, classification="Temporary Total Disability")
		self.assertEqual(incident.notification_status, "Overdue")

		report = wair.build_report(incident.name)
		self.assertTrue(report["refused"])
		self.assertIn("notification window closed", report["reason"])
		self.assertNotIn("lti_days", report)

	# ---- the render, proven separately -------------------------------------

	def test_the_report_renders_to_real_pdf_bytes(self):
		"""A thin render over the payload, proven on its own terms.

		Frappe's default renderer is used: weasyprint is absent from this bench
		and forcing wkhtmltopdf fails, so neither is passed (D-031).
		"""
		incident = self._incident(hours_ago=2, classification="Temporary Total Disability")
		report = wair.build_report(incident.name)
		self.assertFalse(report["refused"])

		pdf = wair.render_pdf(report)
		self.assertTrue(pdf, "the render produced no bytes")
		self.assertTrue(pdf.startswith(b"%PDF-"), "the render is not a PDF")

	# ---- fixtures ----------------------------------------------------------

	def _incident(self, hours_ago, classification):
		project = self._project()
		seq = len(frappe.get_all("WAIR Incident", pluck="name")) + 1
		return frappe.get_doc(
			{
				"doctype": "WAIR Incident",
				"incident_number": f"WAIR-RPT-{seq:04d}",
				"lti_classification": classification,
				"project": project,
				"occurred_at": add_to_date(frappe.utils.now(), hours=-hours_ago),
				"incident_type": "Injury",
				"location": "Level 2 Grid A-C",
				"persons_involved": 3,
				"injured_persons": 1,
				"fatalities": 0,
				"immediate_actions": "Area cordoned; first aid given.",
			}
		).insert(ignore_permissions=True)

	@staticmethod
	def _project():
		name = "PH4-03 Project"
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
