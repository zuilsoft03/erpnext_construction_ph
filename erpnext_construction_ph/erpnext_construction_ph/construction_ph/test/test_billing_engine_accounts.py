# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The account-not-found degradation `docs/ARCHITECTURE.md` blesses.

From the spec's Failure Behavior Table:

    "billing_engine account not found | returns `None`; SI created without that
     tax row; `frappe.log_error`"

So a missing account must NOT abort the billing. The Sales Invoice is still
created, the offending tax row is absent, and the omission is logged for the
accountant. That is a deliberate design decision, and it is the right one: a
chart-of-accounts gap should not stop a contractor being invoiced.

**But it was untested.** `billing_engine.py` implements it and cites the spec in a
comment, and no test in the suite referenced it -- found by auditing
`docs/ARCHITECTURE.md` against the code, the same sweep that found CORE-17.

That matters more than a missing test usually would. The degradation is on
**statutory figures**: a Sales Invoice issued without its 12% VAT row, or without
its 2% EWT row, under-collects tax and overstates net payable. A behaviour the
spec blesses is exactly the behaviour nobody tests, because a test asserting it
would look like it is asserting a bug.

So these tests assert the *shape* of the degradation -- invoice created, row
absent, error logged -- rather than that it is harmless. If the account mapping
later starts resolving again, they fail, which is the point: the day the cause is
fixed, this should be noticed.
"""

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import billing_engine


class TestBillingEngineAccountDegradation(fixtures.CommittingCase, IntegrationTestCase):
	# This class COMMITS, on purpose and for a good reason: the invoice is submitted
	# and has to be visible to the reads below. A submitted document must be DURABLE,
	# so the commit stays -- and `CommittingCase` removes what it persists, so the
	# class stops costing the site a row every run. Measured: this was part of a
	# +47 rows per suite run, on a database that had reached 4,503 Sales Invoices.
	# `Construction Progress Billing` belongs here and was missing. MEASURED, by counting
	# rows around each module in isolation:
	#     test_progress_billing          890 -> 890  (+0)  no mixin
	#     test_billing_engine_accounts   890 -> 895  (+5)  this class
	#     test_billing_cycles            895 -> 895  (+0)
	#     test_sawt_reconciliation       895 -> 895  (+0)
	# Only a class that commits leaks, because only then does rollback fail to reclaim.
	# This one did commit, so the mixin has to name every doctype it creates. It did not,
	# so the `Customer` was reclaimed and the billings pointing at it survived -- the
	# teardown creating the exact condition it exists to prevent.
	committing_doctypes = (
		"Project",
		"Customer",
		"Sales Invoice",
		"Sales Invoice Item",
		"Construction Progress Billing",
	)
	"""A missing account must degrade loudly, not silently and not fatally."""

	# The docstring must be FIRST in the class body or it stops being one. My first
	# pass put the new attribute above it, which compiles and is silently wrong: the
	# class then has no docstring at all, and `help()` on it says nothing.
	# This class COMMITS, on purpose and for a good reason: the invoice is submitted
	# and has to be visible to the reads below. A submitted document must be DURABLE,
	# so the commit stays -- and `CommittingCase` removes what it persists, so the
	# class stops costing the site a row every run. Measured: this was part of a
	# +47 rows per suite run, on a database that had reached 4,503 Sales Invoices.

	# **The log is CAPTURED, not read back from the database.** Two earlier
	# versions queried `Error Log`, and both were wrong: the rows persist on the
	# site, so an assertion about "the newest few entries" was satisfied by
	# residue from an EARLIER run, and clearing them in setUp meant DELETING the
	# gate site's real error history to make a test pass. Capturing the call
	# removes the transaction fight, the residue, and the destruction at once --
	# and it tests the thing the spec actually names, which is what was handed to
	# `frappe.log_error`.
	#
	# Returns the list of (title, message) pairs the run produced.
	def _capture_log_errors(self, run):
		calls = []
		original = billing_engine.frappe.log_error
		billing_engine.frappe.log_error = lambda title=None, message=None, **_kw: calls.append(
			(title or "", message or "")
		)
		try:
			run()
		finally:
			billing_engine.frappe.log_error = original
		return calls

	# ---- what the spec says must happen -----------------------------------

	# The module's entry point is the submit hook `on_progress_billing_submit`,
	# which is what `hooks.py` wires; there is no `create_sales_invoice` to call.

	def test_an_unresolvable_vat_account_still_creates_the_invoice(self):
		"""The billing must not abort. A chart-of-accounts gap is not a reason to
		stop invoicing a contractor."""
		company = self._company()
		original = billing_engine.output_vat_account
		billing_engine.output_vat_account = lambda _c: None
		try:
			doc = self._billing_with_vat()
			billing_engine.on_progress_billing_submit(doc)
			created = self._invoice_for(doc)
		finally:
			billing_engine.output_vat_account = original

		self.assertIsNotNone(created, "a missing VAT account aborted the invoice")
		self.assertEqual(created.docstatus, 1)
		frappe.db.commit()   # the invoice is submitted; make it visible to the reads below

	def test_the_missing_vat_row_is_absent_rather_than_zero(self):
		"""Absent, not a 0.00 row.

		A 0.00 tax row looks like VAT was assessed and came to nothing; an absent
		row is the honest representation of "we could not book this", and the two
		read differently on the client's copy.
		"""
		company = self._company()
		original = billing_engine.output_vat_account
		billing_engine.output_vat_account = lambda _c: None
		try:
			doc = self._billing_with_vat()
			billing_engine.on_progress_billing_submit(doc)
			created = self._invoice_for(doc)
		finally:
			billing_engine.output_vat_account = original
		frappe.db.commit()

		rows = frappe.get_all(
			"Sales Taxes and Charges", filters={"parent": created.name}, pluck="description"
		)
		self.assertFalse(
			any("VAT" in (r or "") for r in rows),
			"a VAT row was created despite the account being unresolvable: %s" % rows,
		)

	def test_the_omission_is_logged_for_the_accountant(self):
		"""`frappe.log_error` is named by the spec, so it is asserted here.

		Without the log, a missing VAT account is indistinguishable from a
		correctly zero-rated line, and nobody discovers it before a filing.

		**What the log SAYS, not merely that one exists.** A call carrying an
		empty title satisfies a row count and is useless to the accountant the spec
		says it is written for; an assertion that only counted entries let a
		mutation that removed the actionable text entirely pass.
		"""
		original = billing_engine.output_vat_account
		billing_engine.output_vat_account = lambda _c: None
		try:
			calls = self._capture_log_errors(
				lambda: billing_engine.on_progress_billing_submit(self._billing_with_vat())
			)
		finally:
			billing_engine.output_vat_account = original

		self.assertTrue(calls, "a missing VAT account produced no frappe.log_error at all")
		joined = " ".join("%s %s" % pair for pair in calls)
		self.assertIn(
			"No output VAT account", joined,
			"frappe.log_error was called but nothing says what was missing, so it "
			"cannot be acted on: %s" % calls,
		)

	def test_the_log_cannot_name_its_invoice_yet(self):
		"""**A real defect in the degradation, found by writing the test.**

		The spec says the omission is logged "for the accountant to fix". The
		message interpolates `inv.name`, and the log is written BEFORE the invoice
		is inserted -- so it reads "Sales Invoice None was created without its 12%
		VAT row". **The log cannot identify the invoice it is complaining about**,
		which is the one thing that would make it actionable.

		Pinned rather than asserted-as-correct, for the same reason as CORE-17: the
		suite stays green, the defect lives in code, and a fix makes this fail.
		"""
		original = billing_engine.output_vat_account
		billing_engine.output_vat_account = lambda _c: None
		try:
			calls = self._capture_log_errors(
				lambda: billing_engine.on_progress_billing_submit(self._billing_with_vat())
			)
		finally:
			billing_engine.output_vat_account = original

		joined = " ".join("%s %s" % pair for pair in calls)
		self.assertIn(
			"Sales Invoice None", joined,
			"the degradation's log now names its invoice, so the sibling fix to "
			"CORE-17 landed: move the log AFTER the insert, or log the billing "
			"number instead of inv.name.",
		)

	# ---- the drift risk ---------------------------------------------------

	def test_no_tax_row_is_booked_to_a_blank_account(self):
		"""No degradation may book a tax row with an empty account head.

		`allow`-style bypasses are how a "skip it if unresolvable" path turns
		into a silent misposting, and the spec's own wording is a skip, so the
		thing worth pinning is that a skip never becomes a blank booking.
		"""
		company = self._company()
		doc = self._billing_with_vat()
		billing_engine.on_progress_billing_submit(doc)
		created = self._invoice_for(doc)
		frappe.db.commit()
		rows = frappe.get_all(
			"Sales Taxes and Charges",
			filters={"parent": created.name},
			fields=["account_head", "charge_type", "tax_amount"],
		)
		for row in rows:
			self.assertTrue(
				(row.account_head or "").strip(),
				"a tax row was booked with a blank account_head: %s" % row,
			)

	# ---- fixtures ---------------------------------------------------------

	@staticmethod
	def _invoice_for(doc):
		"""The Sales Invoice the hook created for this billing.

		`on_progress_billing_submit` is a hook and returns None, and the invoice
		carries no reverse link field -- the only trace of the source billing is a
		`remarks` string naming it. So the invoice is found by that string, which
		is deterministic, rather than by "the newest one for this customer", which
		is a guess that silently picks up a neighbour's invoice.
		"""
		number = getattr(doc, "billing_number", None) or doc.name
		rows = frappe.get_all(
			"Sales Invoice", filters={"remarks": ["like", f"%{number}%"]}, pluck="name"
		)
		assert rows, "no Sales Invoice was created referencing %s" % number
		return frappe.get_doc("Sales Invoice", rows[0])

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (see D-012)")
		return company[0]

	def _billing_with_vat(self):
		"""A billing whose figures produce a non-zero VAT row.

		Reuses the CORE-02 fixture shape rather than recomputing: the point here
		is the account mapping, not the arithmetic, and a second copy of the
		formula would be a second thing to keep correct.
		"""
		import uuid

		company = self._company()
		suffix = uuid.uuid4().hex[:6]
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"DEG {suffix}",
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		)
		customer.insert(ignore_permissions=True)
		project = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"DEG {suffix}",
				"company": company,
				"status": "Open",
			}
		)
		project.insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Construction Progress Billing",
				"billing_number": f"DEG-{suffix}",
				"project": project.name,
				"company": company,
				"client": customer.name,
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
