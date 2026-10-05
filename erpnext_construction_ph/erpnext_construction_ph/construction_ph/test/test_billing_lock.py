# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The class A billing lock named in `docs/ARCHITECTURE.md`:

    "Billing lock | Progress Billing is submittable; amendments require Cancel+Amend
     workflow"

Found while auditing `docs/ARCHITECTURE.md`'s Reliability Rules against the code,
which is the one place the spec can be right and the tracker never notice, because
the tracker does not claim to be the spec.

The JSON shows no money field carrying `allow_on_submit`, which is the necessary
condition -- but a JSON attribute is not a proof, and reading it is exactly the
"proven from reading" habit this project keeps catching. So the lock is exercised:
a submitted billing is edited, and the edit must be refused.

**Why the whole thing matters.** A certified progress billing is a statutory
figure the contractor is charged against. If a submitted one can be edited in
place, the CIAP 102 arithmetic that `docs/ARCHITECTURE.md:84-88` calls
never-break can be restated after certification, and every downstream document
that referenced it -- the SAWT return, the ledger, the client's portal -- is
carrying a number that was never certified.
"""

import frappe
from frappe.tests import IntegrationTestCase

DT = "Construction Progress Billing"


class TestBillingLock(IntegrationTestCase):
	"""A submitted billing cannot be restated in place."""

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (see D-012)")
		return company[0]

	def _customer(self, suffix):
		existing = frappe.get_all("Customer", filters={"customer_name": f"BLK {suffix}"}, pluck="name")
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"BLK {suffix}",
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _project(self, suffix):
		existing = frappe.get_all("Project", filters={"project_name": f"BLK {suffix}"}, pluck="name")
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"BLK {suffix}",
				"company": self._company(),
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _submitted_billing(self):
		# Unique per run: a submitted billing cannot be deleted, so a fixed
		# billing_number makes the second run of this suite collide on PRIMARY.
		tag = str(self._testMethodName)[:12] + "-" + frappe.generate_hash(length=6)
		doc = frappe.get_doc(
			{
				"doctype": DT,
				"billing_number": "BLK-" + tag,
				"project": self._project("lock"),
				"company": self._company(),
				"client": self._customer("lock"),
				"posting_date": "2026-09-28",
				"billing_period_start": "2026-09-01",
				"billing_period_end": "2026-09-30",
				"contract_amount": 10_000_000.00,
				"period_accomplishment_percent": 10.0,
				"mobilization_recoupment": 0.0,
				"cumulative_accomplishment": 1_000_000.00,
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	# ---- the lock ---------------------------------------------------------

	def test_a_money_field_cannot_be_changed_after_submission(self):
		"""**FIXED 2026-09-28 (CORE-17).** A submitted billing's contract amount is
		now refused, and this test asserts the refusal rather than pinning the defect.

		Why three earlier attempts failed, all now measured rather than assumed:
		  1. A guard in `validate` -- the controller's `validate` is **not called at
		     all** on the edit path. Probed on a real submitted-doc save, the only
		     controller hook that fires is `validate_update_after_submit`.
		  2. A guard in `before_save` -- also never runs. Probed the same way,
		     `before_save` is not among the hooks Frappe fires for a submitted
		     document, so a guard placed there looks right and does nothing.
		  3. Blaming `_restore_masked_fields_from_db()` for hiding the edit -- **it
		     does not**. Invoked directly, the field still reads the NEW value
		     afterwards, because `contract_amount` is not `allow_on_submit` and is
		     therefore never masked in the first place.

		The working enforcement is `guard_certified_figures()`, called from
		`validate_update_after_submit` -- the one hook that fires on this path and
		that does see the edited value. It compares the in-memory figure against
		`frappe.db.get_value`, so it reads the committed row rather than a snapshot.
		"""
		billing = self._submitted_billing()
		before = frappe.db.get_value(DT, billing.name, "contract_amount")

		edited = frappe.get_doc(DT, billing.name)
		edited.contract_amount = 9_999_999.00
		with self.assertRaises(frappe.ValidationError):
			edited.save()

		after = frappe.db.get_value(DT, billing.name, "contract_amount")
		self.assertEqual(after, before, "a refused edit must not change the stored figure")
		self.assertNotEqual(after, 9_999_999.00)

	def test_the_accomplishment_percent_cannot_be_changed_after_submission(self):
		"""The figure that drives retention -- same fix, same hook (CORE-17)."""
		billing = self._submitted_billing()
		before = frappe.db.get_value(DT, billing.name, "period_accomplishment_percent")

		edited = frappe.get_doc(DT, billing.name)
		edited.period_accomplishment_percent = 99.0
		with self.assertRaises(frappe.ValidationError):
			edited.save()

		after = frappe.db.get_value(DT, billing.name, "period_accomplishment_percent")
		self.assertEqual(after, before, "a refused edit must not change the stored figure")
		self.assertNotEqual(after, 99.0)

	def test_an_untouched_submitted_billing_is_still_savable(self):
		"""The over-blocking half of the control, which the refusal assertions above
		cannot see: a guard that refuses every save of a submitted billing would
		pass both of them and break the document.
		"""
		billing = self._submitted_billing()
		before = frappe.db.get_value(DT, billing.name, ["contract_amount", "docstatus"],
			as_dict=True)
		frappe.get_doc(DT, billing.name).save()  # must not raise
		after = frappe.db.get_value(DT, billing.name, ["contract_amount", "docstatus"],
			as_dict=True)
		self.assertEqual(after.contract_amount, before.contract_amount)
		self.assertEqual(after.docstatus, 1, "a no-op save must not change docstatus")

	def test_a_submitted_billing_can_still_be_cancelled(self):
		"""Cancellation is the amendment route the spec names, so the lock must not
		stand in its way."""
		billing = self._submitted_billing()
		frappe.get_doc(DT, billing.name).cancel()
		self.assertEqual(frappe.db.get_value(DT, billing.name, "docstatus"), 2)

	def test_submitting_a_billing_is_not_itself_refused(self):
		"""The regression a future CORE-17 fix is most likely to introduce.

		Frappe sets `docstatus` to 1 BEFORE running `validate` during a submit, so
		a guard written against `self.docstatus` refuses the submission itself.
		"""
		billing = self._submitted_billing()
		self.assertEqual(
			frappe.db.get_value(DT, billing.name, "docstatus"), 1,
			"precondition: the billing should have submitted",
		)

	def test_no_money_field_is_editable_after_submission(self):
		"""Close the class, not an instance.

		Two named fields prove the mechanism works; this proves nothing in the
		money set was quietly marked `allow_on_submit` to work around a symptom.
		The two that legitimately are allowed are named, so the assertion has a
		reason rather than a number.
		"""
		allowed = sorted(f.fieldname for f in frappe.get_meta(DT).fields if f.allow_on_submit)
		self.assertEqual(
			allowed, ['sales_invoice', 'submitted_on'],
			'the set of fields Frappe will not mask has changed. Opening one is how a '
			'future fix would make the guard SEE an edit, and each opened field then '
			'needs a guard that refuses -- see the three attempts in CORE-17.',
		)

	def test_a_submitted_billing_is_locked_but_still_readable(self):
		"""The lock must not become unreadable -- it is an amendment rule, not a freeze.

		A client portal that could not display a certified billing would be a
		second, quieter defect introduced by the fix for the first.
		"""
		billing = self._submitted_billing()
		read_back = frappe.get_doc(DT, billing.name)
		self.assertEqual(read_back.docstatus, 1)
		self.assertTrue(read_back.net_amount_due)
		self.assertTrue(read_back.submitted_on)
