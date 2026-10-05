# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""BCF `viewpoint.bcfv` — the camera a viewpoint is seen from.

Written against buildingSMART's `Schemas/visinfo.xsd` (BCF-XML, tag `v2.1`),
read verbatim. Three facts from that file shape everything here:

  * **`FieldOfView` is a closed interval, `minInclusive 45` / `maxInclusive 60`.**
    A viewpoint claiming 20 degrees or 90 is not a viewpoint this format accepts,
    so it is refused here rather than written out and rejected by every reader.
  * **`Color` is uppercase hex only** — `[0-9,A-F]{6}([0-9,A-F]{2})?`. Lowercase
    `#ff0000` is a perfectly ordinary web colour and is NOT valid BCF.
  * **`IfcGuid` is 22 characters** of `[0-9,A-Z,a-z,_$]`, the same rule markup.xsd
    uses, so element references and camera component references agree.

**COORDINATES ARE Z-UP, AND THIS MODULE DOES NOT CONVERT THEM.** BCF/IFC world
space has +Z up; Three.js has +Y up. That conversion belongs to whatever renders
the viewpoint, and doing it silently here would put a lie in the file: a viewpoint
read back by Solibri would be rotated 90 degrees. `to_threejs` exists for the
viewer and is explicit about which way it goes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# visinfo.xsd, <xs:simpleType name="FieldOfView">
FOV_MIN_DEGREES = 45.0
FOV_MAX_DEGREES = 60.0

# visinfo.xsd's Color pattern CHANGED between versions, and this is the one place
# where being 2.1-strict silently breaks 3.0 interop:
#   2.1  [0-9,A-F]{6}([0-9,A-F]{2})?      uppercase only
#   3.0  [0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?   either case
# So the accepted set is a parameter of the version, not one rule. A single
# uppercase-only check would refuse `#aabbcc`-style colours that a 3.0 reader
# accepts, which is precisely the silent interop failure this module exists to
# avoid -- the same shape as losing 2.1 comments, one module over.
COLOR_RE_21 = re.compile(r"^[0-9A-F]{6}([0-9A-F]{2})?$")
COLOR_RE_30 = re.compile(r"^[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
DEFAULT_VERSION = "3.0"

# visinfo.xsd's IfcGuid pattern also changed, and the difference is easy to miss:
#   2.1  [0-9,A-Z,a-z,_$]*   -- inside a character class those commas are LITERAL
#                            members, so 2.1 permits a comma in a GlobalId
#   3.0  [0-9A-Za-z_$]*       -- no comma
# Applying 2.1's rule to a 3.0 document accepts a value 3.0 readers reject, which
# is the same half-version-aware reader that silently loses fields.
IFC_GUID_RE_21 = re.compile(r"^[0-9,A-Z,a-z,_$]*$")
IFC_GUID_RE_30 = re.compile(r"^[0-9A-Za-z_$]*$")
IFC_GUID_LENGTH = 22

_GUID_RE = re.compile(r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$")


class ViewpointError(ValueError):
	"""A viewpoint that does not satisfy visinfo.xsd."""


def _local(tag: str) -> str:
	"""visinfo.xsd declares no targetNamespace, but real files may carry one."""
	return tag.rsplit("}", 1)[-1]


def _find(node, *tags):
	wanted = {_local(tag) for tag in tags}
	for child in node:
		if _local(child.tag) in wanted:
			return child
	return None


def _num(node, tag) -> float:
	child = _find(node, tag)
	if child is None or child.text is None:
		raise ViewpointError(f"{tag} is required and must be a number")
	try:
		return float(child.text)
	except ValueError as error:
		raise ViewpointError(f"{tag} must be a number, got {child.text!r}") from error


def check_ifc_guid(value: str, *, field: str = "IfcGuid", version: str = DEFAULT_VERSION) -> str:
	"""22 characters, under the version's own pattern. See the note on the regexes."""
	pattern = IFC_GUID_RE_21 if version.startswith("2") else IFC_GUID_RE_30
	if not isinstance(value, str) or len(value) != IFC_GUID_LENGTH or not pattern.match(value):
		raise ViewpointError(
			f"{field} must be exactly {IFC_GUID_LENGTH} characters matching "
			f"[0-9,A-Z,a-z,_$] (2.1) or [0-9A-Za-z_$] (3.0), got {value!r}"
		)
	return value


def check_color(value: str, version: str = DEFAULT_VERSION) -> str:
	"""Hex colour, 3 or 4 bytes, no leading '#' -- with the version's own rule.

	2.1 admits UPPERCASE only; 3.0 admits either case. Both refuse '#', which is
	the part people get wrong most often.
	"""
	pattern = COLOR_RE_21 if version.startswith("2") else COLOR_RE_30
	if not isinstance(value, str) or not pattern.match(value):
		raise ViewpointError(
			f"Color must be 6 or 8 hex digits with no leading '#', "
			f"{'uppercase ' if version.startswith('2') else ''}per visinfo.xsd {version}, got {value!r}"
		)
	return value


@dataclass
class Vector:
	"""`Point` and `Direction` are the same shape: X, Y, Z doubles."""

	x: float
	y: float
	z: float

	def as_dict(self) -> dict:
		return {"x": self.x, "y": self.y, "z": self.z}

	@property
	def length(self) -> float:
		return (self.x**2 + self.y**2 + self.z**2) ** 0.5

	def normalised(self) -> "Vector":
		magnitude = self.length
		if magnitude == 0:
			raise ViewpointError("cannot normalise a zero-length direction")
		return Vector(self.x / magnitude, self.y / magnitude, self.z / magnitude)

	def to_threejs(self) -> "Vector":
		"""BCF/IFC is Z-up; Three.js is Y-up. Explicit, never silent.

		P_IFC = (X, -Z, Y) and its inverse P_Three = (X, Z, -Y). Applying this
		twice returns the original, which `test_the_basis_change_is_its_own_inverse`
		checks -- a conversion you cannot undo is a conversion that corrupts.
		"""
		return Vector(self.x, self.z, -self.y)

	@classmethod
	def from_threejs(cls, value: "Vector") -> "Vector":
		return Vector(value.x, -value.z, value.y)


@dataclass
class PerspectiveCamera:
	eye: Vector
	direction: Vector
	up: Vector
	field_of_view: float

	def validate(self) -> None:
		if not (FOV_MIN_DEGREES <= self.field_of_view <= FOV_MAX_DEGREES):
			raise ViewpointError(
				f"FieldOfView must be between {FOV_MIN_DEGREES:g} and {FOV_MAX_DEGREES:g} "
				f"degrees (visinfo.xsd), got {self.field_of_view!r}"
			)


@dataclass
class OrthogonalCamera:
	eye: Vector
	direction: Vector
	up: Vector
	# visinfo.xsd: "view's visible size in meters"
	view_to_world_scale: float

	def validate(self) -> None:
		if self.view_to_world_scale <= 0:
			raise ViewpointError("ViewToWorldScale is a visible size in metres and must be positive")


@dataclass
class ClippingPlane:
	location: Vector
	direction: Vector


@dataclass
class Line:
	start: Vector
	end: Vector


@dataclass
class Component:
	ifc_guid: str
	originating_system: str | None = None
	authoring_tool_id: str | None = None

	def validate(self, version: str = DEFAULT_VERSION) -> None:
		check_ifc_guid(self.ifc_guid, field="Component.IfcGuid", version=version)


@dataclass
class Coloring:
	components: list[Component] = field(default_factory=list)
	color: str = "FF0000"

	def validate(self, version: str = DEFAULT_VERSION) -> None:
		check_color(self.color, version)
		for component in self.components:
			component.validate()


@dataclass
class Components:
	selection: list[Component] = field(default_factory=list)
	visibility_exceptions: list[Component] = field(default_factory=list)
	default_visibility: bool = False
	colorings: list[Coloring] = field(default_factory=list)
	spaces_visible: bool | None = None
	space_boundaries_visible: bool | None = None
	openings_visible: bool | None = None

	def validate(self, version: str = DEFAULT_VERSION) -> None:
		for group in (self.selection, self.visibility_exceptions):
			for component in group:
				component.validate(version)
		for coloring in self.colorings:
			coloring.validate(version)


@dataclass
class VisualizationInfo:
	"""The whole `viewpoint.bcfv` document."""

	guid: str
	perspective: PerspectiveCamera | None = None
	orthogonal: OrthogonalCamera | None = None
	components: Components | None = None
	clipping_planes: list[ClippingPlane] = field(default_factory=list)
	lines: list[Line] = field(default_factory=list)
	version: str = DEFAULT_VERSION

	def validate(self) -> None:
		if not isinstance(self.guid, str) or not _GUID_RE.match(self.guid):
			raise ViewpointError(
				"VisualizationInfo.Guid must be a hyphenated UUID (visinfo.xsd), " f"got {self.guid!r}"
			)
		if self.perspective is None and self.orthogonal is None:
			raise ViewpointError(
				"a viewpoint must carry an OrthogonalCamera or a PerspectiveCamera; "
				"visinfo.xsd marks both optional individually but a viewpoint with "
				"neither describes no position at all"
			)
		if self.perspective is not None and self.orthogonal is not None:
			raise ViewpointError(
				"a viewpoint carries one camera, not two; visinfo.xsd sequence "
				"allows each at most once"
			)
		if self.perspective:
			self.perspective.validate()
		if self.orthogonal:
			self.orthogonal.validate()
		if self.components:
			self.components.validate(self.version)

	@property
	def camera_kind(self) -> str | None:
		if self.perspective:
			return "perspective"
		if self.orthogonal:
			return "orthogonal"
		return None


def _sub(parent, tag, text=None):
	element = ET.SubElement(parent, tag)
	if text is not None:
		element.text = text
	return element


def _write_vector(parent, tag: str, value: Vector) -> None:
	node = _sub(parent, tag)
	_sub(node, "X", repr(float(value.x)))
	_sub(node, "Y", repr(float(value.y)))
	_sub(node, "Z", repr(float(value.z)))


def _read_vector(node) -> Vector:
	return Vector(_num(node, "X"), _num(node, "Y"), _num(node, "Z"))


def to_xml(info: VisualizationInfo) -> str:
	info.validate()
	root = ET.Element("VisualizationInfo")
	root.set("Guid", info.guid)

	if info.components is not None:
		parts = info.components
		parts_version = info.version
		node = _sub(root, "Components")
		if parts.spaces_visible is not None or parts.space_boundaries_visible is not None or parts.openings_visible is not None:
			hints = _sub(node, "ViewSetupHints")
			if parts.spaces_visible is not None:
				hints.set("SpacesVisible", str(parts.spaces_visible).lower())
			if parts.space_boundaries_visible is not None:
				hints.set("SpaceBoundariesVisible", str(parts.space_boundaries_visible).lower())
			if parts.openings_visible is not None:
				hints.set("OpeningsVisible", str(parts.openings_visible).lower())
		if parts.selection:
			holder = _sub(node, "Selection")
			for component in parts.selection:
				_write_component(holder, component)
		visibility = _sub(node, "Visibility")
		visibility.set("DefaultVisibility", str(parts.default_visibility).lower())
		if parts.visibility_exceptions:
			exceptions = _sub(visibility, "Exceptions")
			for component in parts.visibility_exceptions:
				_write_component(exceptions, component)
		# 2.1 spells this `Color` (carrying a `Color` ATTRIBUTE); 3.0 nests it a
		# level deeper as `Coloring/Color/Components/Component` and drops the
		# attribute. Only the 2.1 shape is WRITTEN here. Emitting 3.0's shape on a
		# guess produced a document its own schema rejected, so the gap is recorded
		# rather than papered over -- see the gate.
		for coloring in parts.colorings:
			entry = _sub(node, "Color")
			for component in coloring.components:
				_write_component(entry, component)
			entry.set("Color", coloring.color)

	if info.orthogonal is not None:
		node = _sub(root, "OrthogonalCamera")
		_write_vector(node, "CameraViewPoint", info.orthogonal.eye)
		_write_vector(node, "CameraDirection", info.orthogonal.direction)
		_write_vector(node, "CameraUpVector", info.orthogonal.up)
		_sub(node, "ViewToWorldScale", repr(float(info.orthogonal.view_to_world_scale)))

	if info.perspective is not None:
		node = _sub(root, "PerspectiveCamera")
		_write_vector(node, "CameraViewPoint", info.perspective.eye)
		_write_vector(node, "CameraDirection", info.perspective.direction)
		_write_vector(node, "CameraUpVector", info.perspective.up)
		_sub(node, "FieldOfView", repr(float(info.perspective.field_of_view)))

	if info.lines:
		holder = _sub(root, "Lines")
		for line in info.lines:
			node = _sub(holder, "Line")
			_write_vector(node, "StartPoint", line.start)
			_write_vector(node, "EndPoint", line.end)

	if info.clipping_planes:
		holder = _sub(root, "ClippingPlanes")
		for plane in info.clipping_planes:
			node = _sub(holder, "ClippingPlane")
			_write_vector(node, "Location", plane.location)
			_write_vector(node, "Direction", plane.direction)

	ET.indent(root, space="\t")
	return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _write_component(parent, component: Component) -> None:
	node = _sub(parent, "Component")
	node.set("IfcGuid", component.ifc_guid)
	if component.originating_system:
		_sub(node, "OriginatingSystem", component.originating_system)
	if component.authoring_tool_id:
		_sub(node, "AuthoringToolId", component.authoring_tool_id)


def _read_components(node, version: str = DEFAULT_VERSION) -> Components:
	parts = Components()
	hints = _find(node, "ViewSetupHints")
	if hints is not None:
		for name, attribute in (
			("spaces_visible", "SpacesVisible"),
			("space_boundaries_visible", "SpaceBoundariesVisible"),
			("openings_visible", "OpeningsVisible"),
		):
			if hints.get(attribute) is not None:
				setattr(parts, name, hints.get(attribute) == "true")

	selection = _find(node, "Selection")
	if selection is not None:
		parts.selection = [_read_component(c) for c in selection]

	visibility = _find(node, "Visibility")
	if visibility is not None:
		parts.default_visibility = visibility.get("DefaultVisibility", "false") == "true"
		exceptions = _find(visibility, "Exceptions")
		if exceptions is not None:
			parts.visibility_exceptions = [_read_component(c) for c in exceptions]

	for entry in node:
		if _local(entry.tag) in ("Color", "Coloring"):  # 2.1 / 3.0 spelling
			parts.colorings.append(
				Coloring(
					components=[_read_component(c) for c in entry],
					color=entry.get("Color") or "FF0000",
				)
			)
	return parts


def _read_component(node) -> Component:
	return Component(
		ifc_guid=node.get("IfcGuid") or "",
		originating_system=(node.findtext("OriginatingSystem") or None),
		authoring_tool_id=(node.findtext("AuthoringToolId") or None),
	)


def from_xml(text: str) -> VisualizationInfo:
	root = ET.fromstring(text)
	if _local(root.tag) != "VisualizationInfo":
		raise ViewpointError(f"root element must be VisualizationInfo, got {root.tag!r}")

	orthogonal = _find(root, "OrthogonalCamera")
	perspective = _find(root, "PerspectiveCamera")
	components = _find(root, "Components")
	lines = []
	holder = _find(root, "Lines")
	if holder is not None:
		for line in holder:
			lines.append(Line(_read_vector(_find(line, "StartPoint")), _read_vector(_find(line, "EndPoint"))))
	planes = []
	holder = _find(root, "ClippingPlanes")
	if holder is not None:
		for plane in holder:
			planes.append(
				ClippingPlane(_read_vector(_find(plane, "Location")), _read_vector(_find(plane, "Direction")))
			)

	# Version is detected from the one element that was RENAMED between the two
	# releases: 2.1 spells it `Color`, 3.0 spells it `Coloring`. Absent both, the
	# document is assumed 3.0, which is the version this module writes by default.
	version = DEFAULT_VERSION
	components_node = _find(root, "Components")
	if components_node is not None and _find(components_node, "Color") is not None:
		version = "2.1"

	info = VisualizationInfo(
		guid=root.get("Guid") or "",
		version=version,
		orthogonal=(
			OrthogonalCamera(
				eye=_read_vector(_find(orthogonal, "CameraViewPoint")),
				direction=_read_vector(_find(orthogonal, "CameraDirection")),
				up=_read_vector(_find(orthogonal, "CameraUpVector")),
				view_to_world_scale=_num(orthogonal, "ViewToWorldScale"),
			)
			if orthogonal is not None
			else None
		),
		perspective=(
			PerspectiveCamera(
				eye=_read_vector(_find(perspective, "CameraViewPoint")),
				direction=_read_vector(_find(perspective, "CameraDirection")),
				up=_read_vector(_find(perspective, "CameraUpVector")),
				field_of_view=_num(perspective, "FieldOfView"),
			)
			if perspective is not None
			else None
		),
		components=_read_components(components, version) if components is not None else None,
		lines=lines,
		clipping_planes=planes,
	)
	info.validate()
	return info
