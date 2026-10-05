# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Field-facing API for the site log.

A site log is written on site, often before anyone in the office knows the day
happened. That shapes two decisions:

**The log and its Stock Entry are created together, or not at all.** A pour
recorded without the Material Issue that moved the concrete is half a record:
the crew believes the pour is logged, and reconciliation later finds a log with
no material movement behind it. `create_site_log` therefore creates both in one
transaction, and the reverse link is set on each.

**The caller names the project, but never the ERP project.** `project` is a
`Construction Project` here; the `Stock Entry` and the WBS `Task` both live
against the **native** ERPNext Project, which is resolved here rather than
pushed onto the caller. That asymmetry has cost a cycle five times in this
project (see ops/STATE.md), and a field user filling in a form has no way to
know which of the two they are looking at.
"""

import frappe
from frappe import _
from frappe.utils import flt, now

from erpnext_construction_ph.api.portal_data import may_view, permitted_projects

# Roles allowed to WRITE a site log. Deliberately narrower than the portal's
# read roles: this path creates a Stock Entry, which moves inventory and posts
# to the GL, so "can read a project" is nowhere near sufficient. A Sales User or
# a client-scoped portal user must not be able to post material for a project
# they merely have visibility of.
WRITE_ROLES = ("System Manager", "Construction Project Manager", "Construction Site Manager")


def _assert_may_write(user, project):
	"""Authorise the write half of this module.

	`insert(ignore_permissions=False)` defers to Frappe's permission model, but
	that model answers "may this user create a Stock Entry", not "may this user
	post material *for this project*". A user with ordinary stock rights could
	otherwise post inventory against any project in the portfolio by passing its
	name. So both are checked: the role, and the project scope.
	"""
	if user == "Administrator":
		return
	roles = set(frappe.get_roles(user))
	if not any(role in roles for role in WRITE_ROLES):
		frappe.throw(
			_("Your role does not permit recording site activity."),
			frappe.PermissionError,
		)
	if not may_view(user, project):
		frappe.throw(_("You do not have access to this project."), frappe.PermissionError)


@frappe.whitelist()
def create_site_log(
	project,
	log_type,
	pour_item=None,
	pour_volume=None,
	pour_task=None,
	weather_condition=None,
	delay_hours=None,
	delay_reason=None,
	subcontractor=None,
	headcount=None,
	location=None,
	log_date=None,
	log_number=None,
	remarks=None,
):
	"""Create a site log and, for a pour, the Stock Entry that backs it.

	Returns the log name. Raises on anything the doctype refuses, so a caller
	sees the same message the controller produced rather than a generic 500.
	"""
	_assert_may_write(frappe.session.user, project)
	company = frappe.db.get_value("Construction Project", project, "company")
	if not company:
		frappe.throw(_("{0} is not a known project.").format(project), title=_("Unknown Project"))

	values = {
		"doctype": "Construction Site Log",
		"project": project,
		"log_type": log_type,
		"log_date": log_date or now(),
		"location": location,
		"logged_by": frappe.session.user,
		"weather_condition": weather_condition,
		"delay_hours": flt(delay_hours),
		"delay_reason": delay_reason,
		"subcontractor": subcontractor,
		"headcount": headcount,
		"remarks": remarks,
	}
	# Always set one. The doctype has no autoname and `log_number` is mandatory,
	# so relying on the caller to supply it made the ordinary path -- a crew that
	# just fills in the form -- fail on a field it never sees.
	values["log_number"] = log_number or _next_log_number()
	if pour_item:
		values["pour_item"] = pour_item
	if pour_volume is not None:
		values["pour_volume"] = flt(pour_volume)
	if pour_task:
		values["pour_task"] = pour_task

	entry = None
	if flt(values.get("pour_volume")) > 0 and pour_item:
		entry = _material_issue(project, company, pour_item, flt(values["pour_volume"]))

	if entry:
		# Link before insert so the controller's agreement check runs against the
		# real entry rather than a second guess at what it will contain.
		values["stock_entry"] = entry.name

	log = frappe.get_doc(values).insert(ignore_permissions=False)
	if entry:
		entry.db_set("remarks", "Created for site log {0}".format(log.name))
	return log.name


def _next_log_number():
	"""A short, speakable log number.

	Deliberately sequential rather than timestamp-derived: a site log number is
	read aloud over radio, and `20260928145501` is not something a foreman can
	relay without a typo.
	"""
	existing = frappe.get_all("Construction Site Log", pluck="name")
	seq = len(existing) + 1
	while frappe.db.exists("Construction Site Log", f"SL-{seq:05d}"):
		seq += 1
	return f"SL-{seq:05d}"


def _material_issue(project, company, item, qty):
	"""Create the Material Issue that backs a pour.

	The warehouse is taken from an existing issued entry when there is one, so a
	single-project site does not need a second fixture just to say where concrete
	came from.
	"""
	from_warehouse = frappe.db.get_value(
		"Stock Entry", {"purpose": "Material Issue", "docstatus": 1}, "from_warehouse"
	)
	to_warehouse = frappe.db.get_value(
		"Stock Entry", {"purpose": "Material Issue", "docstatus": 1}, "to_warehouse"
	)
	warehouses = frappe.get_all("Warehouse", pluck="name")
	if not from_warehouse:
		from_warehouse = warehouses[0] if warehouses else None
	if not to_warehouse:
		to_warehouse = warehouses[-1] if warehouses else None
	if not from_warehouse or not to_warehouse:
		frappe.throw(_("No warehouse is available to record the material issue."), title=_("No Warehouse"))

	return frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"stock_entry_type": "Material Issue",
			"company": company,
			"from_warehouse": from_warehouse,
			"to_warehouse": to_warehouse,
			"items": [
				{
					"item_code": item,
					"qty": qty,
					"transfer_qty": qty,
					"basic_rate": 0.0,
					"allow_zero_valuation_rate": 1,
				}
			],
		}
	).insert(ignore_permissions=False)


@frappe.whitelist()
def open_tasks(project):
	"""WBS tasks for `project`, for the pour form's task picker.

	Returns the *native* Project's tasks, because that is what
	`Construction Site Log.pour_task` links to. Returning Construction Project
	names here would make every submitted log fail link validation.

	Scoped through `portal_data.may_view`, the same gate `get_portal_payload`
	uses. Without it any authenticated user could pass an arbitrary project name
	and read back its WBS structure -- the same cross-project disclosure class
	as PH2-04's portal scoping.
	"""
	if not may_view(frappe.session.user, project):
		frappe.throw(_("You do not have access to this project."), frappe.PermissionError)
	erp_project = frappe.db.get_value("Construction Project", project, "erpnext_project")
	if not erp_project:
		return []
	return frappe.get_all(
		"Task",
		filters={"project": erp_project, "status": ["!=", "Cancelled"]},
		fields=["name", "subject", "status"],
		order_by="subject asc",
	)


@frappe.whitelist()
def recent_logs(project=None, limit=20):
	"""Recent site logs, newest first, restricted to what the caller may see.

	With no `project` this used to return recent logs across *every* project,
	which hands any authenticated user the site activity of the whole portfolio.
	It now falls back to the caller's permitted projects -- an empty list for a
	user scoped to nothing, which is the correct answer rather than "everything".
	"""
	if project:
		if not may_view(frappe.session.user, project):
			frappe.throw(_("You do not have access to this project."), frappe.PermissionError)
		filters = {"project": project}
	else:
		allowed = permitted_projects(frappe.session.user)
		if not allowed:
			return []
		filters = {"project": ["in", allowed]}
	return frappe.get_all(
		"Construction Site Log",
		filters=filters,
		fields=[
			"name",
			"log_number",
			"project",
			"log_date",
			"log_type",
			"location",
			"pour_volume",
			"delay_hours",
			"headcount",
		],
		order_by="log_date desc",
		limit=limit,
	)
