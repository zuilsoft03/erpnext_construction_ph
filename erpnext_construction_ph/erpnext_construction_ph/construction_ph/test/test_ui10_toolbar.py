# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-10: the floating toolbar.

Seven tools, and the two claims that are easy to get wrong:

  * **Isolate is not the inverse of Hide.** The spec describes it that way, which
    is true of the operation and false of the state. Tracking `hidden` and
    `isolated` as two flags lets them disagree -- isolate, then show a third
    element, and the model is in a state nobody asked for. So there is ONE hidden
    set and isolate is a rule over it, which is also why `effectiveHidden` needs
    the full GUID list: "everything else" cannot be enumerated from a selection.

  * **Reset is the camera AND the visibility.** A user who isolates and presses
    Reset expects the building back, not a well-framed photograph of nothing.

Plus: a tool whose component this build lacks says WHICH, because the toolbar is
for someone who cannot open a console.

The mutation is treating isolate as a plain hide of the complement -- the spec's
literal wording -- which loses the isolation when anything is shown afterwards.
"""

import os
import sys
import unittest

# js_harness is a SIBLING, imported by path rather than as a package member: the
# bench's loader and the host resolve `erpnext_construction_ph.construction_ph`
# differently, and a package-qualified import was green in one and
# ModuleNotFoundError in the other.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from js_harness import module_path, require_node, run_js  # noqa: E402

MODULE = "erpnext_construction_ph/public/js/bim/toolbar.js"
EXPORTS = ["createToolbar", "createVisibility", "TOOLS", "SECTIONS"]


def run(body):
	return run_js(body, module_path(MODULE), EXPORTS)


class TestToolbar(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path(MODULE)

	def test_the_seven_tools_are_present_and_ordered_as_the_spec_lists_them(self):
		result = run(
			"""
			const { TOOLS } = module.exports;
			console.log(JSON.stringify(TOOLS.map((t) => t.id)));
			"""
		)
		self.assertEqual(
			result,
			["select", "measure", "clip", "section", "hide", "isolate", "reset"],
		)

	def test_hide_accumulates_and_reset_clears_it(self):
		result = run(
			"""
			const { createVisibility } = module.exports;
			const v = createVisibility();
			v.hide(["a", "b"]);
			v.hide(["c"]);
			const hidden = v.effectiveHidden().sort();
			v.reset();
			console.log(JSON.stringify({ hidden, afterReset: v.effectiveHidden() }));
			"""
		)
		self.assertEqual(result["hidden"], ["a", "b", "c"])
		self.assertEqual(result["afterReset"], [])

	def test_isolate_hides_everything_except_the_selection(self):
		"""'Everything else' is not derivable from a selection, so the visibility
		state is CONSTRUCTED with the universe. Passing it per call instead was a
		footgun this file walked straight into: `isolate(["b"])` with no list
		quietly isolated nothing."""
		result = run(
			"""
			const { createVisibility } = module.exports;
			const v = createVisibility({ allGuids: ["a", "b", "c", "d"] });
			const hidden = v.isolate(["b"]).sort();
			console.log(JSON.stringify({ hidden, isolating: v.isIsolating() }));
			"""
		)
		self.assertEqual(result["hidden"], ["a", "c", "d"])
		self.assertTrue(result["isolating"])

	def test_isolate_is_not_just_hide_complement(self):
		"""The spec calls isolate 'the inverse of hide'. That is the OPERATION, not
		the state: after isolating, showing one element must restore it WITHOUT
		losing the isolation."""
		result = run(
			"""
			const { createVisibility } = module.exports;
			const v = createVisibility({ allGuids: ["a", "b", "c"] });
			v.isolate(["b"]);
			// The user notices a third element and shows it.
			const afterShow = v.show(["a"]).sort();
			console.log(JSON.stringify({ afterShow, stillIsolating: v.isIsolating() }));
			"""
		)
		# "a" was hidden BY THE ISOLATION, never added to the hidden set, so
		# `show(["a"])` has to extend the isolation's keep-set. It must NOT smuggle
		# the rest of the model back into view -- "c" stays hidden.
		self.assertEqual(result["afterShow"], ["c"])
		self.assertTrue(result["stillIsolating"])

	def test_a_tool_whose_component_is_missing_says_which(self):
		result = run(
			"""
			const { createToolbar } = module.exports;
			const host = document.createElement("div");
			// An engine with no measurement and no clipper components.
			const toolbar = createToolbar(host, { getEngine: () => ({ components: {} }) });
			const measure = toolbar.apply("measure");
			const clip = toolbar.apply("clip");
			console.log(JSON.stringify({ measure, clip }));
			"""
		)
		# The NAMES the tools now require. The test's intent is unchanged -- a tool
		# whose component is missing must say WHICH -- but it was written against
		# `LengthMeasurement` and `EdgesClipper`, which occur ZERO times in
		# @thatopen/components 3.4.8. The gate was amended to name what the build
		# actually ships (`Clipper`, `MeasurementUtils`), so these assertions follow;
		# a test that pins component names the engine cannot have is a test that
		# passes for the wrong reason.
		self.assertFalse(result["measure"]["ok"])
		self.assertIn("MeasurementUtils", result["measure"]["reason"])
		self.assertFalse(result["clip"]["ok"])
		self.assertIn("Clipper", result["clip"]["reason"])

	def test_hide_with_nothing_selected_is_refused_with_a_reason(self):
		result = run(
			"""
			const { createToolbar } = module.exports;
			const host = document.createElement("div");
			const toolbar = createToolbar(host, { getEngine: () => ({ components: {} }), getSelection: () => [] });
			console.log(JSON.stringify({ hide: toolbar.apply("hide"), isolate: toolbar.apply("isolate") }));
			"""
		)
		self.assertFalse(result["hide"]["ok"])
		self.assertIn("Select", result["hide"]["reason"])
		self.assertFalse(result["isolate"]["ok"])

	def test_reset_restores_the_camera_and_the_visibility(self):
		result = run(
			"""
			const { createToolbar } = module.exports;
			let fitted = 0;
			const host = document.createElement("div");
			const toolbar = createToolbar(host, {
				getEngine: () => ({ components: {}, fit: () => { fitted++; } }),
				getSelection: () => ["a"],
				getAllGuids: () => ["a", "b", "c"],
			});
			toolbar.apply("isolate");
			const whileIsolated = toolbar.visibility.effectiveHidden().sort();
			const outcome = toolbar.apply("reset");
			console.log(JSON.stringify({ fitted, whileIsolated, outcome, after: toolbar.visibility.effectiveHidden() }));
			"""
		)
		self.assertEqual(result["fitted"], 1, "the camera was not fitted")
		self.assertEqual(result["whileIsolated"], ["b", "c"])
		self.assertEqual(result["after"], [], "Reset left the model hidden")
