# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-1: the PDF viewport. The Y-flip, undo/redo, and multi-page.

The spec's WARNING names one thing to preserve: `to_page_units()` flips Y because
PDF's origin is bottom-left and the canvas's is top-left, and "it's correct."

It is -- and it is only half the pair. The original also drew those PDF-space
points back onto a top-left-origin canvas with `([x, y]) => [x, (y / h) * h]`,
which is `[x, y]`: the flip cancelled algebraically instead of being undone by a
flip. Every marker was therefore drawn VERTICALLY MIRRORED from where the surveyor
clicked, and because both calibration points mirror together the error is
invisible until someone checks a captured area against a known one.

So the first test here is a ROUND TRIP: a click at a known screen position must
come back to the same screen position after going through PDF user space. That test
fails on the original implementation and passes on this one, which is the whole
point of extracting it.

The rest: undo/redo (the spec's "the QS has to clear and restart on a misclick"),
multi-page navigation, calibration persisting ACROSS pages, and counts being
per-page.
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





class TestPdfViewport(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path('erpnext_construction_ph/public/js/bim/pdf_viewport.js')

	def test_a_click_survives_the_round_trip_through_pdf_space(self):
		"""The Y-flip, both directions. This is the defect the spec's WARNING missed."""
		result = run_js("""
			const { makeConverter } = module.exports;
			const W = 800, H = 1000;
			const rect = { left: 50, top: 20, width: 800, height: 1000 };
			const conv = makeConverter(W, H);

			// Click positions across the whole canvas, including the edges.
			const clicks = [[50, 20], [450, 520], [850, 1020], [50, 1020], [850, 20]];
			const roundTrip = clicks.map(([x, y]) => {
				const pdfPoint = conv.to_page_units(x, y, rect);
				const [cx, cy] = conv.to_canvas_point(pdfPoint, H);
				return { click: [x - rect.left, y - rect.top], back: [cx, cy] };
			});

			// And the flip is a real flip, not an identity: a click near the TOP of
			// the canvas must land NEAR THE BOTTOM in PDF user space, whose origin
			// is bottom-left. If this ever becomes true, the round trip is passing
			// for the wrong reason -- two bugs cancelling.
			const topClick = conv.to_page_units(50, 20, rect);
			const bottomClick = conv.to_page_units(50, 1020, rect);

			console.log(JSON.stringify({
				roundTrip,
				topClickY: topClick[1],
				bottomClickY: bottomClick[1],
				pageHeight: H,
			}));
			""",
			self.module,
			["makeConverter", "createHistory", "createPdfViewport", "MODES"])
		for entry in result["roundTrip"]:
			for got, want in zip(entry["back"], entry["click"]):
				self.assertAlmostEqual(
					got, want, places=6,
					msg=f"a click at {entry['click']} drew at {entry['back']} -- the "
						"marker is drawn somewhere other than where it was clicked",
				)
		# The flip must be genuine, and inverted relative to the canvas.
		self.assertGreater(result["topClickY"], result["pageHeight"] * 0.9)
		self.assertLess(result["bottomClickY"], result["pageHeight"] * 0.1)

	def test_undo_and_redo_move_one_click_at_a_time(self):
		"""The spec: "the QS has to clear and restart on a misclick"."""
		result = run_js("""
			const { createHistory } = module.exports;
			const h = createHistory();
			const log = [];
			h.onChange((s) => log.push([s.canUndo, s.canRedo]));

			h.push({ kind: "point", point: [1, 1] });
			h.push({ kind: "point", point: [2, 2] });
			h.push({ kind: "point", point: [3, 3] });
			const a = h.undo();
			const b = h.undo();
			const c = h.undo();          // nothing left
			const r = h.redo();
			const r2 = h.redo();
			const r3 = h.redo();         // nothing in the future
			console.log(JSON.stringify({
				undone: [a && a.point, b && b.point, c && c.point],
				redone: [r && r.point, r2 && r2.point, r3 && r3.point],
				states: log,
			}));
			""",
			self.module,
			["makeConverter", "createHistory", "createPdfViewport", "MODES"])
		# Pushed 1, 2, 3 -- so undo must return 3, 2, 1, and then nothing.
		# (This assertion was written forwards the first time, and caught the TEST
		# being wrong rather than the stack.)
		self.assertEqual(result["undone"], [[3, 3], [2, 2], [1, 1]], "undo did not pop LIFO")
		# Three undos means three redos are available, replaying oldest-first.
		# (Asserted as two, expecting None at the end -- the test was wrong; the
		# stack was right, and consistent with the LIFO assertion above.)
		self.assertEqual(result["redone"], [[1, 1], [2, 2], [3, 3]], "redo did not replay in order")

	def test_a_new_action_discards_the_redo_branch(self):
		"""Redoing into a future that no longer follows from what was done is a lie."""
		result = run_js("""
			const { createHistory } = module.exports;
			const h = createHistory();
			h.push({ kind: "point", point: [1, 1] });
			h.push({ kind: "point", point: [2, 2] });
			h.undo();
			const couldRedoBefore = h.canRedo();
			h.push({ kind: "point", point: [9, 9] });
			console.log(JSON.stringify({ couldRedoBefore, canRedoAfter: h.canRedo(), depth: h.depth() }));
			""",
			self.module,
			["makeConverter", "createHistory", "createPdfViewport", "MODES"])
		self.assertTrue(result["couldRedoBefore"])
		self.assertFalse(result["canRedoAfter"], "a new action left a stale redo branch")

	def test_the_four_measurement_modes_are_present(self):
		result = run_js("""
			const { MODES } = module.exports;
			console.log(JSON.stringify(MODES.map((m) => [m.id, m.unit])));
			""",
			self.module,
			["makeConverter", "createHistory", "createPdfViewport", "MODES"])
		self.assertEqual(
			result,
			[["area", "m2"], ["length", "m"], ["count", "no"], ["perimeter", "m"]],
		)

	def test_navigation_stays_on_the_document_and_keeps_the_calibration(self):
		"""Page 1 -> 3 -> 1, with the calibration intact and the count per page."""
		result = run_js("""
			const { createPdfViewport } = module.exports;
			const host = document.createElement("div");

			// A stand-in for a two-page PDF. The point is page navigation and what
			// survives it, not rasterising anything.
			let rendered = [];
			const fakeDoc = {
				numPages: 3,
				async getPage(n) {
					return {
						getViewport: () => ({ width: 800, height: 1000 }),
						render: ({ viewport }) => {
							rendered.push(n);
							return { promise: Promise.resolve() };
						},
					};
				},
			};

			const vp = createPdfViewport(host, {
				loadPdf: async () => fakeDoc,
			});
			vp.canvas.getContext = () => null;   // no canvas 2D in this shim
			vp.canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 1000 });

			await vp.open({ arrayBuffer: async () => new ArrayBuffer(8) });
			vp.setCalibration({ metres_per_unit: 0.0042, known_length_m: 10, points: [[10, 990], [790, 990]] });

			// Count three on page 1, then look at page 3. The mode has to be set
			// first: the viewport opens in "area", where a click is a polygon
			// vertex rather than a count.
			vp.setMode("count");
			for (const p of [[100, 100], [200, 200], [300, 300]]) {
				vp.canvas.dispatch("click", { clientX: p[0], clientY: p[1] });
			}
			await vp.showPage(3);
			const onPage3 = vp.getState();
			await vp.showPage(1);
			const backOnPage1 = vp.getState();

			console.log(JSON.stringify({
				rendered,
				page: backOnPage1.page,
				calibrationKept: backOnPage1.calibration && backOnPage1.calibration.metres_per_unit,
				page1Counts: backOnPage1.pageCounts["1"],
				page3Counts: onPage3.pageCounts["3"] || 0,
				totalCounts: backOnPage1.counts.length,
			}));
			""",
			self.module,
			["makeConverter", "createHistory", "createPdfViewport", "MODES"])
		self.assertEqual(result["rendered"], [1, 3, 1], "the wrong pages were rendered")
		self.assertEqual(result["page"], 1)
		self.assertAlmostEqual(result["calibrationKept"], 0.0042, places=6,
							   msg="the calibration did not survive a page change")
		self.assertEqual(result["page1Counts"], 3)
		self.assertEqual(result["page3Counts"], 0, "counts leaked onto a page nothing was counted on")
		self.assertEqual(result["totalCounts"], 3)

	def test_undo_removes_exactly_one_click_from_the_viewport(self):
		result = run_js("""
			const { createPdfViewport } = module.exports;
			const host = document.createElement("div");
			const fakeDoc = {
				numPages: 1,
				async getPage() {
					return {
						getViewport: () => ({ width: 800, height: 1000 }),
						render: () => ({ promise: Promise.resolve() }),
					};
				},
			};
			const vp = createPdfViewport(host, { loadPdf: async () => fakeDoc });
			vp.canvas.getContext = () => null;
			vp.canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 1000 });
			await vp.open({ arrayBuffer: async () => new ArrayBuffer(8) });

			vp.canvas.dispatch("click", { clientX: 100, clientY: 100 });
			vp.canvas.dispatch("click", { clientX: 200, clientY: 200 });
			vp.canvas.dispatch("click", { clientX: 300, clientY: 300 });
			const afterThree = vp.getState().polygon.length;
			vp.undo();
			const afterUndo = vp.getState().polygon.length;
			vp.redo();
			const afterRedo = vp.getState().polygon.length;
			console.log(JSON.stringify({ afterThree, afterUndo, afterRedo }));
			""",
			self.module,
			["makeConverter", "createHistory", "createPdfViewport", "MODES"])
		self.assertEqual(result["afterThree"], 3)
		self.assertEqual(result["afterUndo"], 2, "undo did not remove exactly one vertex")
		self.assertEqual(result["afterRedo"], 3, "redo did not restore it")
