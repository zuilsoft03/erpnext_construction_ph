# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""IFC ingest for the BIM model: validate, index, and drive the conversion.

**What this module owns, and what it deliberately does not.** It owns
everything around the conversion -- recognising that a file really is an IFC,
extracting the element GlobalIds into the child table, driving the status
machine, refusing to call a model `Ready` without a `.frag` URL, and keeping
geometry bytes out of MariaDB. It does **not** produce a `.frag`: that is
`@thatopen`'s fragments processor, a JavaScript library, and this environment has
no `node` and no npm (GATES.md PH3-01). The converter is therefore a **seam** --
a callable passed in or resolved by name -- so the contract this app is
responsible for is testable without pretending a `.frag` was encoded.

**Why a `.frag` and not the IFC served raw.** A `.frag` is pre-tessellated
geometry: the browser downloads triangles, not a 100MB parse-and-build. That
moves the cost from the client's GPU to a one-off server job. It is also why the
storage invariant below matters -- the point of the whole row is that the heavy
bytes live in object storage and never in the database.

**IFC parsing here is deliberately minimal.** An IFC is ISO-10303-21 (STEP)
text, so GlobalIds are readable without a toolkit. This reads the DATA section
and pulls the GlobalId out of rooted entities. It does not build geometry and
does not attempt to be a general IFC implementation.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint

# An IFC GlobalId is 22 characters over a 64-symbol alphabet. This is a shape
# check, not a decode: the value is stored exactly as the file wrote it.
GUID_RE = re.compile(r"^[0-9A-Za-z_$]{22}$")

# Entity families whose first attribute after the type name is a GlobalId,
# because they derive from IfcRoot. IFC4 roots everything through IfcRooted, and
# these are the families a construction model actually contains. Entities outside
# this set are not indexed -- stated plainly rather than papered over, because a
# silently incomplete index looks identical to a model with no elements.
ROOTED_PREFIXES = (
	"IFCPROJECT",
	"IFCWALL", "IFCWALLSTANDARDCASE", "IFCWALLELEMENTEDCASE",
	"IFCSLAB", "IFCSLABSTANDARDCASE", "IFCSLABELEMENTEDCASE",
	"IFCBEAM", "IFCBEAMSTANDARDCASE", "IFCBEAMELEMENTEDCASE",
	"IFCCOLUMN", "IFCCOLUMNSTANDARDCASE", "IFCCOLUMNELEMENTEDCASE",
	"IFCSLAB", "IFCDOOR", "IFCDOORSTANDARDCASE", "IFCWINDOW", "IFCWINDOWSTANDARDCASE",
	"IFCSTAIR", "IFCSTAIRFLIGHT", "IFCRAMP", "IFCRAMPFLIGHT", "IFCRAILING",
	"IFCROOF", "IFCSIAB", "IFCCOVERING", "IFCFOOTING", "IFCPILE", "IFCCOLUMNSTANDARDCASE",
	"IFCBUILDINGELEMENTPROXY", "IFCBUILDINGELEMENTPARTCONSTANT", "IFCDISCRETEACCESSORY",
	"IFCMECHANICALFASTENER", "IFCREINFORCINGBAR", "IFCREINFORCINGMESH",
	"IFCELEMENTASSEMBLY", "IFCBUILTELEMENT", "IFCGEOGRAPHICELEMENT",
	"IFCANNOTATION", "IFCGRID", "IFCVIRTUALELEMENT",
	"IFCSPATIALSTRUCTUREELEMENT", "IFCSPATIALZONE", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY",
	"IFCPROCESS", "IFCPRODUCT", "IFCRESOURCE", "IFCGROUP",
)

# `#123 = IFCWALL('1abc...', #5, 'Wall A', ...);` -- entity instances only.
INSTANCE_RE = re.compile(r"^#(\d+)\s*=\s*([A-Za-z0-9_]+)\s*\((.*)\)\s*;\s*$")

# The header an IFC must carry, regardless of what the file is called.
IFC_HEADER = "ISO-10303-21"
IFC_SCHEMA_MARKER = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*['\"]?IFC", re.IGNORECASE)


class ConversionError(Exception):
	"""Raised when a file cannot be ingested. Carries a message for the user."""


def is_ifc(head_bytes):
	"""True when `head_bytes` looks like an IFC **by content**, not by extension.

	The name is ignored on purpose. Renaming a JPEG to `.ifc` is the most common
	way this is wrong in the field, and a pipeline that trusts the extension will
	fail deep inside a parser with an unrelated message.
	"""
	if not head_bytes:
		return False
	head = head_bytes[:4096]
	if isinstance(head, bytes):
		head = head.decode("utf-8", errors="replace")
	if IFC_HEADER not in head:
		return False
	return bool(IFC_SCHEMA_MARKER.search(head))


def _split_args(argstring):
	"""Split a STEP argument list on top-level commas.

	Naive `split(",")` breaks on `IFCLABEL('a, b')` and on nested entities, both
	of which are common in real IFCs.
	"""
	args, depth, quote, buf = [], 0, None, []
	for ch in argstring:
		if quote:
			buf.append(ch)
			if ch == quote:
				quote = None
			continue
		if ch in "'\"":
			quote = ch
			buf.append(ch)
			continue
		if ch == "(":
			depth += 1
		elif ch == ")":
			depth -= 1
		if ch == "," and depth == 0:
			args.append("".join(buf).strip())
			buf = []
			continue
		buf.append(ch)
	if buf:
		args.append("".join(buf).strip())
	return args


def _unquote(token):
	token = token.strip()
	if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
		return token[1:-1].replace("''", "'")
	return None


def extract_guids(ifc_text):
	"""Return `[(ifc_id, guid, entity_type, name)]` for every rooted entity.

	Reads the DATA section only; HEADER and the schema preamble carry no
	GlobalIds. `name` is the third attribute (a plain Name for most products) and
	is best-effort -- it is a convenience for diagnostics, not a contract.
	"""
	guids = []
	in_data = False
	for raw in ifc_text.splitlines():
		line = raw.strip()
		if not line:
			continue
		if line.startswith("DATA"):
			in_data = True
			continue
		if line.startswith("ENDSEC"):
			in_data = False
			continue
		if not in_data:
			continue
		match = INSTANCE_RE.match(line)
		if not match:
			continue
		ifc_id, entity, argstring = match.group(1), match.group(2).upper(), match.group(3)
		if not entity.startswith(ROOTED_PREFIXES):
			continue
		args = _split_args(argstring)
		if not args:
			continue
		# IfcRooted declares GlobalId as its FIRST attribute, so it is argument
		# index 0. Verified against a real IFC4 export:
		#   #12160 = IFCPROJECT('2s9U9jFJb9q8EbDUmAG_ft', $, $, ...)
		# Index 1 is OwnerHistory. Reading index 1 yields a reference or a `$`,
		# never a GUID, which is why the first version of this function returned
		# an empty list rather than wrong GUIDs.
		guid = _unquote(args[0])
		if not guid or not GUID_RE.match(guid):
			continue
		# IfcRoot -> IfcObjectDefinition -> IfcObject puts Name third, after
		# OwnerHistory. Best-effort: some rooted types (IfcProject) differ.
		name = _unquote(args[2]) if len(args) > 2 else None
		guids.append((cint(ifc_id), guid, entity, name))
	return guids


def default_converter(model, ifc_path):
	"""The production converter hook.

	NOT implemented, and still deliberately so -- but the reason recorded here used
	to be WRONG and is now measured. It said "this bench has no node runtime"; that
	stopped being true once node v24.12.0 was found under `nvm` in the frontend
	container. What is actually true as of 2026-09-28:

	  - `@thatopen/components@3.4.8` and `@thatopen/fragments@3.4.7` install cleanly
	    (30 packages, ~5s) and are now pinned in package.json.
	  - A browser runtime has BOTH `Worker` and WebGL2 on a real GPU (ANGLE/NVIDIA
	    RTX 4070 Ti, maxTextureSize 16384) -- ops/tmp-ph3-01/webgl-probe.json.
	  - Node 24 has NO WebGL context, so the fragments processor cannot run in an
	    RQ worker, which is the architecture this row scopes. Driving a browser
	    for conversion would put a browser in the production worker path; that is a
	    decision to take on purpose, not a workaround to adopt.
	  - A real `.ifc` IS present in the repo (`school_str.ifc`, 8.6 MB / 93,501
	    lines, gitignored so it is not committed); it is Node's missing WebGL
	    context, not a missing fixture, that stops the conversion here.

	Raising a named error is still right: the caller sets `Failed` with a real
	reason, and nobody mistakes a missing converter for a converted model. But the
	message must name the REAL wall, or it sends the next person after a runtime
	that has been sitting there for hours.
	"""
	raise ConversionError(
		"No .frag converter is implemented. The blocker is NOT a missing node runtime "
		"and NOT a missing @thatopen package -- both are resolved and pinned. It is "
		"that @thatopen's fragments processor needs a WebGL context, which an RQ "
		"worker in Node 24 does not have. A real fixture IS present: school_str.ifc, "
		"8.6 MB / 93,501 lines. See GATES.md PH3-01."
	)


def index_guids(model, ifc_text, converter_guids=None):
	"""Replace the model's GUID rows.

	`converter_guids` wins when supplied. The converter is the authority on what
	ended up in the `.frag`, and PH3-02 picks an element *in the frag* and looks
	its GUID up here -- so when the converter can tell us its identifiers, that
	list is the one that must be recorded. The local STEP parse is the fallback
	for when the converter is unavailable, and the two agree in practice only if
	`@thatopen` preserves IFC GlobalIds verbatim. **That assumption is NOT PROVEN
	in this environment**, because the converter cannot run here (no node); it is
	recorded in GATES.md PH3-01 rather than asserted.

	Replaces rather than appends: re-running a conversion must not double the
	child table, which would make a later element count quietly wrong.
	"""
	found = converter_guids if converter_guids is not None else extract_guids(ifc_text)
	model.set("model_guids", [])
	rows = []
	for entry in found:
		if isinstance(entry, dict):
			ifc_id, guid, entity = entry.get("ifc_id"), entry.get("guid"), entry.get("type") or ""
			name = entry.get("name")
		else:
			ifc_id, guid, entity, name = entry
		rows.append(
			{
				"element_guid": guid,
				"element_type": entity,
				"element_name": name or "",
				"ifc_id": ifc_id,
			}
		)
	# `Document.append` takes ONE row, not a list. Passing the whole list is a
	# common and confusing failure: it dies deep inside the ORM with a message
	# about the child table rather than about the argument shape.
	for row in rows:
		model.append("model_guids", row)
	return found


def ingest(model_name, converter=None, ifc_path=None, enqueue=True):
	"""Validate, index, and QUEUE one BIM Model for conversion. Returns the document.

	**Split from the encoding so the pipeline is asynchronous**, which is what
	`docs/ROADMAP.md` Phase 3 scopes (browser-side conversion per D-043, the
	wording corrected from the superseded queue pipeline). The
	previous shape did all of it inline, so a 100MB model held a web worker for
	the length of the conversion -- and the phase gate's own risk is a 100MB
	model. The pattern already exists in this app: `api/dxf_processor.py` uses
	`frappe.enqueue`.

	The split is drawn at the only sensible line. Validating the upload and
	indexing the GUIDs are **cheap and must be synchronous**, so a bad file is
	rejected at upload with the filename in the message rather than surfacing
	minutes later as a worker failure nobody is watching. Encoding the `.frag` is
	**expensive and belongs on a worker**.

	`enqueue=False` runs the worker inline, which is what the tests use and what
	a caller wants when it already holds a worker.
	"""
	model = frappe.get_doc("BIM Model", model_name)
	converter = converter or default_converter

	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "BIM Model", "attached_to_name": model_name},
		pluck="name",
	)
	if not files:
		frappe.throw(_("No IFC file is attached to this model."))

	# `file_url` is site-relative; `get_full_path` is Frappe's own resolution and
	# gets the public/private split right, which a hand-rolled join does not.
	source = frappe.get_doc("File", files[0])
	source_path = source.get_full_path()

	model.conversion_status = "Queued"
	model.save()

	try:
		# 1. Refuse a non-IFC by content, naming the file, before any parsing.
		with open(source_path, "rb") as handle:
			head = handle.read(4096)
		if not is_ifc(head):
			raise ConversionError(
				_("{0} is not an IFC file: it has no ISO-10303-21 header or carries a "
				  "non-IFC FILE_SCHEMA.").format(source.file_name)
			)

		# 2. Read the text and index the GUIDs. Geometry is never written to the
		#    database; only the identifiers are.
		with open(source_path, "r", encoding="utf-8", errors="replace") as handle:
			ifc_text = handle.read()
		index_guids(model, ifc_text)

		model.file_size_bytes = cint(source.file_size)
		model.save()
	except ConversionError as exc:
		# A validation or indexing failure lands on `Failed` here, in the request,
		# with the filename in the message. No rollback: nothing has been written
		# that needs undoing -- `frag_url` is only set on the success path -- and
		# rolling back would discard the caller's surrounding transaction, which
		# in a test means discarding the fixture with it. The status machine is
		# recorded by writing Failed, not by unwinding.
		model.conversion_status = "Failed"
		model.frag_url = ""
		model.remarks = str(exc)[:500]
		model.save()
		return model

	# 3. Hand the .frag encoding to a worker. `Queued` is the status a reader
	#    sees while it runs, and it is deliberately NOT `Converting`: nothing is
	#    converting until a worker picks it up, so a model stuck at `Converting`
	#    would be indistinguishable from one being worked on.
	if enqueue:
		try:
			frappe.enqueue(convert_queued, queue="short", timeout=1800, model_name=model_name)
		except Exception as exc:
			# **The enqueue itself can fail**, and this is the one failure the
			# async split introduces: the queue is down, or the broker refuses the
			# job. Without this the model sits at `Queued` forever -- which is
			# precisely what this module's contract forbids, "never leaves a model
			# looking converted when it is not", in its worst form: a model that
			# claims to be waiting for work that will never arrive, with nobody
			# alerted. Land it on `Failed` with the reason, so it is visible and
			# retryable rather than silently stuck.
			model.conversion_status = "Failed"
			model.frag_url = ""
			model.remarks = "could not be queued for conversion: {0}".format(exc)[:500]
			model.save()
			return model
	else:
		return convert_queued(model_name, converter=converter)
	return model


def convert_queued(model_name, converter=None):
	"""Worker entry point: encode the `.frag` and land the model on its final status.

	`Queued` -> `Ready` with a `.frag` URL, or `Failed` with the reason. A model
	never sits at `Queued` pretending to be converted: that is the same class of
	bug as an empty ledger reading as "no billings yet".

	**A model can be stranded at `Queued`** if the worker never runs -- the queue
	is down, the worker is killed mid-job, the enqueue itself is lost. That is
	inherent to an asynchronous pipeline, and it is why the status is a real
	readable value rather than an assumption: a stale `modified` on a `Queued`
	model means stalled, and that is distinguishable from a model being worked on.
	The alternative, a synchronous pipeline, has no such state and also cannot
	convert a 100MB model without holding a web worker for the duration.
	"""
	frappe.set_user("Administrator")
	model = frappe.get_doc("BIM Model", model_name)
	converter = converter or default_converter
	model.conversion_status = "Converting"
	model.save()

	try:
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "BIM Model", "attached_to_name": model_name},
			pluck="name",
		)
		if not files:
			raise ConversionError(_("No IFC file is attached to this model."))
		source = frappe.get_doc("File", files[0])
		source_path = source.get_full_path()
		with open(source_path, "r", encoding="utf-8", errors="replace") as handle:
			ifc_text = handle.read()

		produced = converter(model, source_path)
		# A converter may return a bare URL string, or a dict carrying the URL
		# plus the identifiers it actually wrote into the `.frag`.
		if isinstance(produced, dict):
			frag_url = produced.get("url")
			index_guids(model, ifc_text, converter_guids=produced.get("guids"))
		else:
			frag_url = produced
		if not (frag_url or "").strip():
			raise ConversionError(_("The converter returned no .frag URL."))

		model.frag_url = frag_url
		model.conversion_status = "Ready"
		model.converted_on = frappe.utils.now()
		# Counted from the child's own rows rather than a local: indexing already
		# happened in the request, and a worker reading a local it no longer has
		# is how a count silently becomes zero on the async path only.
		indexed = len(model.get("model_guids") or [])
		model.remarks = "{0} elements indexed from {1}".format(indexed, source.file_name)
		model.save()
	except ConversionError as exc:
		model.conversion_status = "Failed"
		model.frag_url = ""
		model.remarks = str(exc)[:500]
		model.save()
		return model

	return model


@frappe.whitelist()
def resolve_element(model, element_guid):
	"""Resolve an element GUID to the indexed row that holds it.

	This is the server-side half of what the BIM workspace viewer does when a user
	clicks an element: the browser holds an IFC GUID from the geometry, and the
	answer -- which model, which element type, which name -- has to come from the
	index rather than from the render, because the render only knows triangles.

	**A GUID that is not in the index does not resolve.** It is not a soft
	failure and it is not an empty row: a viewer that cannot distinguish "this
	element is not in the index" from "this model has no elements indexed yet"
	will happily display an element the model has never heard of, and that reads
	as a real pick.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	frappe.get_doc("BIM Model", model)  # raises if the model does not exist
	project = frappe.db.get_value("BIM Model", model, "project")
	if project and not may_view(frappe.session.user, project):
		frappe.throw(_("You do not have access to this model."), frappe.PermissionError)

	rows = frappe.get_all(
		"BIM Model GUID",
		filters={"parent": model, "element_guid": element_guid},
		fields=[
			"name",
			"element_guid",
			"element_type",
			"element_name",
			"ifc_id",
			# The MEASURED geometry, not the drawing's. These were missing, and the
			# consequence was that the Properties card reported "Not measured" for
			# every element in a model whose areas are all measured.
			"geom_surface_area",
			"geom_footprint_area",
		],
		limit=1,
	)
	if not rows:
		# RETURNED, NOT THROWN, and this is the fix. An element the index does not hold
		# is a NORMAL, REPORTABLE state -- the docstring below says so explicitly -- and
		# throwing turned it into an HTTP 500. That made the workspace's not-in-index
		# branch UNREACHABLE: `frappe.call`'s success callback never fired, so the page
		# rendered nothing at all and the user saw a console traceback instead of a
		# sentence explaining that the IFC and the index disagree.
		#
		# The branch was not dead by accident, it was dead by construction: correct code
		# in the page, an exception thrown before it could run. Nothing tested it, because
		# the test would have had to call the endpoint with a guid the index does not
		# contain -- which is exactly the input nobody tried.
		return {
			"model": model,
			"element_guid": element_guid,
			"not_indexed": True,
			"reason": _("This element is not in this model's index. The IFC and the index disagree."),
		}
	row = rows[0]
	from erpnext_construction_ph.api.bim_frag import area_class

	return _with_boq_bridge(model, {
		"model": model,
		"element_guid": row.element_guid,
		"element_type": row.element_type,
		"element_name": row.element_name,
		"ifc_id": row.ifc_id,
		# MEASURED, and measured in METRES -- the vertices are, and the model spans
		# 134.85 units for a school. No conversion is applied and none should be.
		"area": None if row.geom_surface_area is None else float(row.geom_surface_area),
		"footprint": None if row.geom_footprint_area is None else float(row.geom_footprint_area),
		# The basis travels WITH the number. A bare area is ambiguous between the two
		# figures measured, and a reader who cannot tell them apart will treat them as
		# one and be wrong by a factor that varies per element.
		"area_basis": "sum of triangle areas over the element mesh, each triangle once",
		"footprint_basis": "XY bounding box of the element mesh",
		"area_class": area_class(row.element_type),
		"area_unit": "m2",
	})


def _region(value):
	"""Parse `x,y,w,h` into four numbers, or say why it cannot be.

	Parsed and NOT stored verbatim. A rectangle kept as the string the user drew is one
	the writer never learns is malformed and the reader can never compare -- and
	`defaultResolve` compares, so a bad region would make the element look unlinked
	forever with nothing to indicate why.
	"""
	if not value:
		return None
	parts = [p.strip() for p in str(value).split(",")]
	if len(parts) != 4:
		return None
	try:
		nums = [float(p) for p in parts]
	except (TypeError, ValueError):
		return None
	if not all(n == n and abs(n) != float("inf") for n in nums):  # NaN / inf
		return None
	if nums[2] <= 0 or nums[3] <= 0:
		return None  # a region with no width or height selects nothing
	return nums


@frappe.whitelist()
def link_element_to_region(model, element_guid, drawing, page=None, region=None, mapped_area=None):
	"""Point an element at a region of a sheet. The write half of the map.

	`mapped_area` is the SHEET's figure for that region and is stored only when the
	caller supplies it. It is never derived from the element's measured area: those are
	different numbers about different things, and putting one where the other belongs is
	a wrong figure that looks right.
	"""
	frappe.get_doc("BIM Model", model)  # raises if the model does not exist
	project = frappe.db.get_value("BIM Model", model, "project")
	if project and not _may_view(project):
		frappe.throw(_("You do not have access to this model."), frappe.PermissionError)

	if not (drawing or "").strip():
		frappe.throw(_("A sheet is required to map an element to."))

	box = _region(region)
	if region and not box:
		frappe.throw(
			_("The region must be four numbers: x,y,w,h."),
			title=_("Region Not Understood"),
		)

	names = frappe.get_all(
		"BIM Model GUID",
		filters={"parent": model, "element_guid": element_guid},
		pluck="name",
		limit=2,
	)
	if not names:
		frappe.throw(
			_("Element {0} is not in this model's index.").format(element_guid),
			title=_("Element Not Indexed"),
		)
	if len(names) > 1:
		frappe.throw(
			_("That element is indexed more than once; refusing to guess which."),
			title=_("Ambiguous Element"),
		)

	# MEASURED the real schema rather than the JSON: `drawing_page` is NOT NULL
	# default 0, and so is `mapped_area`. So an omitted page is 0, and an omitted area
	# is 0 -- which is why the READER has to treat a zero area as UNKNOWN. A region
	# with no area recorded is not a region of 0 m2; it is a region nobody has
	# measured, and storing 0 without saying so is the false measurement this whole
	# row exists to prevent. `mapped_area` is never computed from the element.
	values = {
		"drawing": (drawing or "").strip(),
		"drawing_page": int(page) if str(page or "").strip().isdigit() else 0,
		"drawing_region": ",".join(repr(n) for n in box) if box else None,
		"mapped_area": float(mapped_area) if mapped_area not in (None, "") else 0,
	}
	if box and not values["mapped_area"]:
		# A region of no area cannot be selected, so recording one is a mistake worth
		# naming rather than a quantity to store.
		frappe.throw(
			_("A region was given with no area. A rectangle of zero area cannot be mapped."),
			title=_("Region Has No Area"),
		)
	frappe.db.set_value("BIM Model GUID", names[0], values, update_modified=False)
	frappe.db.commit()
	return {"ok": True, "element_guid": element_guid, **values}


def _may_view(project):
	from erpnext_construction_ph.api.portal_data import may_view

	return may_view(frappe.session.user, project)


@frappe.whitelist()
def element_map(model, element_guid):
	"""The sheet mapping for ONE element, or nulls. The read half of the map.

	Nothing supplied `defaultResolve.getMap` before this, so the engine resolved null
	for every element in the model and the comparison never ran. `drawing` is the field
	the reader keys on -- without it an element is unlinked whatever else is set.

	A zero `mapped_area` is returned as NULL, not 0. The column is NOT NULL with a
	default of 0, so "nobody measured this region" and "this region is 0 m2" are one
	stored value, and only one of them is true. Reporting 0 would print a confident
	difference against the model's own area.
	"""
	frappe.get_doc("BIM Model", model)
	project = frappe.db.get_value("BIM Model", model, "project")
	if project and not _may_view(project):
		frappe.throw(_("You do not have access to this model."), frappe.PermissionError)

	row = frappe.db.get_value(
		"BIM Model GUID",
		{"parent": model, "element_guid": element_guid},
		# `element_type` is LOAD-BEARING and I left it out the first time. The engine
		# derives the element's class with `areaClassOf(row.element_type)`
		# (sync_engine.js:247) and refuses a comparison when the classes differ, so an
		# `element_map` that returned the region but not the type handed the engine an
		# undefined class and EVERY mapped element read UNCLASSIFIED. The map would
		# have been capturable, readable, and still refusing every comparison -- a
		# capability that looks finished and is inert.
		["drawing", "drawing_page", "drawing_region", "mapped_area", "element_type"],
		as_dict=True,
	)
	if not row or not row.drawing:
		return {
			"drawing": None,
			"drawing_page": None,
			"drawing_region": None,
			"mapped_area": None,
			"element_type": row.element_type if row else None,
			"linked": False,
		}
	box = _region(row.drawing_region)
	return {
		"drawing": row.drawing,
		"drawing_page": row.drawing_page or None,
		"drawing_region": box,          # parsed, so the reader compares numbers
		"mapped_area": (None if row.mapped_area in (None, 0, 0.0) else float(row.mapped_area)),
		"element_type": row.element_type,
		"linked": True,
	}


def _with_boq_bridge(model, payload):
	"""Add the BOQ bridge the workspace's Properties card reads.

	The spec's Card 1 shows Area, BOQ Item, Cost and Progress. `resolve_element`
	returned only the five index fields, so three of those four had NO data source
	-- the plan records this as a correction: the task is to EXTEND the resolver,
	not merely render what it already returns.

	THE IMPORTANT PART IS WHAT HAPPENS WHEN THERE IS NO ANSWER. Every missing
	value is `None` and its key is listed in `unknown`, and the panel renders
	"not measured" rather than a number. A rendered `0` here would be a false
	measurement in a quantity-surveying tool: a zero area reads as "this element
	has no area", which is a claim about the building, not about what the database
	happens to hold. `None` is the absence of a measurement; `0` is a measurement.
	"""
	payload.setdefault("area", None)
	payload.setdefault("boq_item", None)
	payload.setdefault("cost", None)
	payload.setdefault("progress_percent", None)

	# The link between an IFC element and a BOQ line is a field on
	# `BCF Element Reference` (added in UI-8), which is per-TOPIC, so the element's
	# BOQ line is whichever reference names it. Taking the first is deliberate: an
	# element appearing on two issues' references is one element, and the panel is
	# showing properties, not a join table.
	refs = frappe.get_all(
		"BCF Element Reference",
		filters={"global_id": payload["element_guid"]},
		fields=["boq_item", "parent"],
		limit=5,
	)
	boq = next((r.get("boq_item") for r in refs if r.get("boq_item")), None)
	source = "issue reference" if boq else None

	# THE TAKEOFF'S OWN MAPPING, and only when an issue has not already spoken. A
	# takeoff maps elements to lines -- that is the discipline -- and it records the
	# mapping on the line, where it belongs. Before this, the ONLY source was
	# `BCF Element Reference`, which is PER TOPIC, so an element nobody had raised an
	# issue about had no BOQ line at all and the card said "Not measured" three times
	# on a project whose takeoff plainly covered it. The NORMAL case, not an edge case.
	#
	# ORDER IS THE WHOLE POINT. An issue's element reference is a deliberate,
	# issue-specific statement about that element, so it is consulted FIRST and is not
	# weakened: where an issue names a different line from the takeoff's own, the
	# issue's line wins. A takeoff-wide default must never silently override it.
	if not boq:
		boq = frappe.db.get_value(
			"Construction BOQ Item", {"bim_element": payload["element_guid"]}, "name"
		)
		source = "takeoff mapping" if boq else None
	if not boq:
		payload["unknown"] = ["boq_item", "cost", "progress_percent"]
		return payload

	boq_row = frappe.db.get_value(
		"Construction BOQ Item",
		boq,
		["item_code", "description", "unit", "quantity", "total", "actual_percent_complete"],
		as_dict=True,
	)
	if not boq_row:
		payload["unknown"] = ["cost", "progress_percent"]
		return payload

	payload["boq_item"] = boq_row
	# Which source answered, so a reader can tell a takeoff's own mapping from an
	# issue's deliberate reference. A cost nobody can trace is a cost they must not act on.
	payload["boq_source"] = source
	# `total` is the extended amount. It is read, never computed from a unit rate
	# here, because the BOQ's own arithmetic is the billing system's business and a
	# second implementation of it in a viewer is how two numbers start disagreeing.
	payload["cost"] = boq_row.total
	payload["progress_percent"] = boq_row.actual_percent_complete
	# `area` is NOT listed as unknown when it was measured. It used to be, with
	# the comment "area is measured, not stored" -- true when written, false now:
	# `geom_surface_area` holds the measured value. Listing a present value as
	# unknown is what made the panel say "Not measured" for every element.
	payload["unknown"] = [] if payload.get("area") is not None else ["area"]
	return payload


@frappe.whitelist()
def list_elements(model, limit=200):
	"""Indexed elements for a model, for the workspace page's element list."""
	from erpnext_construction_ph.api.portal_data import may_view

	project = frappe.db.get_value("BIM Model", model, "project")
	if project and not may_view(frappe.session.user, project):
		frappe.throw(_("You do not have access to this model."), frappe.PermissionError)
	return frappe.get_all(
		"BIM Model GUID",
		filters={"parent": model},
		fields=["element_guid", "element_type", "element_name"],
		order_by="ifc_id asc",
		limit=limit,
	)
