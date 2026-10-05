# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""UNRESOLVED, CORE-16: cross-check between the SHIPPING controller and the shadow run.

**TWO RETENTION BASES EXIST AND THEY ARE 12% APART. WHICH ONE CIAP 102 SPECIFIES IS
NOT YET KNOWN, AND THIS MODULE DOES NOT GUESS.**

1. SHIPPING controller -- `ConstructionProgressBilling.calculate_certification`
   (`construction_ph/doctype/construction_progress_billing/construction_progress_billing.py:160,168`)

       net_before_retention = taxable_base + vat_amount
       retention_amount     = net_before_retention * 0.10     # VAT-INCLUSIVE base

   On BILL-001 (1,000,000 contract, 10% period, 20,000 recoupment):
   retention = PHP **8,960.00**, net_amount_due = PHP 79,040.00.

2. SHADOW harness -- `erpnext_construction_ph.api.shadow_run.erpn_components`
   (`api/shadow_run.py:56`)

       retention_amount = taxable_base * 0.10                 # VAT-EXCLUSIVE base

   On the SAME claim: retention = PHP **8,000.00**, net_amount_due = PHP 80,000.00.

`8,000 x 1.12 = 8,960` exactly, so the ratio is 1.12 wherever retention is
non-zero and 1.00 where it is not. The two implementations **agree on
`net_before_retention` itself** (89,600.00 both) -- the shadow HAS the
VAT-inclusive figure and simply does not charge retention on it. This is a
definitional difference, not arithmetic drift.

**This is NOT a claim that the controller is wrong.** The shadow's VAT-exclusive
reading may equally be the misreading. Nobody in this project has ever held the
DPWH-published CIAP 102 document: that is **CORE-16, BLOCKED** on an input only
the user can supply. D-013 forbids quoting itself as source-verified. Until
CORE-16 closes, the honest status of the retention figure is **"two readings,
12% apart, unresolved"**.

**WHAT THIS MODULE DOES.** It calls the REAL shipping controller (never a
second implementation -- D-025) and the REAL shadow helper on IDENTICAL inputs,
asserts the components both readings agree on to 0.00, and pins the retention
divergence as exactly the VAT gross-up across the whole 0-50% band where
retention is non-zero, plus both sides of the 50% milestone boundary (D-013:
`cum <= 50%` retains, `> 50%` stops).

**WHERE THE TWO DISAGREE IS MEASURED, AND IT IS NOT THE BOUNDARY.** Both
implementations reach the SAME withhold/stop decision at cum 49.99%, at exactly
50.00%, at 50.01% and at 60.00%. The D-013 `<=` milestone rule is therefore NOT
what CORE-46 is about: **the entire 12% gap is the retention BASE**, and it is
present at every point in the band where retention fires, boundary included.
This narrows what the DPWH document has to settle -- **a clause about `<` vs `<=`
would leave 8,960 vs 8,000 untouched.** The open source question is the base.

**WHAT IT DELIBERATELY DOES NOT DO.** It does not choose a base, and it does not
compare the two against each other where they disagree and call that a pass.
"""

# This module PINS the disagreement on purpose. `assertNotEqual` below is not
# tautology: if a future change makes the two agree, this suite goes RED, and that
# is the correct outcome rather than a regression to be silenced. It means the
# retention base changed somewhere other than the DPWH CIAP 102 source document.
# The fix for that red is to close CORE-16 with the published workbook and rewrite
# this module to assert ONE base, citing the document. Do NOT relax the assertion
# to make it green.

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import shadow_run
from erpnext_construction_ph.construction_ph.doctype.construction_progress_billing.construction_progress_billing import (
	ConstructionProgressBilling,
)

from erpnext_construction_ph.construction_ph.doctype.construction_progress_billing import (
	construction_progress_billing as _shipping,
)

#: The CIAP 102 rates, written as SPEC LITERALS -- deliberately, and this is the one
#: place in the codebase where a copied constant is the right call.
#:
#: The obvious objection is that a literal is a second place the rate is written down and
#: will drift. The answer is what the literal is FOR: it is the **spec anchor**, and the
#: test below asserts that BOTH implementations equal it. If the anchor were read from
#: either implementation instead, a joint drift in both would move the expectation with
#: it and go undetected -- measured, not assumed: the identity
#: `controller - shadow == RETENTION_RATE * vat_amount` holds for ANY rate the two
#: SHARE, so importing the rate makes that assertion blind to a joint change.
#:
#: With the literal, a joint drift fails by name (rate parity vs CIAP 102) instead of
#: passing silently. The cost of a duplicated constant is therefore paid deliberately
#: against the benefit of a spec assertion, not by accident.
#:
#: CIAP 102: 10% retention, 12% VAT. See D-013 for our reading of the base, which is
#: itself NOT source-verified -- CORE-16 is BLOCKED on the published document.
CIAP_102_RETENTION_RATE = 0.10
CIAP_102_VAT_RATE = 0.12

#: What each implementation currently declares, read from the modules themselves.
SHIPPING_RETENTION_RATE = _shipping.RETENTION_RATE
SHIPPING_VAT_RATE = _shipping.VAT_RATE
SHADOW_RETENTION_RATE = shadow_run.RETENTION_RATE
SHADOW_VAT_RATE = shadow_run.VAT_RATE

#: Components both readings agree on. If any of these diverge, that is a NEW and
#: separate defect -- not the known retention-base question.
UNDISPUTED_COMPONENTS = (
	"gross_accomplishment_period",
	"taxable_base",
	"vat_amount",
	"net_before_retention",
	"cwt_withheld",
)

#: The whole 0-50% band where retention is NON-ZERO, which is the region where a
#: contractor actually has money withheld. A fixture sitting only past 50% would
#: pass while the money-withholding region went entirely uncovered.
#: label, contract, period_pct, cumulative_amount, recoupment
BAND_0_TO_50_CASES = [
	("BILL-001 (cum 10% <= 50%)", 1_000_000.0, 10.0, 100_000.0, 20_000.0),
	("BILL-002 (cum 40% <= 50%)", 1_000_000.0, 30.0, 400_000.0, 20_000.0),
	("CYC-01 (contract 2.5M, cum 10%)", 2_500_000.0, 100.0, 250_000.0, 375_000.0),
	("intermediate (cum 25%, recoupment 50,000)", 2_000_000.0, 15.0, 500_000.0, 50_000.0),
]

#: D-013 boundary asserted on BOTH sides. `should_retain` encodes `cum <= 50%` retains.
#: label, contract, period_pct, cumulative_amount, recoupment, should_retain
MILESTONE_BOUNDARY_CASES = [
	("just BELOW the milestone (cum 49.99% <= 50% -> retains)", 1_000_000.0, 10.0, 499_900.0, 10_000.0, True),
	("AT the 50% milestone exactly (cum 50.00% <= 50% -> retains)", 1_000_000.0, 10.0, 500_000.0, 10_000.0, True),
	("JUST PAST the milestone (cum 50.01% > 50% -> stops)", 1_000_000.0, 10.0, 500_100.0, 10_000.0, False),
	("well past the milestone (cum 60.00% > 50% -> stops)", 1_000_000.0, 10.0, 600_000.0, 20_000.0, False),
]

#: The WITHHOLD/STOP decision alone, walked across the boundary. This is the case
#: that isolates WHERE the two implementations differ -- and the measurement is
#: that they do NOT differ here. Both withhold at 49.99% and at exactly 50.00%,
#: and both stop at 50.01%. The D-013 `<=` milestone rule is therefore NOT the
#: dispute; the entire 12% gap is the retention BASE (D-013 pt 2), and it is
#: present at every point in the band where retention fires, boundary included.
#: label, contract, period_pct, cumulative_amount, recoupment, should_withhold
MILESTONE_DECISION_CASES = [
	("cum 49.99%", 1_000_000.0, 10.0, 499_900.0, 10_000.0, True),
	("cum 50.00% exactly", 1_000_000.0, 10.0, 500_000.0, 10_000.0, True),
	("cum 50.01%", 1_000_000.0, 10.0, 500_100.0, 10_000.0, False),
	("cum 60.00%", 1_000_000.0, 10.0, 600_000.0, 20_000.0, False),
]


class TestCore46UnresolvedRetentionBase(IntegrationTestCase):
	"""UNRESOLVED, CORE-16: two retention bases, 12% apart, authoritative one NOT YET KNOWN.

	The shipping controller charges 10% retention on a VAT-INCLUSIVE base
	(`net_before_retention`); the shadow harness charges it on a VAT-EXCLUSIVE base
	(`taxable_base`). Both are executed for real, on identical inputs, below.

	NEITHER IS ASSERTED CORRECT. Deciding which one CIAP 102 specifies requires the
	DPWH-published CIAP 102 document, which this project does not have -- CORE-16,
	BLOCKED. Everything retention-dependent in the ledger is marked
	"unverified: bases disagree -- CORE-46" for that reason.
	"""

	def _run_pair(self, contract, period_pct, cumulative, recoupment):
		"""Drive the SHIPPING controller and the shadow helper on IDENTICAL inputs.

		The controller side is an in-memory `Construction Progress Billing` whose
		`calculate_certification()` is called directly: nothing is saved, submitted or
		written. `type(doc).calculate_certification` is asserted against the shipping
		class so a future edit cannot quietly substitute a local reimplementation here
		and make this gate compare a thing against itself -- the exact failure
		GATES.md recorded for `test_shadow_run.py` and `run_shadow_run`.
		"""
		doc = frappe.new_doc("Construction Progress Billing")  # in-memory only; never saved
		doc.contract_amount = contract
		doc.period_accomplishment_percent = period_pct
		doc.cumulative_accomplishment = cumulative
		doc.mobilization_recoupment = recoupment

		self.assertIs(
			type(doc).calculate_certification,
			ConstructionProgressBilling.calculate_certification,
			"the ERP side of this cross-check must be the SHIPPING controller's own method",
		)
		doc.calculate_certification()  # the REAL shipping path (D-025)

		# The shadow takes the cumulative as a PERCENT; the controller takes it as a
		# peso figure and derives the percent itself. Identical inputs means the same
		# cumulative expressed in each side's own units -- the ratio is computed the
		# way the controller computes it, so the milestone decision is the same one.
		cumulative_percent = flt(cumulative) / flt(contract) * 100.0
		self.assertAlmostEqual(flt(doc.cumulative_percent), cumulative_percent, places=6)
		shadow = shadow_run.erpn_components(contract, period_pct, cumulative_percent, recoupment)
		return doc, shadow

	def _assert_undisputed_components_agree(self, label, doc, shadow):
		for key in UNDISPUTED_COMPONENTS:
			self.assertAlmostEqual(
				flt(getattr(doc, key)),
				flt(shadow[key]),
				places=2,
				msg=f"{label}: `{key}` must agree between the two implementations",
			)

	# ---- 1a clause 1 + 3: the components both readings agree on, across 0-50% ----

	def test_UNRESOLVED_CORE16_both_implementations_agree_on_every_undisputed_component(self):
		"""Gross, taxable base, VAT, net-before-retention and CWT agree to 0.00 across the 0-50% band.

		Those five are the components the retention-base question does NOT touch. If
		one of them diverges, that is a separate arithmetic defect and this gate says
		so rather than reporting it as agreement.
		"""
		for label, contract, period_pct, cumulative, recoupment in BAND_0_TO_50_CASES:
			doc, shadow = self._run_pair(contract, period_pct, cumulative, recoupment)
			self._assert_undisputed_components_agree(label, doc, shadow)

	def test_UNRESOLVED_CORE16_both_implementations_charge_the_CIAP_102_rates(self):
		"""Each implementation's rate must equal the SPEC's — not merely each other's.

		Both the retention rate and the VAT rate are asserted against the CIAP 102 spec
		value this module holds as a deliberate literal. Two things break if that is
		skipped, and both are invisible to a name check:

		* If ONE implementation's rate moved, the gap assertion below would fail for a
		  reason that has nothing to do with the base. Saying so here reports it by NAME
		  instead of as a baffling base-dispute failure.
		* If BOTH moved together, the two stay equal to each other and
		  `controller - shadow == rate * vat_amount` still holds for any rate they
		  share -- so parity cannot see it, and neither can a rate imported from either
		  module, because the expectation moves with the code. Only the spec literal can.
		  MUTANT E is the proof: setting BOTH to 0.12 turns this test red on the spec rate
		  (`0.1 != 0.12 ... but CIAP 102 specifies 0.1`), and also turns the gap assertions
		  red (1152.00 vs 960.00) -- because the spec literal is what moved the expectation.
		  Under a parity-only or imported-rate assertion those gap checks would have passed.
		"""
		for name, declared in (
			("shipping controller ConstructionProgressBilling", SHIPPING_RETENTION_RATE),
			("shadow harness shadow_run.erpn_components", SHADOW_RETENTION_RATE),
		):
			self.assertEqual(
				flt(CIAP_102_RETENTION_RATE), flt(declared),
				f"the {name} declares a retention rate of {declared}, but CIAP 102 specifies "
				f"{CIAP_102_RETENTION_RATE}. A rate that drifts in BOTH implementations leaves "
				"them equal to each other and satisfies the gap identity for any shared rate, "
				"so this is the only assertion that can see it. This is a NEW defect and is not "
				"the retention-base question, which stays UNRESOLVED under CORE-16.",
			)

		for name, declared in (
			("shipping controller ConstructionProgressBilling", SHIPPING_VAT_RATE),
			("shadow harness shadow_run.erpn_components", SHADOW_VAT_RATE),
		):
			self.assertEqual(
				flt(CIAP_102_VAT_RATE), flt(declared),
				f"the {name} declares a VAT rate of {declared}, but CIAP 102 specifies "
				f"{CIAP_102_VAT_RATE}. `vat_amount` is the shared term the 12% gap is measured "
				"against, so a drift here would move the expected gap with it.",
			)

	# ---- 1a clause 1 + 3 + 5: the disagreement itself, pinned as unresolved ----

	def test_UNRESOLVED_CORE16_retention_bases_differ_by_exactly_the_12_percent_vat_rate(self):
		"""UNRESOLVED, CORE-16: in the 0-50% band the two bases are exactly 1.12 apart.

		This is the finding this gate exists to hold. It asserts the divergence is
		PRECISELY the VAT gross-up and not drift, rounding or an unrelated slip --
		and it asserts neither reading is authoritative, because that is CORE-16's
		answer to give, from a document this project does not have.
		"""
		for label, contract, period_pct, cumulative, recoupment in BAND_0_TO_50_CASES:
			doc, shadow = self._run_pair(contract, period_pct, cumulative, recoupment)
			controller_retention = flt(doc.retention_amount)
			shadow_retention = flt(shadow["retention_amount"])

			self.assertGreater(
				controller_retention, 0.0,
				f"{label}: fixture must sit where retention is actually withheld, or it proves nothing",
			)
			self.assertGreater(
				shadow_retention, 0.0,
				f"{label}: shadow retention must be non-zero here too",
			)

			# The disagreement is real and is NOT being tolerated silently.
			self.assertNotEqual(
				controller_retention, shadow_retention,
				f"{label}: UNRESOLVED CORE-16 -- if the bases now agree, CORE-16 has been answered "
				"and this module must be rewritten against the DPWH source document, not relaxed",
			)

			# ... and the gap between them is EXACTLY the retention applied to the VAT
			# both of them agree on. Stated as a GAP rather than as one side equalling
			# the other times 1.12, because "controller == shadow x 1.12" puts the shadow
			# at the base of the multiplication and quietly makes it the reference
			# figure. Neither reading is established as correct -- that is CORE-16's call
			# from the DPWH document -- so the assertion must not have a referent.
			#
			# `vat_amount` is a component BOTH implementations compute identically
			# (asserted in test 1). Using it as the explanation is therefore symmetric by
			# construction: neither implementation supplies the reference value, the
			# shared VAT figure does.
			retention_gap = flt(round(controller_retention - shadow_retention, 2))
			expected_gap = flt(round(flt(doc.vat_amount) * CIAP_102_RETENTION_RATE, 2))
			self.assertAlmostEqual(
				retention_gap, expected_gap, places=2,
				msg=f"{label}: the two readings must differ by exactly the retention the two "
				f"bases dispute -- 10% of the VAT they AGREE on. Gap {retention_gap} vs "
				f"expected {expected_gap} ({CIAP_102_VAT_RATE:.0%} of VAT {flt(doc.vat_amount)}). "
				"The gap is measured, not derived from either reading, so neither is the reference.",
			)

			# Direction of MAGNITUDE is asserted; direction of CORRECTNESS is not. The
			# VAT-inclusive base covers strictly more money, so its figure is the larger
			# one. That is a fact about today's code and tells you nothing about which
			# reading CIAP 102 requires -- those are different questions, and conflating
			# them is the failure this module exists to avoid.
			self.assertEqual(
				controller_retention > shadow_retention, True,
				f"{label}: MEASURED FACT, not a verdict on correctness -- the VAT-INCLUSIVE "
				f"reading is the larger figure ({controller_retention} vs {shadow_retention})",
			)

			# The retention base is the ONLY difference, so it must be the ONLY
			# difference: the gap in the net is exactly the gap in retention, with no
			# second offsetting error hiding inside it.
			net_gap = flt(round(flt(shadow["net_amount_due"]) - flt(doc.net_amount_due), 2))
			# `retention_gap` is already bound above, from the same two figures.
			self.assertAlmostEqual(
				net_gap, retention_gap, places=2,
				msg=f"{label}: the net difference ({net_gap}) must be entirely the retention "
				f"difference ({retention_gap}); anything else is a second defect",
			)

		# The figures the ledger quotes for BILL-001, pinned so a drift in either
		# implementation is a red suite rather than a quietly different report.
		doc, shadow = self._run_pair(1_000_000.0, 10.0, 100_000.0, 20_000.0)
		self.assertAlmostEqual(flt(doc.retention_amount), 8_960.00, places=2, msg="BILL-001 controller retention")
		self.assertAlmostEqual(flt(shadow["retention_amount"]), 8_000.00, places=2, msg="BILL-001 shadow retention")
		self.assertAlmostEqual(flt(doc.net_before_retention), flt(shadow["net_before_retention"]), places=2)

	# ---- 1a: D-013 boundary asserted on BOTH sides, in BOTH implementations ----

	def test_UNRESOLVED_CORE16_retention_milestone_boundary_is_50_percent_on_both_sides(self):
		"""D-013: `cum <= 50%` retains, `cum > 50%` stops -- asserted exactly, on both sides.

		Exactly 50.00% still withholds; the first peso past it stops withholding. A
		test that only checked "past the milestone" could not tell `<=` from `<`, and
		that off-by-one is the difference between a contractor's money being held and
		released one claim early.
		"""
		for label, contract, period_pct, cumulative, recoupment, should_retain in MILESTONE_BOUNDARY_CASES:
			doc, shadow = self._run_pair(contract, period_pct, cumulative, recoupment)
			controller_retention = flt(doc.retention_amount)
			shadow_retention = flt(shadow["retention_amount"])

			self._assert_undisputed_components_agree(label, doc, shadow)

			if should_retain:
				self.assertEqual(
					doc.retention_milestone_reached, 0,
					f"{label}: the milestone flag must not be raised at or before 50%",
				)
				self.assertGreater(controller_retention, 0.0, f"{label}: the controller must withhold at <= 50%")
				self.assertGreater(shadow_retention, 0.0, f"{label}: the shadow must withhold at <= 50%")
				# Still the same unresolved divergence -- the boundary does not
				# accidentally reconcile the bases. Stated as a GAP against the shared,
				# mutually-agreed VAT figure rather than as one reading being the other
				# times 1.12, for the same reason as in the band test: an assertion with a
				# referent quietly settles which base is the reference, and that is
				# CORE-16's call, not this test's.
				self.assertAlmostEqual(
					flt(round(controller_retention - shadow_retention, 2)),
					flt(round(flt(doc.vat_amount) * CIAP_102_RETENTION_RATE, 2)),
					places=2,
					msg=f"{label}: at the milestone the gap must still be exactly the 10% of "
					"the VAT both readings agree on",
				)
			else:
				self.assertEqual(
					doc.retention_milestone_reached, 1,
					f"{label}: the milestone flag must be raised past 50%",
				)
				self.assertEqual(controller_retention, 0.0, f"{label}: controller retention must be 0.00 past the milestone")
				self.assertEqual(shadow_retention, 0.0, f"{label}: shadow retention must be 0.00 past the milestone")
				# Past the milestone the two agree on EVERY component, net included --
				# which is the control proving the divergence is confined to the base.
				self.assertAlmostEqual(
					flt(doc.net_amount_due), flt(shadow["net_amount_due"]), places=2,
					msg=f"{label}: with retention at 0.00 both sides must agree on the net to the peso",
				)

	def test_UNRESOLVED_CORE16_the_milestone_decision_is_NOT_where_the_two_disagree(self):
		"""MEASURED: both implementations make the SAME withhold/stop decision at 49.99 / 50.00 / 50.01 / 60.00.

		**The D-013 `<=` milestone rule is not the dispute.** The whole 12% gap is the
		retention BASE (D-013 pt 2), and it is present at every point in the band where
		retention fires -- boundary included.

		Recorded because it narrows what CORE-16 has to settle: **a source clause about
		`<` vs `<=` would leave the 8,960 vs 8,000 gap untouched.** Only the base does.
		So the open question to put to DPWH is the BASE, not the boundary.
		"""
		for label, contract, period_pct, cumulative, recoupment, should_withhold in MILESTONE_DECISION_CASES:
			doc, shadow = self._run_pair(contract, period_pct, cumulative, recoupment)
			controller_withholds = flt(doc.retention_amount) > 0.0
			shadow_withholds = flt(shadow["retention_amount"]) > 0.0

			self.assertEqual(
				controller_withholds, shadow_withholds,
				f"{label}: the two implementations must reach the SAME withhold/stop decision -- "
				"if they diverge here the disagreement is about the milestone, not the base",
			)
			self.assertEqual(
				controller_withholds, should_withhold,
				f"{label}: D-013 says `cum <= 50%` retains and `> 50%` stops",
			)
			self.assertEqual(
				doc.retention_milestone_reached, 0 if should_withhold else 1,
				f"{label}: the recorded milestone flag must match the decision actually taken",
			)
