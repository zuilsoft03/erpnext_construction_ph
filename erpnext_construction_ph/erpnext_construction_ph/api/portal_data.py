# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Row-level scoped data for the client portal.

**Architectural note.** `docs/ARCHITECTURE.md:98` and D-004 assume the client
portal is built on Frappe's native `Portal User` role, and blueprint §15.1 says the
same. That role does not exist in the ERPNext version this app targets (checked on
the gate site: no role matching "%ortal%", no `User Portal Settings`). The portal
is therefore built on an ordinary Frappe `User` plus **User Permission on
Project**, which is the scoping mechanism D-004's Auth Model already names for
internal roles, and which needs no new doctype (D-016 pins the module at 13).

Scoping is enforced here, in one place, rather than in the template. A template
that filters is one edit away from leaking another client's billing ledger.
"""

import frappe
from frappe import _

READ_ROLES = ("System Manager", "Construction Project Manager", "Sales User")

# Client-facing roles. **Holding a Desk role is not enough to see the client's
# model** (PH3-04's gate: "an internal Desk role alone is not sufficient"). The
# portal is where a client's own project is shown to them; the Desk is where staff
# work. Sharing one predicate between the two means every staff account with
# project access inherits client visibility, and the read-only viewer stops being
# a client surface and becomes a staff one.
CLIENT_ROLES = ("Construction Client", "Portal User")


def _unrestricted(user):
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	return any(r in roles for r in READ_ROLES)


def _permitted_erp_projects(user):
	"""Native Project names this user holds a User Permission for.

	User Permission.for_value links to the *native* Project, not to
	Construction Project -- comparing it against a Construction Project name never
	matches and silently grants nothing.
	"""
	if _unrestricted(user):
		return frappe.get_all("Project", pluck="name")

	return frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Project"},
		pluck="for_value",
	)


def permitted_projects(user):
	"""Construction Project names this user may see."""
	allowed = set(_permitted_erp_projects(user))
	if not allowed:
		return []
	return sorted(
		row["name"]
		for row in frappe.get_all(
			"Construction Project", fields=["name", "erpnext_project"]
		)
		if row["erpnext_project"] in allowed
	)


def may_view(user, project):
	"""True when `user` is scoped to the Construction Project `project`."""
	if not project:
		return False
	if _unrestricted(user):
		return bool(frappe.db.exists("Construction Project", project))
	erp_project = frappe.db.get_value("Construction Project", project, "erpnext_project")
	return bool(erp_project) and erp_project in set(_permitted_erp_projects(user))


def evm_curve(construction_project, erp_project, on_date=None):
	"""The S-curve for the portal, shaped for display.

	`on_date` defaults to today, which is the question a client actually asks.
	Returns a payload with `available: False` rather than raising when there is
	nothing to show, so the page can say "no plan yet" instead of erroring.
	"""
	from erpnext_construction_ph.api import earned_value

	if not on_date:
		on_date = frappe.utils.nowdate()
	try:
		raw = earned_value.curve(construction_project, on_date)
	except Exception as exc:
		# A curve that cannot be computed must not take the billing ledger down
		# with it; the client still gets their money figures. But it must NOT look
		# like "no plan on record" -- a blanket `available: False` makes a broken
		# calculation indistinguishable from a project nobody has planned, which
		# is how a maths defect ships as an empty state.
		frappe.log_error(title="EVM curve failed", exception=exc)
		return {"available": False, "error": True, "message": str(exc)[:200]}
	return {
		"available": True,
		"as_at": str(raw["as_at"]),
		"pv": raw["pv"],
		"ev": raw["ev"],
		"ac": raw["ac"],
		"spi": raw["spi"],
		"cpi": raw["cpi"],
		"item_count": raw["item_count"],
		"erp_project": erp_project,
	}


@frappe.whitelist()
def get_portal_payload(project, user=None):
	"""Overview plus billing ledger for one project, or a refusal.

	Returns a `denied` payload rather than raising, so the portal page can show a
	clear message instead of a stack trace; the HTTP layer turns `denied` into a
	403.
	"""
	user = user or frappe.session.user

	if not user or user == "Guest":
		return {"denied": True, "reason": "login_required"}

	if not may_view(user, project):
		# Deliberately the same message whether the project does not exist or the
		# user simply is not assigned to it, so the portal cannot be used to
		# enumerate project names.
		return {"denied": True, "reason": "not_permitted"}

	row = frappe.db.get_value(
		"Construction Project", project, ["name", "company", "contract_amount", "erpnext_project"], as_dict=True
	)
	if not row:
		return {"denied": True, "reason": "not_permitted"}

	company = row["company"]
	contract_amount = flt_(row["contract_amount"])
	# Construction Progress Billing.project links to the *native* Project, so the
	# ledger has to be fetched through Construction Project.erpnext_project.
	# Filtering on the Construction Project name returns an empty ledger for every
	# real project, which looks like "no billings yet" rather than a bug.
	erp_project = row["erpnext_project"]

	billings = frappe.get_all(
		"Construction Progress Billing",
		filters={"project": erp_project, "docstatus": 1},
		fields=[
			"name",
			"billing_number",
			"posting_date",
			"gross_accomplishment_period",
			"retention_amount",
			"cwt_withheld",
			"vat_amount",
			"net_amount_due",
		],
		order_by="posting_date asc",
	)

	totals = {
		"billed": flt_(sum(flt_(b["net_amount_due"]) for b in billings)),
		"retention": flt_(sum(flt_(b["retention_amount"]) for b in billings)),
		"cwt": flt_(sum(flt_(b["cwt_withheld"]) for b in billings)),
		"vat": flt_(sum(flt_(b["vat_amount"]) for b in billings)),
		"count": len(billings),
	}
	contracted = flt_(contract_amount)
	totals["billed_pct"] = flt_((totals["billed"] / contracted) * 100, 2) if contracted else 0.0

	return {
		"denied": False,
		"project": project,
		"company": company,
		"contract_amount": contract_amount,
		"totals": totals,
		"billings": billings,
		# The S-curve is computed here rather than in the template so it inherits
		# the same `may_view` gate as the rest of the payload: a client who is not
		# scoped to this project never reaches this line, so the curve cannot
		# become a side channel. Placed after the `denied` returns on purpose.
		"evm": evm_curve(project, erp_project),
		"available_projects": permitted_projects(user),
	}


def flt_(value, precision=2):
	from frappe.utils import flt

	return flt(value, precision)


def may_view_bim(user, project):
    """True when `user` may see the read-only viewer for `project`.

    Two conditions, both required:

    1. `may_view(project)` -- the same project scoping the billing ledger uses.
       A model is more sensitive than a ledger line in one way that matters here:
       its inventory names the structure of the client's building.
    2. A client-facing role. `READ_ROLES` deliberately does NOT satisfy this,
       which is the whole point -- an internal Desk role alone is not sufficient.

    Returns False rather than raising: the embed's absence is a normal state for
    most callers, and the page renders "not available" instead of a 403.
    """
    if not project or not may_view(user, project):
        return False
    if user == "Administrator":
        return True
    roles = set(frappe.get_roles(user))
    return any(r in roles for r in CLIENT_ROLES)


def entitled_projects(user):
    """Construction Projects this user may view as a client. An explicit list.

    **Added because the scoping was not ours.** Removing this app's own
    authorisation from `bim_viewer.list_models` entirely left the cross-project
    enumeration test PASSING -- `frappe.get_all` applies User Permission
    implicitly, so the protection was coming from the framework, not from a
    filter we wrote.

    That is a disclosure control resting on a layer that vanishes under
    `ignore_permissions=True`, under a doctype exempted from user-permission
    enforcement, and under any later "simplification" of the query. Returning an
    explicit list lets the caller filter on it directly, so the control is ours
    and survives those.
    """
    if not user:
        return []
    # Administrator only. NOT `_unrestricted(user)`, which includes the Desk
    # roles in `READ_ROLES` -- and using it here reopened the hole the row exists
    # to close: a `Construction Project Manager` became "unrestricted" and was
    # handed every project, which is exactly what "an internal Desk role alone is
    # not sufficient" forbids. Caught by
    # `test_an_internal_desk_role_gets_nothing_from_the_endpoint`.
    if user == "Administrator":
        return frappe.get_all("Construction Project", pluck="name")
    if not set(frappe.get_roles(user)) & set(CLIENT_ROLES):
        return []
    permitted = set(_permitted_erp_projects(user))
    if not permitted:
        return []
    return frappe.get_all(
        "Construction Project", filters={"erpnext_project": ["in", list(permitted)]}, pluck="name"
    )
