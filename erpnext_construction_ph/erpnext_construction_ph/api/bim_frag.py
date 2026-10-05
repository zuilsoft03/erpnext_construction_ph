# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Server side of the browser-converted `.frag` cache (D-043).

The architecture this belongs to is the one OpenProject's IFC viewer uses: the
**browser** converts the IFC with That Open's WASM importer, and the resulting
`.frag` is a *cache* that exists so later sessions skip re-parsing. The server
stores that cache and serves it back. It never transcodes geometry itself.

That is the whole point, and it is worth being explicit about why, because this
project spent its whole Phase 3 believing the opposite. The recorded blocker said
the fragments processor needs a WebGL context and therefore cannot run in a
Node worker. It does not need one: `FRAGS.IfcImporter().process()` ran on a real
8.6 MB IFC in plain Node, in about a second, and the loader resolved
`web-ifc-node.wasm` — a CPU module. **WebGL is required to RENDER, not to
CONVERT.** Evidence: `ops/tmp-ph3-01/conversion-probe.json`.

The consequence of getting this right is that production needs no headless-GL
stack, no GPU, and no second copy of the model: whoever opens a model in a browser
pays the conversion once, and everyone after them reads the cache.

So this module has three jobs and no fourth:
  * accept a converted `.frag` from the browser, and store it as a private File;
  * serve that File to a user who is entitled to see the model;
  * keep `conversion_status` honest, so a model with no cache reads as
    "not yet converted" rather than "Ready" with nothing behind it.
"""

import hashlib
import re

import frappe
from frappe import _
from frappe.utils import cint, nowdate

# `.frag` is a compressed binary blob. A model that is worth keeping is worth
# serving in full, and truncating it would produce a fragment that loads and then
# renders a partial building -- which reads as corruption rather than as a cap.
MAX_FRAG_BYTES = 256 * 1024 * 1024

# Frappe's magic for "attach a File that only entitled users can fetch": a File with
# `is_private = 1` is served from `/private/files/...` through `download_file`, which
# performs the permission check. Serving the frag from `sites/assets` instead would
# put one client's building model in a world-readable directory, and this is exactly
# the class of leak `list_models` and `list_topics` were each hardened against.
FRAG_DOCTYPE = "File"

# Which gate protects a `.frag`. `may_view_bim`, not `may_view`: the geometry is the
# sensitive half, it names the structure of the client's building, and the rest of
# this app's BIM surface already uses the stronger check. Using the weaker guard here
# would make the cache the one endpoint an internal Desk role could read directly.


def _require_model_access(model_name):
	"""Load a BIM Model, or fail. Raises rather than returning None.

	Returns the doc only after the caller is entitled to see it. Doing the check
	here rather than at each call site is deliberate: three entry points, one
	authorisation, and no way to add a fourth and forget it.
	"""
	from erpnext_construction_ph.api.portal_data import may_view_bim

	if not model_name:
		frappe.throw(_("No BIM model was named."))
	try:
		model = frappe.get_doc("BIM Model", model_name)
	except frappe.DoesNotExistError:
		frappe.throw(_("BIM model {0} does not exist.").format(model_name))

	project = model.project
	# A model with no project is an orphan: there is no tenant to check it against,
	# so there is no basis on which to say this caller may see it. Refusing is the
	# only safe reading of an absent boundary.
	if not project:
		frappe.throw(
			_("This model is not attached to a project, so its geometry cannot be served."),
			frappe.PermissionError,
		)
	# The stronger guard: project scoping AND a client-facing role. The geometry
	# names the client's building, and `bim_viewer.list_models`/`list_topics` already
	# gate on this, so `may_view` here would make the cache the one endpoint an
	# internal Desk role could read directly.
	if not may_view_bim(frappe.session.user, project):
		frappe.throw(_("You do not have access to this model."), frappe.PermissionError)
	return model


def cache_path(model_name):
	"""The File name the model's `.frag` is stored under.

	Deterministic, so re-converting replaces the cache rather than accumulating one
	file per page load, and scoped by the model docname so two projects cannot
	collide on a shared model NAME.

	**Slugified, and this is a fix rather than cosmetics.** The first version built
	the name as `bim-frag-<model_name>.frag`, and `BIM Model` is autonamed
	`field:model_name` — so the file came out as `bim-frag-Cached Model d80dfc.frag`,
	spaces and all. That is not a valid URL path segment: the browser
	percent-encodes the space, the File's own `file_url` does not, and the fetch
	404s on a model the server reports as Ready. A deterministic slug keeps the URL
	and the stored name byte-identical, which is the entire basis of
	`get_frag_url`'s existence check.
	"""
	slug = re.sub(r"[^A-Za-z0-9_-]+", "-", model_name).strip("-")
	return f"bim-frag-{slug or 'model'}.frag"


@frappe.whitelist()
def store_frag(model, frag_b64, file_size=None, max_bytes=None):
	"""Store a `.frag` the browser produced. Returns `{file_url, file_name, bytes}`.

	`frag_b64` is base64 because `frappe.call` passes JSON, and a `.frag` is binary.
	The browser reads the ArrayBuffer the importer returned, base64-encodes it, and
	posts it here. That is a copy in memory, and it is the honest cost of a
	JSON API: the alternative is a raw upload endpoint, which would need its own
	content-length limit, its own entitlement check, and its own place in the gate.
	"""
	model_doc = _require_model_access(model)

	if not frag_b64:
		frappe.throw(_("No fragment data was supplied."))

	import base64

	try:
		raw = base64.b64decode(frag_b64, validate=True)
	except Exception:
		# A corrupt or truncated payload must not be stored: a half-written `.frag`
		# loads in the viewer and renders an incomplete building, which is far harder
		# to diagnose than a refusal.
		frappe.throw(_("The fragment data was not valid base64."))

	if not raw:
		frappe.throw(_("The fragment data was empty."))
	# `max_bytes` is a parameter, not a module constant read here, so the refusal is
	# TESTABLE: proving a 256 MB ceiling needs 256 MB of payload, and a test that
	# skips itself "because the buffer is large" is a test that never runs. Callers
	# pass nothing and get the ceiling below.
	limit = max_bytes or MAX_FRAG_BYTES
	if len(raw) > limit:
		frappe.throw(
			_("The fragment is {0} bytes, over the {1} byte limit.").format(len(raw), limit)
		)

	# A `.frag` is a compressed binary that begins with binary noise. Confirming the
	# content rather than trusting the extension or the client's word: this value is
	# served back to every viewer of this model, and a stored text file with a
	# `.frag` name would be fetched and handed to a binary parser.
	if raw[:2] == b"PK" or raw[:4] == b"\x1f\x8b\x08\x00":
		# A zip/gzip is not a raw .frag. Refused rather than stored-and-hoped-for.
		frappe.throw(_("That looks like a compressed archive, not a .frag."))

	name = cache_path(model_doc.name)
	existing = frappe.db.exists(FRAG_DOCTYPE, {"file_name": name, "attached_to_doctype": "BIM Model"})
	if existing:
		frappe.delete_doc(FRAG_DOCTYPE, existing, force=True, ignore_permissions=True)

	file_doc = frappe.get_doc(
		{
			"doctype": FRAG_DOCTYPE,
			"file_name": name,
			"attached_to_doctype": "BIM Model",
			"attached_to_name": model_doc.name,
			"is_private": 1,
			"content": raw,
		}
	).insert(ignore_permissions=True)

	model_doc.frag_url = f"/private/files/{name}"
	model_doc.file_size_bytes = cint(file_size) or len(raw)
	model_doc.conversion_status = "Ready"
	model_doc.converted_on = nowdate()
	model_doc.save(ignore_permissions=True)

	return {
		"file_url": model_doc.frag_url,
		"file_name": name,
		"bytes": len(raw),
		# The digest is returned so the browser can tell whether its cached copy is
		# still the one the server holds, without downloading the model to find out.
		"sha256": hashlib.sha256(raw).hexdigest(),
	}


# Types whose area is reinforcement rather than a finished surface. Measured: a
# reinforcing bar has a surface area, but it is not a surface anyone builds, and
# adding one to a slab overstates a quantity. Classified here so a caller is not left
# to guess from a type name.
REINFORCEMENT_TYPES = ("IFCREINFORCINGBAR", "IFCREINFORCINGMESH", "IFCREBAR")


def area_class(element_type):
	"""Which family a type belongs to, from the type name itself.

	Deliberately a small explicit list rather than a pattern. A rule that guesses --
	"anything with BAR in it" -- would silently reclassify a type nobody has seen, and
	a wrong classification is worse than an absent one because it looks authoritative.
	An unrecognised type returns UNCLASSIFIED, which forces a decision rather than
	quietly joining a total.
	"""
	t = (element_type or "").upper()
	if t in REINFORCEMENT_TYPES:
		return "REINFORCEMENT"
	if t in ("IFCWALL", "IFCWALLSTANDARDCASE", "IFCCURTAINWALL", "IFCSLAB",
			"IFCSLABSTANDARDCASE", "IFCROOF", "IFCFOOTING", "IFCPLATE",
			"IFCCOLUMN", "IFCBEAM", "IFCDOOR", "IFCWINDOW", "IFCSTAIR",
			"IFCSTAIRFLIGHT", "IFCRAMP", "IFCRAILING", "IFCMEMBER", "IFCBUILDINGELEMENTPROXY"):
		return "SURFACE"
	return "UNCLASSIFIED"


# IFC entity families, by their STRUCTURE rather than by their name.
#
# A definition is an `IfcTypeObject` / `IfcTypeProduct`: the template other things are
# cut from. It is not in the building. Everything else that appears in a model is
# either an element or an assembly of them, and both are real.
#
# Expressed as FAMILIES so a type nobody has seen still lands correctly. The previous
# approach -- matching names ending in "TYPE" -- would have gone stale silently the
# first time a model contained an `IfcCoveringType`.
# IFC's own convention: a product TYPE class always ends in "Type".
#
# `IfcTypeObject` -> `IfcTypeProduct` -> `IfcElementType` -> `IfcBeamType`/
# `IfcWallType`/`IfcCoveringType`, against `IfcProduct` -> `IfcElement` ->
# `IfcWall`/`IfcSlab`/`IfcBeam`. The two branches are SIBLINGS, not parent and child,
# so no single prefix catches both -- which is why the obvious "does it start with
# IfcTypeProduct" version is wrong, and MEASURED wrong: it returned False for
# IfcTypeProduct, IfcBeamType and IfcCoveringType alike.
#
# So the test is the FORMAT's rule rather than this project's list. That is the
# difference that matters: a hand-written blocklist of type names this author happened
# to think of goes stale silently on the first `IfcCoveringType` nobody predicted, and
# a wrong count is exactly the kind of failure that looks like success.
DEFINITION_SUFFIX = "type"
# The two ABSTRACT roots of the type branch, named in the IFC schema itself. They are
# the exception the suffix rule cannot catch, because they end in "Object" and
# "Product" rather than "Type". They are abstract in IFC and never appear in instance
# data -- every real type in a model is a concrete `IfcXxxType` -- so naming them is
# about the rule being COMPLETE rather than about fixing a count.
DEFINITION_ROOTS = ("ifctypeobject", "ifctypeproduct")


def is_definition(kind):
	"""Is this IFC entity a definition rather than a thing in the building?

	`kind` is the entity's IFC CLASS (`IfcTypeProduct`, `IfcBeamType`, `IfcWall`),
	never a per-project blocklist. An unrecognised or missing kind is NOT a
	definition: guessing "definition" would silently delete real elements from a
	takeoff, which is far worse than counting a template.
	"""
	k = str(kind or "").strip().lower()
	if not k or not k.startswith("ifc"):
		return False
	return k.endswith(DEFINITION_SUFFIX) or k in DEFINITION_ROOTS


@frappe.whitelist()
def repair_definition_rows(model):
	"""Remove definition rows ALREADY in the index, and say how many.

	Refusing them at ingest is not enough on its own: the file was imported once, and
	~590 rows that cannot be measured are sitting in the index right now, inflating
	every count the project reports. This removes them and RETURNS THE NUMBER, because
	a repair nobody can see is indistinguishable from a repair that did not happen.

	Rows are matched on the structural test, never on a name list, and a real element
	with no geometry is NOT removed -- that is a gap in the model, and hiding it would
	trade a wrong count for a missing one.
	"""
	model_doc = _require_model_access(model)
	rows = frappe.get_all(
		"BIM Model GUID",
		filters={"parent": model_doc.name},
		fields=["name", "element_type"],
	)
	defined = [r.name for r in rows if is_definition(r.element_type)]
	for name in defined:
		frappe.db.delete("BIM Model GUID", {"name": name})
	frappe.db.commit()
	return {
		"model": model_doc.name,
		"removed": len(defined),
		"remaining": len(rows) - len(defined),
	}


@frappe.whitelist()
def element_counts(model):
	"""How many ELEMENTS there are, and how many of those can be MEASURED.

	The honest question is not "how many things are in the file" but "how many things
	can be measured", and only the second is a takeoff. Reporting the first as though it
	were the second is what put ~590 unmeasurable rows into this project's own totals --
	in a count that read as a success.
	"""
	model_doc = _require_model_access(model)
	row = frappe.db.sql(
		"""SELECT
		     COUNT(*)                                                    AS indexed,
		     SUM(CASE WHEN geom_surface_area IS NOT NULL THEN 1 ELSE 0 END) AS measurable
		   FROM `tabBIM Model GUID` WHERE parent=%s""",
		(model_doc.name,),
		as_dict=True,
	)[0]
	indexed = int(row.indexed or 0)
	measurable = int(row.measurable or 0)
	return {
		"model": model_doc.name,
		"indexed_rows": indexed,
		"measurable_elements": measurable,
		"present_but_unmeasurable": indexed - measurable,
		"note": (
			"measurable_elements is the count a takeoff may use. The rest are rows in "
			"the index that carry no geometry: definitions, or elements the model does "
			"not describe well enough to measure. Both are real gaps and neither is a "
			"quantity."
		),
	}


@frappe.whitelist()
def measured_count(model):
	"""How many of this model's elements already carry an area. Just the count.

	Exists so the viewer can skip a re-measure. MEASURED: with 1,502 areas already
	stored, opening the model still sent 9 `record_areas` POSTs, because nothing
	asked whether the work had been done. A count and not a summary -- `area_summary`
	groups and sums, and this runs on every model open.

	Authorisation is the same `_require_model_access` every other endpoint here uses,
	so this cannot be used to ask about a model the caller may not see. It reports
	only geometry counts, never content.
	"""
	model_doc = _require_model_access(model)
	return int(
		frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabBIM Model GUID`
			   WHERE parent=%s AND geom_surface_area IS NOT NULL""",
			(model_doc.name,),
		)[0][0]
	)


@frappe.whitelist()
def area_summary(model):
	"""Per-type area subtotals for a model, with each type classified.

	A grand total is returned ONLY together with the classification that produced it.
	Offering a bare 22,338 m2 to a quantity surveyor is the failure this replaces: the
	figure is arithmetically right and mixes reinforcement with finished surfaces.
	"""
	model_doc = _require_model_access(model)
	rows = frappe.db.sql(
		"""
		SELECT element_type,
		       COUNT(*)                        AS n,
		       SUM(geom_surface_area)          AS surface,
		       SUM(geom_footprint_area)        AS footprint
		FROM `tabBIM Model GUID`
		WHERE parent=%s AND geom_surface_area IS NOT NULL
		GROUP BY element_type
		ORDER BY n DESC
		""",
		(model_doc.name,),
		as_dict=True,
	)

	total_rows = sum(r.n for r in rows) or 0
	total_surface = float(sum((r.surface or 0) for r in rows))

	# Per class, so a caller can take a SURFACE total without re-deriving it.
	by_class = {}
	for r in rows:
		k = area_class(r.element_type)
		b = by_class.setdefault(
			k, {"rows": 0, "surface_m2": 0.0, "footprint_m2": 0.0, "types": []}
		)
		b["rows"] += r.n
		b["surface_m2"] += float(r.surface or 0)
		b["footprint_m2"] += float(r.footprint or 0)
		b["types"].append(r.element_type)

	types = [
		{
			"element_type": r.element_type,
			"class": area_class(r.element_type),
			"rows": r.n,
			"surface_m2": round(float(r.surface or 0), 1),
			"footprint_m2": round(float(r.footprint or 0), 1),
			# Both shares, because they do not move together and a reader shown only
			# one of them will draw the wrong conclusion from it.
			"share_of_rows": round(r.n / total_rows * 100, 1) if total_rows else 0,
			"share_of_surface": round(float(r.surface or 0) / total_surface * 100, 1)
			if total_surface
			else 0,
		}
		for r in rows
	]

	return {
		"model": model_doc.name,
		"measured_rows": total_rows,
		"by_type": types,
		"by_class": by_class,
		# Named for what it is, and never returned alone.
		"all_types_surface_m2": round(total_surface, 1),
		"note": (
			"all_types_surface_m2 is EVERY type summed, reinforcement included. It is not a "
			"surface quantity. Use by_class.SURFACE.surface_m2 for surfaces, and treat "
			"by_class.UNCLASSIFIED as a decision still owed, not as a total."
		),
	}


@frappe.whitelist()
def measured_areas(model, limit=50):
	"""The stored areas, newest measurement first, with the BASIS stated.

	An upload with nothing reading it back proves only that nothing threw. This is
	the other half of the round trip, and it names the basis beside each number so a
	reader is never left inferring which "area" a column holds.
	"""
	model_doc = _require_model_access(model)
	rows = frappe.get_all(
		"BIM Model GUID",
		filters={
			"parent": model_doc.name,
			"geom_surface_area": ["is", "set"],
		},
		fields=["element_guid", "geom_surface_area", "geom_footprint_area"],
		order_by="geom_surface_area desc",
		limit_page_length=min(int(limit or 50), 500),
	)
	return [
		{
			"element_guid": r["element_guid"],
			"surface_m2": r["geom_surface_area"],
			"surface_basis": "sum of triangle areas over the element mesh, each triangle once",
			"footprint_m2": r["geom_footprint_area"],
			"footprint_basis": "XY bounding box of the element mesh",
		}
		for r in rows
	]


@frappe.whitelist()
def indexed_guids(model):
	"""The GlobalIds this model has indexed, so a browser can keep only those.

	A measurement whose GUID is not in the index cannot be joined to an element on
	any later read, so storing it is cost with no possible use. The browser asks this
	once and uses it as the allow-list for the upload.
	"""
	model_doc = _require_model_access(model)
	guids = frappe.get_all(
		"BIM Model GUID",
		filters={"parent": model_doc.name},
		pluck="element_guid",
		limit_page_length=0,
	)
	return sorted({g for g in guids if g})


@frappe.whitelist()
def record_areas(model, areas):
	"""Store per-element surface and footprint area, computed by the browser.

	**Why the browser computes them.** The server never transcodes geometry (D-043),
	and the per-element MESH only exists in the browser's fragments model:
	`getItemsVolume(ids)` returns a single number for the whole model -- it ignores
	the ids -- so per-element volume is NOT available server-side at all. But
	`getItemsGeometry(ids)` returns one entry per id, so the browser can walk each
	element's triangles and produce both numbers while the geometry is already loaded.

	**Why BOTH numbers.** "Area" is a commercial quantity and the two answers are not
	interchangeable. For a 200 x 3000 x 200 wall the surface is about 2.5 m2 and the
	footprint about 0.06 m2. Storing only one would let the split-view comparison
	compare a surface against a plan-area takeoff and report a confident mismatch
	that is entirely an artefact of the basis. Storing both, and having the
	comparison declare which it used, is the only version that cannot mislead.

	**The wire keys are `surface_area`/`footprint_area`; the COLUMNS they land in are
	`geom_surface_area`/`geom_footprint_area`.** The child table carries three
	quantities a reader would call "area" — `mapped_area`, which the coordinator
	typed — and the geometry-derived pair is prefixed so nobody has to guess which is
	which. The payload keys stay short because they are a transport, not a column.

	Authorised by `_require_model_access`, the same check as `store_frag` and
	`stream_frag`, so there is one authorisation and not three that can drift.
	"""
	model_doc = _require_model_access(model)
	if not isinstance(areas, (list, tuple)) or not areas:
		frappe.throw(_("No element areas were supplied."))

	# Bounded, and deliberately: this writes a row per element, and a caller that
	# sends a million of them is a denial of service, not a measurement.
	# `frappe.conf.get(...)`, NOT `getattr(frappe.conf, ..., default)`: conf is
	# dict-like and returns None for a missing key rather than raising, so the
	# getattr default never applies and the comparison raises
	# `TypeError: '>' not supported between instances of 'int' and 'NoneType'`.
	# The same trap swallowed `frappe.socketio_port` on the portal a row ago.
	LIMIT = frappe.conf.get("bim_area_index_limit") or 50000
	if len(areas) > LIMIT:
		frappe.throw(_("Too many element areas in one request (max {0}).").format(LIMIT))

	rows = {}
	skipped = []
	for item in areas:
		if not isinstance(item, dict):
			continue
		guid = (item.get("element_guid") or "").strip()
		if not guid:
			continue
		# WIRE KEY -> COLUMN, written out rather than derived.
		#
		# The browser posts `surface_area` / `footprint_area`; those land in
		# `geom_surface_area` / `geom_footprint_area`. A rename that swept both ends
		# to the `geom_` names would have left the browser sending keys this function
		# no longer reads, and the upload would have written nothing while returning
		# a cheerful `written: N`. So the mapping is explicit and is the first thing
		# to check if the numbers ever come back empty.
		#
		# Both spellings are accepted, so a client mid-migration is not silently
		# dropped -- but the wire key is the documented one.
		wire_surface = item.get("surface_area", item.get("geom_surface_area"))
		wire_footprint = item.get("footprint_area", item.get("geom_footprint_area"))
		# `frappe.utils.flt` rather than float(): an absent, empty or non-numeric
		# value must store NULL, not zero. Zero is a real measurement for a
		# degenerate element, and a reader cannot tell it from "not measured".
		# A definition is refused AT THE DOOR rather than indexed and counted. The
		# client sends what the entity IS, so this does not depend on a list of names
		# that goes stale the first time a model contains a type nobody has seen.
		if is_definition(item.get("ifc_kind")):
			skipped.append(guid)
			continue
		rows[guid] = {
			"geom_surface_area": frappe.utils.flt(wire_surface) if wire_surface is not None else None,
			"geom_footprint_area": frappe.utils.flt(wire_footprint) if wire_footprint is not None else None,
			"element_name": (item.get("element_name") or "").strip() or None,
		}

	if not rows:
		# NOT an error, and specifically not "nothing was supplied". An upload of
		# nothing but definitions is a caller that did everything right and had
		# everything it sent correctly refused -- reporting that as an empty upload
		# would send a well-behaved client round again on the false belief that it had
		# forgotten to send anything. The distinction is the whole difference between
		# a refusal the caller can act on and one that sends it into a retry loop.
		if skipped:
			frappe.db.commit()
			return {
				"model": model_doc.name,
				"written": 0,
				"definitions_skipped": len(skipped),
				"note": "Every element in this upload was an IFC definition, so none was indexed.",
			}
		frappe.throw(_("No element areas were supplied."))

	existing = frappe.get_all(
		"BIM Model GUID",
		filters={"parent": model_doc.name, "element_guid": ["in", list(rows)]},
		fields=["name", "element_guid"],
	)
	by_guid = {r["element_guid"]: r["name"] for r in existing}

	written = 0
	for guid, values in rows.items():
		if guid in by_guid:
			frappe.db.set_value("BIM Model GUID", by_guid[guid], values, update_modified=False)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "BIM Model GUID",
					"parent": model_doc.name,
					"parenttype": "BIM Model",
					"parentfield": "model_guids",
					"element_guid": guid,
					**values,
				}
			)
			doc.insert(ignore_permissions=True)
		written += 1

	frappe.db.commit()
	return {
		"model": model_doc.name,
		"written": written,
		# Named, not swallowed: a caller that sent 577 definitions and got no word
		# about it cannot tell a clean upload from one that quietly dropped things.
		"definitions_skipped": len(skipped),
	}


@frappe.whitelist()
def stream_frag(model):
	"""Serve a model's `.frag` BYTES to a caller entitled to see it.

	This module's second stated job is "serve that File to a user who is entitled
	to see the model", and until now it did not do it: `get_frag_url` returned
	`/private/files/bim-frag-SCHOOL-STR.frag`, and a portal client entitled to that
	very model got **403** on that URL. Measured, not assumed -- a client scoped to
	VERIFY-PORTAL Beta authenticates, sees SCHOOL-STR in `list_models`, and is then
	refused the fragment the payload just told it to fetch. Frappe's
	`/private/files` authorisation requires READ on `File` and on the doctype the
	file hangs off, and a `Construction Client` has neither.

	So the portal's read-only viewer could not load geometry, and no amount of
	WebGL would have changed that. The 403 was FILE DELIVERY, not rendering.

	Authorisation is `_require_model_access` -- the same one `get_frag_url` and
	`store_frag` use, deliberately, so there is one check rather than three that
	can drift. The File is never exposed by URL: the bytes come back through this
	method, so "can this caller see this model" is answered once, here, on the only
	path that carries geometry off the server.
	"""
	model_doc = _require_model_access(model)

	file_name = cache_path(model_doc.name)
	if not frappe.db.exists(FRAG_DOCTYPE, {"file_name": file_name}):
		frappe.throw(
			_("This model has no cached .frag yet."),
			frappe.DoesNotExistError,
		)

	# BY `file_name`, NOT BY A `like` ON `file_url`. This was the whole cause of
	# `test_an_administrator_gets_the_bytes_and_not_a_url`, and it is a PRODUCTION bug
	# rather than a test one: it fires for any model whose bytes have been stored before,
	# which is the ordinary case for a re-conversion.
	#
	# Frappe's `save_file` DEDUPES BY CONTENT. Store the same bytes twice and it REUSES the
	# existing File row — re-attaching it to the new model, updating `file_name` and
	# `attached_to_*`, but **leaving `file_url` as it was**.
	#
	# `file_url` is not a content address and it is not authoritative: it is a path built
	# from `file_name` at save time, and the row that Frappe picks up already has one. So it
	# is RETAINED FROM WHICHEVER ROW FIRST STORED THESE BYTES, and it can disagree with the
	# same row's own `file_name`:
	#
	#     file_name    'bim-frag-CORE30-ALPHA-d913be.frag'               <- current, correct
	#     file_url     '/private/files/bim-frag-CORE30-ALPHA-b2beb9.frag'  <- from another row
	#     MATCHES LIKE  False
	#
	# Both strings are model-name-derived, which is exactly why this is subtle: they LOOK
	# like the same kind of value and one is stale. Matching `file_url` against a
	# model-derived pattern therefore fails **silently** — the row is present and correctly
	# attached, the query returns nothing, and the error reads "the cached fragment for this
	# model is missing", which is false.
	#
	# `file_name` and `attached_to_name` are both authoritative, and both correct.
	file_row = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "BIM Model", "attached_to_name": model_doc.name,
				 "file_name": file_name},
		pluck="name",
		limit=1,
	)
	if not file_row:
		frappe.throw(_("The cached fragment for this model is missing."), frappe.DoesNotExistError)

	# Frappe's own download contract, matched exactly (see `frappe/handler.py`
	# `download_file`): `filename` + `filecontent` + `type`. Two earlier attempts
	# failed against it and both failed SILENTLY in shape --
	#   * `frappe.read_file(File.file_name)` read 0 bytes, because `file_name` is the
	#     BASE name and not a path, and a bare 500 with no traceback came back;
	#   * adding `display_content_as` made the response fall through to the JSON
	#     encoder, which raised `UnicodeDecodeError` on binary content -- Frappe's own
	#     version does not set it.
	# `File.get_content()` is the supported reader, and it is what the framework uses.
	file_doc = frappe.get_doc("File", file_row[0])
	# `.frag` is not a registered MIME type, so Frappe's download path labels it
	# `application/unknown`. A browser `fetch()` does not care much, but a `.frag` is
	# binary and a text type on binary bytes is how a client ends up corrupting it.
	file_doc.file_type = "application/octet-stream"
	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"


@frappe.whitelist()
def get_frag_url(model):
	"""The URL the viewer should fetch, or a reason there isn't one.

	Named states rather than a bare null, because "no URL" has three quite different
	 causes and a viewer that cannot tell them apart shows a blank canvas in all
	 three — indistinguishable from a broken model.
	"""
	model_doc = _require_model_access(model)

	frag_url = model_doc.frag_url
	if not frag_url:
		return {
			"available": False,
			"reason": "not_converted",
			"message": _(
				"This model has no cached .frag yet. Open it in a browser once to convert it."
			),
		}

	if not frappe.db.exists(FRAG_DOCTYPE, {"file_name": cache_path(model_doc.name)}):
		# The row points at a file that is gone. This is the state that a stale
		# `frag_url` produces, and it must not be reported as "Ready".
		#
		# And it must not be LEFT as "Ready" either. This module's own stated job is
		# "keep `conversion_status` honest, so a model with no cache reads as 'not yet
		# converted' rather than 'Ready' with nothing behind it" -- and the workspace's
		# model DROPDOWN reads that field, so a model marked Ready whose cache is
		# gone is offered to a user who then gets nothing. Measured on the gate site:
		# two models sat at `conversion_status: Ready` with `file_size_bytes: 0` and
		# `get_frag_url` answering `cache_missing`.
		if model_doc.conversion_status == "Ready":
			# BEST EFFORT. This is a read path the viewer calls, and housekeeping
			# must never be able to turn an honest `cache_missing` into a viewer
			# error: a read-only replica, or permissions, would raise here and the
			# model would stop loading at all. The report is the contract; the
			# correction is housekeeping.
			try:
				frappe.db.set_value(
					"BIM Model", model_doc.name, "conversion_status", "Stale", update_modified=False
				)
			except Exception:
				frappe.log_error(
					title="Could not mark a fragment cache stale",
					message=frappe.get_traceback(),
				)
		return {
			"available": False,
			"reason": "cache_missing",
			"message": _("The cached fragment is missing. Convert the model again."),
		}

	return {
		"available": True,
		"reason": None,
		"message": None,
		"frag_url": frag_url,
		"bytes": cint(model_doc.file_size_bytes),
	}


@frappe.whitelist()
def clear_frag(model):
	"""Drop the cache so the next open re-converts from the source IFC."""
	model_doc = _require_model_access(model)
	# Look the File up and delete it by its DOCNAME. `frappe.delete_doc` takes the
	# document name, not `file_name`, and these differ — a File's name is a hash
	# while its file_name is what we chose. Passing the file_name deleted nothing,
	# silently, which is why `test_a_stale_url_is_reported_as_a_missing_cache`
	# failed: the "deleted" cache was still there and the endpoint correctly
	# reported it available.
	existing = frappe.db.get_value(
		FRAG_DOCTYPE, {"file_name": cache_path(model_doc.name)}, "name"
	)
	if existing:
		frappe.delete_doc(FRAG_DOCTYPE, existing, force=True, ignore_permissions=True)
	model_doc.frag_url = None
	model_doc.conversion_status = "Pending"
	model_doc.file_size_bytes = 0
	model_doc.converted_on = None
	model_doc.save(ignore_permissions=True)
	return {"cleared": True}
