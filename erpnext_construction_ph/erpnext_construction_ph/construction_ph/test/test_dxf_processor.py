# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md PH2-02 — CAD DXF layer extraction.

The gate selects this by METHOD name (D-014), so every assertion lives in the
single ``test_dxf_processor`` method.

Expected layer totals are authored by hand from the fixture geometry and written
next to each layer, so the assertions are not simply read back from the parser.
"""

import math
import os
import tempfile

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import dxf_processor as dxf

# (layer, entity, expected total in drawing units)
EXPECTED = {
	# LINE (0,0)->(3,4) is 5, LINE (0,0)->(6,8) is 10  ->  15
	"A-COLS": 15.0,
	# LINE (0,0)->(12,0) is 12
	"A-BEAM": 12.0,
	# closed LWPOLYLINE rectangle 4 x 3 -> 4+3+4+3 = 14
	"M-PIPE": 14.0,
}


class TestDxfProcessor(IntegrationTestCase):
	"""Per-layer length extraction, and refusal of things that are not DXF."""

	def _fixture(self):
		"""Author a DXF with known geometry and return its path."""
		import ezdxf

		doc = ezdxf.new("R2010")
		msp = doc.modelspace()

		msp.add_line((0, 0), (3, 4), dxfattribs={"layer": "A-COLS"})
		msp.add_line((0, 0), (6, 8), dxfattribs={"layer": "A-COLS"})
		msp.add_line((0, 0), (12, 0), dxfattribs={"layer": "A-BEAM"})

		square = msp.add_lwpolyline(
			[(0, 0), (4, 0), (4, 3), (0, 3)], dxfattribs={"layer": "M-PIPE"}
		)
		square.closed = True

		handle, path = tempfile.mkstemp(suffix=".dxf", prefix="dxf_fixture_")
		os.close(handle)
		doc.saveas(path)
		return path

	def test_dxf_processor(self):
		path = self._fixture()
		try:
			# ---- EXPECT: three layers, each matching the authored total.
			lengths = dxf.parse_dxf_takeoff(path)
			self.assertEqual(
				set(lengths), set(EXPECTED), "every fixture layer must be reported"
			)
			self.assertGreaterEqual(len(lengths), 3, "the gate requires at least 3 layers")
			for layer, expected in EXPECTED.items():
				self.assertAlmostEqual(lengths[layer], expected, places=3, msg=layer)

			# A 3-4-5 line is exactly 5, so the geometry is being measured, not
			# approximated.
			self.assertTrue(math.isclose(lengths["A-COLS"] - 5.0 - 10.0, 0.0, abs_tol=1e-6))

			# ---- EXPECT: a single layer can be requested on its own.
			only = dxf.parse_dxf_takeoff(path, target_layer="M-PIPE")
			self.assertEqual(only, {"M-PIPE": 14.0})
			self.assertEqual(dxf.parse_dxf_takeoff(path, target_layer="NOPE"), {})

			# ---- EXPECT: layers present in the drawing are enumerable.
			self.assertEqual(
				dxf.available_layers(path), ["A-BEAM", "A-COLS", "M-PIPE"]
			)

			# ---- NEGATIVE: a file that is not a DXF is refused by name.
			not_dxf = tempfile.mkstemp(suffix=".txt", prefix="not_a_drawing_")[1]
			try:
				with open(not_dxf, "w", encoding="utf-8") as handle:
					handle.write("just some text, not a drawing")
				with self.assertRaises(dxf.DxfParseError) as ctx:
					dxf.parse_dxf_takeoff(not_dxf)
				self.assertIn("not a DXF", str(ctx.exception))
			finally:
				os.remove(not_dxf)

			# ---- NEGATIVE: a missing file is refused, not silently empty.
			with self.assertRaises(dxf.DxfParseError) as ctx:
				dxf.parse_dxf_takeoff("/tmp/definitely_not_here.dxf")
			self.assertIn("does not exist", str(ctx.exception))

			# ---- GATE NOTE: the heavy parse must not be reachable from a request.
			# Only the enqueueing entry point is whitelisted; parse_dxf_takeoff is
			# not, so a 40 MB drawing cannot be read on the request thread.
			# frappe.whitelisted is the registry of HTTP-reachable methods.
			# frappe.is_whitelisted() is a session/app-state access check, not a
			# membership test, so the registry is the right thing to assert on.
			self.assertIn(
				dxf.extract_dxf_takeoff,
				frappe.whitelisted,
				"the enqueueing entry point must be reachable from a Desk request",
			)
			self.assertNotIn(
				dxf.parse_dxf_takeoff,
				frappe.whitelisted,
				"the heavy parse must not be callable straight from a request",
			)
		finally:
			os.remove(path)
