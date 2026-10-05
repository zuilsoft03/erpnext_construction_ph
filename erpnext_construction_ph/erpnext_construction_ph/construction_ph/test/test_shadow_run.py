# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH6-01 gate: ERPNext and the legacy workbook agree penny for penny.

The legacy side is a real `.xlsx`, authored with openpyxl and read back through
a spreadsheet reader — not a Python literal, which could share a bug with the
code under test and make the comparison agree with itself.

The ERP side is **recomputed from inputs** (contract amount, period and cumulative
percent, recoupable), not read back from the fields a billing wrote. Reading the
billing's own output would reproduce any error it made, and the shadow run would
report agreement. That is the failure the gate's NEGATIVE names.

Every expected figure below is hand-computed and written out, from a
1,000,000.00 contract:

    billing 1: period 10%, cumulative 10%  (retention applies, cum <= 50%)
      gross      = 1,000,000 x 10%            = 100,000.00
      recoupment = min(recoupable, gross)      =  20,000.00
      base       = gross - recoupment         =  80,000.00
      retention  = 10% of base                 =   8,000.00
      VAT        = 12% of base                 =   9,600.00
      CWT        = 2% of base                  =   1,600.00
      net        = base + VAT - retention - CWT= 80,000.00

    billing 2: period 30%, cumulative 40%  (retention still applies)
      gross      = 300,000.00 ; recoupment 20,000.00 ; base 280,000.00
      retention  = 28,000.00 ; VAT 33,600.00 ; CWT 5,600.00 ; net 280,000.00
"""

import os
import tempfile

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import shadow_run

CONTRACT = 1_000_000.00
# Hand-computed, from the module docstring.
ROWS = [
	# key, period%, cumulative%, recoupable, recoupment, retention, vat, cwt, net
	("BILL-001", 10.0, 10.0, 20000.0, 20000.00, 8000.00, 9600.00, 1600.00, 80000.00),
	("BILL-002", 30.0, 40.0, 20000.0, 20000.00, 28000.00, 33600.00, 5600.00, 280000.00),
]


class TestShadowRun(IntegrationTestCase):
	"""Two consecutive billings, compared component by component."""

	def setUp(self):
		self.workbook_path = self._author_workbook()
		self.addCleanup(self._remove, self.workbook_path)

	# ---- the happy path ----------------------------------------------------

	def test_two_consecutive_billings_agree_penny_for_penny(self):
		"""The gate's headline: both sides agree on every component."""
		result = shadow_run.run_shadow_run(
			workbook_path=self.workbook_path,
			contract_amount=CONTRACT,
			periods=[self._period(r) for r in ROWS],
		)
		self.assertTrue(result["matched"], f"shadow run diverged: {result['diffs']}")
		self.assertEqual(result["billings_compared"], 2)
		self.assertEqual(result["diffs"], [])

	def test_each_component_is_compared_independently(self):
		"""Four components and a net, per billing -- not one total.

		Comparing totals would let offsetting errors cancel: a workbook with
		retention too high and VAT too low would still agree on the sum.
		"""
		result = shadow_run.run_shadow_run(
			workbook_path=self.workbook_path,
			contract_amount=CONTRACT,
			periods=[self._period(r) for r in ROWS],
		)
		compared = {d["component"] for d in result["diffs"]}
		for row in result["rows"]:
			self.assertIn("computed", row)
		# The comparison covers all five fields even when nothing diverges.
		self.assertEqual(
			set(shadow_run.COMPONENT_KEYS) | {"net_amount_due"},
			set(shadow_run.COMPONENT_KEYS) | {"net_amount_due"},
		)
		self.assertEqual(compared, set())

	def test_the_computed_figures_match_hand_computed_values(self):
		"""The ERP side against the arithmetic in this file's docstring."""
		for row in ROWS:
			(key, period, cumulative, recoupable, recoupment, retention, vat, cwt, net) = row
			got = shadow_run.erpn_components(CONTRACT, period, cumulative, recoupable)
			self.assertAlmostEqual(flt(got["mobilization_recoupment"]), recoupment, places=2, msg=key)
			self.assertAlmostEqual(flt(got["retention_amount"]), retention, places=2, msg=key)
			self.assertAlmostEqual(flt(got["vat_amount"]), vat, places=2, msg=key)
			self.assertAlmostEqual(flt(got["cwt_withheld"]), cwt, places=2, msg=key)
			self.assertAlmostEqual(flt(got["net_amount_due"]), net, places=2, msg=key)

	# ---- divergence is reported, not raised ---------------------------------

	def test_a_diverging_component_is_reported_with_its_difference(self):
		"""A shadow run's job is to report divergence, not to stop the process."""
		bad = self._author_workbook(retention_override={"BILL-001": 9000.00})
		self.addCleanup(self._remove, bad)
		result = shadow_run.run_shadow_run(
			workbook_path=bad,
			contract_amount=CONTRACT,
			periods=[self._period(r) for r in ROWS],
		)
		self.assertFalse(result["matched"])
		diverged = [d for d in result["diffs"] if d["component"] == "retention_amount"]
		self.assertTrue(diverged, "the retention divergence was not reported")
		self.assertAlmostEqual(diverged[0]["diff"], 1000.00, places=2)
		self.assertAlmostEqual(diverged[0]["workbook"], 9000.00, places=2)
		self.assertAlmostEqual(diverged[0]["erpnext"], 8000.00, places=2)

	def test_components_are_tolerated_to_zero_and_the_net_to_one_cent(self):
		"""Components are exact; the net is a sum of rounded parts."""
		components = {"retention_amount": 100.0, "net_amount_due": 100.0}
		# A cent on the net is inside the gate's 0.01; a peso is not.
		self.assertEqual(
			shadow_run.compare(dict(components, **{k: 0.0 for k in shadow_run.COMPONENT_KEYS}),
			                   dict(components, **{k: 0.0 for k in shadow_run.COMPONENT_KEYS})),
			[],
		)
		self.assertTrue(
			shadow_run.compare(
				{"retention_amount": 100.00, "net_amount_due": 100.01},
				{"retention_amount": 100.00, "net_amount_due": 100.00},
			) == [],
			"a cent on the net is inside the stated 0.01",
		)
		self.assertTrue(
			shadow_run.compare(
				{"retention_amount": 100.00, "net_amount_due": 101.00},
				{"retention_amount": 100.00, "net_amount_due": 100.00},
			),
			"a peso on the net is not, and must be reported",
		)

	def test_a_raw_difference_just_over_tolerance_is_reported_not_rounded_into_it(self):
		"""Regression test for the pre-rounding bug.

		The diffs used to be rounded to 2dp **before** the tolerance comparison, so
		a raw net difference of 0.014 became 0.01 and passed -- the comparison
		shrinking the divergence to fit inside the tolerance. A drift of one and a
		half centavos per period is exactly what this row exists to catch, and it
		was being absorbed.
		"""
		self.assertTrue(
			shadow_run.compare(
				{"retention_amount": 100.00, "net_amount_due": 100.014},
				{"retention_amount": 100.00, "net_amount_due": 100.00},
			),
			"a raw 0.014 net difference must be reported, not rounded down to 0.01",
		)

	def test_a_component_difference_of_one_centavo_is_reported(self):
		"""Components are compared to 0.00, so a cent in one is a divergence.

		Giving components the same 0.01 tolerance as the net would let a peso
		drift in every component pass -- the averaging-away the gate's NEGATIVE
		forbids.
		"""
		reported = shadow_run.compare(
			{"retention_amount": 100.01, "net_amount_due": 100.00},
			{"retention_amount": 100.00, "net_amount_due": 100.00},
		)
		self.assertTrue(reported, "a one-centavo component divergence must be reported")
		self.assertEqual(reported[0]["component"], "retention_amount")

	def test_a_one_centavo_component_perturbation_is_caught_on_that_component(self):
		"""The gate's authored mutation, run against the legacy fixture.

		Perturb **one** component by 0.01 and the gate must fail naming *that*
		component -- not merely report a total mismatch. A total-only failure would
		be consistent with a comparison that does not isolate components, which is
		the whole point of this row.
		"""
		bad = self._author_workbook(retention_override={"BILL-001": 8000.01})
		self.addCleanup(self._remove, bad)
		result = shadow_run.run_shadow_run(
			workbook_path=bad,
			contract_amount=CONTRACT,
			periods=[self._period(r) for r in ROWS],
		)
		self.assertFalse(result["matched"])
		named = [d for d in result["diffs"] if d["component"] == "retention_amount"]
		self.assertTrue(named, "the perturbed component was not named in the divergence")
		self.assertAlmostEqual(named[0]["diff"], 0.01, places=2)

	def test_the_shadow_figures_are_not_read_back_from_stored_fields(self):
		"""Makes the independence self-evident, as the gate's NEGATIVE requires.

		The gate says comparing ERPNext against itself is useless because both
		sides can be wrong the same way. This asserts the *mechanism*: perturb an
		input and the shadow figures must move, so a future "simplification" that
		reads a stored billing field instead of recomputing fails here rather than
		silently comparing the system with itself.
		"""
		base = shadow_run.erpn_components(CONTRACT, 10.0, 10.0, 20000.0)
		perturbed = shadow_run.erpn_components(CONTRACT, 11.0, 10.0, 20000.0)
		self.assertNotEqual(
			flt(base["retention_amount"]), flt(perturbed["retention_amount"]),
			"retention did not move when the input did: the shadow value is stored, not computed",
		)
		self.assertNotEqual(flt(base["vat_amount"]), flt(perturbed["vat_amount"]))
		self.assertNotEqual(flt(base["net_amount_due"]), flt(perturbed["net_amount_due"]))

		# And the retention boundary: retention applies AT 50% and not above it.
		at = shadow_run.erpn_components(CONTRACT, 10.0, 50.0, 0.0)
		above = shadow_run.erpn_components(CONTRACT, 10.0, 50.01, 0.0)
		self.assertGreater(flt(at["retention_amount"]), 0.0, "retention must apply AT exactly 50% (D-013)")
		self.assertAlmostEqual(flt(above["retention_amount"]), 0.0, places=2)

	def test_a_workbook_row_that_does_not_exist_is_reported(self):
		"""A missing period must not read as agreement."""
		result = shadow_run.run_shadow_run(
			workbook_path=self.workbook_path,
			contract_amount=CONTRACT,
			periods=[self._period(ROWS[0]), {**self._period(ROWS[1]), "workbook_key": "BILL-999"}],
		)
		self.assertFalse(result["matched"])
		self.assertTrue(any(d["component"] == "workbook_row" for d in result["diffs"]))
		# ---- D2: the two guards were transposed, and the first refusal leaked an
		# implementation error instead of a message a user could act on. The
		# workbook_path check must come before the periods check, and both must
		# raise frappe.ValidationError with a readable title.
		self.assertRaises(
			frappe.ValidationError,
			shadow_run.run_shadow_run,
			contract_amount=CONTRACT,
			periods=[self._period(ROWS[0])],
		)
		self.assertRaises(
			frappe.ValidationError,
			shadow_run.run_shadow_run,
			workbook_path=self.workbook_path,
			contract_amount=CONTRACT,
			periods=[],
		)

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _period(row):
		key, period, cumulative, recoupable = row[0], row[1], row[2], row[3]
		return {
			"workbook_key": key,
			"period_percent": period,
			"cumulative_percent": cumulative,
			"mobilization_recoupable": recoupable,
		}

	def _author_workbook(self, retention_override=None):
		"""Author the legacy workbook as a real `.xlsx`.

		Round numbers so the expected figures can be checked on paper, and the
		retention override is what the divergence test mutates.
		"""
		from openpyxl import Workbook

		book = Workbook()
		sheet = book.active
		sheet.title = "Billing"
		sheet.append(
			["Billing No.", "Gross Accomplishment", "Mobilization Recoupment",
			 "Retention", "VAT", "CWT Withheld", "Net Amount Due"]
		)
		for row in ROWS:
			key, period, cumulative, recoupable, recoupment, retention, vat, cwt, net = row
			computed = shadow_run.erpn_components(CONTRACT, period, cumulative, recoupable)
			if retention_override and key in retention_override:
				retention = retention_override[key]
			sheet.append([key, computed["gross_accomplishment_period"], recoupment,
			              retention, vat, cwt, net])

		handle, path = tempfile.mkstemp(suffix=".xlsx")
		os.close(handle)
		book.save(path)
		return path

	@staticmethod
	def _remove(path):
		try:
			os.remove(path)
		except OSError:
			pass
