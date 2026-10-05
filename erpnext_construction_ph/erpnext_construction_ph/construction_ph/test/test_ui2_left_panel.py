# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-2: the left panel adapts per tab, and adapting costs the surveyor nothing.

Two claims are under test, and only one of them is visible:

  1. WHICH cards are shown per viewport mode. That is the spec's table -- 3D gets
     Models/Tree/Sheets, PDF gets Calibration/Capture/Captured, split gets all four
     with calibration condensed. Easy to assert, easy to pass, and worth little on
     its own.

  2. That switching tabs does not DISCARD what the user established. A surveyor
     who has calibrated against a known 10 m wall and typed a description should
     not lose either by glancing at the model. This is the claim that actually
     matters and the one a re-render-per-tab implementation silently breaks -- the
     panel still looks correct afterwards, so nothing reports the loss.

So the tests here assert both, and the mutation is the obvious wrong
implementation: rebuild the panel on every mode change.
"""

import unittest

# js_harness is a SIBLING, imported by path rather than as a package member.
# The two runners resolve `erpnext_construction_ph.construction_ph` differently --
# frappe's loader puts the app's own directory on sys.path inside the bench, and
# nothing does on the host -- so a package-qualified import was green in the
# container and ModuleNotFoundError on the host. A path-relative import resolves
# the same way in both, or in neither.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from js_harness import module_path, require_node, run_js  # noqa: E402

# `node` exists in this stack but is not on the PATH `bench run-tests` gives a
# subprocess (it lives under /home/frappe/.nvm). Hardcoding "node" made this test
# pass by hand on the host and ERROR in the container with a bare OSError, which
# says nothing about the panel. Resolve it, and skip honestly if it is truly absent
# -- the same approach the UI-0 tests use, deliberately, so the two agree.




class TestLeftPanel(unittest.TestCase):
	"""Per-tab card visibility, and the state that must survive it."""

	def setUp(self):
		require_node(self)
		self.module = module_path('erpnext_construction_ph/public/js/bim/left_panel.js')

	def test_each_tab_shows_the_cards_the_spec_gives_it(self):
		"""The spec's per-tab table, asserted rather than eyeballed."""
		result = run_js("""
			const { createLeftPanel } = module.exports;
			const root = document.createElement("div");
			const panel = createLeftPanel(root, {});
			const shown = (mode) => {
				panel.setMode(mode);
				return Object.keys(panel.cards).filter((k) => !panel.cards[k].el.hidden);
			};
			console.log(JSON.stringify({
				"3d": shown("3d"),
				pdf: shown("pdf"),
				split: shown("split"),
			}));
			""",
			self.module,
			["createLeftPanel", "PANEL_MODES"])
		self.assertEqual(result["3d"], ["models", "tree", "sheets"])
		self.assertEqual(result["pdf"], ["calibration", "capture", "captured"])
		self.assertEqual(result["split"], ["models", "tree", "calibration", "sheets"])

	def test_a_calibration_survives_a_tab_change(self):
		"""The claim a re-render-per-tab panel breaks without reporting it."""
		result = run_js("""
			const { createLeftPanel } = module.exports;
			const root = document.createElement("div");
			const panel = createLeftPanel(root, {});
			panel.setCalibration({ metres_per_unit: 0.0042, known_length_m: 10, points: [[0, 0], [2380, 0]] });

			// The surveyor checks the scale against the model, then comes back.
			panel.setMode("3d");
			panel.setMode("split");
			panel.setMode("pdf");

			const s = panel.getState();
			console.log(JSON.stringify({
				scale: s.calibration && s.calibration.metres_per_unit,
				known: s.calibration && s.calibration.known_length_m,
				// The points are what make a RE-calibration possible without
				// re-clicking in the viewport, which is the expensive part.
				pointsKept: s.calibration && s.calibration.points && s.calibration.points.length,
			}));
			""",
			self.module,
			["createLeftPanel", "PANEL_MODES"])
		self.assertAlmostEqual(result["scale"], 0.0042, places=6)
		self.assertAlmostEqual(result["known"], 10, places=6)
		self.assertEqual(result["pointsKept"], 2, "the calibration points were dropped")

	def test_captured_items_accumulate_across_tabs(self):
		result = run_js("""
			const { createLeftPanel } = module.exports;
			const root = document.createElement("div");
			const panel = createLeftPanel(root, {});
			panel.addCaptured({ description: "Slab F1", quantity: 245.8, unit: "m2" });
			panel.setMode("3d");
			panel.addCaptured({ description: "Wall Grid-A", quantity: 89.3, unit: "m2" });
			panel.setMode("pdf");
			console.log(JSON.stringify({ captured: panel.getState().captured.length }));
			""",
			self.module,
			["createLeftPanel", "PANEL_MODES"])
		self.assertEqual(result["captured"], 2)

	def test_models_dropdown_reports_the_choice(self):
		"""The handler is the only thing that makes the dropdown mean anything."""
		result = run_js("""
			const { createLeftPanel } = module.exports;
			const picked = [];
			const root = document.createElement("div");
			const panel = createLeftPanel(root, { onModelChange: (n) => picked.push(n) });
			panel.setModels([{ name: "ARCH", model_name: "Architecture", conversion_status: "ready" }], "ARCH");
			// Simulate the user picking something else.
			const select = panel.cards.models.body.children[0];
			select.value = "MEP";
			if (select.onchange) select.onchange();
			console.log(JSON.stringify({ picked, hasSelect: !!select }));
			""",
			self.module,
			["createLeftPanel", "PANEL_MODES"])
		self.assertTrue(result["hasSelect"], "no dropdown was built")
		self.assertEqual(result["picked"], ["MEP"], "choosing a model did not report upward")

	def test_an_empty_project_says_what_to_do(self):
		"""A blank panel is indistinguishable from a broken one."""
		result = run_js("""
			const { createLeftPanel } = module.exports;
			const root = document.createElement("div");
			const panel = createLeftPanel(root, {});
			panel.setModels([], null);
			panel.setSheets([]);
			panel.setTree([]);
			console.log(JSON.stringify({
				models: panel.cards.models.body.innerHTML,
				sheets: panel.cards.sheets.body.innerHTML,
				tree: panel.cards.tree.body.innerHTML,
			}));
			""",
			self.module,
			["createLeftPanel", "PANEL_MODES"])
		self.assertIn("No models", result["models"])
		self.assertIn("No takeoff sheets", result["sheets"])
		self.assertIn("No spatial tree", result["tree"])
