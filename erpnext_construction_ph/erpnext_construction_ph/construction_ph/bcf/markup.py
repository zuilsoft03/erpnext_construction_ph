# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""BCF `markup.bcf`: the issue document.

Written against buildingSMART's own `Schemas/markup.xsd` (BCF-XML,
branch `release_3_0`), read verbatim rather than paraphrased. That matters,
because the paraphrases disagree with the schema and with each other on at least
six points -- recorded in `docs` on the row that introduced this module. The
ones that would have produced a file no other tool could read:

  * `markup.xsd` declares **no `targetNamespace`**, so `Markup` and its children
    are in NO namespace. The widely repeated
    `http://www.buildingsmart-tech.org/bcf/bcf/2.1` is not in this schema.
  * `Markup` holds `Header?` and then **exactly one `Topic`**. One issue per
    file, which is why a package has one folder per topic.
  * `Header` contains a `Files` wrapper, and `File` is inside it -- not
    `Header/File`.
  * The viewpoint child is spelled **`ViewPoint`**, capital P.
  * `Comments` and `Viewpoints` are **inside** `Topic`, not siblings of it.
  * `TopicType` and `TopicStatus` are both **required** attributes.

`Topic`'s child order is a strict `xs:sequence`, so serialisation follows the
declared order exactly. Reordering is not cosmetic: a validator rejects it.
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# From shared-types.xsd.
GUID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")

# From markup.xsd, <xs:simpleType name="IfcGuid">: exactly 22 characters.
IFC_GUID_LENGTH = 22
IFC_GUID_RE = re.compile(r"^[0-9A-Za-z_$]*$")


class BCFError(ValueError):
	"""A document that does not satisfy markup.xsd."""


def new_guid() -> str:
	"""A BCF `Guid`: lowercase hex UUID, per shared-types.xsd."""
	return str(uuid.uuid4())


def check_guid(value: str, *, field: str = "Guid") -> str:
	if not isinstance(value, str) or not GUID_RE.match(value):
		raise BCFError(
			f"{field} must be a lowercase hyphenated UUID "
			f"(shared-types.xsd pattern), got {value!r}"
		)
	return value


def check_non_empty(value, *, field: str) -> str:
	"""`NonEmptyOrBlankString`: minLength 1 after whiteSpace collapse."""
	if value is None:
		raise BCFError(f"{field} is required and may not be blank")
	text = " ".join(str(value).split())
	if not text:
		raise BCFError(f"{field} may not be blank")
	return text


def check_ifc_guid(value: str, *, field: str = "IfcGuid") -> str:
	"""`IfcGuid`: exactly 22 characters of `[0-9A-Za-z_$]`."""
	if not isinstance(value, str) or len(value) != IFC_GUID_LENGTH or not IFC_GUID_RE.match(value):
		raise BCFError(
			f"{field} must be exactly {IFC_GUID_LENGTH} characters of [0-9A-Za-z_$] "
			f"(markup.xsd IfcGuid), got {value!r}"
		)
	return value


def _iso(value: datetime.datetime | datetime.date) -> str:
	"""An ISO-8601 instant, in UTC.

	Accepts a `date` as well as a `datetime`. `BCF Topic.due_date` is a **Date**
	field, so a topic carrying one arrives here as `datetime.date`, which has no
	`astimezone` -- and `export_package` raised `AttributeError` for every such
	topic. The round-trip test missed it because its fixtures set `due_date` to a
	value Frappe had already widened to a datetime, or to none at all. Found by
	the PH3-05 gate, which exports rows the app actually holds.
	"""
	if isinstance(value, datetime.datetime):
		return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	# A date carries no time and no zone, while the schema wants a dateTime, so it
	# is written as midnight UTC. That is a stated convention rather than an
	# invented time of day.
	return datetime.datetime(
		value.year, value.month, value.day, tzinfo=datetime.timezone.utc
	).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ElementReference:
	"""One `BimSnippet` on a topic: the binding between an issue and geometry.

	`Reference` is where the spec permits "a targeted model element identifier
	(such as an entity GlobalId)". This app uses the typed `IfcClass/GlobalId`
	form, because an untyped GlobalId cannot say which discipline an element
	belongs to and two elements in different models can share one.
	"""

	reference: str
	schema: str | None = None
	snippet_type: str = "IFC4"
	is_external: bool = False

	@classmethod
	def model_element(cls, ifc_class: str, global_id: str) -> "ElementReference":
		"""Bind to a model element as `IfcClass/GlobalId`."""
		check_ifc_guid(global_id, field="GlobalId")
		entity = check_non_empty(ifc_class, field="IfcClass")
		return cls(reference=f"{entity}/{global_id}", schema="IFC4")

	@property
	def ifc_class(self) -> str | None:
		"""The `IfcClass` half of a typed reference, or None if this is not one."""
		parts = self.reference.split("/", 1)
		return parts[0] if len(parts) == 2 and parts[0] else None

	@property
	def global_id(self) -> str | None:
		"""The GlobalId half, or None.

		Deliberately does NOT raise. `BimSnippet/Reference` is typed
		`NonEmptyOrBlankString` in markup.xsd -- the schema does not constrain it to
		a GlobalId or to an `IfcClass/GlobalId` path, so XSD validation can never
		flag a malformed one. That makes the grammar mine to enforce, and enforcing
		it by rejecting would reject files other tools legitimately wrote. So a
		reference that is neither bare nor typed is reported as such, not refused.
		"""
		candidate = self.reference.split("/", 1)[-1].strip()
		if len(candidate) == IFC_GUID_LENGTH and IFC_GUID_RE.match(candidate):
			return candidate
		return None

	@property
	def form(self) -> str:
		"""Which grammar this reference used: "typed", "bare-guid" or "unrecognised".

		Recorded rather than enforced, so a caller can report what a partner tool
		actually emitted instead of silently dropping the binding.
		"""
		if self.ifc_class and self.global_id:
			return "typed"
		if not self.ifc_class and self.global_id:
			return "bare-guid"
		return "unrecognised"


@dataclass
class DocumentReference:
	guid: str
	url: str | None = None
	document_guid: str | None = None
	description: str | None = None

	def validate(self) -> None:
		check_guid(self.guid, field="DocumentReference.Guid")
		if self.url and self.document_guid:
			raise BCFError("DocumentReference takes a Url OR a DocumentGuid, not both")


@dataclass
class ViewPoint:
	"""`Viewpoints/ViewPoint`. Note the capital P: that is the schema's spelling."""

	guid: str
	viewpoint: str | None = None
	snapshot: str | None = None
	index: int | None = None

	def validate(self) -> None:
		check_guid(self.guid, field="ViewPoint.Guid")


@dataclass
class Comment:
	guid: str
	date: datetime.datetime
	author: str
	comment: str | None = None
	viewpoint_guid: str | None = None
	modified_date: datetime.datetime | None = None
	modified_author: str | None = None

	def validate(self) -> None:
		check_guid(self.guid, field="Comment.Guid")
		if self.viewpoint_guid:
			check_guid(self.viewpoint_guid, field="Comment.Viewpoint.Guid")


@dataclass
class ModelFile:
	"""`Files/File`."""

	filename: str | None = None
	date: datetime.datetime | None = None
	reference: str | None = None
	ifc_project: str | None = None
	ifc_spatial_structure_element: str | None = None
	# markup.xsd defaults IsExternal to TRUE, which surprises people who assume
	# false. It is the schema's default and is reproduced here deliberately.
	is_external: bool = True

	def validate(self) -> None:
		if self.ifc_project:
			check_ifc_guid(self.ifc_project, field="File.IfcProject")
		if self.ifc_spatial_structure_element:
			check_ifc_guid(
				self.ifc_spatial_structure_element, field="File.IfcSpatialStructureElement"
			)


@dataclass
class Topic:
	"""`Topic`. Child order is the schema's `xs:sequence` and is not negotiable."""

	guid: str
	title: str
	topic_type: str
	topic_status: str
	creation_date: datetime.datetime
	creation_author: str
	priority: str | None = None
	index: int | None = None
	labels: list[str] = field(default_factory=list)
	modified_date: datetime.datetime | None = None
	modified_author: str | None = None
	due_date: datetime.datetime | None = None
	assigned_to: str | None = None
	stage: str | None = None
	description: str | None = None
	# `BimSnippet` is maxOccurs=1 in markup.xsd, and so is the `Reference` inside
	# it -- a topic carries AT MOST ONE element reference through that route. A
	# clash names two elements, so `ReferenceLinks/ReferenceLink` (unbounded) is
	# the schema's place for the rest. Both are read; neither is invented.
	reference_links: list[str] = field(default_factory=list)
	snippet: ElementReference | None = None
	document_references: list[DocumentReference] = field(default_factory=list)
	related_topics: list[str] = field(default_factory=list)
	comments: list[Comment] = field(default_factory=list)
	viewpoints: list[ViewPoint] = field(default_factory=list)
	server_assigned_id: str | None = None

	def validate(self) -> None:
		check_guid(self.guid, field="Topic.Guid")
		check_non_empty(self.title, field="Title")
		check_non_empty(self.topic_type, field="TopicType")
		check_non_empty(self.topic_status, field="TopicStatus")
		check_non_empty(self.creation_author, field="CreationAuthor")
		for related in self.related_topics:
			check_guid(related, field="RelatedTopic.Guid")
		if self.snippet:
			check_non_empty(self.snippet.reference, field="BimSnippet.Reference")
			check_non_empty(self.snippet.snippet_type, field="BimSnippet.SnippetType")
		for reference in self.document_references:
			reference.validate()
		for comment in self.comments:
			comment.validate()
		for viewpoint in self.viewpoints:
			viewpoint.validate()
		seen = set()
		for comment in self.comments:
			if comment.viewpoint_guid and comment.viewpoint_guid not in seen:
				seen.add(comment.viewpoint_guid)
		known = {v.guid for v in self.viewpoints}
		# A comment with no viewpoint is legitimate; only one that NAMES a viewpoint
		# the topic does not declare is a dangling reference.
		unknown = [
			c.viewpoint_guid
			for c in self.comments
			if c.viewpoint_guid and c.viewpoint_guid not in known
		]
		if unknown:
			raise BCFError(
				f"Comment references viewpoint {unknown[0]} which the topic does not declare; "
				"a comment cannot point at a viewpoint the file does not contain"
			)

	@property
	def element_guids(self) -> list[str]:
		"""Every IFC GlobalId this topic is bound to, in order.

		A clash names two elements. `BimSnippet` is a single element in the schema,
		so a second element is carried as a second snippet reference rather than
		being silently dropped -- a clash that loses an element is a clash that
		points at the wrong geometry.
		"""
		guids = []
		routes = list(self.reference_links)
		if self.snippet:
			routes.append(self.snippet.reference)
		for route in routes:
			_, separator, candidate = route.partition("/")
			if not separator:
				candidate = route
			candidate = candidate.strip()
			if len(candidate) == IFC_GUID_LENGTH and IFC_GUID_RE.match(candidate):
				guids.append(candidate)
		return guids


@dataclass
class Markup:
	"""The whole document: an optional `Header` and exactly one `Topic`."""

	topic: Topic
	files: list[ModelFile] = field(default_factory=list)
	# Which schema shape this was READ as, detected rather than assumed. "3.0" is
	# written by default; a 2.1 document is read without complaint and reported here.
	version: str = "3.0"

	def validate(self) -> None:
		self.topic.validate()
		for entry in self.files:
			entry.validate()
		if len(self.files) > 1:
			seen = set()
			for entry in self.files:
				if entry.reference and entry.reference in seen:
					raise BCFError(f"File.Reference {entry.reference!r} is declared twice")
				if entry.reference:
					seen.add(entry.reference)


def _sub(parent: ET.Element, tag: str, text=None) -> ET.Element:
	element = ET.SubElement(parent, tag)
	if text is not None:
		element.text = text
	return element


def to_xml(markup: Markup) -> str:
	"""Serialise. Element order follows the schema's `xs:sequence` exactly.

	VERSION-AWARE, and it has to be. `Markup.version` was detection metadata only,
	so this function used to emit ONE shape regardless of it -- and the shape it
	emits is 3.0. Writing that under a `bcf.version` of 2.1 produced a package that
	declared one version and contained another, which is the exact silent interop
	failure this module exists to prevent. `version_agreement()` would have caught
	it on read, which is too late: the file has already gone to a partner.

	The three differences, taken from the vendored v2.1/markup.xsd rather than from
	prose or memory:

	  2.1                                  3.0 (what this emitted unconditionally)
	  ------------------------------       -------------------------------------
	  Header > File (repeated)             Header > Files > File
	  Comment   as a sibling of Topic      Comments  nested inside Topic
	  Viewpoints as a repeated sibling,    Viewpoints nested inside Topic
	    each typed ViewPoint

	Note the 2.1 `Viewpoints` quirk: the element is plural but typed `ViewPoint` and
	REPEATS, so each occurrence IS a viewpoint rather than a holder. Reading it as a
	holder finds no viewpoints at all.
	"""
	markup.validate()
	two_one = str(markup.version).strip() == "2.1"
	root = ET.Element("Markup")

	if markup.files:
		header = _sub(root, "Header")
		# 2.1 hangs File straight off Header; 3.0 wraps them in a Files element.
		files = header if two_one else _sub(header, "Files")
		for entry in markup.files:
			node = _sub(files, "File")
			if entry.ifc_project:
				node.set("IfcProject", entry.ifc_project)
			if entry.ifc_spatial_structure_element:
				node.set("IfcSpatialStructureElement", entry.ifc_spatial_structure_element)
			# `isExternal` in 2.1, `IsExternal` in 3.0 -- a capitalisation change between
			# the two schemas, found by putting the output through the vendored 2.1 XSD.
			# A round trip through our own reader would never have found it.
			node.set(
				"isExternal" if two_one else "IsExternal",
				"true" if entry.is_external else "false",
			)
			if entry.filename:
				_sub(node, "Filename", entry.filename)
			if entry.date:
				_sub(node, "Date", _iso(entry.date))
			if entry.reference:
				_sub(node, "Reference", entry.reference)

	# 2.1 puts Comment and Viewpoints AFTER Topic, as siblings. 3.0 nests them
	# inside it. They are built here and attached at the right level below.
	comments = []
	for comment in markup.topic.comments:
		item = ET.Element("Comment")
		item.set("Guid", comment.guid)
		_sub(item, "Date", _iso(comment.date))
		_sub(item, "Author", comment.author)
		if comment.comment:
			_sub(item, "Comment", comment.comment)
		if comment.viewpoint_guid:
			_sub(item, "Viewpoint").set("Guid", comment.viewpoint_guid)
		if comment.modified_date:
			_sub(item, "ModifiedDate", _iso(comment.modified_date))
		if comment.modified_author:
			_sub(item, "ModifiedAuthor", comment.modified_author)
		comments.append(item)

	# 2.1's top-level element is named `Viewpoints` but TYPED `ViewPoint`, and it
	# REPEATS -- so each occurrence IS a viewpoint and carries its own Guid. It is not
	# a holder. 3.0 instead has a `Viewpoints` holder containing `ViewPoint` children.
	viewpoints = []
	for viewpoint in markup.topic.viewpoints:
		item = ET.Element("ViewPoint" if two_one else "ViewPoint")
		item.set("Guid", viewpoint.guid)
		if viewpoint.viewpoint:
			_sub(item, "Viewpoint", viewpoint.viewpoint)
		if viewpoint.snapshot:
			_sub(item, "Snapshot", viewpoint.snapshot)
		if viewpoint.index is not None:
			_sub(item, "Index", str(viewpoint.index))
		viewpoints.append(item)
	topic = markup.topic
	node = _sub(root, "Topic")
	node.set("Guid", topic.guid)
	if topic.server_assigned_id:
		node.set("ServerAssignedId", topic.server_assigned_id)
	node.set("TopicType", topic.topic_type)
	node.set("TopicStatus", topic.topic_status)

	# 2.1 and 3.0 disagree on the CONTAINERS as well as the placement. From the
	# vendored schemas: 2.1 repeats ReferenceLink and DocumentReference directly and
	# has no RelatedTopics at all, while 3.0 wraps them. Emitting the 3.0 shape into a
	# 2.1 package fails validation with "Expected is one of ( ReferenceLink, Title )",
	# which is exactly what putting the bytes through the vendored XSD caught.
	if topic.reference_links:
		if two_one:
			for link in topic.reference_links:
				_sub(node, "ReferenceLink", link)
		else:
			holder = _sub(node, "ReferenceLinks")
			for link in topic.reference_links:
				_sub(holder, "ReferenceLink", link)
	_sub(node, "Title", topic.title)
	if topic.priority:
		_sub(node, "Priority", topic.priority)
	if topic.index is not None:
		_sub(node, "Index", str(topic.index))
	if topic.labels:
		# 2.1 repeats a `Labels` element per label; 3.0 nests `Label`s in one holder.
		if two_one:
			for label in topic.labels:
				_sub(node, "Labels", label)
		else:
			holder = _sub(node, "Labels")
			for label in topic.labels:
				_sub(holder, "Label", label)
	_sub(node, "CreationDate", _iso(topic.creation_date))
	_sub(node, "CreationAuthor", topic.creation_author)
	if topic.modified_date:
		_sub(node, "ModifiedDate", _iso(topic.modified_date))
	if topic.modified_author:
		_sub(node, "ModifiedAuthor", topic.modified_author)
	if topic.due_date:
		_sub(node, "DueDate", _iso(topic.due_date))
	if topic.assigned_to:
		_sub(node, "AssignedTo", topic.assigned_to)
	if topic.stage:
		_sub(node, "Stage", topic.stage)
	if topic.description:
		_sub(node, "Description", topic.description)
	if topic.snippet:
		snippet = _sub(node, "BimSnippet")
		snippet.set("SnippetType", topic.snippet.snippet_type)
		# Same capitalisation change as on File: `isExternal` in 2.1.
		snippet.set(
			"isExternal" if two_one else "IsExternal",
			"true" if topic.snippet.is_external else "false",
		)
		_sub(snippet, "Reference", topic.snippet.reference)
		_sub(snippet, "ReferenceSchema", topic.snippet.schema or "IFC4")
	if topic.document_references:
		if two_one:
			holders = [node] * len(topic.document_references)
		else:
			holder = _sub(node, "DocumentReferences")
			holders = [holder] * len(topic.document_references)
		for reference, parent in zip(topic.document_references, holders):
			item = _sub(parent, "DocumentReference")
			item.set("Guid", reference.guid)
			if reference.url:
				_sub(item, "Url", reference.url)
			elif reference.document_guid:
				_sub(item, "DocumentGuid", reference.document_guid)
			if reference.description:
				_sub(item, "Description", reference.description)
	if topic.related_topics and not two_one:
		# 2.1's Topic has no RelatedTopics element at all, so a 2.1 writer must not
		# invent one. The links are still carried on the row; only the file omits them.
		holder = _sub(node, "RelatedTopics")
		for related in topic.related_topics:
			_sub(holder, "RelatedTopic").set("Guid", related)

	if two_one:
		# Siblings of Topic, in the schema's sequence: Comment(s) then Viewpoints.
		# Each `Viewpoints` occurrence IS a viewpoint -- the element is plural but
		# typed ViewPoint and repeats.
		for item in comments:
			root.append(item)
		for item in viewpoints:
			# Renamed to the plural spelling the schema requires, keeping the
			# attributes and children -- in 2.1 the element IS the viewpoint.
			element = ET.Element("Viewpoints")
			for key, value in item.attrib.items():
				element.set(key, value)
			for child in list(item):
				element.append(child)
			root.append(element)
	else:
		if comments:
			holder = _sub(node, "Comments")
			for item in comments:
				holder.append(item)
		if viewpoints:
			holder = _sub(node, "Viewpoints")
			for item in viewpoints:
				holder.append(item)

	ET.indent(root, space="  ")
	return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")



def _local(tag: str) -> str:
	"""Strip any namespace from a tag.

	`markup.xsd` declares NO targetNamespace, so a document that validates against it
	is namespace-less -- but real files from Solibri, Navistools and Revizto carry
	`xmlns="http://www.buildingsmart-tech.org/bcf/bcf/2.1"`, and the schema cannot
	see that axis at all. Matching on the local name is the only reading that accepts
	both, and a parser that rejects every real-world file turns a namespace choice
	into a support incident.
	"""
	return tag.rsplit("}", 1)[-1]


def _find(node, *tags):
	"""First direct child whose local name is one of `tags`, namespace or not."""
	wanted = {_local(tag) for tag in tags}
	for child in node:
		if _local(child.tag) in wanted:
			return child
	return None


def _findall(node, *tags):
	wanted = {_local(tag) for tag in tags}
	return [child for child in node if _local(child.tag) in wanted]


	child = _find(node, *tags)
	return child.text if child is not None else None


def _text(node, *tags):
	"""Text of the first child matching any of `tags`, namespace or not."""
	child = _find(node, *tags)
	return child.text if child is not None else None


def _read_repeated(node, *paths):
	"""Read a repeated element that 2.1 and 3.0 spell differently.

	3.0 wraps each value in a plural holder -- `ReferenceLinks/ReferenceLink`,
	`RelatedTopics/RelatedTopic`, `Labels/Label`. 2.1 hangs the value directly off
	`Topic`. Both are read, because reading a partner tool's file is where the
	interop actually lives, and picking only one shape loses data SILENTLY.
	"""
	found = []
	for path in paths:
		holder_name, child_name = path
		for holder in _findall(node, holder_name):
			found.extend(
				n.text for n in _findall(holder, child_name) if n.text and n.text.strip()
			)
		if found:
			break
	return found


def _read_bare(node, name: str) -> list[str]:
	"""A repeated element that IS its own value, as 2.1 spells them.

	2.1 repeats `<Labels>Structure</Labels>` and `<ReferenceLink>...</ReferenceLink>`
	directly under Topic; each element carries the text. 3.0 wraps the same values
	in a plural holder. Reading only the wrapped form loses 2.1 data silently.
	"""
	return [n.text for n in _findall(node, name) if n.text and n.text.strip()]


def _read_labels(node) -> list[str]:
	"""Labels, in either spelling: 3.0 `Labels/Label`, 2.1 a bare `Labels`."""
	wrapped = _read_repeated(node, ("Labels", "Label"))
	return wrapped or _read_bare(node, "Labels") or _read_bare(node, "Label")


def _read_guids(node, holder_name: str, child_name: str) -> list[str]:
	"""Guid-bearing children, wrapped (3.0) or bare (2.1)."""
	guids = [
		n.get("Guid")
		for holder in _findall(node, holder_name)
		for n in _findall(holder, child_name)
		if n.get("Guid")
	]
	return guids


def _when(value: str | None) -> datetime.datetime | None:
	if not value:
		return None
	return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def from_xml(text: str) -> Markup:
	"""Parse and validate. A document that does not satisfy the schema raises here."""
	root = ET.fromstring(text)
	# Matched on the local name: the published schema declares no targetNamespace,
	# but real files carry one, and a root check on the raw tag rejects all of them.
	if _local(root.tag) != "Markup":
		raise BCFError(f"root element must be Markup, got {root.tag!r}")

	# --- detect the version from the SHAPE, not a declaration -----------------
	# Neither 2.1 nor 3.0 declares a targetNamespace, so the namespace tells us
	# nothing. What differs is structure: 3.0 nests Comment/Viewpoints inside
	# Topic and wraps File in `Files`; 2.1 puts Comment/Viewpoints at the top
	# level and hangs File directly off Header. Sniffing is the only reliable
	# signal, and getting it wrong loses comments and viewpoints SILENTLY.
	# Detection is on POSITIVE evidence for each shape, and says "unknown" when
	# there is none. A 2.1 package whose topic has no comments and no viewpoints is
	# shape-identical to 3.0, and defaulting to 3.0 would be a guess presented as a
	# reading. `bcf.version` is the real signal; this is the fallback.
	topic_node = _find(root, "Topic")
	if _find(root, "Comment") is not None or _find(root, "Viewpoints") is not None:
		version = "2.1"
	elif (
		_find(_find(root, "Header") or root, "Files") is not None
		or (topic_node is not None and _find(topic_node, "Comments") is not None)
		or (topic_node is not None and _find(topic_node, "ReferenceLinks") is not None)
	):
		# 3.0 markers: the `Files` wrapper, and Comments/ReferenceLinks NESTED inside
		# Topic rather than sitting beside it.
		version = "3.0"
	else:
		version = "unknown"

	files: list[ModelFile] = []
	header = _find(root, "Header")
	if header is not None:
		# 3.0 wraps File in `Files`; 2.1 hangs it directly off Header.
		holder = _find(header, "Files")
		file_nodes = _findall(holder, "File") if holder is not None else _findall(header, "File")
		if file_nodes:
			for node in file_nodes:
				files.append(
					ModelFile(
						filename=_text(node, "Filename"),
						date=_when(_text(node, "Date")),
						reference=_text(node, "Reference"),
						ifc_project=node.get("IfcProject"),
						ifc_spatial_structure_element=node.get("IfcSpatialStructureElement"),
						is_external=node.get("IsExternal", "true") == "true",
					)
				)

	node = _find(root, "Topic")
	if node is None:
		raise BCFError("Markup must contain exactly one Topic")
	created = _when(_text(node, "CreationDate"))
	if not created:
		raise BCFError("Topic.CreationDate is required")

	snippet = None
	snippet_node = _find(node, "BimSnippet")
	if snippet_node is not None:
		schema = _text(snippet_node, "ReferenceSchema")
		snippet = ElementReference(
			reference=_text(snippet_node, "Reference") or "",
			schema=schema,
			snippet_type=snippet_node.get("SnippetType") or "IFC4",
			is_external=snippet_node.get("IsExternal", "false") == "true",
		)

	documents = []
	# 3.0 wraps them in `DocumentReferences`; 2.1 hangs `DocumentReference` off Topic.
	holder = _find(node, "DocumentReferences")
	document_nodes = (
		_findall(holder, "DocumentReference") if holder is not None else _findall(node, "DocumentReference")
	)
	if document_nodes:
		for item in document_nodes:
			documents.append(
				DocumentReference(
					guid=item.get("Guid") or "",
					url=_text(item, "Url"),
					document_guid=_text(item, "DocumentGuid"),
					description=_text(item, "Description"),
				)
			)

	comments = []
	# 3.0 nests them in `Comments`; 2.1 puts `Comment` at the top level of Markup.
	holder = _find(node, "Comments")
	comment_nodes = _findall(holder, "Comment") if holder is not None else _findall(root, "Comment")
	if comment_nodes:
		for item in comment_nodes:
			viewpoint = _find(item, "Viewpoint")
			comments.append(
				Comment(
					guid=item.get("Guid") or "",
					date=_when(_text(item, "Date")),
					author=_text(item, "Author") or "",
					comment=_text(item, "Comment"),
					viewpoint_guid=viewpoint.get("Guid") if viewpoint is not None else None,
					modified_date=_when(_text(item, "ModifiedDate")),
					modified_author=_text(item, "ModifiedAuthor"),
				)
			)

	viewpoints = []
	# The two versions disagree in a way that looks like a typo and is not:
	# 3.0 nests `<Viewpoints>` inside Topic and repeats `<ViewPoint>` inside it,
	# while 2.1 puts a REPEATED top-level element literally NAMED `Viewpoints` that
	# is TYPED as `ViewPoint` -- so each such element IS a viewpoint and is not a
	# holder. Reading a 2.1 file as if it were 3.0 finds no viewpoints and loses them
	# silently.
	holder = _find(node, "Viewpoints")
	if holder is not None:
		viewpoint_nodes = _findall(holder, "ViewPoint")
	else:
		viewpoint_nodes = _findall(root, "Viewpoints")
	if viewpoint_nodes:
		for item in viewpoint_nodes:
			index = _text(item, "Index")
			viewpoints.append(
				ViewPoint(
					guid=item.get("Guid") or "",
					viewpoint=_text(item, "Viewpoint"),
					snapshot=_text(item, "Snapshot"),
					index=int(index) if index is not None else None,
				)
			)

	index = _text(node, "Index")
	topic = Topic(
		guid=node.get("Guid") or "",
		title=_text(node, "Title") or "",
		topic_type=node.get("TopicType") or "",
		topic_status=node.get("TopicStatus") or "",
		creation_date=created,
		creation_author=_text(node, "CreationAuthor") or "",
		priority=_text(node, "Priority"),
		# The writer emits `DueDate` (markup.xsd marks it optional) and the reader
		# ignored it, so a due date survived neither direction of a round trip --
		# which is precisely what UI-7's own gate claims. Read it the same way
		# `CreationDate` is read.
		due_date=_when(_text(node, "DueDate")),
		index=int(index) if index is not None else None,
		labels=_read_labels(node),
		reference_links=(
			_read_repeated(node, ("ReferenceLinks", "ReferenceLink"))
			or _read_bare(node, "ReferenceLink")
		),
		snippet=snippet,
		document_references=documents,
		related_topics=(
			_read_guids(node, "RelatedTopics", "RelatedTopic")
			or [n.get("Guid") for n in _findall(node, "RelatedTopic") if n.get("Guid")]
		),
		comments=comments,
		viewpoints=viewpoints,
		server_assigned_id=node.get("ServerAssignedId"),
	)
	markup = Markup(topic=topic, files=files, version=version)
	markup.validate()
	return markup
