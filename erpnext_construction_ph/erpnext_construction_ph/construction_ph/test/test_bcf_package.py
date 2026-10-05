# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""`.bcfzip` packages, against buildingSMART's documented layout.

The layout under test comes from `Documentation/README.md` at tag v2.1: one folder
per topic named for the topic's UUID, holding `markup.bcf` plus viewpoints and
snapshots, with `bcf.version` at the root and `project.bcfp` optional.
"""

import io
import unittest
import zipfile

from erpnext_construction_ph.construction_ph.bcf.markup import new_guid
from erpnext_construction_ph.construction_ph.bcf.package import (
	MARKUP_FILENAME,
	SNAPSHOT_FILENAME,
	VERSION_FILENAME,
	VIEWPOINT_FILENAME,
	PackageError,
	link_viewpoints,
	read_package,
	unreferenced_viewpoints,
)
from erpnext_construction_ph.construction_ph.bcf.viewpoint import Vector

PNG = b"\x89PNG\r\n\x1a\n" + b"not-a-real-image-but-bytes-are-bytes"


def _markup_21(topic_guid: str, viewpoint_guid: str) -> str:
	"""A 2.1-shaped markup.bcf: Comment and Viewpoints are TOP-LEVEL siblings."""
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<Markup xmlns="http://www.buildingsmart-tech.org/bcf/bcf/2.1">
  <Header>
    <File IfcProject="24O8sC$qj4q8B0D3sK2L1e" isExternal="true">
      <Filename>structural.ifc</Filename>
      <Date>2026-09-28T08:00:00Z</Date>
    </File>
  </Header>
  <Topic Guid="{topic_guid}" TopicType="Clash" TopicStatus="Open">
    <ReferenceLink>IfcDuctSegment/24O8sC$qj4q8B0D3sK2L1e</ReferenceLink>
    <Title>Duct clashes with beam B102</Title>
    <CreationDate>2026-09-28T08:00:00Z</CreationDate>
    <CreationAuthor>mep.engineer@project.com</CreationAuthor>
    <BimSnippet SnippetType="IFC4" isExternal="false">
      <Reference>IfcBeam/1Ab2Cd$ef3Gh4Ijk5Lmn6O</Reference>
      <ReferenceSchema>IFC4</ReferenceSchema>
    </BimSnippet>
  </Topic>
  <Comment Guid="{new_guid()}" Guid2="x">
    <Date>2026-09-28T08:15:00Z</Date>
    <Author>structural@project.com</Author>
    <Comment>Post-tensioned; reroute under the soffit.</Comment>
    <Viewpoint Guid="{viewpoint_guid}"/>
  </Comment>
  <Viewpoints Guid="{viewpoint_guid}">
    <Viewpoint>{VIEWPOINT_FILENAME}</Viewpoint>
    <Snapshot>{SNAPSHOT_FILENAME}</Snapshot>
    <Index>0</Index>
  </Viewpoints>
</Markup>
"""


def _viewpoint(guid: str) -> str:
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<VisualizationInfo Guid="{guid}">
  <PerspectiveCamera>
    <CameraViewPoint><X>1.0</X><Y>2.0</Y><Z>3.0</Z></CameraViewPoint>
    <CameraDirection><X>0.0</X><Y>0.0</Y><Z>-1.0</Z></CameraDirection>
    <CameraUpVector><X>0.0</X><Y>1.0</Y><Z>0.0</Z></CameraUpVector>
    <FieldOfView>55.0</FieldOfView>
  </PerspectiveCamera>
</VisualizationInfo>
"""


def _zip(entries: dict) -> bytes:
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		for name, data in entries.items():
			archive.writestr(name, data)
	return buffer.getvalue()


class TestPackageLayout(unittest.TestCase):
	"""The documented layout, read the way a real package lays it out."""

	def setUp(self):
		self.topic_guid = new_guid()
		self.viewpoint_guid = new_guid()
		self.package = read_package(
			_zip(
				{
					VERSION_FILENAME: f'<?xml version="1.0"?><BCFVersion Version="{self.topic_guid and "2.1"}"/>',
					f"{self.topic_guid}/{MARKUP_FILENAME}": _markup_21(
						self.topic_guid, self.viewpoint_guid
					),
					f"{self.topic_guid}/{VIEWPOINT_FILENAME}": _viewpoint(self.viewpoint_guid),
					f"{self.topic_guid}/{SNAPSHOT_FILENAME}": PNG,
				}
			)
		)

	def test_a_topic_folder_is_found_and_read(self):
		self.assertEqual(len(self.package.topics), 1)
		topic = self.package.topic(self.topic_guid)
		self.assertEqual(topic.markup.topic.title, "Duct clashes with beam B102")

	def test_the_declared_version_is_read_and_agrees_with_the_shape(self):
		self.assertEqual(self.package.declared_version, "2.1")
		self.assertEqual(self.package.detected_version, "2.1")
		self.assertIsNone(self.package.version_agreement())

	def test_a_disagreement_is_reported_rather_than_normalised_away(self):
		"""A package that DECLARES 3.0 while its markup parses as 2.1 is a finding."""
		package = read_package(
			_zip(
				{
					VERSION_FILENAME: '<?xml version="1.0"?><BCFVersion Version="3.0"/>',
					f"{self.topic_guid}/{MARKUP_FILENAME}": _markup_21(
						self.topic_guid, self.viewpoint_guid
					),
				}
			)
		)
		self.assertIn("declares", package.version_agreement())
		self.assertIn("3.0", package.version_agreement())

	def test_the_viewpoint_and_its_snapshot_come_along(self):
		topic = self.package.topic(self.topic_guid)
		self.assertEqual(len(topic.viewpoints), 1)
		self.assertEqual(topic.viewpoints[0][0], VIEWPOINT_FILENAME)
		self.assertEqual(topic.viewpoints[0][1].perspective.field_of_view, 55.0)
		self.assertIn(SNAPSHOT_FILENAME, [name for name, _ in topic.assets])

	def test_a_comment_links_to_the_viewpoint_it_names(self):
		topic = self.package.topic(self.topic_guid)
		self.assertEqual(link_viewpoints(topic), [VIEWPOINT_FILENAME])
		self.assertEqual(unreferenced_viewpoints(topic), [])

	def test_an_unreferenced_viewpoint_is_reported_not_dropped(self):
		orphan = new_guid()
		package = read_package(
			_zip(
				{
					f"{self.topic_guid}/{MARKUP_FILENAME}": _markup_21(
						self.topic_guid, self.viewpoint_guid
					),
					f"{self.topic_guid}/{VIEWPOINT_FILENAME}": _viewpoint(self.viewpoint_guid),
					f"{self.topic_guid}/extra.bcfv": _viewpoint(orphan),
				}
			)
		)
		topic = package.topic(self.topic_guid)
		self.assertEqual(unreferenced_viewpoints(topic), ["extra.bcfv"])

	def test_the_whole_chain_survives(self):
		"""Project -> element reference -> comment -> viewpoint -> camera."""
		topic = self.package.topic(self.topic_guid)
		self.assertEqual(len(topic.markup.topic.element_guids), 2)
		self.assertEqual(topic.viewpoints[0][1].perspective.eye, Vector(1.0, 2.0, 3.0))
		self.assertEqual(len(topic.markup.topic.comments), 1)


class TestPackageRefusals(unittest.TestCase):
	"""What the reader will not accept, and why each refusal is useful."""

	def test_a_package_with_no_uuid_folders_is_refused(self):
		with self.assertRaises(PackageError) as caught:
			read_package(_zip({VERSION_FILENAME: "<x/>", "loose/markup.bcf": "<Markup/>"}))
		self.assertIn("topic folders", str(caught.exception))

	def test_a_topic_folder_without_markup_is_refused(self):
		guid = new_guid()
		with self.assertRaises(PackageError) as caught:
			read_package(_zip({f"{guid}/{VIEWPOINT_FILENAME}": _viewpoint(guid)}))
		self.assertIn(MARKUP_FILENAME, str(caught.exception))

	def test_an_unparseable_markup_names_the_topic_it_came_from(self):
		guid = new_guid()
		with self.assertRaises(PackageError) as caught:
			read_package(_zip({f"{guid}/{MARKUP_FILENAME}": "<Markup><Nope/></Markup>"}))
		self.assertIn(guid, str(caught.exception))

	def test_mixing_two_markup_shapes_in_one_package_is_refused(self):
		"""Guessing which shape dominates would hide a corrupt package."""
		first, second = new_guid(), new_guid()
		version_30 = _markup_21(first, new_guid()).replace(
			"  <Comment ", "  <CommentX "
		)  # 3.0 nests Comment inside Topic
		with self.assertRaises(Exception):
			read_package(
				_zip(
					{
						f"{first}/{MARKUP_FILENAME}": _markup_21(first, new_guid()),
						f"{second}/{MARKUP_FILENAME}": version_30,
					}
				)
			)


if __name__ == "__main__":
	unittest.main()
