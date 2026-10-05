# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""`viewpoint.bcfv` against buildingSMART's own `Schemas/visinfo.xsd`.

The three schema constraints this file exists to enforce are the ones a
round-trip cannot catch, because a value that is out of range still serialises
and parses back perfectly -- it is only rejected when a real tool reads it.
"""

import unittest
from pathlib import Path

from erpnext_construction_ph.construction_ph.bcf.viewpoint import (
	FOV_MAX_DEGREES,
	FOV_MIN_DEGREES,
	ClippingPlane,
	Coloring,
	Component,
	Components,
	Line,
	OrthogonalCamera,
	PerspectiveCamera,
	Vector,
	VisualizationInfo,
	ViewpointError,
	check_color,
	from_xml,
	to_xml,
)
from erpnext_construction_ph.construction_ph.bcf.markup import new_guid

GLOBAL_ID = "24O8sC$qj4q8B0D3sK2L1e"
SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "bcf" / "schema"


def _perspective(fov: float = 55.0) -> PerspectiveCamera:
	return PerspectiveCamera(
		eye=Vector(1000.0, 2000.0, 3000.0),
		direction=Vector(0.0, 0.0, -1.0).normalised(),
		up=Vector(0.0, 1.0, 0.0),
		field_of_view=fov,
	)


def _info(**overrides) -> VisualizationInfo:
	base = dict(guid=new_guid(), perspective=_perspective())
	base.update(overrides)
	return VisualizationInfo(**base)


class TestSchemaConstraints(unittest.TestCase):
	"""Constraints the schema imposes that a round-trip would never notice."""

	def test_field_of_view_is_bounded_45_to_60(self):
		"""visinfo.xsd: minInclusive 45, maxInclusive 60."""
		self.assertEqual(FOV_MIN_DEGREES, 45.0)
		self.assertEqual(FOV_MAX_DEGREES, 60.0)
		_perspective(45.0).validate()
		_perspective(60.0).validate()
		for bad in (44.9, 60.1, 0.0, 90.0):
			with self.assertRaises(ViewpointError):
				_perspective(bad).validate()

	def test_colour_rules_differ_by_version_and_both_refuse_a_hash(self):
		"""The Color pattern CHANGED between versions; a single rule breaks one.

		2.1 is `[0-9,A-F]{6}([0-9,A-F]{2})?` -- uppercase only. 3.0 is
		`[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?` -- either case. Being 2.1-strict would
		refuse colours a 3.0 reader accepts.
		"""
		for good in ("FF0000", "00FF00AA", "ABCDEF"):
			check_color(good, "2.1")
		with self.assertRaises(ViewpointError):
			check_color("ff0000", "2.1")
		for good in ("FF0000", "00FF00AA", "abcdef", "aAbBcC"):
			check_color(good, "3.0")
		for bad in ("#FF0000", "red", "FFF", "GGGGGG"):
			with self.assertRaises(ViewpointError):
				check_color(bad, "3.0")

	def test_only_the_21_colouring_shape_is_written(self):
		"""3.0 nests colouring as `Coloring/Color/Components/Component`. This module
		writes the 2.1 shape and does NOT claim 3.0 colouring."""
		produced = to_xml(
			_info(components=Components(colorings=[Coloring(components=[Component(GLOBAL_ID)], color="FF0000")]))
		)
		self.assertIn("<Color ", produced)

	def test_the_vendored_visualisation_schemas_really_differ(self):
		"""If they were identical, the version-specific colour test would prove nothing."""
		first = (SCHEMA_ROOT / "v2.1" / "visinfo.xsd").read_text(encoding="utf-8")
		second = (SCHEMA_ROOT / "v3.0" / "visinfo.xsd").read_text(encoding="utf-8")
		self.assertNotEqual(first.replace("\r", ""), second.replace("\r", ""))
		self.assertIn("[0-9,A-F]{6}", first)
		self.assertIn("[0-9A-Fa-f]{6}", second)

	def test_a_viewpoint_must_carry_exactly_one_camera(self):
		with self.assertRaises(ViewpointError):
			_info(perspective=None, orthogonal=None).validate()
		with self.assertRaises(ViewpointError):
			_info(orthogonal=OrthogonalCamera(Vector(0, 0, 0), Vector(0, 0, -1), Vector(0, 1, 0), 5.0)).validate()

	def test_orthogonal_scale_is_a_visible_size_in_metres(self):
		OrthogonalCamera(Vector(0, 0, 0), Vector(0, 0, -1), Vector(0, 1, 0), 2.5).validate()
		with self.assertRaises(ViewpointError):
			OrthogonalCamera(Vector(0, 0, 0), Vector(0, 0, -1), Vector(0, 1, 0), 0.0).validate()

	def test_component_guids_are_22_characters(self):
		Component(GLOBAL_ID).validate()
		with self.assertRaises(ViewpointError):
			Component("short").validate()

	def test_the_guid_alphabet_differs_by_version(self):
		"""2.1's class admits a literal comma; 3.0's does not.

		`[0-9,A-Z,a-z,_$]` reads like a list of ranges, but inside a character
		class those commas are LITERAL members. Applying 2.1's rule to a 3.0
		document accepts a value 3.0 readers reject.
		"""
		comma = ("ABC" + "," + "DEF" + "123456789").ljust(22, "X")[:22]
		self.assertEqual(len(comma), 22)
		self.assertIn(",", comma)
		Component(comma).validate("2.1")            # permitted by 2.1
		with self.assertRaises(ViewpointError):
			Component(comma).validate("3.0")        # refused by 3.0


class TestRoundTrip(unittest.TestCase):
	def test_a_perspective_viewpoint_survives(self):
		info = _info(
			components=Components(
				selection=[Component(GLOBAL_ID, originating_system="IfcOpenShell")],
				default_visibility=True,
				visibility_exceptions=[Component("1Ab2Cd$ef3Gh4Ijk5Lmn6O")],
				colorings=[Coloring(components=[Component(GLOBAL_ID)], color="FF0000")],
				spaces_visible=True,
			),
			clipping_planes=[ClippingPlane(Vector(0, 0, 1500), Vector(0, 0, 1))],
			lines=[Line(Vector(0, 0, 0), Vector(1, 2, 3))],
		)
		back = from_xml(to_xml(info))
		self.assertEqual(back.camera_kind, "perspective")
		self.assertEqual(back.perspective.field_of_view, 55.0)
		self.assertEqual(back.components.selection[0].ifc_guid, GLOBAL_ID)
		self.assertTrue(back.components.default_visibility)
		self.assertEqual(back.components.colorings[0].color, "FF0000")
		self.assertEqual(len(back.clipping_planes), 1)
		self.assertEqual(len(back.lines), 1)

	def test_an_orthogonal_viewpoint_survives(self):
		info = _info(
			perspective=None,
			orthogonal=OrthogonalCamera(
				Vector(10, 20, 30), Vector(0, 0, -1), Vector(0, 1, 0), 4.5
			),
		)
		back = from_xml(to_xml(info))
		self.assertEqual(back.camera_kind, "orthogonal")
		self.assertEqual(back.orthogonal.view_to_world_scale, 4.5)

	def test_a_namespaced_viewpoint_is_parsed(self):
		"""visinfo.xsd declares no targetNamespace, but real files may carry one."""
		produced = to_xml(_info()).replace(
			"<VisualizationInfo", '<VisualizationInfo xmlns="http://x"', 1
		)
		produced = produced.replace('xmlns="http://x" ', 'xmlns="http://x" ', 1)
		self.assertEqual(from_xml(produced).camera_kind, "perspective")


class TestCoordinates(unittest.TestCase):
	def test_bcf_is_z_up_and_the_conversion_is_explicit(self):
		"""+Z up in BCF/IFC. The conversion belongs to the viewer, not the file."""
		ifc = Vector(1.0, 2.0, 3.0)
		three = ifc.to_threejs()
		self.assertEqual((three.x, three.y, three.z), (1.0, 3.0, -2.0))

	def test_the_basis_change_is_its_own_inverse(self):
		original = Vector(4.0, -5.0, 6.5)
		back = Vector.from_threejs(original.to_threejs())
		self.assertAlmostEqual(back.x, original.x)
		self.assertAlmostEqual(back.y, original.y)
		self.assertAlmostEqual(back.z, original.z)

	def test_coordinates_are_not_silently_converted_on_the_round_trip(self):
		"""A viewpoint read by Solibri must not come back rotated."""
		info = _info()
		back = from_xml(to_xml(info))
		self.assertAlmostEqual(back.perspective.eye.z, 3000.0)
		self.assertAlmostEqual(back.perspective.eye.y, 2000.0)


class TestAgainstVisinfoSchema(unittest.TestCase):
	"""Fails if lxml is missing; never skips."""

	def _schema(self, version: str = "v2.1"):
		from lxml import etree

		return etree.XMLSchema(etree.parse(str(SCHEMA_ROOT / version / "visinfo.xsd")))

	def test_the_validator_is_present(self):
		try:
			import lxml.etree  # noqa: F401
		except ImportError:
			self.fail(
				"lxml is required: without it this suite cannot prove schema validity, "
				"and reporting green would be the same lie as a guard that cannot run"
			)

	def test_a_generated_viewpoint_is_schema_valid(self):
		from lxml import etree

		document = etree.fromstring(to_xml(_info()).encode("utf-8"))
		self._schema().assertValid(document)

	def test_an_orthogonal_viewpoint_is_schema_valid(self):
		from lxml import etree

		info = _info(
			perspective=None,
			orthogonal=OrthogonalCamera(Vector(0, 0, 0), Vector(0, 0, -1), Vector(0, 1, 0), 3.0),
		)
		self._schema().assertValid(etree.fromstring(to_xml(info).encode("utf-8")))

	def test_the_schema_rejects_an_out_of_range_field_of_view(self):
		"""Proves the validator bites rather than decorating the suite."""
		from lxml import etree

		document = to_xml(_info()).replace(
			"<FieldOfView>55.0</FieldOfView>", "<FieldOfView>90.0</FieldOfView>", 1
		)
		with self.assertRaises(Exception):
			self._schema().assertValid(etree.fromstring(document.encode("utf-8")))


if __name__ == "__main__":
	unittest.main()
