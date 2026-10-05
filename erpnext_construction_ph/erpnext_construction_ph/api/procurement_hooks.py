# erpnext_construction_ph/api/procurement_hooks.py
"""
Procurement Validation Hooks for erpnext_construction_ph
Enforces Philippine Bureau of Internal Revenue (BIR) Withholding Tax compliance
on all Construction Purchase Orders and Subcontractor Agreements.
"""

import frappe
from frappe import _

def enforce_bir_ewt_withholding(doc, method=None):
    """
    Validates that Purchase Orders linked to construction projects
    have an assigned BIR Tax Withholding Category (WC120 for Corporate, WI120 for Individual).
    """
    # Check if this PO is associated with a construction project
    project = doc.project or getattr(doc, "custom_linked_construction_project", None)
    
    if project:
        # Check supplier tax withholding category
        supplier_doc = frappe.get_doc("Supplier", doc.supplier)
        
        # If not set on PO, auto-assign from supplier or system default
        if not getattr(doc, "tax_withholding_category", None) and not getattr(doc, "custom_tax_withholding_category", None):
            default_cat = getattr(supplier_doc, "tax_withholding_category", None) or "WC120 - 2% Contractors (Corporate)"
            if hasattr(doc, "custom_tax_withholding_category"):
                doc.custom_tax_withholding_category = default_cat
            if hasattr(doc, "tax_withholding_category"):
                doc.tax_withholding_category = default_cat
            frappe.msgprint(
                _("Auto-applied Philippine Contractor Withholding Category: {0}").format(default_cat),
                indicator="blue"
            )
