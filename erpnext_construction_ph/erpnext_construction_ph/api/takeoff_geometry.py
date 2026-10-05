# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Takeoff geometry: two-point calibration and closed-polygon area capture.

This is the arithmetic behind the Takeoff Studio Desk page
(`construction_ph/page/takeoff_studio`). A QS calibrates a PDF or scanned plan by
clicking two points whose real-world separation is known -- typically a dimension
printed on the drawing -- and from then on the page's own coordinates convert to
metres. Quantities are then captured as closed polygons and written onto a Takeoff
Sheet.

Kept server-side and free of any canvas dependency so it is testable: the page
supplies plain coordinate pairs, and every figure in the capture is derived here.
"""

import math

import frappe
from frappe import _

# A calibration click is never exactly coincident. 1mm of separation at typical
# plan scale is noise, not a measurement, and dividing by it produces a scale
# factor that blows up.
MIN_CALIBRATION_SPAN = 1.0


class TakeoffError(frappe.ValidationError):
	"""A measurement the surveyor has to redo."""


def _point(value):
	"""Coerce a (x, y) pair, accepting [x, y] or {"x":..,"y":..}."""
	if isinstance(value, dict):
		x, y = value.get("x"), value.get("y")
	elif isinstance(value, (list, tuple)) and len(value) == 2:
		x, y = value
	else:
		raise TakeoffError(_("Point {0} is not an (x, y) pair.").format(value))
	try:
		return float(x), float(y)
	except (TypeError, ValueError):
		raise TakeoffError(_("Point {0} is not numeric.").format(value))


def span(p1, p2):
	"""Distance between two points in page units."""
	(x1, y1), (x2, y2) = _point(p1), _point(p2)
	return math.hypot(x2 - x1, y2 - y1)


@frappe.whitelist()
def calibrate(p1, p2, known_length_m):
	"""Scale factor (metres per page unit) from a known dimension.

	:returns: metres_per_unit, the raw page-unit span, and the known length used,
		so the surveyor can see what the click was actually based on.
	"""
	page_span = span(p1, p2)
	if page_span < MIN_CALIBRATION_SPAN:
		raise TakeoffError(
			_("Calibration points are {0} apart; click two distinct points on the plan.").format(
				round(page_span, 3)
			)
		)
	try:
		known = float(known_length_m)
	except (TypeError, ValueError):
		raise TakeoffError(_("Known length {0} is not a number.").format(known_length_m))
	if known <= 0:
		raise TakeoffError(_("Known length must be greater than zero."))

	return {
		"metres_per_unit": known / page_span,
		"page_span": page_span,
		"known_length_m": known,
	}


def shoelace_area(points):
	"""Signed polygon area in page units, via the shoelace formula.

	Absolute, so clockwise and counter-clockwise traces agree.
	"""
	pts = [_point(p) for p in points]
	if len(pts) < 3:
		raise TakeoffError(_("A polygon needs at least three points; got {0}.").format(len(pts)))
	total = 0.0
	for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
		total += x1 * y2 - x2 * y1
	return abs(total) / 2.0


def polygon_area_m2(points, metres_per_unit):
	"""Polygon area in square metres.

	Area scales with the *square* of the linear factor, which is the step people
	get wrong: a 0.1 m/unit scale makes a 10 sq-unit shape 10 sq m, not 1.

	An open polygon is refused here rather than only at the write layer: the
	shoelace formula happily closes the trace for you, and a surveyor who misses
	the final click would otherwise get a plausible quantity for a shape they never
	drew. Any future caller inherits the check.
	"""
	if not is_closed(points):
		raise TakeoffError(
			_("Polygon is not closed: the last point must return to the first.")
		)
	area_units = shoelace_area(points)
	return round(area_units * float(metres_per_unit) ** 2, 2)



def is_closed(points, tolerance=1e-6):
	"""True when the last point returns to the first."""
	pts = [_point(p) for p in points]
	if len(pts) < 3:
		return False
	return math.isclose(pts[0][0], pts[-1][0], abs_tol=tolerance) and math.isclose(
		pts[0][1], pts[-1][1], abs_tol=tolerance
	)


@frappe.whitelist()
def capture_area(
	sheet,
	points,
	metres_per_unit,
	boq_item=None,
	description=None,
	unit="m2",
):
	"""Write a captured polygon onto a Takeoff Sheet as a BOQ Item row.

	An open polygon is refused by polygon_area_m2() rather than closed implicitly:
	a surveyor who misses the final click would otherwise get a plausible-looking
	quantity built from a shape they never drew.
	"""
	if not frappe.db.exists("Takeoff Sheet", sheet):
		frappe.throw(_("Takeoff Sheet {0} not found.").format(sheet))

	# polygon_area_m2() refuses an open polygon, so the check lives there only.
	area = polygon_area_m2(points, metres_per_unit)
	if area <= 0:
		raise TakeoffError(_("Captured polygon has zero area."))

	if not frappe.db.exists("UOM", unit):
		raise TakeoffError(
			_("Unit of measure {0} does not exist; create it before capturing.").format(unit)
		)

	# polygon_area_m2() has already refused an open polygon by this point.
	doc = frappe.get_doc("Takeoff Sheet", sheet)
	for row in doc.takeoff_items:
		if boq_item and row.item_code == boq_item and (not description or row.description == description):
			raise TakeoffError(
				_("Takeoff Sheet already has a row for {0}. Remove it first to re-capture.").format(
					boq_item
				)
			)
	doc.append(
		"takeoff_items",
		{
			"item_code": boq_item or "AREA-CAPTURE",
			"description": description or _("Area capture"),
			"unit": unit,
			"quantity": area,
			"rate": 0,
		},
	)
	doc.save(ignore_permissions=True)
	return {"takeoff_sheet": sheet, "quantity": area, "unit": unit}


@frappe.whitelist()
def measure_length(p1, p2, metres_per_unit):
	"""Distance between two clicked points, in metres.

	Viewport spec 265: the PDF viewport has area-only today and needs linear
	measurement. Reuses `span()`, so a length is the same arithmetic as an area's
	edge with the scale applied once -- there is no second distance implementation
	to drift out of step.

	:raises TakeoffError: on an uncalibrated scale, or a degenerate span. A
		zero-length "measurement" is a misclick, and returning 0.0 for one would
		let it be recorded as a real dimension.
	"""
	scale = _scale(metres_per_unit)
	distance = span(p1, p2) * scale
	if distance <= 0:
		raise TakeoffError(_("Those two points are the same point; there is no length to measure."))
	return {"length": distance, "unit": "m"}


@frappe.whitelist()
def count_items(points, unit="no"):
	"""Tally of click points -- rebar, fixings, penetrations.

	Viewport spec 266: point-click counting, where the QS clicks each occurrence
	and the count is the measurement. Deliberately does NOT persist anything: the
	caller holds the live tally while the QS clicks (so a misclick is undoable
	before commit) and decides what to do with it.

	:raises TakeoffError: on an empty click set. A count of zero is not a
		measurement, and returning one would let a QS record a quantity that
		describes nothing.
	"""
	pts = [_point(p) for p in (points or [])]
	if not pts:
		raise TakeoffError(_("No points to count."))
	return {"count": len(pts), "unit": unit or "no"}


def _scale(metres_per_unit):
	"""A usable metres-per-unit figure, or a refusal.

	Shared by measure_length and capture_area's callers so an uncalibrated plan is
	rejected the same way wherever it is measured.
	"""
	try:
		scale = float(metres_per_unit)
	except (TypeError, ValueError):
		raise TakeoffError(_("Calibrate the plan first: the scale is not a number."))
	if scale <= 0:
		raise TakeoffError(_("Calibrate the plan first: the scale is not positive."))
	return scale
