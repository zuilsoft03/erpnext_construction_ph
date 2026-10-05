# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-6: the split-view sync engine and the measurement comparison.

The spec's flow is: pick in 3D -> map the element to a region of the drawing ->
highlight it -> compare the model's measurement against the takeoff. Two of those
steps have no data behind them in this build, and the tests pin that they say so
rather than inventing it:

  * **No element-to-region map exists.** An unlinked element must report that it
    is unlinked, not highlight whatever is nearest.
  * **No per-element IFC area exists** (established in UI-5). So the comparison
    must refuse, and the refusal must be specific about WHICH side is missing --
    because "not comparable" with no reason leaves the user to guess whether the
    model, the takeoff, or the link is at fault.

And the arithmetic that IS available: the delta is SIGNED, because a difference of
0.56% does not say whether the contractor is under- or over-measuring, and that is
the direction a QS needs first.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from js_harness import module_path, require_node, run_js  # noqa: E402

MODULE = "erpnext_construction_ph/public/js/bim/sync_engine.js"
EXPORTS = ["createSyncEngine", "createLinkRegistry", "compare"]


def run(body):
	return run_js(body, module_path(MODULE), EXPORTS)


class TestSyncEngine(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path(MODULE)

	def test_a_linked_element_highlights_on_the_other_side(self):
		result = run(
			"""
			const { createSyncEngine } = module.exports;
			const engine = createSyncEngine();
			engine.links.link("g1", { sheet: "TKF-001", region: { x: 10, y: 20, w: 30, h: 40 }, area: 89.3 });
			engine.setEnabled(true);
			const d = engine.select("g1", "3d");
			console.log(JSON.stringify({ enabled: d.enabled, other: d.other, linked: d.linked }));
			"""
		)
		self.assertEqual(result["other"]["sheet"], "TKF-001")
		self.assertEqual(result["other"]["region"]["w"], 30)
		self.assertTrue(result["linked"])

	def test_an_unlinked_element_says_so_and_highlights_nothing(self):
		"""No map exists in this build, so this is the COMMON case, not an edge case.
		Highlighting the nearest region would be a confident wrong answer."""
		result = run(
			"""
			const { createSyncEngine } = module.exports;
			const engine = createSyncEngine();
			engine.setEnabled(true);
			const d = engine.select("g-unlinked", "3d");
			console.log(JSON.stringify({ other: d.other, linked: d.linked, reason: d.reason }));
			"""
		)
		self.assertIsNone(result["other"], "an unlinked element highlighted something")
		self.assertFalse(result["linked"])
		self.assertIn("not linked", result["reason"])

	def test_sync_off_highlights_nothing_and_is_not_destructive(self):
		"""Turning sync off must leave both viewports as they are, not clear them --
		otherwise 'off' costs the user their place."""
		result = run(
			"""
			const { createSyncEngine } = module.exports;
			const engine = createSyncEngine();
			engine.links.link("g1", { sheet: "S", region: { x: 1 }, area: 5 });
			engine.setEnabled(true);
			const on = engine.select("g1", "3d");
			engine.setEnabled(false);
			const off = engine.select("g1", "3d");
			console.log(JSON.stringify({ onOther: on.other, offOther: off.other, stillSelected: engine.selected() }));
			"""
		)
		self.assertIsNotNone(result["onOther"])
		self.assertIsNone(result["offOther"])
		self.assertEqual(result["stillSelected"], "g1", "turning sync off discarded the selection")

	def test_a_selection_is_remembered_while_sync_is_off(self):
		"""So turning sync ON does not make the user click again."""
		result = run(
			"""
			const { createSyncEngine } = module.exports;
			const engine = createSyncEngine();
			engine.links.link("g1", { sheet: "S", region: { x: 1 }, area: 5 });
			engine.select("g1", "3d");          // while off
			const before = engine.describe();
			engine.setEnabled(true);
			const after = engine.describe();
			console.log(JSON.stringify({ beforeOther: before.other, afterOther: after.other }));
			"""
		)
		self.assertIsNone(result["beforeOther"])
		self.assertIsNotNone(result["afterOther"], "enabling sync did not pick up the selection")

	def test_the_comparison_is_signed_and_says_which_side_is_short(self):
		result = run(
			"""
			const { compare } = module.exports;
			console.log(JSON.stringify({
				short: compare(89.0, 89.5),     // the model measures LESS
				over: compare(90.0, 89.5),      // the model measures MORE
				same: compare(89.5, 89.5),
			}));
			"""
		)
		self.assertLess(result["short"]["delta"], 0, "a shortfall did not read as negative")
		self.assertGreater(result["over"]["delta"], 0)
		self.assertAlmostEqual(result["short"]["delta_percent"], -0.558, places=2)
		self.assertTrue(result["same"]["within_tolerance"])
		self.assertFalse(result["short"]["within_tolerance"])

	def test_a_missing_measurement_refuses_and_names_the_side(self):
		"""'Not comparable' with no reason leaves the user guessing whether the
		model, the takeoff or the link is at fault."""
		result = run(
			"""
			const { compare } = module.exports;
			console.log(JSON.stringify({
				noModel: compare(null, 89.3),
				noTakeoff: compare(89.3, null),
				neither: compare(null, null),
				zeroTakeoff: compare(5, 0),
				zeroModel: compare(0, 89.3),
			}));
			"""
		)
		# `Number(null)` is 0, so a missing model measurement once became a real
		# zero and printed "delta -100%". Both the missing case and the genuine
		# degenerate zero are refused, symmetrically.
		for key in ("noModel", "noTakeoff", "neither", "zeroTakeoff", "zeroModel"):
			self.assertFalse(result[key]["comparable"], f"{key} was treated as comparable")
		self.assertIn("model has no measurement", result["noModel"]["reason"])
		self.assertIn("takeoff has no measurement", result["noTakeoff"]["reason"])
		self.assertIn("zero", result["zeroTakeoff"]["reason"])
		self.assertIn("measures zero", result["zeroModel"]["reason"])

	def test_the_current_selection_compares_or_explains_itself(self):
		result = run(
			"""
			const { createSyncEngine } = module.exports;
			const engine = createSyncEngine();
			const nothing = engine.comparison(10);
			engine.links.link("g1", { sheet: "S", region: {}, area: 89.3 });
			engine.select("g1", "3d");
			const linked = engine.comparison(89.0);
			engine.links.link("g2", { sheet: "S", region: {} });   // region, no measurement
			engine.select("g2", "3d");
			const unmeasured = engine.comparison(89.0);
			console.log(JSON.stringify({ nothing, linked, unmeasured }));
			"""
		)
		self.assertFalse(result["nothing"]["comparable"])
		self.assertTrue(result["linked"]["comparable"])
		# A region can be LINKED without being MEASURED, and the two are different.
		self.assertFalse(result["unmeasured"]["comparable"])
		self.assertIn("takeoff has no measurement", result["unmeasured"]["reason"])
