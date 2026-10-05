# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Variation Order generation: the DUPA build-up, added up (D-028).

**Additive, not multiplicative.** `Construction BOQ Item` prices a line as
`direct_cost × (1 + ocm/100 + profit/100)` — multiplicative, with the percentages
stored per row. This is a different calculation for a different purpose and
deliberately does not reuse it: on a direct cost of 100 those two shapes give
126.50 and 125.00, and a gate asserting only that the total reconciles would pass
under either. The two are left separate because forcing one onto the other would
reprice every existing BOQ line in the system.

**The rates are sourced, not invented.** They come from this row's own gate
(GATES.md PH5-03): "+ 15% OCM, + 10% profit, + 12% VAT". That is the same footing
as CORE-10's 24-hour LTI window — sourced to the approved plan. It is *not* the
same footing as D-029's officer ratios or CORE-12's LTI day counts, which appear
nowhere in the repo and therefore ship empty and refuse.

	direct    = Σ (quantity × rate) over the components
	ocm       = direct × 0.15
	profit    = direct × 0.10
	subtotal  = direct + ocm + profit
	vat       = subtotal × 0.12
	total     = subtotal + vat

The reconciliation the gate asks for is therefore exact rather than approximate,
and it is checked to the cent.
"""

import frappe
from frappe import _
from frappe.utils import flt

# Provenance: GATES.md PH5-03, which states these rates in the row's own CHECK.
# Change them here and the gate's arithmetic changes with them, so they are named
# in one place rather than sprinkled through the calculation.
DUPA_RATES = {"ocm": 0.15, "profit": 0.10, "vat": 0.12}

COMPONENTS = ("Labor", "Material", "Equipment")


def _require_rates():
	"""Refuse if any rate is missing.

	Not defensive boilerplate: a rate that is absent must not silently become
	zero, because a variation priced with a zero OCM looks like a real figure and
	is one nobody can check.
	"""
	missing = [name for name, value in DUPA_RATES.items() if value is None]
	if missing:
		frappe.throw(
			_("No DUPA rate is configured for: {0}. Refusing to price a variation "
			  "with a missing rate rather than treating it as zero.").format(
				", ".join(sorted(missing))
			),
			title=_("DUPA Rates Incomplete"),
		)


def compute(components):
	"""The additive build-up for a list of component dicts.

	Each component is `{component, quantity, rate, basis?}` and contributes
	`quantity × rate` to the direct cost. Returns every intermediate, so a
	reviewer can check the arithmetic rather than trust a single total.
	"""
	_require_rates()

	lines = []
	direct = 0.0
	for entry in components:
		name = (entry.get("component") or "").strip()
		if name not in COMPONENTS:
			frappe.throw(
				_("'{0}' is not a DUPA component. Expected one of: {1}").format(
					name, ", ".join(COMPONENTS)
				),
				title=_("Unknown DUPA Component"),
			)
		amount = flt(flt(entry.get("quantity")) * flt(entry.get("rate")), 2)
		direct = flt(direct + amount, 2)
		lines.append(
			{
				"component": name,
				"basis": entry.get("basis") or "",
				"quantity": flt(entry.get("quantity")),
				"uom": entry.get("uom"),
				"rate": flt(entry.get("rate")),
				"amount": amount,
			}
		)

	ocm = flt(direct * DUPA_RATES["ocm"], 2)
	profit = flt(direct * DUPA_RATES["profit"], 2)
	subtotal = flt(direct + ocm + profit, 2)
	vat = flt(subtotal * DUPA_RATES["vat"], 2)
	total = flt(subtotal + vat, 2)

	return {
		"lines": lines,
		"direct": direct,
		"ocm": ocm,
		"ocm_rate": DUPA_RATES["ocm"],
		"profit": profit,
		"profit_rate": DUPA_RATES["profit"],
		"subtotal": subtotal,
		"vat": vat,
		"vat_rate": DUPA_RATES["vat"],
		"total": total,
	}


def reconciles(result, stated_total=None, tolerance=0.01):
	"""True when the build-up's own total matches `stated_total` (or itself).

	The gate asks that the components "reconcile to the stated total to 0.01", so
	the check is against the figure the Variation Order actually carries, not
	against the generator's own arithmetic re-run twice.
	"""
	target = result["total"] if stated_total is None else flt(stated_total)
	# The epsilon matters: 9800.01 - 9800.00 is 0.0100000000002 in binary floating
	# point, so a bare `<= 0.01` would reject a figure that is inside the stated
	# tolerance purely because of representation.
	return abs(result["total"] - target) <= tolerance + 1e-9


@frappe.whitelist()
def generate_variation_order(project, vo_type, components, cor=None, description=None, vo_date=None):
	"""Generate a Variation Order carrying its DUPA breakdown.

	Returns the document name. The `variation_amount` written to the parent is the
	build-up's `total`, and the parent's own controller re-derives and re-checks the
	lines on every save, so a later edit that breaks the reconciliation is refused
	rather than silently paying a different number.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	if not may_view(frappe.session.user, project):
		frappe.throw(_("You do not have access to this project."), frappe.PermissionError)

	company = frappe.db.get_value("Construction Project", project, "company")
	if not company:
		frappe.throw(_("{0} is not a known project.").format(project), title=_("Unknown Project"))

	result = compute(components)
	original = flt(frappe.db.get_value("Construction Project", project, "contract_amount"))

	seq = len(frappe.get_all("Construction Variation Order", pluck="name")) + 1
	vo = frappe.get_doc(
		{
			"doctype": "Construction Variation Order",
			"vo_number": f"VO-{seq:05d}",
			"project": project,
			"vo_date": vo_date or frappe.utils.nowdate(),
			"vo_type": vo_type,
			"cor": cor,
			"description": description or "Generated DUPA variation",
			"original_contract_amount": original,
			"variation_amount": result["total"],
			"status": "Draft",
			"dupa_breakdown": result["lines"],
		}
	).insert(ignore_permissions=False)
	return vo.name
