# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BOQ matrix: paste a Bill of Quantities out of Excel and land it as BOQ rows.

The worksheet desk page (construction_ph/page/boq_worksheet) copies a range out
of Excel as tab-separated text and posts it here. Estimators' plans use whatever
unit the plan says ("m2", "m3", "pc", "ls"), so an unknown unit is created rather
than rejected -- the alternative is a paste that silently drops every row whose
unit the site has never seen. Every created UOM is reported back to the caller.
"""

import os

import frappe
from frappe import _

from erpnext_construction_ph.api import boq_item_rules
from frappe.utils import cint, cstr, flt

# Column order of the pasted range. A header row is optional and is detected by
# its first cell, because Excel copies the header along with the data.
COLUMNS = (
	"pay_item_number",
	"item_code",
	"description",
	"unit",
	"quantity",
	"direct_cost",
	"ocm",
	"profit",
)

HEADER_TOKENS = {
	"pay item number": "pay_item_number",
	"pay item no": "pay_item_number",
	"pay item": "pay_item_number",
	"item code": "item_code",
	"item no": "item_code",
	"description": "description",
	"unit": "unit",
	"quantity": "quantity",
	"qty": "quantity",
	"direct cost": "direct_cost",
	"unit cost": "direct_cost",
	"cost": "direct_cost",
	"ocm": "ocm",
	"profit": "profit",
}


@frappe.whitelist()
def list_boqs(project=None):
	"""Bill of Quantities the caller may paste rows into.

	**Added because the page had no way to choose one.** The BOQ Worksheet Desk
	Page rendered a `#boq_link` div and then tried to fill it with
	`new frappe.ui.LinkField(...)`, a constructor this Frappe does not have --
	so the call threw, the Desk swallowed it, and the one control the page exists
	to provide was silently absent. A page that cannot choose its target is a
	rendering error wearing a UI's clothes.

	Scoped, not a bare `get_all`: `limit_page_length` is explicit because
	`frappe.get_all` with no limit is how `bim_viewer.list_topics` once returned
	every BCF Topic in the installation. Draft and Approved only, since those are
	the states a BOQ can still take rows in.

	**Was unscoped, which made `project` a filter rather than a permission.**
	Measured on the gate site: an unentitled portal client called this and got
	BOTH BOQs on the site, including one on a project they cannot see. The
	implicit layer never covered it -- `frappe.get_all` applies User Permission
	only through a field pointing at the native `Project`, and
	`Construction BOQ.project` points at `Construction Project`, so the query
	had no filter of its own to fall back on. Every other read in this app
	scopes; this one did not.

	Scoped here for the same reason, and with `ignore_permissions=True` below so
	the control is OURS and does not vary with the framework's implicit layer
	being on or off. That is `bim_viewer.list_models`' arrangement, copied.

	`permitted_projects`, NOT `entitled_projects`: this is a Desk surface, and
	`entitled_projects` requires a client-facing role, so it would hand an
	estimator -- a Desk role in `READ_ROLES` -- an empty dropdown. Both close
	the hole identically for the caller that matters, because an unentitled
	client holds no User Permission and gets nothing either way.
	"""
	from erpnext_construction_ph.api.portal_data import permitted_projects

	# ONE mechanism for both paths, as in `bim_viewer.list_models`: the
	# caller-named `project` is an INTERSECTION with the entitled set, not a
	# filter that replaces it. Naming a project must never be a way past
	# entitlement, and two paths using two different predicates is how they
	# drift apart and one of them quietly stops checking.
	entitled = permitted_projects(frappe.session.user)
	if not entitled:
		return []
	if project:
		if project not in entitled:
			return []
		filters = {"status": ["in", ["Draft", "Approved"]], "project": project}
	else:
		filters = {"status": ["in", ["Draft", "Approved"]], "project": ["in", entitled]}

	return frappe.get_all(
		"Construction BOQ",
		filters=filters,
		fields=["name", "project", "status"],
		order_by="modified desc",
		limit=100,
		limit_page_length=100,
		ignore_permissions=True,
	)


@frappe.whitelist()
def bulk_insert_boq_items(boq, rows, replace=False):
	"""Append pasted TSV rows to a Bill of Quantities.

	:param boq: Construction BOQ name.
	:param rows: tab-separated text, one BOQ row per line. An optional header
		line is detected and skipped.
	:param replace: clear the existing rows first (a re-paste of the same sheet).
	:returns: dict with the BOQ name, how many rows landed, the parsed rows and
		any UOM created on the fly.
	"""
	if not frappe.db.exists("Construction BOQ", boq):
		frappe.throw(_("Bill of Quantities {0} not found.").format(boq), title=_("BOQ Not Found"))

	parsed, skipped_header = parse_tsv_rows(rows)
	if not parsed:
		frappe.throw(
			_("No rows to import. Paste a tab-separated range with at least one line of data."),
			title=_("Nothing To Import"),
		)

	doc = frappe.get_doc("Construction BOQ", boq)
	if cint(replace):
		doc.set("boq_items", [])

	created_uoms = []
	appended = []
	for line_no, values in enumerate(parsed, start=2 if skipped_header else 1):
		row = _build_row(values, line_no, boq, created_uoms)
		doc.append("boq_items", row)
		appended.append(row)

	doc.save(ignore_permissions=True)

	return {
		"boq": boq,
		"inserted": len(appended),
		"header_skipped": skipped_header,
		"created_uoms": created_uoms,
		"rows": appended,
	}


def parse_tsv_rows(rows):
	"""Split pasted TSV into dicts keyed by COLUMNS.

	Returns (rows, header_skipped). Blank lines are dropped. Raises with the
	offending line number when a line has the wrong number of columns, because a
	silently truncated BOQ is worse than a refused paste.
	"""
	if isinstance(rows, list):
		rows = "\n".join(str(r) for r in rows)

	text = str(rows or "").replace("\r\n", "\n").replace("\r", "\n")
	lines = [line for line in text.split("\n") if line.strip()]

	if not lines:
		return [], False

	first = [c.strip().lower() for c in lines[0].split("\t")]
	header_skipped = any(cell in HEADER_TOKENS for cell in first)
	if header_skipped:
		lines = lines[1:]

	out = []
	for offset, line in enumerate(lines):
		line_no = offset + (2 if header_skipped else 1)
		cells = [c.strip() for c in line.split("\t")]
		if len(cells) != len(COLUMNS):
			frappe.throw(
				_("Line {0} has {1} columns; expected {2}.").format(line_no, len(cells), len(COLUMNS)),
				title=_("Malformed BOQ Paste"),
			)
		values = dict(zip(COLUMNS, cells))
		if not values["item_code"]:
			frappe.throw(
				_("Line {0} has no Item Code.").format(line_no), title=_("Malformed BOQ Paste")
			)
		out.append(values)

	return out, header_skipped


def _build_row(values, line_no, boq, created_uoms):
	row = {
		# `boq` is set explicitly as well as via the parent, so rows can be
		# counted and filtered by BOQ the way the worksheet and the CORE-04 gate do.
		"boq": boq,
		"pay_item_number": values["pay_item_number"],
		"item_code": values["item_code"],
		"description": values["description"],
		"quantity": _number(values, "quantity", line_no),
		"direct_cost": _number(values, "direct_cost", line_no),
		"ocm": _number(values, "ocm", line_no),
		"profit": _number(values, "profit", line_no),
	}
	unit = values["unit"]
	if unit:
		row["unit"] = _ensure_uom(unit, created_uoms)
	return row


def _number(values, column, line_no):
	# cstr first: the clipboard path always yields strings, but a workbook cell
	# arrives as a native int/float, and calling .strip() on that is an
	# AttributeError rather than a number.
	raw = cstr(values.get(column) or "").strip()
	if not raw:
		return 0.0
	try:
		return flt(raw.replace(",", ""))
	except (ValueError, TypeError):
		frappe.throw(
			_("Line {0}: {1} is not a number ({2}).").format(
				line_no, column.replace("_", " ").title(), raw
			),
			title=_("Malformed BOQ Paste"),
		)


def _ensure_uom(unit, created_uoms):
	"""Return the UOM name, creating it when the site has never seen it."""
	existing = frappe.db.get_value("UOM", {"uom_name": unit}, "name")
	if existing:
		return existing
	frappe.get_doc({"doctype": "UOM", "uom_name": unit, "uom_category": "Nested UOM"}).insert(
		ignore_permissions=True
	)
	created_uoms.append(unit)
	return unit


# ---------------------------------------------------------------- whole-file import

# The same column contract the clipboard paste uses, plus the description-first
# layout legacy sheets actually arrive in.
WORKBOOK_COLUMNS = COLUMNS


@frappe.whitelist()
def import_boq_file(boq, file_url, sheet_name=None, allow_unmapped=False):
	"""Import a whole `.xlsx` BOQ onto a Bill of Quantities.

	Legacy DPWH and private BOQ sheets are messy: the header may be several rows
	down, column order varies, and descriptions carry the pay item implicitly.
	Every data row lands as a Construction BOQ Item with its pay item number and a
	computed unit price. Descriptions that map to no pay item are **reported**, not
	dropped, and by default the whole import is refused so a short BOQ cannot pass
	as a clean one.

	:param allow_unmapped: import the unmappable rows anyway, with no pay item.
	:returns: counts plus the unmapped descriptions.
	"""
	if not frappe.db.exists("Construction BOQ", boq):
		frappe.throw(_("Bill of Quantities {0} not found.").format(boq), title=_("BOQ Not Found"))

	rows = read_boq_workbook(file_url, sheet_name)
	if not rows:
		frappe.throw(
			_("No BOQ rows found in {0}.").format(file_url), title=_("Nothing To Import")
		)

	classified = boq_item_rules.classify_descriptions(rows)["rows"]
	unmapped = [r for r in classified if not r.get("pay_item")]

	if unmapped and not allow_unmapped:
		listing = ", ".join(cstr(r.get("description")) for r in unmapped[:10])
		frappe.throw(
			_("{0} row(s) map to no DPWH pay item and were not imported: {1}. "
			  "Re-run with allow_unmapped to import them anyway.").format(len(unmapped), listing),
			title=_("Unmapped Pay Items"),
		)

	doc = frappe.get_doc("Construction BOQ", boq)
	created_uoms = []
	appended = []
	for entry in classified:
		row = _build_row(entry, len(appended) + 1, boq, created_uoms)
		row["pay_item_number"] = entry.get("pay_item")
		doc.append("boq_items", row)
		appended.append(row)

	doc.save(ignore_permissions=True)
	return {
		"boq": boq,
		"imported": len(appended),
		"unmapped": unmapped,
		"created_uoms": created_uoms,
	}


def read_boq_workbook(file_url, sheet_name=None):
	"""Read the first data row of a BOQ sheet out of a workbook.

	Finds the header row rather than assuming row 1 -- exporters routinely put a
	title block and a date range above the table.
	"""
	import openpyxl

	path = _local_path(file_url)
	if not os.path.exists(path):
		frappe.throw(_("Workbook {0} not found.").format(file_url), title=_("File Not Found"))

	try:
		book = openpyxl.load_workbook(path, data_only=True, read_only=True)
	except Exception as exc:
		frappe.throw(
			_("Could not read {0} as a workbook: {1}").format(os.path.basename(path), exc),
			title=_("Unreadable Workbook"),
		)

	sheet = book[sheet_name] if sheet_name else book[book.sheetnames[0]]

	header_index = None
	header_map = {}
	for index, row in enumerate(sheet.iter_rows(min_row=1, max_row=40, values_only=True), start=1):
		normalised = [cstr(c).strip().lower() for c in row]
		if "item code" in normalised or "item no" in normalised:
			header_index = index
			header_map = {name: pos for pos, name in enumerate(normalised) if name}
			break

	if header_index is None:
		frappe.throw(
			_("No BOQ header row found in {0}.").format(os.path.basename(path)),
			title=_("Unrecognised BOQ Sheet"),
		)

	# Map each sheet header to its canonical column *here*, so the rest of this
	# function works in canonical names. Doing it at read time avoids filtering on
	# the raw header text ("item code") while looking up "item_code" -- which
	# matches nothing and silently drops every row.
	positions = {}
	for column, position in header_map.items():
		key = _WORKBOOK_ALIASES.get(column)
		if key and key not in positions:
			positions[key] = position

	rows = []
	for raw in sheet.iter_rows(min_row=header_index + 1, values_only=True):
		entry = {
			key: raw[position] if position < len(raw) else None
			for key, position in positions.items()
		}
		if not cstr(entry.get("item_code") or "").strip():
			continue
		rows.append(_workbook_entry(entry))

	book.close()
	return rows


# Legacy sheets name columns as the clipboard contract does but carry the
# description first, so map the workbook's own names onto the canonical columns.
_WORKBOOK_ALIASES = {
	"item code": "item_code",
	"item no": "item_code",
	"description": "description",
	"unit": "unit",
	"qty": "quantity",
	"quantity": "quantity",
	"direct cost": "direct_cost",
	"unit cost": "direct_cost",
	"cost": "direct_cost",
	"ocm": "ocm",
	"profit": "profit",
	"pay item no": "pay_item_number",
}


def _workbook_entry(entry):
	"""Normalise one workbook row, whose keys are already canonical.

	The sheet-header -> column mapping already happened in read_boq_workbook, so
	this must NOT re-apply _WORKBOOK_ALIASES: the alias dict is keyed on header
	text ("item code"), and applying it to canonical keys silently drops every
	column whose name is not itself an alias key.
	"""
	out = {c: "" for c in COLUMNS}
	for key, value in entry.items():
		if key in out:
			out[key] = value
	out["description"] = cstr(out.get("description") or "")
	# A pay item already typed into the sheet is kept; otherwise the catalog reads
	# the description.
	if not cstr(out.get("pay_item_number") or "").strip():
		out["pay_item_number"] = out.get("item_code") or ""
	return out


def _local_path(file_url):
	if file_url and str(file_url).startswith("/"):
		return str(file_url)
	name = os.path.basename(str(file_url))
	candidate = frappe.get_site_path("private", "files", name)
	return candidate if os.path.exists(candidate) else str(file_url)
