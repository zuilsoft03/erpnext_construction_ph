# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Material requisition and the gatepass scan that turns one into a stock issue.

**The cap, and why it is measured against the BOQ line.** A site requisition
requests material against a BOQ item, and the request may not exceed that item's
takeoff quantity. The figure it is compared against is the **BOQ Item's**
`quantity` minus what earlier requisitions already drew — not the requisition's
own quantity, which would make the check a tautology. `custom_boq_item` is what
makes that comparison possible at all (CORE-13).

**The gatepass is where the money lands.** Scanning a requisition at the gate
issues the material: a `Material Issue` Stock Entry whose lines carry
`custom_wbs_task`, which is what `api/stock_hooks` accumulates into
`Task.custom_actual_cost`. Nothing in this module computes a cost — it arranges
for the existing hook to see the right document.

**Stock has to exist first.** The scan therefore takes `incoming_rate` on the
preceding receipt, not `basic_rate`: ERPNext recomputes `basic_rate` from the
item's costing basis, which is zero for an item the site has never bought, so a
receipt carrying only `basic_rate` enters nothing into the Stock Ledger and the
issue that follows values at 0.0. `incoming_rate` is the rate that actually
lands in the ledger, and the issue then derives its valuation from it.
"""

import frappe
from frappe import _
from frappe.utils import flt

# The three purposes this module creates. Named so the caller cannot pass an
# arbitrary Stock Entry purpose and have it silently treated as a gatepass.
RECEIPT_PURPOSE = "Material Receipt"
ISSUE_PURPOSE = "Material Issue"


class RequisitionError(frappe.ValidationError):
	pass


def _boq_item(name):
	row = frappe.db.get_value(
		"Construction BOQ Item", name, ["item_code", "quantity", "unit", "total"], as_dict=True
	)
	if not row:
		_refuse(
			_("BOQ item {0} does not exist, so there is no takeoff quantity to cap against.").format(
				name
			),
			"Unknown BOQ Item",
		)
	return row


def already_requested(boq_item):
	"""Quantity already committed against `boq_item` by other requisitions.

	Counts every `Material Request Item` pointing at the BOQ line, minus this
	one, so re-saving a requisition does not count itself and refuse itself.
	"""
	total = 0.0
	for row in frappe.get_all(
		"Material Request Item",
		filters={"custom_boq_item": boq_item, "parenttype": "Material Request"},
		fields=["qty", "parent"],
	):
		total += flt(row.qty)
	return total


def headroom(boq_item, exclude_request=None):
	"""How much may still be requested against `boq_item`."""
	item = _boq_item(boq_item)
	committed = already_requested(boq_item)
	if exclude_request:
		for row in frappe.get_all(
			"Material Request Item",
			filters={"parent": exclude_request, "custom_boq_item": boq_item},
			fields=["qty"],
		):
			committed -= flt(row.qty)
	return flt(item.quantity) - committed, item


def check_against_takeoff(boq_item, qty, exclude_request=None, request_name=None):
	"""Refuse a requisition that exceeds the takeoff quantity.

	Called both from `raise_requisition` and directly, because a Material
	Request can also be created through the Desk form — and a cap enforced only
	in one entry point is a cap that is trivially walked around.
	"""
	available, item = headroom(boq_item, exclude_request=exclude_request)
	if flt(qty) > available + 1e-9:
		_refuse(
			_(
				"Requested {0} against BOQ item {1}, but only {2} remains of the "
				"takeoff quantity of {3}. A site order cannot exceed the BOQ."
			).format(flt(qty), boq_item, available, flt(item.quantity)),
			"Requisition Exceeds Takeoff",
		)
	return available


def _refuse(reason, title):
	frappe.throw(_(reason), RequisitionError, title=_(title))


@frappe.whitelist()
def raise_requisition(project, boq_item, qty, uom=None, schedule_date=None, from_warehouse=None):
	"""Raise a Material Request for `qty` against a BOQ line, refusing overruns.

	Returns the Material Request name.

	**Scoped to a project the caller may view.** `project` used to be read for
	one thing only -- resolving the company -- and nothing checked that the
	caller had any claim on it. So any user holding Material Request create
	rights could raise a requisition against ANY Construction Project by naming
	it, and the document that landed carried that project's company. Same
	disclosure class as `boq_matrix.list_boqs` handing back another client's
	BOQ: the argument was a selector, not a permission.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	# Ahead of every write, and ahead of the BOQ-line lookup, so the refusal is
	# about the PROJECT -- which is the thing the caller got wrong -- rather
	# than arriving disguised as a takeoff-cap failure. A caller who may not
	# touch this project learns nothing about its BOQ lines on the way out.
	#
	# `may_view`, NOT `entitled_projects`: a requisition is raised by site
	# staff, and `entitled_projects` demands a client-facing role, so it would
	# refuse every estimator. `may_view` is project scope and nothing more,
	# which is the entire claim being made here.
	if not may_view(frappe.session.user, project):
		_refuse(
			_(
				"You are not assigned to project {0}, so you cannot raise a "
				"requisition against it."
			).format(project),
			"Project Not Permitted",
		)

	company = frappe.db.get_value("Construction Project", project, "company")
	if not company:
		_refuse(_("{0} is not a known project.").format(project), "Unknown Project")

	item = _boq_item(boq_item)
	check_against_takeoff(boq_item, qty)

	return frappe.get_doc(
		{
			"doctype": "Material Request",
			"material_request_type": "Purchase",
			"company": company,
			"transaction_date": frappe.utils.nowdate(),
			"schedule_date": schedule_date or frappe.utils.add_days(frappe.utils.nowdate(), 7),
			"set_warehouse": from_warehouse,
			"items": [
				{
					"item_code": item.item_code,
					"qty": flt(qty),
					"uom": uom or item.unit,
					"schedule_date": schedule_date or frappe.utils.add_days(frappe.utils.nowdate(), 7),
					"custom_boq_item": boq_item,
				}
			],
		}
	).insert(ignore_permissions=False).name


@frappe.whitelist()
def receive_into_store(boq_item, qty, warehouse, rate=0.0):
	"""Receive material into the site store, creating real stock value.

	`rate` lands on `basic_rate` together with `set_basic_rate_manually`, which is
	what stops ERPNext recomputing it. This build has no `incoming_rate` field;
	the flag is the lever, and without it the receipt enters no stock value and
	the gatepass that follows is worth nothing.
	"""
	item = _boq_item(boq_item)
	company = frappe.db.get_value("Construction Project", item.parent, "company") or frappe.get_all(
		"Company", limit=1, pluck="name"
	)[0]

	entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": RECEIPT_PURPOSE,
			"stock_entry_type": RECEIPT_PURPOSE,
			"company": company,
			"to_warehouse": warehouse,
			"items": [
				{
					"item_code": item.item_code,
					"qty": flt(qty),
					"transfer_qty": flt(qty),
					"basic_rate": flt(rate),
					# Without this flag ERPNext RECOMPUTES `basic_rate` from the
					# item's costing basis, which is 0.0 for an item the site has
					# never bought -- so the receipt enters no stock value and the
					# gatepass that follows is valued at nothing. There is no
					# `incoming_rate` field on this ERPNext; this flag is the lever.
					"set_basic_rate_manually": 1,
					"allow_zero_valuation_rate": 1,
				}
			],
		}
	).insert(ignore_permissions=False)
	entry.submit()
	return entry.name


@frappe.whitelist()
def gatepass_scan(request, warehouse, task=None):
	"""Issue a requisition's material at the gate, tagged to a WBS task.

	The Stock Entry's lines carry `custom_wbs_task`, so submitting this document
	fires `api/stock_hooks` and accumulates the cost onto the WBS task. That is the
	whole point of the scan: it is where material becomes a WBS actual cost.
	"""
	req = frappe.get_doc("Material Request", request)
	company = req.company

	entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": ISSUE_PURPOSE,
			"stock_entry_type": ISSUE_PURPOSE,
			"company": company,
			"from_warehouse": warehouse,
			"to_warehouse": warehouse,
			"items": [
				{
					"item_code": row.item_code,
					"qty": flt(row.qty),
					"transfer_qty": flt(row.qty),
					"allow_zero_valuation_rate": 1,
					"custom_wbs_task": task or row.custom_wbs_task or None,
				}
				for row in req.items
			],
		}
	).insert(ignore_permissions=False)
	entry.submit()
	return entry.name
