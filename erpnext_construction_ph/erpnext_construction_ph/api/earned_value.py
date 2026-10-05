# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Earned Value Management for the portal S-curve.

Implements D-026. The single decision that shapes everything here: **PV is
time-phased from item dates, and EV is actual progress**, and the two are read
from different fields on purpose.

If both read ERPNext's one `Project.percent_complete` field they would be equal
at every point in time, SPI would be identically 1.0, and the "S-curve" would be
a straight line that measures nothing. Separating the planned source (the BOQ
item's `planned_start`/`planned_end` window) from the actual source (the item's
`actual_percent_complete`) is what gives SPI the ability to be less than one,
which is the only reason to compute it.

	PV(t) = sum over items of  total_i * planned_progress_i(t)
	EV(t) = sum over items of  total_i * actual_percent_complete_i / 100
	AC    = Project.total_purchase_cost + Project.total_consumed_material_cost
	SPI   = EV / PV        (None when PV is zero -- undefined, not an error)
	CPI   = EV / AC        (None when AC is zero)

`AC` is the weakest leg and is recorded as such in D-026: subcontractor and
labour cost are not accumulated anywhere this app can read yet, so AC understates
real cost and CPI reads optimistic until Subcon Claim can feed it.
"""

from datetime import date, datetime

import frappe
from frappe.utils import flt

def _as_date(value):
	"""Coerce a Frappe date/datetime/str to a `date`, or None."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	parsed = frappe.utils.getdate(value)
	return parsed


def planned_progress(item, on_date):
	"""Fraction of `item` that should be complete by `on_date`, in [0, 1].

	Linear across the planned window, clamped outside it. A zero-length item
	(`planned_end == planned_start`) has no window to interpolate across, so it is
	0 strictly before that day and 1 on and after it -- never a division by zero.
	"""
	as_of = _as_date(on_date)
	start = _as_date(item.planned_start)
	end = _as_date(item.planned_end)

	if start is None or end is None:
		# No plan on record. Contributes nothing to PV rather than guessing one.
		return 0.0
	if as_of is None or as_of < start:
		return 0.0
	if as_of >= end:
		return 1.0
	if end == start:
		# Unreachable given the ordering above, but keep the guard explicit: a
		# zero-length window must never divide by zero.
		return 1.0
	elapsed = (as_of - start).days
	span = (end - start).days
	return max(0.0, min(1.0, elapsed / float(span)))


def _project_items(project_name):
	"""BOQ Items across every BOQ on the project, keyed for summing.

	`total` is the item's value and `actual_percent_complete` a 0-100 percent;
	both come from the same child row so the two sums stay consistent.
	"""
	rows = frappe.db.sql(
		"""
		select bi.total, bi.planned_start, bi.planned_end, bi.actual_percent_complete
		from `tabConstruction BOQ Item` bi
		inner join `tabConstruction BOQ` b on b.name = bi.parent
		where b.project = %s
		""",
		(project_name,),
		as_dict=True,
	)
	return rows


def actual_cost(project):
	"""AC from the two cost accumulators ERPNext actually maintains (D-026).

	Takes the Construction Project name and resolves it to the native ERPNext
	Project, because the cost accumulators live on the native Project and
	`Construction BOQ.project` points at the Construction Project.
	"""
	erp_project = frappe.db.get_value("Construction Project", project, "erpnext_project")
	if not erp_project:
		return 0.0
	row = frappe.db.get_value(
		"Project",
		erp_project,
		["total_purchase_cost", "total_consumed_material_cost"],
		as_dict=True,
	)
	if not row:
		return 0.0
	return flt(row.total_purchase_cost) + flt(row.total_consumed_material_cost)


def curve(project, on_date):
	"""PV, EV, AC, SPI and CPI for `project` as at `on_date`.

	`project` is the **Construction Project** name, because that is what
	`Construction BOQ.project` links to. Note the asymmetry that has bitten this
	project repeatedly: `Construction BOQ.project` links to Construction Project,
	while `Construction Progress Billing.project` and `User Permission.for_value`
	link to the **native** ERPNext Project. Getting this backwards matches no
	rows, which yields PV = EV = 0 and looks exactly like "no progress yet".
	"""
	items = _project_items(project)

	pv = 0.0
	ev = 0.0
	for item in items:
		total = flt(item.total)
		pv += total * planned_progress(item, on_date)
		ev += total * flt(item.actual_percent_complete) / 100.0

	ac = actual_cost(project)

	# SPI and CPI are undefined -- not infinite, not an exception -- when the
	# denominator is zero. A project before its first planned start has no
	# planned value yet, and dividing there would raise.
	spi = (ev / pv) if pv else None
	cpi = (ev / ac) if ac else None

	return {
		"project": project,
		"as_at": _as_date(on_date),
		"pv": round(pv, 2),
		"ev": round(ev, 2),
		"ac": round(ac, 2),
		"spi": round(spi, 4) if spi is not None else None,
		"cpi": round(cpi, 4) if cpi is not None else None,
		"item_count": len(items),
	}
