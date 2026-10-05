# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-3: BCF issue pins, and the camera jump.

Two claims, and the second is the one that matters operationally:

  1. **Pins are positioned from a normalised coordinate, not from the server's
     percentage.** The spec positions with `left: ${screen_x}%`, which is only right
     while the viewport is exactly the size the percentage was computed against --
     and `screen_x`/`screen_y` come from the server, computed against the canvas the
     issue was raised from. So the pin drifts off the thing it names the moment
     anyone resizes. The test resizes the host and asserts the pin follows the same
     RELATIVE position, not the same pixels.

  2. **Clicking a pin moves the camera, and a viewpoint that cannot be flown to
     says so.** A pin that does nothing looks like a broken feature rather than
     like an issue raised from a sheet instead of the model -- which is a real state
     in this app, not a hypothetical.

The mutation is the spec's literal form: position in raw percentages, so the pin
holds its pixel position while the viewport changes size.
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

MODULE = "erpnext_construction_ph/public/js/bim/pin_overlay.js"
EXPORTS = ["createPinOverlay", "pinColour", "flyTo", "PIN_COLOURS"]


def run(body):
	return run_js(body, module_path(MODULE), EXPORTS)


class TestPinOverlay(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path(MODULE)

	def test_a_pin_holds_its_relative_position_across_a_resize(self):
		"""The drift the spec's percentage form would produce."""
		result = run(
			"""
			const { createPinOverlay } = module.exports;
			// A host with a controllable layout size, which is what a real
			// container reports and what the percentages were NOT computed against.
			const host = document.createElement("div");
			host.clientWidth = 1000;
			host.clientHeight = 500;
			const overlay = createPinOverlay(host, () => {});
			overlay.setTopics([
				{ topic_guid: "a", title: "A", status: "Open", screen_x: 60, screen_y: 25 },
			]);
			const at1000 = { x: overlay.get("a").el.style.left, y: overlay.get("a").el.style.top };

			// The panel opens; the canvas gets narrower and shorter.
			host.clientWidth = 400;
			host.clientHeight = 200;
			overlay.layout();
			const at400 = { x: overlay.get("a").el.style.left, y: overlay.get("a").el.style.top };

			console.log(JSON.stringify({ at1000, at400 }));
			"""
		)
		# 60% of 1000 = 600px, then 60% of 400 = 240px.
		self.assertEqual(result["at1000"]["x"], "600.0px")
		self.assertEqual(result["at400"]["x"], "240.0px", "the pin did not follow the viewport")
		self.assertEqual(result["at400"]["y"], "50.0px")

	def test_pins_are_removed_when_their_topic_goes_away(self):
		"""Otherwise a reopened panel accumulates ghosts that still answer clicks."""
		result = run(
			"""
			const { createPinOverlay } = module.exports;
			const host = document.createElement("div");
			host.clientWidth = 800; host.clientHeight = 600;
			const overlay = createPinOverlay(host, () => {});
			overlay.setTopics([
				{ topic_guid: "a", status: "Open", screen_x: 10, screen_y: 10 },
				{ topic_guid: "b", status: "Open", screen_x: 20, screen_y: 20 },
			]);
			const afterTwo = overlay.count();
			overlay.setTopics([{ topic_guid: "a", status: "Open", screen_x: 10, screen_y: 10 }]);
			const afterOne = overlay.count();
			const ghosts = overlay.element.children.length;
			console.log(JSON.stringify({ afterTwo, afterOne, ghosts }));
			"""
		)
		self.assertEqual(result["afterTwo"], 2)
		self.assertEqual(result["afterOne"], 1, "a pin outlived its topic")
		self.assertEqual(result["ghosts"], 1, "the removed pin is still in the DOM")

	def test_pin_colour_follows_status_and_unknown_ones_still_render(self):
		"""An unmapped status must not make an issue invisible."""
		result = run(
			"""
			const { pinColour } = module.exports;
			console.log(JSON.stringify({
				open: pinColour("Open"),
				progress: pinColour("In Progress"),
				resolved: pinColour("Resolved"),
				closed: pinColour("Closed"),
				unknown: pinColour("Something New"),
			}));
			"""
		)
		self.assertEqual(result["open"], "#e03131")
		self.assertEqual(result["resolved"], "#2f9e44")
		self.assertTrue(result["unknown"], "an unmapped status produced no colour at all")

	def test_clicking_a_pin_reports_the_topic(self):
		result = run(
			"""
			const { createPinOverlay } = module.exports;
			const host = document.createElement("div");
			host.clientWidth = 800; host.clientHeight = 600;
			const picked = [];
			const overlay = createPinOverlay(host, (t) => picked.push(t.topic_guid));
			overlay.setTopics([
				{ topic_guid: "a", status: "Open", screen_x: 10, screen_y: 10 },
				{ topic_guid: "b", status: "Open", screen_x: 20, screen_y: 20 },
			]);
			overlay.get("b").el.click();
			console.log(JSON.stringify({ picked, count: picked.length }));
			"""
		)
		self.assertEqual(result["picked"], ["b"], "the pin reported the wrong topic")

	def test_flying_to_a_viewpoint_moves_the_camera(self):
		result = run(
			"""
			const { flyTo } = module.exports;
			const calls = [];
			const engine = {
				world: { camera: { controls: { setLookAt: (e, t, u) => calls.push([e, t, u]) } } },
				highlight: (guids, colour) => calls.push(["highlight", guids, colour]),
			};
			const ok = flyTo(engine, {
				eye: [10, 20, 30], target: [1, 2, 3], up: [0, 1, 0],
				element_guids: ["abc", "def"],
			}, { highlight: true });
			console.log(JSON.stringify({ ok, calls }));
			"""
		)
		self.assertTrue(result["ok"]["ok"])
		self.assertEqual(result["calls"][0], [[10, 20, 30], [1, 2, 3], [0, 1, 0]])
		self.assertEqual(result["calls"][1], ["highlight", ["abc", "def"], "red"])

	def test_a_viewpoint_with_no_camera_says_so_instead_of_doing_nothing(self):
		"""An issue raised from a sheet has no camera. That is a real state."""
		result = run(
			"""
			const { flyTo } = module.exports;
			let called = 0;
			const engine = { world: { camera: { controls: { setLookAt: () => { called++; } } } } };
			const empty = flyTo(engine, {});
			const noEngine = flyTo(null, { eye: [1, 2, 3] });
			console.log(JSON.stringify({ empty, noEngine, called }));
			"""
		)
		self.assertFalse(result["empty"]["ok"])
		self.assertIn("eye point", result["empty"]["reason"])
		self.assertFalse(result["noEngine"]["ok"])
		self.assertEqual(result["called"], 0, "the camera was moved with no viewpoint")
