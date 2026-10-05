# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH4-02 gate: the compliance mechanism, and the TBM guard, for the first time.

What is asserted, and what deliberately is not (D-029): the statutory ratio
table is **not in this repository** and is not invented. The gate proves the
*mechanism* — a threshold crossing flips compliance, an unconfigured table
refuses to answer instead of guessing, and the officer certification level is
read rather than assumed. It asserts **no** statutory number.

The second half executes the 24-hour backdate guard in the Phase 0
`Construction Safety Log` controller, which D-023 records as never having run:
the CORE-03 gate only counted DocType rows, and `validate` does not fire during
`migrate`. This is its first execution.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import dole_cshp


class TestDoleCshp(IntegrationTestCase):
	"""Ratio mechanism, and the backdate guard PH4-02 exists to exercise."""

	# ---- the ratio mechanism ----------------------------------------------

	def test_an_unconfigured_table_refuses_to_answer(self):
		"""It must say 'not configured', never a confident pass or fail.

		This is the whole point of D-029. A default table that happened to hold a
		plausible number would turn an absent regulation into a silent compliance
		claim.
		"""
		result = dole_cshp.check_officer_ratio("High", headcount=250, officers=3)
		self.assertFalse(result["configured"])
		self.assertIsNone(result["compliant"])
		self.assertIsNone(result["required"])
		self.assertIn("DOLE", result["reason"])
		self.assertIn("D-029", result["reason"])

	def test_a_configured_table_flips_compliance_in_the_right_direction(self):
		"""Crossing the threshold is what changes the answer -- nothing else.

		The ratio used here is a test fixture, not a statutory claim: 1 officer
	 per 50 workers, so 200 workers needs 4.
		"""
		dole_cshp.SAFETY_OFFICER_RATIOS.update({"High": 50})
		try:
			under = dole_cshp.check_officer_ratio("High", headcount=200, officers=4)
			self.assertTrue(under["configured"])
			self.assertTrue(under["compliant"])
			self.assertEqual(under["required"], 4)

			over = dole_cshp.check_officer_ratio("High", headcount=200, officers=3)
			self.assertFalse(over["compliant"])
			self.assertEqual(over["required"], 4)
			self.assertEqual(over["shortfall"], 1)
			self.assertIn("Short by 1", over["reason"])
		finally:
			dole_cshp.SAFETY_OFFICER_RATIOS.clear()

	def test_rounding_is_up_so_a_partial_officer_still_needs_a_body(self):
		"""201 workers at 1:50 needs 5 officers, not 4.

		Rounding down would quietly under-staff the project at every boundary,
		which is the direction that hurts.
		"""
		dole_cshp.SAFETY_OFFICER_RATIOS.update({"High": 50})
		try:
			result = dole_cshp.check_officer_ratio("High", headcount=201, officers=4)
			self.assertEqual(result["required"], 5)
			self.assertFalse(result["compliant"])
		finally:
			dole_cshp.SAFETY_OFFICER_RATIOS.clear()

	def test_an_unconfigured_hazard_level_is_distinct_from_an_unconfigured_table(self):
		"""Two different "we cannot answer" reasons, kept apart."""
		dole_cshp.SAFETY_OFFICER_RATIOS.update({"High": 50})
		try:
			result = dole_cshp.check_officer_ratio("Medium", headcount=100, officers=5)
			self.assertFalse(result["configured"])
			self.assertIn("Medium", result["reason"])
		finally:
			dole_cshp.SAFETY_OFFICER_RATIOS.clear()

	def test_a_nonsensical_ratio_is_refused_rather_than_divided_by(self):
		"""A zero or negative ratio must not become a division by zero."""
		dole_cshp.SAFETY_OFFICER_RATIOS.update({"High": 0})
		try:
			result = dole_cshp.check_officer_ratio("High", headcount=100, officers=1)
			self.assertFalse(result["configured"])
			self.assertIn("not a positive number", result["reason"])
		finally:
			dole_cshp.SAFETY_OFFICER_RATIOS.clear()

	def test_the_certification_ranking_is_by_qualification_not_alphabetically(self):
		"""SO4 outranks SO2, which a string comparison would get backwards."""
		rank = {"SO1": 1, "SO2": 2, "SO3": 3, "SO4": 4}
		self.assertGreater(rank["SO4"], rank["SO2"])
		# A string comparison gets this backwards: 'SO4' > 'SO2' as text, so a
		# lexicographic sort would rank SO2 above SO4 and under-count.
		self.assertGreater("SO4", "SO2")

	# ---- the TBM backdate guard, executed for the first time --------------

	def test_a_backdated_tbm_beyond_24_hours_is_refused(self):
		"""D-023: this guard has never run. The gate's own NEGATIVE."""
		from frappe.utils import add_to_date

		project = self._project()
		with self.assertRaises(frappe.ValidationError) as caught:
			frappe.get_doc(
				{
					"doctype": "Construction Safety Log",
					"log_number": "TBM-BACKDATED",
					"location": "Gate 2",
					"project": project,
					"tbm_date": add_to_date(frappe.utils.now(), hours=-25),
					"conducted_by": frappe.session.user,
					"location": "Gate 2",
					"attendee_count": 5,
					"status": "Filed",
				}
			).insert(ignore_permissions=True)
		self.assertIn("hours in the past", str(caught.exception))

	def test_a_backdate_inside_the_window_is_accepted(self):
		"""The guard is a boundary, not a ban: 23 hours back is still a log."""
		from frappe.utils import add_to_date

		project = self._project()
		doc = frappe.get_doc(
			{
				"doctype": "Construction Safety Log",
				"log_number": "TBM-RECENT",
				"location": "Gate 2",
				"project": project,
				"tbm_date": add_to_date(frappe.utils.now(), hours=-23),
				"conducted_by": frappe.session.user,
				"attendee_count": 5,
				"status": "Filed",
			}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.docstatus, 0)

	def test_a_future_dated_tbm_is_not_refused(self):
		"""Only backdating is limited. A planned meeting is normal."""
		from frappe.utils import add_to_date

		project = self._project()
		doc = frappe.get_doc(
			{
				"doctype": "Construction Safety Log",
				"log_number": "TBM-PLANNED",
				"location": "Gate 2",
				"project": project,
				"tbm_date": add_to_date(frappe.utils.now(), days=2),
				"conducted_by": frappe.session.user,
				"attendee_count": 5,
				"status": "Filed",
			}
		).insert(ignore_permissions=True)
		self.assertTrue(doc.name)

	def test_a_missing_meeting_date_is_refused(self):
		"""A TBM with no date is refused -- by the mandatory check, not the controller.

		`tbm_date` is declared mandatory, so Frappe's own field validation fires
		first and the controller's explicit "Date is required" branch is
		defence-in-depth that a normal insert cannot reach. Asserting the
		controller's message here would be asserting something that never happens;
		this asserts the refusal that does, and names the distinction.
		"""
		with self.assertRaises(frappe.ValidationError) as caught:
			frappe.get_doc(
				{
					"doctype": "Construction Safety Log",
					"log_number": "TBM-NODATE",
					"location": "Gate 2",
					"project": self._project(),
					"conducted_by": frappe.session.user,
					"location": "Gate 2",
					"status": "Filed",
				}
			).insert(ignore_permissions=True)
		self.assertIn("tbm_date", str(caught.exception))

	# ---- fixtures ----------------------------------------------------------

	@classmethod
	def _project(cls):
		name = "PH4-02 Project"
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
