# erpnext_construction_ph/api/stock_hooks.py
"""
Stock Movement Hooks for erpnext_construction_ph
Synchronizes physical site material issuance with Work Breakdown Structure (WBS) Tasks.
"""

import frappe
from frappe.utils import flt

def update_wbs_task_material_actuals(doc, method=None):
    """
    Triggered when a Stock Entry of type 'Material Issue' is submitted.
    Aggregates actual material quantities and costs against the linked Project Task.
    """
    if doc.purpose != "Material Issue":
        return

    for item_row in doc.items:
        task_id = item_row.get("task") or getattr(item_row, "custom_wbs_task", None)
        if task_id and frappe.db.exists("Task", task_id):
            task_doc = frappe.get_doc("Task", task_id)
            
            # Accumulate WBS actual cost. The field is `custom_actual_cost`,
            # provisioned by setup/custom_fields.py; the guard is kept because a
            # site installed before CORE-11 has no such column, and assigning a
            # missing field would set a Python attribute that save() silently
            # drops. Measured: on this build hasattr is False for an absent
            # field (BaseDocument.__getattr__ raises), so the guard is live
            # rather than inert -- see D-030.
            if hasattr(task_doc, "custom_actual_cost"):
                task_doc.custom_actual_cost = flt(task_doc.custom_actual_cost) + flt(item_row.amount)
                task_doc.save(ignore_permissions=True)
