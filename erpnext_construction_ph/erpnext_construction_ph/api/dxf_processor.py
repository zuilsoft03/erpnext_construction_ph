# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""CAD DXF layer extraction for takeoff.

A QS drops a structural or MEP drawing and wants lengths per CAD layer, so the
BOQ can be built from the drawing instead of re-typed. Layers are the natural unit
here: DPWH and consultants organise drawings that way, and the estimator already
thinks in "Part A / Item 103".

The parse itself lives in `parse_dxf_takeoff` and is deliberately free of any
Frappe request context, so it can be exercised directly. The whitelisted entry
point `extract_dxf_takeoff` only enqueues — a 40 MB drawing must not block the
request thread (see the GATES.md note for this row).

Only geometry that carries a length is measured. Anything else (dimensions, text,
blocks, hatches) is ignored rather than guessed at.
"""

import math
import os

import frappe
from frappe import _


class DxfParseError(frappe.ValidationError):
	"""The file could not be read as a DXF drawing."""


def _load(file_path):
	"""Read a DXF, turning every failure mode into one named error."""
	if not file_path or not os.path.exists(file_path):
		raise DxfParseError(_("Drawing {0} does not exist.").format(file_path))

	try:
		import ezdxf
	except ImportError:
		raise DxfParseError(
			_("ezdxf is not installed; the DXF extractor cannot run on this site.")
		)

	# A DXF is a text (or binary) file with a recognizable header. Check it before
	# handing the path to ezdxf so a stray PDF produces a message naming the file
	# rather than a parser stack trace from deep inside the library.
	with open(file_path, "rb") as handle:
		head = handle.read(64)
	try:
		text = head.decode("utf-8", errors="ignore")
	except Exception:
		text = ""
	if "SECTION" not in text.upper() and not head.startswith(b"AutoCAD Binary DXF"):
		raise DxfParseError(
			_("{0} is not a DXF drawing (no SECTION header found).").format(
				os.path.basename(file_path)
			)
		)

	try:
		return ezdxf.readfile(file_path)
	except DxfParseError:
		raise
	except Exception as exc:
		raise DxfParseError(
			_("Could not read {0} as DXF: {1}").format(os.path.basename(file_path), exc)
		)


def _length_of(entity):
	"""Length in drawing units, or None for entities that carry no length."""
	dxftype = entity.dxftype()

	if dxftype == "LINE":
		start, end = entity.dxf.start, entity.dxf.end
		return math.hypot(end.x - start.x, end.y - start.y)

	if dxftype == "LWPOLYLINE":
		points = list(entity.get_points("xy"))
		return _closed_path_length(points, getattr(entity, "closed", False))

	if dxftype == "POLYLINE":
		points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
		return _closed_path_length(points, bool(entity.is_closed))

	return None


def _closed_path_length(points, closed):
	if len(points) < 2:
		return 0.0
	total = 0.0
	for (x1, y1), (x2, y2) in zip(points, points[1:]):
		total += math.hypot(x2 - x1, y2 - y1)
	if closed and points[0] != points[-1]:
		(x1, y1), (x2, y2) = points[-1], points[0]
		total += math.hypot(x2 - x1, y2 - y1)
	return total


def parse_dxf_takeoff(file_path, target_layer=None, precision=3):
	"""Total length per CAD layer, in drawing units.

	:param target_layer: restrict the result to one layer.
	:returns: {layer_name: total_length}, sorted by layer name.
	"""
	doc = _load(file_path)

	totals = {}
	for entity in doc.modelspace():
		length = _length_of(entity)
		if not length:
			continue
		layer = entity.dxf.layer
		totals[layer] = round(totals.get(layer, 0.0) + length, precision)

	if target_layer:
		return {target_layer: totals[target_layer]} if target_layer in totals else {}

	return dict(sorted(totals.items()))


def available_layers(file_path):
	"""Every layer present in the drawing, whether or not it carries geometry."""
	doc = _load(file_path)
	return sorted({e.dxf.layer for e in doc.modelspace()})


@frappe.whitelist()
def extract_dxf_takeoff(file_url, take_off_sheet=None, target_layer=None):
	"""Enqueue a DXF parse. Returns a job reference, not the result.

	The caller polls `frappe.get_doc("Job", job_id)` or, when a Takeoff Sheet is
	given, waits for `write_takeoff` to append the captured lengths. Running the
	parse inline would put a 40 MB read on the request thread.
	"""
	return frappe.enqueue(
		"erpnext_construction_ph.api.dxf_processor._run_extraction",
		queue="long",
		timeout=1800,
		file_url=file_url,
		take_off_sheet=take_off_sheet,
		target_layer=target_layer,
	)


def _run_extraction(file_url, take_off_sheet=None, target_layer=None):
	"""Worker body: download, parse, and optionally write onto a Takeoff Sheet."""
	path = _materialise(file_url)
	try:
		lengths = parse_dxf_takeoff(path, target_layer)
		if take_off_sheet and lengths:
			write_takeoff(take_off_sheet, lengths)
		return {"layers": lengths, "take_off_sheet": take_off_sheet}
	finally:
		if path and file_url and not str(file_url).startswith("/"):
			try:
				os.remove(path)
			except OSError:
				pass


def _materialise(file_url):
	"""Local filesystem path for a drawing.

	Accepts either an absolute site path or a private File name/url; the worker
	needs a real path because ezdxf reads from disk.
	"""
	if file_url and str(file_url).startswith("/"):
		return str(file_url)
	name = os.path.basename(str(file_url))
	candidate = frappe.get_site_path("private", "files", name)
	return candidate if os.path.exists(candidate) else str(file_url)


def write_takeoff(take_off_sheet, lengths):
	"""Append one BOQ Item row per CAD layer to a Takeoff Sheet.

	Units are metres: a DXF is normally drawn 1:1, so a drawing unit is a metre.
	"""
	doc = frappe.get_doc("Takeoff Sheet", take_off_sheet)
	for layer, length in lengths.items():
		doc.append(
			"takeoff_items",
			{
				"item_code": f"DXF-{layer}"[:140],
				"description": _("CAD layer {0}").format(layer),
				"unit": "m",
				"quantity": round(float(length), 3),
				"rate": 0,
			},
		)
	doc.save(ignore_permissions=True)
	return take_off_sheet
