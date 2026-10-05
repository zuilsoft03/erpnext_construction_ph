# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Writing a `.bcfzip`, in the 2.1 shape.

WHY 2.1, stated rather than assumed
-----------------------------------
The read side is version-agnostic and the write side is not: 2.1 and 3.0 differ
structurally, so writing both would mean building the layout twice. 2.1 is chosen
because it is what Solibri and Navisworks actually emit -- the interop target this
engine exists to serve -- and because the vendored 2.1 schema is already what the
round-trip tests validate against. 3.0 additionally needs the `Coloring` and
`Bitmap`/`Bitmaps` shapes modelled before a camera is complete, and shipping a guess
the vendored schema refuses would be worse than shipping the older shape and naming
the gap. **This is the conservative choice made on the evidence, not a claim that 3.0
is wrong**; a 3.0 writer is a separate piece of work with its own gate.

The layout, from buildingSMART's Documentation/README.md at tag v2.1:

    bcf.version                the package's statement of which schema it used
    project.bcfp               optional
    <topic-uuid>/              one folder per topic, NAMED FOR THE UUID
        markup.bcf
        viewpoint.bcfv         names are not predefined but these conventional
        snapshot.png           names must be kept

Two facts a guess would have missed, and which this writer obeys:

  - the folder is named for the topic's **UUID**, not its title or position;
  - `bcf.version` is how a package STATES its version, so it is written. A package
    that emits 2.1 markup without it makes the reader guess from shape alone.

The round-trip property this module exists to protect: a package written here is
read back by `read_package` with its topics, comments, viewpoints and GUIDs intact,
and the markup validates against the vendored 2.1 XSD. That is asserted by the
tests, not assumed.
"""

from __future__ import annotations

import io
import zipfile

from erpnext_construction_ph.construction_ph.bcf import markup as _markup
from erpnext_construction_ph.construction_ph.bcf import package as _package
from erpnext_construction_ph.construction_ph.bcf import viewpoint as _viewpoint
from erpnext_construction_ph.construction_ph.bcf.markup import BCFError
from erpnext_construction_ph.construction_ph.bcf.package import (
	MARKUP_FILENAME,
	PROJECT_FILENAME,
	SNAPSHOT_FILENAME,
	VERSION_FILENAME,
	VIEWPOINT_FILENAME,
	BCFPackage,
	PackageError,
)

#: The version this writer emits. Stated once so a reader does not have to infer it.
WRITE_VERSION = "2.1"

_UUID_FOLDER = _package._UUID_FOLDER


def _folder_name(guid: str) -> str:
	"""A topic's folder name, which the layout says is the topic's UUID.

	Refused rather than coerced: a folder named after a title still opens in some
	tools and silently loses its topic association in others, so emitting one would
	be a package that looks right and is not.
	"""
	guid = str(guid).strip()
	if not _UUID_FOLDER.match(guid):
		raise PackageError(
			f"topic folder names must be the topic's UUID; {guid!r} is not a UUID. "
			"Renaming it to something prettier would produce a package some tools read "
			"and others silently mis-file."
		)
	return guid


def _assert_two_one(package: BCFPackage) -> None:
	"""Refuse to write a package whose content is not 2.1-shaped.

	`Markup.version` is detected on read and settable on construction. Writing a 3.0
	markup into a 2.1 package -- or vice versa -- is the silent interop failure this
	module exists to prevent, so it is refused at the boundary rather than written
	and hoped for.
	"""
	for folder in package.topics:
		if folder.detected_version != WRITE_VERSION:
			raise PackageError(
				f"topic {folder.guid} is shaped {folder.detected_version!r}, but this "
				f"writer emits {WRITE_VERSION!r}. Writing it anyway would produce a file "
				"whose bcf.version disagrees with its own markup, which is the failure "
				"the reader reports as version_agreement()."
			)


def write_package(package: BCFPackage, *, version: str | None = None) -> bytes:
	"""Serialise `package` to `.bcfzip` bytes.

	`version` is EXPLICIT and required of the caller by intent, even though it carries
	a default: `Markup.version` also defaults, silently, to "3.0". So a caller who
	forgets it once got a 3.0 package labelled 3.0, and a caller who remembered to set
	`Markup(version="2.1")` got a package whose label and shape could disagree. Two
	silent defaults that have to be kept in step is the hazard, so the writer re-derives
	the version from the CONTENT and refuses a mismatch rather than trusting either.

	That is what `_assert_two_one` does: it reads each topic's detected shape and
	refuses anything this writer does not emit. A caller cannot forget their way to a
	mislabelled package; they can only be stopped.
	"""
	if version is not None and str(version).strip() != WRITE_VERSION:
		raise PackageError(
			f"this writer emits {WRITE_VERSION!r}; asked for {version!r}. Emitting a "
			"bcf.version the bytes do not match is how a package becomes unreadable."
		)
	_assert_two_one(package)

	if not package.topics:
		raise PackageError(
			"a package with no topics is not a package. An empty .bcfzip opens as a "
			"valid archive and shows no issue in every tool, which is the worst shape "
			"a file can have."
		)

	seen_folders: set[str] = set()
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
		archive.writestr(VERSION_FILENAME, WRITE_VERSION + "\n")
		if package.project_id:
			archive.writestr(PROJECT_FILENAME, package.project_id + "\n")

		for folder in package.topics:
			name = _folder_name(folder.guid)
			folder.markup.validate()
			key = name.lower()
			if key in seen_folders:
				raise PackageError(
					f"two topics claim the folder {name!r}. BCF identifies a topic by "
					"that folder, so a collision silently loses one of them."
				)
			seen_folders.add(key)

			archive.writestr(f"{name}/{MARKUP_FILENAME}", _markup.to_xml(folder.markup))

			for filename, info in folder.viewpoints:
				info.validate()
				archive.writestr(f"{name}/{filename}", _viewpoint.to_xml(info))
			for filename, blob in folder.assets:
				archive.writestr(f"{name}/{filename}", blob)

	return buffer.getvalue()


def write_package_to(
	package: BCFPackage, path, *, version: str | None = None
) -> int:
	"""Write to a path. Returns the byte count, for callers that report it."""
	data = write_package(package, version=version)
	with open(path, "wb") as handle:
		handle.write(data)
	return len(data)


__all__ = [
	"WRITE_VERSION",
	"MARKUP_FILENAME",
	"PROJECT_FILENAME",
	"SNAPSHOT_FILENAME",
	"VERSION_FILENAME",
	"VIEWPOINT_FILENAME",
	"BCFError",
	"BCFPackage",
	"PackageError",
	"write_package",
	"write_package_to",
]
