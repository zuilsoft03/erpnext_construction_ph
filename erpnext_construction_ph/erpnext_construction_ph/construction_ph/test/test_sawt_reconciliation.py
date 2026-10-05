# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md CORE-47 / PH1-02 — SAWT reconciliation of billed CWT against the CWT asset.

The gate selects this by METHOD name (D-014), so every assertion lives in the
single ``test_sawt_reconciliation`` method.

Billings are driven through the real submit path, not written straight into the
table, so the Sales Invoice and its GL entries are produced by
api/billing_engine.py. A reconciliation over fabricated rows would prove nothing
about the engine that actually posts the CWT.

**The window is 2027 on purpose, and the test brings its own Fiscal Year.** MEASURED
2026-10-05 on test.localhost: every one of the 910 live `Construction Progress
Billing` rows is dated `2026-09-28`, and the only CWT rows on account 11050 inside
2026 Q1 are `ACC-SINV-2026-04990` and `ACC-SINV-2026-04991` — this project's own
residue from 2026-09-30 (`ops/tmp-realign/04-sawt-crosscheck.md`,
`05-cpb-left-leg.md`). Reconciling over 2026 therefore reconciled against another
test class's leftovers, and this module was RED for exactly that reason:
`72500.0 != 30000.00`. Nothing in 2027 exists on the site, so every figure below
is this test's own — and ERPNext will not post a 2027 invoice without an active
fiscal year covering it, so the fiscal year is created here too.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, getdate

from erpnext_construction_ph.api import sawt_reconciliation

CONTRACT_AMOUNT = 10_000_000.00

# The four fixtures. `posting_date` is chosen so that 2027-03-31 and 2027-04-01
# sit either side of a quarter edge, with a billing ON each side of the boundary.
Q1_START, Q1_END = "2027-01-01", "2027-03-31"
Q2_START, Q2_END = "2027-04-01", "2027-06-30"
STRADDLE_START, STRADDLE_END = "2027-03-15", "2027-04-15"
EMPTY_START, EMPTY_END = "2027-10-01", "2027-12-31"

FY_NAME = "CORE-47 Fiscal Year 2027"
FY_START, FY_END = "2027-01-01", "2027-12-31"

# Non-billing CWT the fixtures put on 2307 with no billing behind it.
UNMATCHED_AMOUNT = 15_000.00


class TestSAWTReconciliation(IntegrationTestCase):
	"""Billed CWT subledger vs the movement on the CWT asset account."""

	def _company(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		self.assertTrue(company, "gate site needs a Company (D-012)")
		return company[0]

	def _fiscal_year(self, company):
		"""An ACTIVE Fiscal Year covering the fixture horizon, created if absent.

		ERPNext refuses to post a document dated outside an active fiscal year —
		`accounts/utils.get_fiscal_year` raises `FiscalYearError` — and this site's
		calendar stops at 2026. The fixtures are dated 2027 because that is what
		keeps them away from ambient data, so the fiscal year travels with them
		instead of the window being dragged back into the contaminated one.
		"""
		if frappe.db.exists("Fiscal Year", FY_NAME):
			return FY_NAME
		for row in frappe.get_all(
			"Fiscal Year", filters={"disabled": 0}, fields=["name", "year_start_date", "year_end_date"]
		):
			if getdate(row["year_start_date"]) <= getdate(FY_START) and getdate(row["year_end_date"]) >= getdate(FY_END):
				if company in frappe.get_all("Fiscal Year Company", filters={"parent": row["name"]}, pluck="company"):
					return row["name"]
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": FY_NAME,
				"disabled": 0,
				"is_short_year": 0,
				"year_start_date": FY_START,
				"year_end_date": FY_END,
				"companies": [{"company": company}],
			}
		).insert(ignore_permissions=True)
		return FY_NAME

	def _project(self):
		"""Return the ERPNext Project the billings link to.

		Construction Progress Billing.project points at the native Project, so that
		real name is what a billing needs; the Construction Project governance
		record is created alongside it because the contract-amount lock lives there.
		"""
		name = "SAWT Test Construction Project"
		if frappe.db.exists("Construction Project", name):
			return self._erp_project()
		customer = "SAWT Test Client"
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
		erp_project_name = "SAWT Test Project"
		# ERPNext autonames Project as PROJ-000N, so resolve the real name rather
		# than assuming project_name is the document name.
		hits = frappe.get_all("Project", filters={"project_name": erp_project_name}, pluck="name")
		if hits:
			erp_project = hits[0]
		else:
			erp_project = frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": erp_project_name,
					"company": self._company(),
					"status": "Open",
				}
			).insert(ignore_permissions=True).name
		frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": name,
				"erpnext_project": erp_project,
				"company": self._company(),
				"client": customer,
				"contract_date": FY_START,
				"contract_amount": CONTRACT_AMOUNT,
			}
		).insert(ignore_permissions=True)
		return self._erp_project()

	def _erp_project(self):
		hits = frappe.get_all("Project", filters={"project_name": "SAWT Test Project"}, pluck="name")
		return hits[0] if hits else None

	def _cost_center(self, company):
		row = frappe.db.get_value(
			"Cost Center", {"company": company, "is_group": 0}, "name"
		)
		self.assertTrue(row, "gate site needs a non-group Cost Center")
		return row

	def _counterpart_account(self, company):
		"""A leaf account to balance the test journal against.

		The gate site's CoA has no Bank or Cash leaf (ERPNext creates those only
		when a company is set up through the wizard), and a Receivable would demand
		a party on the row. A Fixed Asset leaf needs neither. The entry is synthetic
		by design -- its whole job is to put GL movement on the CWT asset with no
		progress billing behind it.
		"""
		row = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Fixed Asset", "is_group": 0}, "name"
		)
		self.assertTrue(row, "gate site needs a leaf Fixed Asset account to balance the journal")
		return row

	def _bill(self, number, posting_date, period_pct, recoup, cumulative, period_start, period_end):
		"""Submit a real progress billing; the engine posts its CWT to the GL.

		``cwt_withheld`` is DERIVED by the doctype — taxable_base x 2%, where
		taxable_base is contract x period percent less mobilization recoupment. The
		expected figure is written out longhand at each call site below, so the
		assertion is against the CIAP 102 arithmetic and not against whatever the
		document happens to say.
		"""
		doc = frappe.get_doc(
			{
				"doctype": "Construction Progress Billing",
				"billing_number": number,
				"project": self._project(),
				"company": self._company(),
				"client": "SAWT Test Client",
				"posting_date": posting_date,
				"billing_period_start": period_start,
				"billing_period_end": period_end,
				"contract_amount": CONTRACT_AMOUNT,
				"period_accomplishment_percent": period_pct,
				"mobilization_recoupment": recoup,
				"cumulative_accomplishment": cumulative,
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	def _assert_clean_window(self, result, expected_total, what):
		"""The whole period ties: subledger = GL, nothing unmatched, reconciled."""
		self.assertEqual(flt(result["billed_cwt_total"]), expected_total, f"{what}: billed CWT")
		self.assertEqual(flt(result["gl_total"]), expected_total, f"{what}: GL movement")
		self.assertEqual(flt(result["variance"]), 0.00, f"{what}: variance")
		self.assertEqual(flt(result["unmatched_gl_total"]), 0.00, f"{what}: unmatched GL")
		self.assertEqual(result["unmatched_gl"], [], f"{what}: unmatched list")
		self.assertTrue(result["reconciled"], f"{what}: reconciled")

	def test_sawt_reconciliation(self):
		company = self._company()
		self._fiscal_year(company)
		account = sawt_reconciliation.cwt_asset_account(company)

		# ---- CLAUSE 7, FIRST HALF: the fixture window is empty BEFORE anything is
		# created. Every figure the rest of this method asserts therefore belongs
		# to this test and to nothing else on the site.
		pre = sawt_reconciliation.reconcile(Q1_START, Q2_END, company=company)
		self.assertEqual(pre["billings"], [], "the fixture window must start empty")
		self.assertEqual(flt(pre["billed_cwt_total"]), 0.00)
		self.assertEqual(flt(pre["gl_total"]), 0.00)
		self.assertEqual(pre["unmatched_gl"], [])
		self.assertEqual(
			len(pre["csv"].strip().split("\n")), 1, "an empty period's CSV is a header and nothing else"
		)

		# ---- CLAUSE 3, the expected figures computed HERE, longhand, from the
		# CIAP 102 inputs the call sites pass. Nothing below is read back out of
		# `reconcile()`, and nothing is copied out of the implementation.
		#
		# b1: 10,000,000 x 20% = 2,000,000 gross; less 500,000 recoupment =
		#     1,500,000 taxable; CWT 2% = 30,000.00
		# b2: 10,000,000 x 10% = 1,000,000 taxable;               CWT 2% = 20,000.00
		# b3: 10,000,000 x 15% = 1,500,000 taxable;               CWT 2% = 30,000.00
		# b4: 10,000,000 x 10% = 1,000,000 taxable;               CWT 2% = 20,000.00
		B1, B2, B3, B4 = 30_000.00, 20_000.00, 30_000.00, 20_000.00
		# Written as the arithmetic rather than as the product, so the number is
		# derived from the CIAP 102 inputs instead of asserted against the engine.
		self.assertEqual(B1, 1_500_000.00 * 0.02, "b1 expectation is 2% of the recouped base")
		self.assertEqual(B2, 1_000_000.00 * 0.02, "b2 expectation is 2% of the full base")
		self.assertEqual(B3, 1_500_000.00 * 0.02, "b3 expectation is 2% of the full base")
		self.assertEqual(B4, 1_000_000.00 * 0.02, "b4 expectation is 2% of the full base")

		# ---- CLAUSE 1: this test creates its OWN billings, inside its own window,
		# through the real submit path so the engine posts their CWT.
		first = self._bill("SAWT-2027-001", "2027-02-15", 20, 500_000.00, 2_000_000.00, Q1_START, Q1_END)
		self.assertEqual(flt(first.cwt_withheld), B1, "the doctype's own derivation must match the arithmetic")
		# ON the quarter edge itself.
		edge = self._bill("SAWT-2027-002", Q1_END, 10, 0.00, 3_000_000.00, Q1_START, Q1_END)
		self.assertEqual(flt(edge.cwt_withheld), B2, "the doctype's own derivation must match the arithmetic")
		# The day AFTER the edge — the other side of the same boundary.
		after = self._bill("SAWT-2027-003", Q2_START, 15, 0.00, 4_500_000.00, Q2_START, Q2_END)
		self.assertEqual(flt(after.cwt_withheld), B3, "the doctype's own derivation must match the arithmetic")
		mid = self._bill("SAWT-2027-004", "2027-05-20", 10, 0.00, 5_500_000.00, Q2_START, Q2_END)
		self.assertEqual(flt(mid.cwt_withheld), B4, "the doctype's own derivation must match the arithmetic")

		q1_expected, q2_expected, straddle_expected = B1 + B2, B3 + B4, B2 + B3

		# The CWT is in the GL, not only in the subledger.
		self.assertTrue(
			frappe.db.exists(
				"GL Entry", {"account": account, "voucher_type": "Sales Invoice", "is_cancelled": 0}
			),
			"the engine must post the CWT to account 11050",
		)

		# ---- CLAUSE 2 + 4a, PERIOD 1 of 3: 2027 Q1. The boundary billing dated
		# 2027-03-31 must be INSIDE and the one dated 2027-04-01 must be OUTSIDE.
		q1 = sawt_reconciliation.reconcile(Q1_START, Q1_END, company=company)
		self.assertEqual([r["billing_number"] for r in q1["billings"]], ["SAWT-2027-001", "SAWT-2027-002"])
		self._assert_clean_window(q1, q1_expected, "Q1")
		self.assertEqual(q1["form"], "1601-EQ")
		self.assertEqual(q1["period_start"], Q1_START)
		self.assertEqual(q1["period_end"], Q1_END)
		# 4a: per-billing, the subledger figure equals the GL movement that
		# accounts for it. A total can be right by accident; these rows cannot.
		for row in q1["billings"]:
			self.assertIsNotNone(row["sales_invoice"], "a submitted billing must link its Sales Invoice")
			self.assertFalse(row["unposted"])
			self.assertEqual(flt(row["gl_cwt"]), flt(row["billed_cwt"]), f"GL for {row['billing_number']}")
			self.assertEqual(flt(row["variance"]), 0.00, f"row variance for {row['billing_number']}")

		# ---- CLAUSE 2, PERIOD 2 of 3: the SAME boundary from the other side.
		# Now 2027-04-01 must be inside and 2027-03-31 must be outside. A boundary
		# read from one side only proves nothing, which is the whole clause.
		q2 = sawt_reconciliation.reconcile(Q2_START, Q2_END, company=company)
		self.assertEqual([r["billing_number"] for r in q2["billings"]], ["SAWT-2027-003", "SAWT-2027-004"])
		self._assert_clean_window(q2, q2_expected, "Q2")
		for row in q2["billings"]:
			self.assertEqual(flt(row["variance"]), 0.00, f"row variance for {row['billing_number']}")

		# ---- CLAUSE 2, PERIOD 3 of 3: STRADDLING the quarter edge. It must carry
		# the 2027-03-31 billing AND the 2027-04-01 one, and leave out the
		# 2027-02-15 and 2027-05-20 ones.
		straddle = sawt_reconciliation.reconcile(STRADDLE_START, STRADDLE_END, company=company)
		self.assertEqual(
			[r["billing_number"] for r in straddle["billings"]], ["SAWT-2027-002", "SAWT-2027-003"]
		)
		self._assert_clean_window(straddle, straddle_expected, "straddling quarter")

		# ---- MUTATION (PH1-02): perturb one billed figure by a centavo. The two
		# sides are read from different places, so they must now disagree by
		# exactly 0.01 rather than the difference being absorbed.
		frappe.db.set_value("Construction Progress Billing", after.name, "cwt_withheld", B3 + 0.01)
		perturbed = sawt_reconciliation.reconcile(Q2_START, Q2_END, company=company)
		# compared at the field's own precision: set_value bypasses the document
		# rounding, so the raw float carries representation error
		self.assertEqual(flt(perturbed["variance"], 2), 0.01, "a 0.01 divergence must surface, not be absorbed")
		self.assertFalse(perturbed["reconciled"])
		frappe.db.set_value("Construction Progress Billing", after.name, "cwt_withheld", B3)

		# ---- CLAUSE 4b, THE OTHER DIRECTION: non-billing CWT on account 11050. A
		# manual journal is the real case — it lands on 2307 with no progress
		# billing behind it. It must be NAMED in `unmatched_gl`, must NOT be
		# folded into the billing leg, and must leave the period unreconciled.
		# This is the coverage the ambient 2026 rows were producing by accident;
		# replacing them with fixtures is the only way to keep it.
		journal = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Bank Entry",
				"company": company,
				"posting_date": "2027-02-28",
				"cheque_no": "SAWT-TEST-JE",
				"cheque_date": "2027-02-28",
				"doc_type": "Bank Entry",
				"accounts": [
					{
						"account": account,
						"debit_in_account_currency": UNMATCHED_AMOUNT,
						"credit_in_account_currency": 0.0,
						"cost_center": self._cost_center(company),
					},
					{
						"account": self._counterpart_account(company),
						"debit_in_account_currency": 0.0,
						"credit_in_account_currency": UNMATCHED_AMOUNT,
						"cost_center": self._cost_center(company),
					},
				],
			}
		)
		journal.insert(ignore_permissions=True)
		journal.submit()

		with_journal = sawt_reconciliation.reconcile(Q1_START, Q1_END, company=company)
		self.assertEqual(
			[(u["voucher_no"], u["voucher_type"], flt(u["amount"])) for u in with_journal["unmatched_gl"]],
			[(journal.name, "Journal Entry", UNMATCHED_AMOUNT)],
			"the journal must be named in unmatched_gl with its voucher type and amount",
		)
		self.assertEqual(flt(with_journal["unmatched_gl_total"]), UNMATCHED_AMOUNT)
		self.assertEqual(
			flt(with_journal["billed_cwt_total"]),
			q1_expected,
			"a journal entry must not inflate the billed subledger",
		)
		self.assertEqual(flt(with_journal["gl_total"]), q1_expected + UNMATCHED_AMOUNT)
		self.assertEqual(flt(with_journal["variance"]), -UNMATCHED_AMOUNT, "the unmatched GL must not net away")
		self.assertFalse(with_journal["reconciled"], "unmatched GL must not report as reconciled")

		# ---- CLAUSE 7, SECOND HALF: a deliberately EMPTY window, run with every
		# fixture above sitting in the database. Identical result to the empty
		# window measured before them, so nothing this test created is reaching a
		# period it does not belong to.
		empty = sawt_reconciliation.reconcile(EMPTY_START, EMPTY_END, company=company)
		self._assert_clean_window(empty, 0.00, "empty window")
		self.assertEqual(empty["billings"], [])
		self.assertTrue(empty["reconciled"], "an empty window has nothing to reconcile and reconciles")
		self.assertEqual(empty["period_start"], EMPTY_START)
		self.assertEqual(empty["period_end"], EMPTY_END)

		# ---- EXPECT: the monthly form and its bounds come from the same code path.
		start, end = sawt_reconciliation.period_bounds("2027-02-15", "monthly")
		self.assertEqual(str(start), "2027-02-01")
		self.assertEqual(str(end), "2027-03-01")
		self.assertEqual(
			(sawt_reconciliation.period_bounds("2027-02-15", "quarterly")[0]).isoformat(), Q1_START
		)

		# ---- EXPECT: the CSV carries one line per billing, variance included.
		csv = sawt_reconciliation.to_csv(q1["billings"])
		self.assertIn("SAWT-2027-001", csv)
		self.assertIn("SAWT-2027-002", csv)
		self.assertIn("variance", csv.split("\n")[0])
