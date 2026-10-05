# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-4 client half: the capture, and the order the submit has to happen in.

Two claims under test.

  * **The capture happens on OPEN, not on submit.** `canvas.toDataURL()` reads the
    framebuffer; by the time the dialog is open the engine may have re-rendered
    and that frame is gone. So the context is captured before the user types, and
    the test pins that the camera in the captured context is the one at open time
    even if the engine has moved since.

  * **The snapshot is stored BEFORE the topic is created, and the PATH is what the
    viewpoint gets.** `add_viewpoint(snapshot=...)` takes a file path and requires
    one; the browser produces a data URL. The plan records the loop as
    unimplementable as specified because nobody wrote this step. The test asserts
    the ORDER, because getting it right by accident and getting it right by design
    look identical until the data URL is rejected.

And the failure that must not be silent: a canvas the browser refuses to read
(tainted, or never drawn) must still let the issue be filed, saying so.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from js_harness import module_path, require_node, run_js  # noqa: E402

MODULE = "erpnext_construction_ph/public/js/bim/issue_modal.js"
EXPORTS = ["captureContext", "createIssueModal"]


def run(body):
	return run_js(body, module_path(MODULE), EXPORTS)


class TestIssueModal(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path(MODULE)

	def test_the_snapshot_and_the_camera_are_captured_together(self):
		result = run(
			"""
			const { captureContext } = module.exports;
			const canvas = { toDataURL: () => "data:image/png;base64,AAA", clientWidth: 800, clientHeight: 600 };
			const camera = { eye: {x:1,y:2,z:3}, target: {x:0,y:0,z:0}, up: {x:0,y:1,z:0}, screenX: 200, screenY: 300 };
			const ctx = captureContext({ canvas, camera, selection: ["g1","g2"], model: "M", project: "P" });
			console.log(JSON.stringify(ctx));
			"""
		)
		self.assertTrue(result["snapshot"].startswith("data:image/png"))
		self.assertEqual(result["camera_vectors"]["eye"], [1, 2, 3])
		self.assertEqual(result["element_guids"], ["g1", "g2"])
		# 200 of 800 = 25%, and 300 of 600 = 50%.
		self.assertEqual(result["camera_vectors"]["screen_x"], 25)
		self.assertEqual(result["camera_vectors"]["screen_y"], 50)

	def test_an_unreadable_canvas_still_lets_the_issue_be_filed(self):
		"""A tainted canvas THROWS on toDataURL. Uncaught, that loses the dialog
		and with it everything the user had typed."""
		result = run(
			"""
			const { captureContext } = module.exports;
			const canvas = { toDataURL: () => { throw new Error("tainted"); }, clientWidth: 800 };
			const ctx = captureContext({ canvas, camera: null, selection: ["g"], model: "M", project: "P" });
			console.log(JSON.stringify({ snapshot: ctx.snapshot, err: ctx.snapshotError || null,
			                          camera: ctx.camera_vectors }));
			"""
		)
		self.assertIsNone(result["snapshot"], "a throw was swallowed into a value")
		self.assertTrue(result["err"], "the failure was not reported to the user")
		self.assertIsNone(result["camera"])

	def test_screen_positions_are_clamped_into_the_viewport(self):
		"""A pin outside 0..100 is invisible AND unclickable, which reads as an
		issue that was never filed."""
		result = run(
			"""
			const { captureContext } = module.exports;
			const canvas = { toDataURL: () => "x", clientWidth: 800, clientHeight: 600 };
			const out = captureContext({ canvas, camera: { eye:{x:0,y:0,z:0}, target:{x:0,y:0,z:0}, up:{x:0,y:1,z:0}, screenX: 5000, screenY: -40 },
				selection: [], model: "M", project: "P" });
			console.log(JSON.stringify({ x: out.camera_vectors.screen_x, y: out.camera_vectors.screen_y }));
			"""
		)
		self.assertEqual(result["x"], 100)
		self.assertEqual(result["y"], 0)

	def test_the_snapshot_is_stored_before_the_viewpoint_and_gets_a_path(self):
		"""The order the plan records as unimplementable without it."""
		result = run(
			"""
			const { createIssueModal } = module.exports;
			const calls = [];
			const host = document.createElement("div");
			const modal = createIssueModal(host, {
				storeSnapshot: async (a) => { calls.push(["storeSnapshot", a.project]); return { file_url: "/files/x.png" }; },
				onCreate: async (a) => { calls.push(["onCreate", a.title]); return { topic_guid: "g1", name: "T1" }; },
				addViewpoint: async (a) => { calls.push(["addViewpoint", a.snapshot, a.topic_guid]); return {}; },
			});
			modal.open({ snapshot: "data:image/png;base64,AAA", camera_vectors: { eye: [1,2,3] },
			              element_guids: ["e1"], model: "M", project: "P" });
			host.querySelector(".bim-issue-name").value = "Duct clashes with beam";
			await host.querySelector(".bim-issue-create").onclick();
			console.log(JSON.stringify({ calls, stillOpen: modal.isOpen() }));
			"""
		)
		order = [c[0] for c in result["calls"]]
		self.assertEqual(
			order, ["storeSnapshot", "onCreate", "addViewpoint"],
			f"wrong order: {order}. add_viewpoint needs a PATH; the browser has a data URL.",
		)
		# And the viewpoint is handed the PATH, not the data URL.
		self.assertEqual(result["calls"][2][1], "/files/x.png")
		self.assertEqual(result["calls"][2][2], "g1")
		self.assertFalse(result["stillOpen"], "the dialog stayed open after a successful create")

	def test_a_titleless_issue_is_refused_rather_than_filed_blank(self):
		result = run(
			"""
			const { createIssueModal } = module.exports;
			const calls = [];
			const host = document.createElement("div");
			const modal = createIssueModal(host, {
				storeSnapshot: async () => { calls.push("storeSnapshot"); return { file_url: "/f.png" }; },
				onCreate: async () => { calls.push("onCreate"); return { topic_guid: "g" }; },
				addViewpoint: async () => { calls.push("addViewpoint"); return {}; },
			});
			modal.open({ snapshot: null, camera_vectors: null, element_guids: [], model: "M", project: "P" });
			host.querySelector(".bim-issue-name").value = "   ";
			await host.querySelector(".bim-issue-create").onclick();
			console.log(JSON.stringify({ calls, error: (host.querySelector(".bim-issue-error")||{}).textContent,
			                          stillOpen: modal.isOpen() }));
			"""
		)
		self.assertEqual(result["calls"], [], "a blank issue was filed")
		self.assertIn("title", result["error"].lower())
		self.assertTrue(result["stillOpen"], "the dialog closed on a refused submit")

	def test_no_snapshot_means_no_viewpoint_call(self):
		"""add_viewpoint REQUIRES a snapshot path, so calling it with none is a
		server error the user would have to interpret."""
		result = run(
			"""
			const { createIssueModal } = module.exports;
			const calls = [];
			const host = document.createElement("div");
			const modal = createIssueModal(host, {
				storeSnapshot: async () => { calls.push("storeSnapshot"); return { file_url: "/f.png" }; },
				onCreate: async () => { calls.push("onCreate"); return { topic_guid: "g" }; },
				addViewpoint: async () => { calls.push("addViewpoint"); return {}; },
			});
			modal.open({ snapshot: null, camera_vectors: { eye: [0,0,1] }, element_guids: [], model: "M", project: "P" });
			host.querySelector(".bim-issue-name").value = "Something";
			await host.querySelector(".bim-issue-create").onclick();
			console.log(JSON.stringify({ calls }));
			"""
		)
		self.assertEqual(result["calls"], ["onCreate"])
