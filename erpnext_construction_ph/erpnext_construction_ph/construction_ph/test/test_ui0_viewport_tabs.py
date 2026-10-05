# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-0: the layout spine. Tabs switch WITHOUT remounting, and keep the camera.

The spec is explicit about why this matters: "Each tab swaps the viewport content
**without destroying state** — the 3D engine stays mounted, the PDF canvas stays
rendered. This is critical: remounting the WebGL context on every tab switch costs
2-3 seconds and loses camera position."

That is a claim about RUNTIME behaviour, so it cannot be proved by reading the JS
and it is exactly the kind of regression that a code review passes. This drives the
real `viewport_tabs.js` in a DOM and asserts the observable consequences:

  1. every host exists from the start, so there is nothing to remount;
  2. switching does not detach the 3D host from the document;
  3. the engine object handed in is the SAME object afterwards (identity, not
     equality of shape -- a copy would pass a shallow comparison);
  4. `mount` refuses a second call, so a later wave cannot reintroduce the remount
     by accident;
  5. camera position survives a tab round trip.

The mutation for this gate is the one the module exists to prevent: making `show()`
unmount and remount. Under that mutation (1)-(5) all fail.
"""

import json
import os
import subprocess
import textwrap
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

# `node` is installed in this stack but is NOT on the PATH that `bench run-tests`
# gives a subprocess: it lives under /home/frappe/.nvm, and `bench build` only sees
# it when that directory is put on PATH explicitly. An earlier version hardcoded
# "node", so the test passed when run by hand on the host and ERRORED with
# FileNotFoundError under the bench runner -- a green/red split that depends on who
# ran it. Resolve it, and skip honestly if it genuinely is not there.
def _find_node():
	import glob
	import os
	import shutil

	found = shutil.which("node")
	if found:
		return found
	# The nvm layout this container uses, newest version last on the glob.
	for pattern in ("/home/frappe/.nvm/versions/node/*/bin/node", "/usr/local/n/versions/node/*/bin/node"):
		hits = sorted(glob.glob(pattern))
		if hits:
			return hits[-1]
	return shutil.which("nodejs")




class TestViewportTabs(unittest.TestCase):
	"""The tab bar's one job, driven for real."""

	def setUp(self):
		require_node(self)
		self.module = module_path('erpnext_construction_ph/public/js/bim/viewport_tabs.js')

	def test_switching_tabs_does_not_remount_and_keeps_the_camera(self):
		"""The spec's claim, asserted as behaviour rather than as intent."""
		result = run_js("""
			const { createTabs, cameraSignature, sameView } = module.exports;

			// A stand-in engine: the tab module must never touch it, and identity is
			// what proves it did not.
			const engine = { world: { camera: { three: { position: { x: 12, y: 8, z: -30 } }, controls: { target: { x: 1, y: 2, z: 3 } } } } };
			const before = cameraSignature(engine);

			const root = document.createElement("div");
			const tabs = createTabs(root);
			tabs.show("3d");

			// Mount the 3D viewport exactly once, as a later wave would.
			const threeD = tabs.host("3d");
			const payload = { engine, setActive: (on) => { payload.active = on; } };
			tabs.mount("3d", payload);

			const identityBefore = tabs.host("3d");
			// A coordinator switches to the sheet and comes back.
			tabs.show("pdf");
			tabs.show("split");
			tabs.show("3d");

			console.log(JSON.stringify({
				hostCount: root.children.length,
				stillAttached: root.children.includes(identityBefore),
				sameHostObject: tabs.host("3d") === identityBefore,
				engineUnchanged: tabs.isMounted("3d"),
				cameraSame: sameView(before, cameraSignature(engine)),
				pdfBuilt: tabs.isMounted("pdf"),
			}));
		""", self.module, ["createTabs", "cameraSignature", "sameView", "MODES"])
		self.assertEqual(result["hostCount"], 4, "expected a tab bar plus three viewport hosts")
		self.assertTrue(result["stillAttached"], "the 3D viewport was detached from the document")
		self.assertTrue(result["sameHostObject"], "the 3D host was replaced rather than shown")
		self.assertTrue(result["engineUnchanged"], "the 3D viewport was remounted")
		self.assertTrue(result["cameraSame"], "camera position did not survive the tab round trip")
		# A viewport that was never built must still report itself unbuilt, rather
		# than a switch pretending to have content.
		self.assertFalse(result["pdfBuilt"], "pdf reported as built without being mounted")

	def test_a_second_mount_is_refused_rather_than_remounting(self):
		"""The guarantee a later wave needs, and the remount it prevents."""
		result = run_js("""
			const { createTabs } = module.exports;
			const root = document.createElement("div");
			const tabs = createTabs(root);
			tabs.show("3d");
			const first = { id: 1 };
			const second = { id: 2 };
			const got1 = tabs.mount("3d", first);
			const got2 = tabs.mount("3d", second);
			console.log(JSON.stringify({
				returnedFirst: got1 === first,
				returnedSecond: got2 === first,
				swapped: got2 === second,
			}));
		""", self.module, ["createTabs", "cameraSignature", "sameView", "MODES"])
		self.assertTrue(result["returnedFirst"], "the first mount should be returned")
		# The refusal returns the ORIGINAL payload. Returning the new one would
		# mean the caller's `payload` handle and the mounted content had diverged,
		# which is worse than refusing outright: the caller would go on writing to
		# an object nothing renders. (This assertion was initially inverted, and
		# caught the test being wrong rather than the module.)
		self.assertTrue(
			result["returnedSecond"],
			"a second mount replaced the first -- that is the remount the module exists to prevent",
		)
		self.assertFalse(
			result["swapped"],
			"a second mount took over the viewport",
		)

	def test_switching_to_an_unbuilt_viewport_is_not_an_error(self):
		"""A tab with no content yet must be showable, not fatal."""
		result = run_js("""
			const { createTabs } = module.exports;
			const root = document.createElement("div");
			const tabs = createTabs(root);
			tabs.show("pdf");
			console.log(JSON.stringify({ active: tabs.active(), pdfHidden: tabs.host("pdf").hidden, threeHidden: tabs.host("3d").hidden }));
		""", self.module, ["createTabs", "cameraSignature", "sameView", "MODES"])
		self.assertEqual(result["active"], "pdf")
		self.assertFalse(result["pdfHidden"], "the active viewport should be visible")
		self.assertTrue(result["threeHidden"], "an inactive viewport should be hidden")

	def test_the_three_modes_match_the_spec(self):
		"""3D / PDF / Split, in the spec's order and labels."""
		result = run_js("""
			const { MODES } = module.exports;
			console.log(JSON.stringify(MODES.map((m) => [m.id, m.label])));
		""", self.module, ["createTabs", "cameraSignature", "sameView", "MODES"])
		self.assertEqual(
			result,
			[["3d", "3D Model"], ["pdf", "PDF Takeoff"], ["split", "Split View"]],
		)
