# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-07 gate: the CIAP 102 30-day clock starts at submission, not creation.

The row exists because a progress billing can sit in draft for a fortnight
before anyone certifies it. Charging the contractor 30 days from *creation*
bills time they were never given, so the clock's start is evidence -- and
evidence that can be moved after the fact is not evidence.

The column itself is also half the gate: the recorded blocker claimed the
`submitted_on` column could not be created on this site. It can, and the
assertion that it exists is the first test here, so a future schema regression
fails loudly rather than leaving the rest of this file to fail obscurely.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now

DOCTYPE = "Construction Progress Billing"


class TestSubmittedOn(IntegrationTestCase):
	"""A draft reports no submission time; submitting stamps it once."""

	# ---- the schema, which the blocker said could not exist ---------------

	def test_the_submitted_on_column_exists(self):
		"""The gate's precondition, asserted rather than assumed.

		Every other test here reads or writes this column, so if the schema ever
		stops syncing they would fail with a confusing field error instead of
		naming the real cause.
		"""
		columns = [c[0] for c in frappe.db.sql("SHOW COLUMNS FROM `tab%s`" % DOCTYPE)]
		self.assertIn("submitted_on", columns)
		self.assertTrue(frappe.get_meta(DOCTYPE).get_field("submitted_on"))

	# ---- a draft reports nothing -----------------------------------------

	def test_a_draft_reports_no_submission_time(self):
		billing = self._billing()
		self.assertFalse(billing.submitted_on)

	def test_a_draft_survives_a_save_without_gaining_a_stamp(self):
		"""Re-saving a draft must not quietly start the clock."""
		billing = self._billing()
		billing.remarks = "recalculated"
		billing.save()
		self.assertFalse(billing.reload().submitted_on)

	# ---- submission starts the clock -------------------------------------

	def test_submitting_stamps_the_submission_time(self):
		billing = self._billing()
		before = now()
		billing.submit()
		after = now()
		stamp = billing.reload().submitted_on
		self.assertTrue(stamp)
		self.assertGreaterEqual(str(stamp), str(add_days(before, 0)))
		self.assertLessEqual(str(stamp), str(after))

	def test_the_clock_starts_at_submission_not_creation(self):
		"""**The business reason this row exists.**

		A draft that sat for ten days must be stamped with today's date. Stamping
		it with the creation date charges the contractor ten days they were never
		given, and that is a dispute rather than a bug report.
		"""
		billing = self._billing()
		# Backdate the creation, as a draft genuinely left alone would be.
		frappe.db.set_value(DOCTYPE, billing.name, "creation", add_days(now(), -10))
		self.assertNotEqual(str(frappe.db.get_value(DOCTYPE, billing.name, "creation")), str(now()))

		billing.reload()
		billing.submit()
		stamp = billing.reload().submitted_on
		self.assertNotEqual(str(stamp)[:10], str(add_days(now(), -10))[:10])

	def test_the_stamp_does_not_move_on_a_later_save(self):
		"""Submitting twice must not restart the clock."""
		billing = self._billing()
		billing.submit()
		first = billing.reload().submitted_on

		billing.reload()
		billing.save()  # a no-op save on a submitted doc
		self.assertEqual(str(billing.reload().submitted_on), str(first))

	# ---- the stamp is not editable ----------------------------------------

	def test_moving_the_clock_start_is_refused(self):
		"""**The negative, and the reason the field is guarded.**

		A stamp that can be re-dated after the fact is not a clock start: it
		reopens the 30-day window while still displaying a valid timestamp.
		"""
		billing = self._billing()
		billing.submit()

		billing.reload()
		billing.submitted_on = add_days(now(), -5)
		with self.assertRaises(frappe.ValidationError) as caught:
			billing.save()
		self.assertIn("cannot be changed", str(caught.exception))

	def test_a_refused_re_date_leaves_the_original_stamp(self):
		"""The refusal must not half-apply."""
		billing = self._billing()
		billing.submit()
		original = billing.reload().submitted_on

		billing.reload()
		billing.submitted_on = add_days(now(), -5)
		with self.assertRaises(frappe.ValidationError):
			billing.save()
		self.assertEqual(str(frappe.db.get_value(DOCTYPE, billing.name, "submitted_on")), str(original))

	# ---- fixtures ---------------------------------------------------------

	def _billing(self):
		"""A valid draft billing. Unique per test: reuse collides on submission."""
		import uuid

		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		return frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"billing_number": "PB-CORE07-" + uuid.uuid4().hex[:8],
				# `project` on this doctype links to ERPNext's Project, not to
				# Construction Project -- naming the wrong one fails at link
				# validation and says only "Could not find Project".
				"project": self._erpnext_project(company),
				"company": company,
				"client": self._customer(),
				"posting_date": "2026-01-31",
				"billing_period_start": "2026-01-01",
				"billing_period_end": "2026-01-31",
				"contract_amount": 1000000.00,
				"period_accomplishment_percent": 10.0,
				"mobilization_recoupment": 0.0,
				"cumulative_accomplishment": 100000.00,
			}
		).insert(ignore_permissions=True)

	@staticmethod
	def _erpnext_project(company):
		name = "PH CORE-07 project"
		existing = frappe.get_all("Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]
		return frappe.get_doc(
			{"doctype": "Project", "project_name": name, "company": company}
		).insert(ignore_permissions=True).name

	@staticmethod
	def _customer():
		name = "PH CORE-07 Customer"
		if not frappe.db.exists("Customer", name):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
				}
			).insert(ignore_permissions=True)
		return name
