# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The `.bcfzip` writer: a round-trip, and refusals that must not be quiet.

PH1-12 follow-on. The read side was already version-agnostic and tested; this covers
the 2.1 write side, which was deliberately absent until the target was chosen.

The load-bearing property is the ROUND TRIP: a package written here is read back by
`read_package` with its topics, comments, viewpoints and element GUIDs intact. A zip
that opens proves nothing -- the failure this module exists to prevent is a file that
looks right and loses data in a partner tool.
"""

import datetime
import io
import pathlib
import unittest
import zipfile

from lxml import etree

from erpnext_construction_ph.construction_ph.bcf import markup as m
from erpnext_construction_ph.construction_ph.bcf import package as p
from erpnext_construction_ph.construction_ph.bcf import writer as w
from erpnext_construction_ph.construction_ph.bcf.markup import BCFError
from erpnext_construction_ph.construction_ph.bcf.package import BCFPackage, PackageError

TOPIC_UUID = "0fc0a2c0-9e5a-4c5b-9e2e-1d5a7b3c9e11"
OTHER_UUID = "3b7c1e44-2a19-4d8e-8f60-77c2b9d4a512"
WHEN = datetime.datetime(2026, 9, 28, 9, 0, 0)
# Two IFC GUIDs already proven elsewhere in this suite, at exactly 22 characters.
# The first draft hand-typed GUIDs that came out 23 long, and `element_guids` filters
# on length, so the round trip silently reported NO element references -- the exact
# "looks right, lost the data" failure these tests exist to catch.
DUCT_GUID = "24O8sC$qj4q8B0D3sK2L1e"
BEAM_GUID = "1Ab2Cd$ef3Gh4Ijk5Lmn6O"


def _topic(version="2.1", **overrides):
	"""A clash topic, built against the module's REAL dataclass fields.

	Written by reading `markup.py` rather than by guessing: an earlier draft used
	`text=`, `author=`, `status=` and `element_guids=`, and every one of those is
	wrong -- `Comment` wants `comment=` and a `date=`, `Topic` wants `topic_status=`
	and `creation_author=`, and `element_guids` is a derived property, not a field.
	Ten tests errored on the constructor before this was read.
	"""
	fields = dict(
		guid=TOPIC_UUID,
		title="Clash: duct vs beam",
		topic_type="Clash",
		topic_status="Open",
		creation_date=WHEN,
		creation_author="QS",
		comments=[
			m.Comment(
				guid="11111111-1111-4111-8111-111111111111",
				date=WHEN,
				author="QS",
				comment="Overlapping geometry",
			)
		],
		snippet=m.ElementReference(
			reference=DUCT_GUID,
			snippet_type="IFC4",
			schema="IFC4",
		),
		reference_links=[BEAM_GUID],
	)
	fields.update(overrides)
	return m.Topic(**fields)


def _package(version="2.1", topic=None):
	folder = p.TopicFolder(
		guid=TOPIC_UUID,
		markup=m.Markup(topic=topic or _topic(version), version=version),
	)
	return BCFPackage(topics=[folder], declared_version=version)


class TestBcfWriter(unittest.TestCase):
	def setUp(self):
		self.pkg = _package()

	# ---- the round trip ------------------------------------------------

	def test_a_written_package_reads_back_with_its_topic(self):
		back = p.read_package(io.BytesIO(w.write_package(self.pkg)))
		self.assertEqual(back.declared_version, "2.1")
		self.assertEqual(back.detected_version, "2.1")
		self.assertIsNone(back.version_agreement(), "the package contradicts itself")
		topic = back.topic(TOPIC_UUID).markup.topic
		self.assertEqual(topic.title, "Clash: duct vs beam")
		self.assertEqual(topic.topic_type, "Clash")
		self.assertEqual(topic.topic_status, "Open")

	def test_comments_and_element_guids_survive_the_round_trip(self):
		"""The two things a round-trip that only checks the file exists would lose."""
		back = p.read_package(io.BytesIO(w.write_package(self.pkg)))
		topic = back.topic(TOPIC_UUID).markup.topic
		self.assertEqual(len(topic.comments), 1)
		self.assertEqual(topic.comments[0].comment, "Overlapping geometry")
		self.assertEqual(topic.comments[0].author, "QS")
		self.assertEqual(
			list(topic.element_guids),
			# Order is the module's:  walks reference_links first and
			# then the snippet, and the test must pin the real order rather than the one
			# I assumed when writing it.
			[BEAM_GUID, DUCT_GUID],
			"element references are the whole point of a clash topic, and they must be "
			"read from BOTH routes: the ReferenceLink and the BimSnippet. A reader that "
			"only followed one would still pass a one-element topic.",
		)

	def test_the_folder_is_named_for_the_topic_uuid(self):
		"""The layout says the folder IS the UUID, not a title or an index."""
		names = zipfile.ZipFile(io.BytesIO(w.write_package(self.pkg))).namelist()
		self.assertIn(f"{TOPIC_UUID}/markup.bcf", names)

	def test_bcf_version_is_written(self):
		"""A package that omits it makes the reader infer from shape alone."""
		raw = zipfile.ZipFile(io.BytesIO(w.write_package(self.pkg)))
		self.assertIn(p.VERSION_FILENAME, raw.namelist())
		self.assertEqual(raw.read(p.VERSION_FILENAME).decode().strip(), "2.1")

	# ---- refusals ------------------------------------------------------

	def test_a_3_0_shaped_topic_is_refused_rather_than_mislabelled(self):
		"""Writing 3.0 markup under a 2.1 bcf.version is the silent interop failure."""
		mixed = _package("3.0")
		with self.assertRaises(PackageError) as caught:
			w.write_package(mixed)
		self.assertIn("2.1", str(caught.exception))

	def test_a_folder_that_is_not_a_uuid_is_refused(self):
		"""A prettier folder name still opens, and silently loses the association."""
		folder = p.TopicFolder(guid="Clash 01", markup=m.Markup(topic=_topic(), version="2.1"))
		with self.assertRaises(PackageError) as caught:
			w.write_package(BCFPackage(topics=[folder]))
		self.assertIn("UUID", str(caught.exception))

	def test_a_package_with_no_topics_is_refused(self):
		"""An empty .bcfzip is a valid archive showing no issue in any tool."""
		with self.assertRaises(PackageError) as caught:
			w.write_package(BCFPackage())
		self.assertIn("no topics", str(caught.exception))

	def test_two_topics_claiming_one_folder_are_refused(self):
		first = p.TopicFolder(guid=TOPIC_UUID, markup=m.Markup(topic=_topic(), version="2.1"))
		second_topic = _topic(title="Second")
		second = p.TopicFolder(
			guid=TOPIC_UUID, markup=m.Markup(topic=second_topic, version="2.1")
		)
		with self.assertRaises(PackageError) as caught:
			w.write_package(BCFPackage(topics=[first, second]))
		self.assertIn("folder", str(caught.exception))

	def test_asking_for_a_version_this_writer_does_not_emit_is_refused(self):
		with self.assertRaises(PackageError) as caught:
			w.write_package(self.pkg, version="3.0")
		self.assertIn("3.0", str(caught.exception))

	def test_an_invalid_topic_is_refused_before_anything_is_written(self):
		bad = m.Topic(
			guid=TOPIC_UUID,
			title="",
			topic_type="Clash",
			topic_status="Open",
			creation_date=WHEN,
			creation_author="QS",
		)
		folder = p.TopicFolder(guid=TOPIC_UUID, markup=m.Markup(topic=bad, version="2.1"))
		with self.assertRaises((BCFError, ValueError)):
			w.write_package(BCFPackage(topics=[folder]))
	def test_a_caller_cannot_reach_a_mislabelled_package_by_forgetting_the_version(self):
		"""`Markup.version` defaults to "3.0" silently, so the writer must not trust it.

		Two defaults that have to be kept in step is the hazard: forget `version=` here
		and set `Markup(version="2.1")` there, and the package's label and its shape
		disagree. The writer re-derives the shape from the content and refuses.
		"""
		forgotten = p.TopicFolder(
			guid=TOPIC_UUID,
			markup=m.Markup(topic=_topic()),  # version NOT set -> defaults to 3.0
		)
		with self.assertRaises(PackageError):
			w.write_package(BCFPackage(topics=[forgotten]))


class TestBcfWriterValidatesAgainstTheSchema(unittest.TestCase):
	"""A round trip proves self-consistency, not validity. This is the real check.

	The writer's whole risk is emitting a file that OUR reader accepts and a partner
	tool rejects -- the failure mode that has produced every interop bug in this
	project. So the bytes are put through the vendored XSD, which is the only
	authority that can settle it.
	"""

	def setUp(self):
		self.directory = pathlib.Path(
			"/home/frappe/frappe-bench/apps/erpnext_construction_ph/"
			"erpnext_construction_ph/construction_ph/bcf/schema"
		)
		if not self.directory.exists():  # running outside the bench
			self.skipTest("vendored schemas are not present in this tree")

	def _markup_bytes(self, version):
		topic = _topic(
			comments=[
				m.Comment(
					guid="11111111-1111-4111-8111-111111111111",
					date=WHEN,
					author="QS",
					comment="Overlapping geometry",
				)
			],
			viewpoints=[m.ViewPoint(guid="22222222-2222-4222-8222-222222222222", index=0)],
		)
		markup = m.Markup(
			topic=topic,
			files=[m.ModelFile(filename="model.ifc", reference="24O8sC$qj4q8B0D3sK2L1e")],
			version=version,
		)
		# 2.1 goes through the writer, which is 2.1-only and refuses anything else.
		# 3.0 is serialised directly: this test is about `to_xml`'s 3.0 path still
		# being valid, not about the writer accepting a version it refuses by design.
		if version == "2.1":
			folder = p.TopicFolder(guid=TOPIC_UUID, markup=markup)
			archive = zipfile.ZipFile(io.BytesIO(w.write_package(BCFPackage(topics=[folder]))))
			return archive.read(f"{TOPIC_UUID}/markup.bcf")
		return m.to_xml(markup).encode("utf-8")

	def test_21_output_validates_against_the_vendored_21_schema(self):
		schema = etree.XMLSchema(etree.parse(str(self.directory / "v2.1" / "markup.xsd")))
		document = etree.fromstring(self._markup_bytes("2.1"))
		schema.assertValid(document)

	def test_30_output_still_validates_against_the_vendored_30_schema(self):
		"""The 3.0 path must not have been broken by making to_xml version-aware."""
		schema = etree.XMLSchema(etree.parse(str(self.directory / "v3.0" / "markup.xsd")))
		document = etree.fromstring(self._markup_bytes("3.0"))
		schema.assertValid(document)

	def test_the_21_shape_puts_comments_beside_the_topic_not_inside_it(self):
		"""The difference the vendored schema encodes, asserted directly.

		Without this the writer could pass both schema checks while being 3.0-shaped
		inside a 2.1 package, because our own reader sniffs the shape either way.
		"""
		root = etree.fromstring(self._markup_bytes("2.1"))
		tags = [child.tag for child in root]
		self.assertIn("Comment", tags, "2.1 puts Comment as a sibling of Topic")
		self.assertNotIn("Comments", tags, "a Comments wrapper is the 3.0 shape")
		topic = root.find("Topic")
		self.assertIsNone(
			topic.find("Comments"),
			"2.1 must not nest Comments inside Topic; that is 3.0's arrangement",
		)

	def test_the_21_header_hangs_file_directly_with_no_files_wrapper(self):
		root = etree.fromstring(self._markup_bytes("2.1"))
		header = root.find("Header")
		self.assertIsNotNone(header)
		self.assertIsNotNone(
			header.find("File"),
			"2.1 hangs File straight off Header; the Files wrapper is 3.0's shape",
		)
		self.assertIsNone(header.find("Files"))

