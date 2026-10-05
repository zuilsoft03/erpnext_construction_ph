# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""DOLE D.O. 13 CSHP support: safety-officer ratio compliance and TBM capture.

**Read D-029 before changing the ratio logic.** The statutory ratio table is
**not in this repository** and is deliberately not invented. What exists in the
codebase is the officer *certification level* (SO1 Basic OSH through SO4 OSH
Specialist, defined in `setup/custom_fields.py:310`) — an attribute of a person,
not a hazard level. `philippine_construction_erpnext_blueprint.md:107` says the
mandatory officer count depends on "hazard level and workforce headcount" and
gives no numbers.

So this module ships the **mechanism** and refuses to assert a constant:

- `SAFETY_OFFICER_RATIOS` is empty by default, keyed by hazard level.
- An empty table makes `check_officer_ratio` return `configured: False` and a
  reason, never a confident pass or a confident fail.

A ratio that looks authoritative and is wrong is worse than no ratio: it would be
quoted in a compliance submission and nobody would know it was a guess. Supply the
real table from DOLE D.O. 13 and it becomes live with no code change.
"""

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now

# Hazard levels this app recognises. Ordered most to least hazardous so a project
# carrying a higher level inherits the stricter requirement.
HAZARD_LEVELS = ("High", "Medium", "Low")

# Safety officer certification levels, matching setup/custom_fields.py:310.
# These describe a *person's* qualification, not the project's hazard level.
OFFICER_LEVELS = {
	"SO1": "Basic OSH",
	"SO2": "BOSH / COSH 40-hr",
	"SO3": "Advanced OSH + 2yr Exp",
	"SO4": "OSH Specialist / Consultant",
}

# The statutory table. EMPTY ON PURPOSE -- see D-029.
# Shape, when populated, is {"High": <officers required per N workers>, ...}.
# Populate from DOLE D.O. 13; do not infer.
SAFETY_OFFICER_RATIOS = {}

MAX_BACKDATE_HOURS = 24


def check_officer_ratio(hazard_level, headcount, officers=0):
	"""Compare the workforce against the required number of safety officers.

	Returns a dict that always says *why*, and says `configured: False` rather
	than guessing when the table has no entry for the hazard level.
	"""
	headcount = flt(headcount)
	officers = flt(officers)

	if not SAFETY_OFFICER_RATIOS:
		return {
			"configured": False,
			"hazard_level": hazard_level,
			"headcount": headcount,
			"officers": officers,
			"required": None,
			"compliant": None,
			"reason": _(
				"No safety-officer ratio table is configured. The statutory figures "
				"come from DOLE D.O. 13 and are deliberately not guessed (D-029)."
			),
		}

	ratio = SAFETY_OFFICER_RATIOS.get(hazard_level)
	if ratio is None:
		return {
			"configured": False,
			"hazard_level": hazard_level,
			"headcount": headcount,
			"officers": officers,
			"required": None,
			"compliant": None,
			"reason": _("No ratio is configured for hazard level '{0}'.").format(hazard_level),
		}

	if flt(ratio) <= 0:
		return {
			"configured": False,
			"hazard_level": hazard_level,
			"headcount": headcount,
			"officers": officers,
			"required": None,
			"compliant": None,
			"reason": _("The configured ratio for '{0}' is not a positive number.").format(hazard_level),
		}

	required = int(headcount / flt(ratio) + 0.999999) if headcount > 0 else 0
	shortfall = max(0, required - int(officers))
	return {
		"configured": True,
		"hazard_level": hazard_level,
		"headcount": headcount,
		"officers": int(officers),
		"required": required,
		"shortfall": shortfall,
		"compliant": shortfall == 0,
		"reason": None
		if shortfall == 0
		else _("Short by {0} safety officer(s) for a workforce of {1}.").format(shortfall, int(headcount)),
	}


def qualified_officer_count(so_level, headcount=None):
	"""Count employees whose `so_certification_level` meets or exceeds `so_level`.

	SO1 is the entry qualification and SO4 the most advanced, so the ordering is
	by that rank rather than lexicographically -- which would put SO2 above SO3.
	"""
	rank = {"SO1": 1, "SO2": 2, "SO3": 3, "SO4": 4}
	wanted = rank.get((so_level or "").strip().upper(), 1)
	# `custom_dole_so_level` is the field `setup/custom_fields.py:307` actually
	# creates. The blueprint calls it `so_certification_level`, and that field does
	# not exist -- querying it raises, so the count would have been unusable
	# rather than merely wrong.
	counts = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["custom_dole_so_level"],
	)
	return sum(1 for c in counts if rank.get((c.custom_dole_so_level or "").strip().upper(), 0) >= wanted)


@frappe.whitelist()
def project_compliance(project, hazard_level=None, so_level="SO1"):
	"""Officer-ratio compliance for `project`, scoped to the caller."""
	from erpnext_construction_ph.api.portal_data import may_view

	if not may_view(frappe.session.user, project):
		frappe.throw(_("You do not have access to this project."), frappe.PermissionError)

	# Hazard level is required from the caller. `Construction Project` has no
	# `hazard_level` field -- the field list is model_name, project, status,
	# client, contract_date, contract_amount, erpnext_project -- and nothing else
	# in the app records one. Deriving it would be a third invented statutory
	# input alongside the ratio table, so it is demanded rather than guessed.
	if not hazard_level:
		return {
			"configured": False,
			"compliant": None,
			"project": project,
			"reason": _(
				"No hazard level is supplied and none is stored on the project. "
				"This app does not define one; it must be supplied by the caller "
				"from the project's DOLE D.O. 13 risk assessment (D-029)."
			),
		}

	headcount = frappe.db.count(
		"Construction Site Log", filters={"project": project, "headcount": (">", 0)}
	)
	officers = qualified_officer_count(so_level)
	result = check_officer_ratio(hazard_level, headcount, officers)
	result["project"] = project
	result["so_level"] = so_level
	return result


@frappe.whitelist()
def record_tbm(project, conducted_by=None, location=None, attendee_count=0, weather="Clear", hazards=None):
	"""Record a Toolbox Meeting, refusing a backdate beyond DOLE D.O. 13's window.

	The 24-hour guard lives in the `Construction Safety Log` controller and has
	never been executed before this row (D-023). It is not duplicated here: the
	controller is the single place that rule lives, and a second copy would be one
	more thing to keep in step.
	"""
	seq = len(frappe.get_all("Construction Safety Log", pluck="name")) + 1
	return frappe.get_doc(
		{
			"doctype": "Construction Safety Log",
			"log_number": f"TBM-{seq:04d}",
			"project": project,
			"tbm_date": now(),
			"location": location,
			"conducted_by": conducted_by or frappe.session.user,
			"attendee_count": int(flt(attendee_count)),
			"weather": weather,
			"safety_hazards": hazards or "",
			"status": "Filed",
		}
	).insert(ignore_permissions=False).name
