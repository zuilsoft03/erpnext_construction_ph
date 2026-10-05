# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""`markup.bcf` against buildingSMART's own schema.

The round-trip tests here are necessary and NOT sufficient: a self-round-trip
passes on a wrong namespace, a wrong element order and a misspelled child, and
the failure only appears in Solibri or Navisworks -- the tools this work exists to
talk to. So `test_the_document_validates_against_the_published_xsd` is the test
that carries the row, and it is skipped loudly when the schema is not reachable
rather than quietly passing.
"""

import datetime
import unittest
from pathlib import Path

from erpnext_construction_ph.construction_ph.bcf.markup import (
	BCFError,
	Comment,
	DocumentReference,
	ElementReference,
	Markup,
	ModelFile,
	Topic,
	ViewPoint,
	check_ifc_guid,
	from_xml,
	new_guid,
	to_xml,
)

GLOBAL_ID = "24O8sC$qj4q8B0D3sK2L1e"
OTHER_GLOBAL_ID = "1Ab2Cd$ef3Gh4Ijk5Lmn6O"


def _documents():
	"""A document exercising every construct the schema lets us emit.

	Module level, not a method: it was lost twice when a class around it was
	replaced, and a fixture that vanishes with a refactor is a fixture that stops
	being checked.
	"""
	return Markup(
		topic=_topic(
			snippet=ElementReference.model_element("IfcBeam", GLOBAL_ID),
			viewpoints=[ViewPoint(guid=new_guid(), viewpoint="v.bcfv", snapshot="s.png", index=0)],
			comments=[Comment(guid=new_guid(), date=_now(), author="a@b.c", comment="ok")],
			document_references=[DocumentReference(guid=new_guid(), url="https://cde/s-102.pdf")],
		),
		files=[ModelFile(filename="m.ifc", date=_now(), ifc_project=GLOBAL_ID)],
	)


def _now():
	return datetime.datetime(2026, 9, 28, 8, 0, 0, tzinfo=datetime.timezone.utc)


def _topic(**overrides):
	base = dict(
		guid=new_guid(),
		title="Supply Air Duct intersects reinforced concrete beam B102",
		topic_type="Clash",
		topic_status="Open",
		creation_date=_now(),
		creation_author="mep.engineer@project.com",
		description="Duct 400x200 intersects beam B102 on Level 01 by 85mm.",
	)
	base.update(overrides)
	return Topic(**base)


class TestSchemaFacts(unittest.TestCase):
	"""Facts read off `Schemas/markup.xsd` that the paraphrases get wrong."""

	def test_ifc_guid_is_exactly_22_characters(self):
		self.assertEqual(check_ifc_guid(GLOBAL_ID), GLOBAL_ID)
		self.assertEqual(len(GLOBAL_ID), 22)
		for bad in ("2O8sC$qj4q8B0D3sK2L1", GLOBAL_ID + "X", "", "with space here 1234567890"):
			with self.assertRaises(BCFError):
				check_ifc_guid(bad)

	def test_guid_must_be_a_lowercase_uuid(self):
		with self.assertRaises(BCFError):
			_topic(guid="8F5B4C12-3456-4789-A1B2-C3D4E5F60718").validate()

	def test_topic_type_and_status_are_required_not_optional(self):
		"""markup.xsd marks both `use="required"`. The paraphrases called them optional."""
		with self.assertRaises(BCFError):
			_topic(topic_type="").validate()
		with self.assertRaises(BCFError):
			_topic(topic_status="").validate()

	def test_markup_holds_exactly_one_topic(self):
		with self.assertRaises(BCFError):
			from_xml("<Markup><Header/></Markup>")

	def test_root_must_be_markup(self):
		with self.assertRaises(BCFError):
			from_xml("<Topics/>")


class TestElementReference(unittest.TestCase):
	"""The binding the whole app was missing: an issue to geometry."""

	def test_a_model_element_is_typed_ifcclass_slash_globalid(self):
		reference = ElementReference.model_element("IfcBeam", GLOBAL_ID)
		self.assertEqual(reference.reference, f"IfcBeam/{GLOBAL_ID}")
		self.assertEqual(reference.ifc_class, "IfcBeam")
		self.assertEqual(reference.global_id, GLOBAL_ID)

	def test_a_bad_global_id_is_refused_rather_than_written_out(self):
		with self.assertRaises(BCFError):
			ElementReference.model_element("IfcBeam", "too-short")

	def test_a_clash_carries_both_elements(self):
		"""A clash names two elements; losing one points at the wrong geometry.

		Through `ReferenceLinks`, because `BimSnippet` is maxOccurs=1 and so is the
		`Reference` inside it -- the multi-element form is the schema's, not an
		extension of ours. The full argument is in
		`TestRealWorldTolerance.test_a_two_element_clash_uses_reference_links...`.
		"""
		topic = _topic(
			reference_links=[f"IfcDuctSegment/{GLOBAL_ID}"],
			snippet=ElementReference.model_element("IfcBeam", OTHER_GLOBAL_ID),
		)
		self.assertEqual(topic.element_guids, [GLOBAL_ID, OTHER_GLOBAL_ID])

	def test_an_untyped_reference_reports_no_global_id(self):
		self.assertIsNone(ElementReference(reference="https://cde/x.ifc").global_id)


class TestRoundTrip(unittest.TestCase):
	def test_a_full_topic_survives_a_round_trip(self):
		viewpoint_guid = new_guid()
		topic = _topic(
			priority="High",
			labels=["Structure", "HVAC", "Clash"],
			modified_date=_now(),
			modified_author="structural@project.com",
			due_date=_now(),
			assigned_to="mep.engineer@project.com",
			stage="Detailed Design",
			snippet=ElementReference.model_element("IfcBeam", GLOBAL_ID),
			document_references=[
				DocumentReference(guid=new_guid(), url="https://cde/dwg/S-102.pdf", description="S-102")
			],
			related_topics=[new_guid()],
			comments=[
				Comment(
					guid=new_guid(),
					date=_now(),
					author="structural@project.com",
					comment="Post-tensioned; reroute under the soffit.",
					viewpoint_guid=viewpoint_guid,
				)
			],
			viewpoints=[
				ViewPoint(guid=viewpoint_guid, viewpoint="viewpoint.bcfv", snapshot="snapshot.png", index=0)
			],
		)
		markup = Markup(
			topic=topic,
			files=[ModelFile(filename="structural.ifc", date=_now(), ifc_project=GLOBAL_ID)],
		)
		back = from_xml(to_xml(markup))
		self.assertEqual(back.topic.title, topic.title)
		self.assertEqual(back.topic.labels, ["Structure", "HVAC", "Clash"])
		self.assertEqual(back.topic.snippet.global_id, GLOBAL_ID)
		self.assertEqual(len(back.topic.comments), 1)
		self.assertEqual(back.topic.viewpoints[0].snapshot, "snapshot.png")
		self.assertEqual(back.files[0].ifc_project, GLOBAL_ID)

	def test_the_viewpoint_child_is_spelled_viewpoint_with_a_capital_p(self):
		"""The schema says ViewPoint. Viewpoints/Viewpoint would not validate."""
		text = to_xml(Markup(topic=_topic(viewpoints=[ViewPoint(guid=new_guid())])))
		self.assertIn("<ViewPoint ", text)
		self.assertNotIn("<Viewpoint ", text)

	def test_comments_live_inside_the_topic(self):
		text = to_xml(
			Markup(
				topic=_topic(
					comments=[Comment(guid=new_guid(), date=_now(), author="a@b.c", comment="hi")]
				)
			)
		)
		self.assertLess(text.index("<Comments>"), text.index("</Topic>"))

	def test_a_comment_cannot_point_at_an_undeclared_viewpoint(self):
		topic = _topic(
			comments=[
				Comment(guid=new_guid(), date=_now(), author="a@b.c", viewpoint_guid=new_guid())
			]
		)
		with self.assertRaises(BCFError):
			topic.validate()

	def test_a_blank_title_is_refused(self):
		with self.assertRaises(BCFError):
			_topic(title="   ").validate()

	def test_the_element_order_follows_the_schema_sequence(self):
		text = to_xml(Markup(topic=_topic()))
		order = [
			"<Title>",
			"<CreationDate>",
			"<CreationAuthor>",
		]
		positions = [text.index(tag) for tag in order]
		self.assertEqual(positions, sorted(positions), "Topic children must follow xs:sequence")


class TestAgainstVendoredSchema(unittest.TestCase):
	"""The tests that carry the row.

	Each version's schemas sit in their own directory so the relative
	`<xs:include schemaLocation="shared-types.xsd"/>` resolves exactly as upstream
	wrote it -- no splicing, no reconstructed schema. They are VENDORED, not
	fetched: a gate that downloads its schema can be silently repointed at another
	revision, and an offline run degrades to a skip, which is the same lie as a guard
	that cannot run reporting clean. So this FAILS if lxml is absent.
	"""

	SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "bcf" / "schema"

	def _schema(self, version: str):
		from lxml import etree

		directory = self.SCHEMA_ROOT / version
		return etree.XMLSchema(etree.parse(str(directory / "markup.xsd")))

	def test_the_validator_is_present(self):
		"""Never skip silently. A schema test that skips proves nothing."""
		try:
			import lxml.etree  # noqa: F401
		except ImportError:
			self.fail(
				"lxml is required: without it this suite cannot prove schema validity, "
				"and reporting green would be the same lie as a guard that cannot run"
			)

	def test_a_30_document_is_valid_against_the_30_schema(self):
		from lxml import etree

		document = etree.fromstring(to_xml(_documents()).encode("utf-8"))
		self._schema("v3.0").assertValid(document)

	def test_a_21_sample_is_valid_against_the_21_schema(self):
		"""The document a Solibri file actually looks like, checked by the 2.1 schema."""
		from lxml import etree

		document = etree.fromstring(BCF_21_SAMPLE.encode("utf-8"))
		self._schema("v2.1").assertValid(document)

	def test_the_validator_bites_on_30(self):
		from lxml import etree

		broken = to_xml(Markup(topic=_topic())).replace(' TopicType="Clash"', "", 1)
		with self.assertRaises(Exception):
			self._schema("v3.0").assertValid(etree.fromstring(broken.encode("utf-8")))

	def test_every_vendored_schema_is_real_and_its_includes_resolve(self):
		"""Guards the vendoring itself, which is how a 404 got committed as a schema.

		A `v2.1/Schemas/shared-types.xsd` was fetched and saved even though
		upstream has no such file at v2.1 -- the 14-byte body of a 404 was
		committed as though it were a schema. 2.1's markup.xsd is self-contained
		and has no include, so nothing failed; the file was simply a lie sitting
		in the repository looking authoritative. This asserts every vendored file
		parses as XML and that each `xs:include` resolves inside its own
		directory.
		"""
		from lxml import etree

		ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
		for version in ("v2.1", "v3.0"):
			directory = self.SCHEMA_ROOT / version
			found = sorted(directory.glob("*.xsd"))
			self.assertTrue(found, f"{version} has no vendored schema")
			for path in found:
				raw = path.read_bytes()
				# A 404 body is a valid fetch and invalid XML. Assert the shape
				# explicitly so a saved error page can never look like a schema.
				self.assertTrue(
					raw.lstrip().startswith(b"<?xml"),
					f"{version}/{path.name} does not begin with an XML declaration",
				)
				self.assertIn(
					b"xs:schema", raw, f"{version}/{path.name} contains no xs:schema element"
				)
				tree = etree.parse(str(path))  # raises on a 404 body, not XML
				self.assertEqual(
					tree.getroot().tag,
					"{http://www.w3.org/2001/XMLSchema}schema",
					f"{path.name} is not an XMLSchema document",
				)
				for include in tree.xpath("//xs:include", namespaces=ns):
					target = directory / include.get("schemaLocation")
					self.assertTrue(
						target.exists(),
						f"{version}/{path.name} includes {include.get('schemaLocation')}, "
						"which is not vendored beside it",
					)

	def test_vendored_schemas_are_byte_identical_to_upstream(self):
		"""CC BY-ND's operative half is NoDerivatives, and "parses as XML" does not
		enforce it -- an edited schema still parses. The constraint is byte-identity,
		so the digests are pinned and asserted.

		If this fails, the vendored schema was edited. Do not fix it here: report it
		upstream and change the code instead.
		"""
		import hashlib

		expected = {
			"v2.1/markup.xsd": "15e9af22065870d93ad45431147748adc5b32ef830d6dfe7b4eb10cf5e6c4441",
			"v2.1/visinfo.xsd": "c46100eeab8211ecd62b888fdb41d1c0c47b74fa210e35da18259ddf6156e7b9",
			"v3.0/markup.xsd": "6c80f1c566ace8b405ae0ab530438aec2014bf35dd14cfc3c4cf0c5aa0b9d169",
			"v3.0/visinfo.xsd": "0855bc002310b4a9decd67b1b21631bd2e0368f0878183da8448e34d807e7418",
			"v3.0/shared-types.xsd": "93d20dfcc6d7f451ae85a8277559b0347478c278218a42f0ef41dcc5550a2090",
		}
		for name, digest in expected.items():
			actual = hashlib.sha256((self.SCHEMA_ROOT / name).read_bytes()).hexdigest()
			self.assertEqual(
				actual, digest, f"{name} is not byte-identical to upstream"
			)

	def test_the_vendored_schemas_keep_their_licence_and_attribution(self):
		"""buildingSMART publishes these under CC BY-ND 4.0.

		ND is the part that binds: these files may be redistributed VERBATIM with
		attribution and may never be edited. The NOTICE is the attribution, and a
		guard that a future edit to a vendored schema is a mistake rather than a
		fix -- which is the whole reason the engine validates against these files
		rather than working around them.
		"""
		notice = (self.SCHEMA_ROOT / "NOTICE.md").read_text(encoding="utf-8")
		self.assertIn("Attribution", notice)
		self.assertIn("NoDerivatives", notice)
		self.assertIn("buildingSMART", notice)
		self.assertIn("creativecommons.org/licenses/by-nd/4.0", notice)
		# The licence is stated on one branch and asserted nowhere; the NOTICE says so
		# rather than presenting CC BY-ND as settled.
		self.assertIn("NOASSERTION", notice)
		self.assertIn("no `LICENSE` at the `v2.1` tag", notice)

	def test_the_two_schemas_really_differ(self):
		"""If the versions were identical, the version-specific tests would prove nothing."""
		first = (self.SCHEMA_ROOT / "v2.1" / "markup.xsd").read_text(encoding="utf-8")
		second = (self.SCHEMA_ROOT / "v3.0" / "markup.xsd").read_text(encoding="utf-8")
		self.assertNotEqual(first, second)


class TestRealWorldTolerance(unittest.TestCase):
	"""Things the schema cannot check, and where a strict parser becomes an incident.

	`markup.xsd` declares no targetNamespace and types `BimSnippet/Reference` as
	`NonEmptyOrBlankString`. So the validator is structurally blind to the namespace,
	and it permits ANY non-blank reference. Both are places where refusing a file
	would reject something a partner tool legitimately wrote.
	"""

	NS = "http://www.buildingsmart-tech.org/bcf/bcf/2.1"

	def test_a_namespaced_document_is_parsed(self):
		"""Real Solibri/Navisworks output carries an xmlns this schema never sees."""
		produced = to_xml(
			Markup(topic=_topic(snippet=ElementReference.model_element("IfcBeam", GLOBAL_ID)))
		)
		namespaced = produced.replace("<Markup>", f'<Markup xmlns="{self.NS}">', 1)
		back = from_xml(namespaced)
		self.assertEqual(back.topic.title, "Supply Air Duct intersects reinforced concrete beam B102")
		self.assertEqual(back.topic.snippet.global_id, GLOBAL_ID)

	def test_a_namespaced_document_still_carries_its_labels_and_viewpoints(self):
		guid = new_guid()
		namespaced = to_xml(
			Markup(
				topic=_topic(
					labels=["Clash"],
					viewpoints=[ViewPoint(guid=guid, viewpoint="v.bcfv")],
				)
			)
		).replace("<Markup>", f'<Markup xmlns="{self.NS}">', 1)
		back = from_xml(namespaced)
		self.assertEqual(back.topic.labels, ["Clash"])
		self.assertEqual(back.topic.viewpoints[0].viewpoint, "v.bcfv")

	def test_an_unrecognised_reference_is_reported_not_refused(self):
		"""`Reference` is NonEmptyOrBlankString; only a blank string is invalid."""
		external = ElementReference(reference="https://cde.example/snippet.ifc")
		self.assertEqual(external.form, "unrecognised")
		Markup(topic=_topic(snippet=external)).validate()  # must not raise

	def test_both_reference_forms_are_recognised(self):
		self.assertEqual(
			ElementReference.model_element("IfcBeam", GLOBAL_ID).form, "typed"
		)
		self.assertEqual(ElementReference(reference=GLOBAL_ID).form, "bare-guid")
		self.assertEqual(
			ElementReference(reference=f"IfcBeam/{GLOBAL_ID}").global_id, GLOBAL_ID
		)

	def test_a_two_element_clash_uses_reference_links_not_a_comma_joined_string(self):
		"""`BimSnippet` is maxOccurs=1 and so is its `Reference`.

		A clash names two elements, so the second has to go in
		`ReferenceLinks/ReferenceLink`, which IS unbounded. A comma-joined reference
		round-trips and validates -- it is just a string -- and no other tool would
		read it, which is precisely why it must not be the representation.
		"""
		topic = _topic(
			reference_links=[f"IfcDuctSegment/{GLOBAL_ID}"],
			snippet=ElementReference.model_element("IfcBeam", OTHER_GLOBAL_ID),
		)
		self.assertEqual(topic.element_guids, [GLOBAL_ID, OTHER_GLOBAL_ID])
		text = to_xml(Markup(topic=topic))
		self.assertIn("<ReferenceLinks>", text)
		self.assertIn(f"IfcDuctSegment/{GLOBAL_ID}", text)
		back = from_xml(text)
		self.assertEqual(back.topic.element_guids, [GLOBAL_ID, OTHER_GLOBAL_ID])

	def test_reference_links_is_the_first_child_of_the_topic(self):
		"""`ReferenceLinks` leads the Topic xs:sequence."""
		text = to_xml(Markup(topic=_topic(reference_links=[f"IfcBeam/{GLOBAL_ID}"])))
		self.assertLess(text.index("<ReferenceLinks>"), text.index("<Title>"))


BCF_21_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<Markup>
  <Header>
    <File IfcProject="24O8sC$qj4q8B0D3sK2L1e">
      <Filename>structural.ifc</Filename>
      <Date>2026-09-28T08:00:00Z</Date>
    </File>
  </Header>
  <Topic Guid="8f5b4c12-3456-4789-a1b2-c3d4e5f60718" TopicType="Clash" TopicStatus="Open">
    <ReferenceLink>IfcDuctSegment/24O8sC$qj4q8B0D3sK2L1e</ReferenceLink>
    <Title>Duct clashes with beam B102</Title>
    <Priority>High</Priority>
    <Labels>Structure</Labels>
    <Labels>HVAC</Labels>
    <CreationDate>2026-09-28T08:00:00Z</CreationDate>
    <CreationAuthor>mep.engineer@project.com</CreationAuthor>
    <BimSnippet SnippetType="IFC4" isExternal="false">
      <Reference>IfcBeam/1Ab2Cd$ef3Gh4Ijk5Lmn6O</Reference>
      <ReferenceSchema>IFC4</ReferenceSchema>
    </BimSnippet>
    <RelatedTopic Guid="1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9"/>
  </Topic>
  <Comment Guid="8b8241fa-e91b-4f93-b673-81b0a85012a2">
    <Date>2026-09-28T08:15:00Z</Date>
    <Author>structural@project.com</Author>
    <Comment>Post-tensioned; reroute under the soffit.</Comment>
    <Viewpoint Guid="4e3d2c1b-a098-7654-3210-fedcba987654"/>
  </Comment>
  <Viewpoints Guid="4e3d2c1b-a098-7654-3210-fedcba987654">
    <Viewpoint>viewpoint.bcfv</Viewpoint>
    <Snapshot>snapshot.png</Snapshot>
    <Index>0</Index>
  </Viewpoints>
</Markup>
"""


class TestReadingBothVersions(unittest.TestCase):
	"""2.1 and 3.0 are different SHAPES, and reading the wrong one loses data silently.

	A 3.0-only reader takes a real 2.1 file, finds the `Topic`, and returns a topic
	with no comments and no viewpoints -- no error, just missing data. That is the
	worst failure mode available and it is the interop this work exists to deliver.
	"""

	def test_a_21_document_is_detected(self):
		self.assertEqual(from_xml(BCF_21_SAMPLE).version, "2.1")

	def test_a_30_document_is_detected_from_its_own_structure(self):
		"""Detection needs positive evidence, so the document must CARRY 3.0-only
		structure: the `Files` wrapper. A 3.0 document without one is shape-identical
		to a minimal 2.1 one, and the reader says "unknown" rather than guessing."""
		# Comments nested inside Topic, which only 3.0 does.
		nested = _topic(
			comments=[Comment(guid=new_guid(), date=_now(), author="a@b.c", comment="hi")]
		)
		self.assertEqual(from_xml(to_xml(Markup(topic=nested))).version, "3.0")
		# And a Header with the `Files` wrapper, the other 3.0-only marker.
		with_files = Markup(
			topic=_topic(),
			files=[ModelFile(filename="m.ifc", date=_now(), ifc_project=GLOBAL_ID)],
		)
		self.assertEqual(from_xml(to_xml(with_files)).version, "3.0")

	def test_a_minimal_document_is_reported_as_unknown_not_guessed(self):
		"""No 2.1-only and no 3.0-only structure: the honest answer is "unknown"."""
		self.assertEqual(from_xml(to_xml(Markup(topic=_topic()))).version, "unknown")

	def test_a_21_comments_survive(self):
		back = from_xml(BCF_21_SAMPLE)
		self.assertEqual(len(back.topic.comments), 1)
		self.assertEqual(back.topic.comments[0].author, "structural@project.com")

	def test_a_21_viewpoints_survive(self):
		back = from_xml(BCF_21_SAMPLE)
		self.assertEqual(len(back.topic.viewpoints), 1)
		self.assertEqual(back.topic.viewpoints[0].snapshot, "snapshot.png")

	def test_a_21_top_level_labels_survive(self):
		self.assertEqual(from_xml(BCF_21_SAMPLE).topic.labels, ["Structure", "HVAC"])

	def test_a_21_reference_link_survives(self):
		back = from_xml(BCF_21_SAMPLE)
		self.assertEqual(back.topic.reference_links, [f"IfcDuctSegment/{GLOBAL_ID}"])
		self.assertEqual(
			back.topic.element_guids, [GLOBAL_ID, OTHER_GLOBAL_ID]
		)

	def test_a_21_related_topic_survives(self):
		self.assertEqual(
			from_xml(BCF_21_SAMPLE).topic.related_topics,
			["1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9"],
		)

	def test_a_21_header_file_survives(self):
		back = from_xml(BCF_21_SAMPLE)
		self.assertEqual(len(back.files), 1)
		self.assertEqual(back.files[0].filename, "structural.ifc")
		self.assertEqual(back.files[0].ifc_project, GLOBAL_ID)

	def test_a_21_comment_may_point_at_a_top_level_viewpoint(self):
		"""The 2.1 view is what a Solibri file actually looks like."""
		back = from_xml(BCF_21_SAMPLE)
		known = {v.guid for v in back.topic.viewpoints}
		self.assertIn(back.topic.comments[0].viewpoint_guid, known)


if __name__ == "__main__":
	unittest.main()
