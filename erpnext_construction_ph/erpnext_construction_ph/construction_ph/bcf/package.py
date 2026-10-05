# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The `.bcfzip` package: a zip with one folder per topic.

Layout, from buildingSMART's own `Documentation/README.md` (BCF 2.1):

    <root>/
        project.bcfp      optional; schema project.xsd
        bcf.version       the version file -- see below
        <topic-guid>/     ONE FOLDER PER TOPIC, named by the topic's UUID
            markup.bcf
            viewpoint.bcfv  names are not predefined, but ONE must be called this
            snapshot.png    likewise; ONE must be called this

Two things that file says and a guess would not have:

  * **`bcf.version` is how a package states its version**, rather than the reader
    inferring one. It is preferred here, and the shape-sniff in `markup.from_xml`
    is the fallback for packages that omit it. Reporting the *declared* version
    separately from the *detected* one is the point: a package that declares 2.1
    but parses as 3.0 is a finding, not something to paper over.
  * **A comment may reference at most ONE viewpoint.** "A viewpoint can have
    multiple comments, but a comment can only refer to one viewpoint." The
    many-to-many the schema would permit is explicitly not allowed.

Reading is version-agnostic and is not blocked by which version this project
chooses to WRITE. Writing is, and is deliberately not implemented here.

Zip paths use `/` as the separator, which the .bcfzip encoding guide requires.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

from .markup import BCFError, Markup, from_xml as markup_from_xml
from .viewpoint import VisualizationInfo, ViewpointError, from_xml as viewpoint_from_xml

MARKUP_FILENAME = "markup.bcf"
VIEWPOINT_FILENAME = "viewpoint.bcfv"
SNAPSHOT_FILENAME = "snapshot.png"
VERSION_FILENAME = "bcf.version"
PROJECT_FILENAME = "project.bcfp"

_UUID_FOLDER = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class PackageError(ValueError):
	"""A `.bcfzip` that does not follow the documented layout."""


@dataclass
class TopicFolder:
	"""One topic's folder, and everything it carries."""

	guid: str
	markup: Markup
	viewpoints: list[tuple[str, VisualizationInfo]] = field(default_factory=list)
	# (filename, bytes) for snapshots and any extra files, kept verbatim.
	assets: list[tuple[str, bytes]] = field(default_factory=list)

	@property
	def detected_version(self) -> str:
		"""The version inferred from the markup's SHAPE, not from bcf.version."""
		return self.markup.version


@dataclass
class BCFPackage:
	"""A whole `.bcfzip`."""

	topics: list[TopicFolder] = field(default_factory=list)
	# The version the package DECLARES in bcf.version, if it declares one at all.
	declared_version: str | None = None
	project_id: str | None = None

	@property
	def detected_version(self) -> str | None:
		versions = {topic.detected_version for topic in self.topics}
		if not versions:
			return None
		if len(versions) > 1:
			raise PackageError(
				f"a package mixes markup shapes: {sorted(versions)}. That is either a "
				"corrupt package or a reader bug, and guessing which would hide it."
			)
		return versions.pop()

	def version_agreement(self) -> str | None:
		"""None when the two agree (or either is absent), else a description.

		A package that DECLARES 2.1 and PARSES as 3.0 is worth reporting rather
		than normalising away -- the declared version is what the sender believes,
		and the shape is what it actually emitted.
		"""
		if not self.declared_version or not self.detected_version:
			return None
		if self.declared_version.strip() == self.detected_version.strip():
			return None
		return (
			f"package declares {self.declared_version.strip()!r} in {VERSION_FILENAME} "
			f"but its markup parses as {self.detected_version!r}"
		)

	def topic(self, guid: str) -> TopicFolder:
		for folder in self.topics:
			if folder.guid.lower() == guid.lower():
				return folder
		raise PackageError(f"no topic {guid!r} in this package")


def _read_version(archive: zipfile.ZipFile) -> str | None:
	"""`bcf.version` is the package's own statement of which schema it used."""
	try:
	 raw = archive.read(VERSION_FILENAME).decode("utf-8", "replace")
	except KeyError:
		return None
	# The file is XML following version.xsd; the version may be an attribute or an
	# element, and both spellings appear in the wild, so accept either.
	attribute = re.search(r'Version\s*=\s*"([^"]+)"', raw)
	if attribute:
		return attribute.group(1)
	element = re.search(r"<Version>([^<]+)</Version>", raw)
	return element.group(1) if element else raw.strip() or None


def _read_project(archive: zipfile.ZipFile) -> str | None:
	try:
		raw = archive.read(PROJECT_FILENAME).decode("utf-8", "replace")
	except KeyError:
		return None
	match = re.search(r'ProjectId\s*=\s*"([^"]*)"', raw) or re.search(
		r"<ProjectId>([^<]*)</ProjectId>", raw
	)
	return match.group(1) if match else None


def _is_viewpoint(name: str) -> bool:
	return name.lower().endswith(".bcfv")


def _is_snapshot(name: str) -> bool:
	return name.lower().endswith((".png", ".jpg", ".jpeg"))


def read_package(source) -> BCFPackage:
	"""Read a `.bcfzip` from a path, bytes, or an open zip.

	Topic folders are located by the UUID in the path rather than by position, so
	a package that also carries a `project.bcfp` or stray top-level file still
	reads.
	"""
	archive = _open(source)
	with archive as zf:
		package = BCFPackage(
			declared_version=_read_version(zf),
			project_id=_read_project(zf),
		)
		folders = sorted(
			{
				name.split("/")[0]
				for name in zf.namelist()
				if "/" in name and _UUID_FOLDER.match(name.split("/")[0])
			}
		)
		if not folders:
			raise PackageError(
				"no topic folders found: every topic must live in a folder named for "
				"its UUID, and none matched"
			)
		for folder in folders:
			package.topics.append(_read_topic(zf, folder))
	return package


def _open(source):
	"""Open a package, converting a corrupt archive into a PackageError.

	`zipfile.BadZipFile` is not a `PackageError`, so a truncated or non-zip upload
	escaped as a stack trace instead of the reader's own refusal. A corrupt file is
	an expected input here, not an exception to propagate.
	"""
	if isinstance(source, zipfile.ZipFile):
		return source
	stream = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
	try:
		return zipfile.ZipFile(stream)
	except zipfile.BadZipFile as error:
		raise PackageError(f"not a readable zip archive: {error}") from error


def _read_topic(archive: zipfile.ZipFile, folder: str) -> TopicFolder:
	prefix = f"{folder}/"
	markup_name = prefix + MARKUP_FILENAME
	try:
		raw = archive.read(markup_name).decode("utf-8", "replace")
	except KeyError as error:
		raise PackageError(
			f"topic folder {folder!r} has no {MARKUP_FILENAME}"
		) from error
	try:
		markup = markup_from_xml(raw)
	except BCFError as error:
		raise PackageError(f"topic {folder}: {error}") from error

	topic = TopicFolder(guid=folder, markup=markup)
	# The documented rule: names are not predefined, but ONE viewpoint and ONE
	# snapshot must keep the conventional names. A package that honours that gets
	# its viewpoint matched to the markup; anything else is kept by filename and
	# linked where the GUIDs agree.
	for name in archive.namelist():
		if not name.startswith(prefix):
			continue
		leaf = name[len(prefix) :]
		if "/" in leaf or not leaf:
			continue
		if _is_viewpoint(leaf):
			try:
				topic.viewpoints.append(
					(leaf, viewpoint_from_xml(archive.read(name).decode("utf-8", "replace")))
				)
			except ViewpointError as error:
				raise PackageError(f"topic {folder}, viewpoint {leaf}: {error}") from error
		elif _is_snapshot(leaf):
			topic.assets.append((leaf, archive.read(name)))
		elif leaf not in (MARKUP_FILENAME, VERSION_FILENAME):
			topic.assets.append((leaf, archive.read(name)))
	return topic


def link_viewpoints(topic: TopicFolder) -> list[str]:
	"""Viewpoint files whose GUID the topic's markup actually references.

	A viewpoint in the folder that no comment points at is still a viewpoint; it
	is reported rather than dropped, because "we ignored a file you shipped" is
	worse than "this file is unreferenced".
	"""
	wanted = {
		comment.viewpoint_guid.lower()
		for comment in topic.markup.topic.comments
		if comment.viewpoint_guid
	}
	return [name for name, info in topic.viewpoints if str(info.guid).lower() in wanted]


def unreferenced_viewpoints(topic: TopicFolder) -> list[str]:
	"""Viewpoint files present in the folder that no comment references."""
	referenced = {
		comment.viewpoint_guid.lower() for comment in topic.markup.topic.comments if comment.viewpoint_guid
	}
	return [name for name, info in topic.viewpoints if str(info.guid).lower() not in referenced]
