# erpnext_construction_ph/setup/custom_fields.py
"""
Custom Fields Provisioning for erpnext_construction_ph
Extends native ERPNext DocTypes (Project, Item, Supplier, Purchase Order, Warehouse, Employee)
using Frappe's official create_custom_fields API without modifying core tables.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    "Project": [
        {
            "fieldname": "custom_ciap_section",
            "label": "Philippine Construction & CIAP 102 Terms",
            "fieldtype": "Section Break",
            "insert_after": "notes"
        },
        {
            "fieldname": "custom_pcab_category",
            "label": "PCAB License Category",
            "fieldtype": "Select",
            "options": "\nAAAA\nAAA\nAA\nA\nB\nC\nD\nTrade / E",
            "default": "AAA",
            "insert_after": "custom_ciap_section"
        },
        {
            "fieldname": "custom_contract_type",
            "label": "Contract Framework",
            "fieldtype": "Select",
            "options": "CIAP 102 Private Construction\nDPWH Public Infrastructure (RA 9184)\nDesign-Build Turnkey\nPrivate Lump Sum",
            "default": "CIAP 102 Private Construction",
            "insert_after": "custom_pcab_category"
        },
        {
            "fieldname": "custom_advance_payment_percentage",
            "label": "Mobilization Advance (%)",
            "fieldtype": "Percent",
            "default": "15.0",
            "insert_after": "custom_contract_type"
        },
        {
            "fieldname": "custom_total_advance_payment",
            "label": "Total Advance Mobilization (PHP)",
            "fieldtype": "Currency",
            "options": "Company:company:default_currency",
            "read_only": 1,
            "insert_after": "custom_advance_payment_percentage"
        },
        {
            "fieldname": "custom_retention_rate",
            "label": "Retention Money Rate (%)",
            "fieldtype": "Percent",
            "default": "10.0",
            "insert_after": "custom_total_advance_payment"
        },
        {
            "fieldname": "custom_accumulated_retention",
            "label": "Accumulated Retention Held (PHP)",
            "fieldtype": "Currency",
            "options": "Company:company:default_currency",
            "read_only": 1,
            "insert_after": "custom_retention_rate"
        },
        {
            "fieldname": "custom_is_50_pct_retention_capped",
            "label": "50% Accomplishment Milestone Reached (Retention Capped)",
            "fieldtype": "Check",
            "read_only": 1,
            "insert_after": "custom_accumulated_retention"
        },
        {
            "fieldname": "custom_safety_col_break",
            "fieldtype": "Column Break",
            "insert_after": "custom_is_50_pct_retention_capped"
        },
        {
            "fieldname": "custom_dole_cshp_number",
            "label": "DOLE Approved CSHP No.",
            "fieldtype": "Data",
            "insert_after": "custom_safety_col_break"
        },
        {
            "fieldname": "custom_lead_safety_officer",
            "label": "Lead Safety Officer",
            "fieldtype": "Link",
            "options": "Employee",
            "insert_after": "custom_dole_cshp_number"
        },
        {
            "fieldname": "custom_project_site_coordinates",
            "label": "Project Site Coordinates (Geotag)",
            "fieldtype": "Geolocation",
            "insert_after": "custom_lead_safety_officer"
        }
    ],
    "Task": [
        {
            "fieldname": "custom_actual_cost",
            "label": "Actual Cost (WBS)",
            "fieldtype": "Currency",
            "options": "Company:company:default_currency",
            "read_only": 1,
            "description": "Material cost accumulated against this WBS task, written by api/stock_hooks on Stock Entry submit. Named with the custom_ prefix like every other field here, so it is visibly ours rather than a field ERPNext might later add; api/stock_hooks reads custom_actual_cost to match.",
        }
    ],
    "Material Request Item": [
        {
            "fieldname": "custom_boq_item",
            "label": "BOQ Item",
            "fieldtype": "Link",
            "options": "Construction BOQ Item",
            "description": "The BOQ line this requisition draws against. The requisition is capped at that line's quantity, so a phantom site order cannot exceed the takeoff (blueprint 10.1).",
        }
    ],
    "Stock Entry Detail": [
        {
            "fieldname": "custom_wbs_task",
            "label": "WBS Task",
            "fieldtype": "Link",
            "options": "Task",
            "description": "The WBS task this material line delivers against. Read by api/stock_hooks to accumulate WBS actual cost; without it task_id is always None and the hook never runs.",
        }
    ],
    "Item": [
        {
            "fieldname": "custom_logistics_section",
            "label": "Construction Logistics & Sourcing",
            "fieldtype": "Section Break",
            "insert_after": "description"
        },
        {
            "fieldname": "custom_material_site_coordinates",
            "label": "Quarry / Batch Plant Coordinates (Geotag)",
            "fieldtype": "Geolocation",
            "insert_after": "custom_logistics_section"
        },
        {
            "fieldname": "custom_quarry_depot_name",
            "label": "Quarry / Source Terminal Name",
            "fieldtype": "Data",
            "insert_after": "custom_material_site_coordinates"
        },
        {
            "fieldname": "custom_bulk_density_ton_m3",
            "label": "Bulk Density (Tons / Unit)",
            "fieldtype": "Float",
            "default": "1.0",
            "description": "Used by Haversine Freight Engine to calculate road tonnage hauling cost.",
            "insert_after": "custom_quarry_depot_name"
        },
        {
            "fieldname": "custom_dpwh_pay_item_ref",
            "label": "Standard DPWH Pay Item Ref",
            "fieldtype": "Data",
            "insert_after": "custom_bulk_density_ton_m3"
        }
    ],
    "Supplier": [
        {
            "fieldname": "custom_hauling_section",
            "label": "Regional Hauling & Construction Sourcing",
            "fieldtype": "Section Break",
            "insert_after": "supplier_details"
        },
        {
            "fieldname": "custom_supplier_coordinates",
            "label": "Supplier Dispatch Yard Coordinates",
            "fieldtype": "Geolocation",
            "insert_after": "custom_hauling_section"
        },
        {
            "fieldname": "custom_supplier_region",
            "label": "Philippine Administrative Region",
            "fieldtype": "Select",
            "options": "\nNCR (National Capital Region)\nRegion I (Ilocos)\nRegion II (Cagayan Valley)\nRegion III (Central Luzon)\nRegion IV-A (CALABARZON)\nRegion IV-B (MIMAROPA)\nRegion V (Bicol)\nRegion VI (Western Visayas)\nRegion VII (Central Visayas)\nRegion VIII (Eastern Visayas)\nRegion IX (Zamboanga)\nRegion X (Northern Mindanao)\nRegion XI (Davao)\nRegion XII (SOCCSKSARGEN)\nRegion XIII (Caraga)\nBARMM",
            "default": "NCR (National Capital Region)",
            "insert_after": "custom_supplier_coordinates"
        },
        {
            "fieldname": "custom_freight_rate_ton_km",
            "label": "Hauling Rate (PHP / Ton-km)",
            "fieldtype": "Currency",
            "default": "4.50",
            "insert_after": "custom_supplier_region"
        },
        {
            "fieldname": "custom_supplier_quality_score",
            "label": "Material Quality Score (0-100)",
            "fieldtype": "Percent",
            "default": "80.0",
            "description": "Based on concrete cylinder strength and rebar tensile lab test pass rates.",
            "insert_after": "custom_freight_rate_ton_km"
        }
    ],
    "Purchase Order": [
        {
            "fieldname": "custom_tax_section",
            "label": "Philippine Construction Withholding",
            "fieldtype": "Section Break",
            "insert_after": "taxes"
        },
        {
            "fieldname": "custom_tax_withholding_category",
            "label": "BIR Withholding Tax Category",
            "fieldtype": "Link",
            "options": "Tax Withholding Category",
            "default": "WC120 - 2% Contractors (Corporate)",
            "insert_after": "custom_tax_section"
        },
        {
            "fieldname": "custom_linked_construction_project",
            "label": "Construction Project Link",
            "fieldtype": "Link",
            "options": "Project",
            "insert_after": "custom_tax_withholding_category"
        }
    ],
    "Warehouse": [
        {
            "fieldname": "custom_is_site_laydown",
            "label": "Is Construction Site Laydown Yard",
            "fieldtype": "Check",
            "insert_after": "is_group"
        },
        {
            "fieldname": "custom_linked_project",
            "label": "Dedicated Construction Project",
            "fieldtype": "Link",
            "options": "Project",
            "depends_on": "eval:doc.custom_is_site_laydown==1",
            "insert_after": "custom_is_site_laydown"
        }
    ],
    # BIR EIS profile. Stored as Company custom fields rather than a bespoke
    # doctype because D-016 pins module "Construction PH" at 13 DocTypes, which
    # the CORE-03 gate counts; a new doctype for this would break it.
    "Company": [
        {
            "fieldname": "custom_bir_eis_section",
            "label": "BIR Electronic Invoicing (EIS)",
            "fieldtype": "Section Break",
            "insert_after": "company_name"
        },
        {
            "fieldname": "custom_bir_registered_tin",
            "label": "BIR Registered TIN",
            "fieldtype": "Data",
            "insert_after": "custom_bir_eis_section"
        },
        {
            "fieldname": "custom_bir_rdo_code",
            "label": "RDO Code",
            "fieldtype": "Data",
            "insert_after": "custom_bir_registered_tin"
        },
        {
            "fieldname": "custom_bir_business_style",
            "label": "Business Style",
            "fieldtype": "Select",
            "options": "\nProfessional\nGovernment\nOrdinary",
            "insert_after": "custom_bir_rdo_code"
        },
        {
            "fieldname": "custom_bir_qz_prefix",
            "label": "QZ Series Prefix",
            "fieldtype": "Data",
            "insert_after": "custom_bir_business_style"
        },
        {
            "fieldname": "custom_bir_atp_start",
            "label": "ATP Range Start",
            "fieldtype": "Int",
            "insert_after": "custom_bir_qz_prefix"
        },
        {
            "fieldname": "custom_bir_atp_end",
            "label": "ATP Range End",
            "fieldtype": "Int",
            "insert_after": "custom_bir_atp_start"
        }
    ],
    # Alphanumeric Tax Code. Read by the EIS exporter to stamp deduction lines.
    "Tax Withholding Category": [
        {
            "fieldname": "custom_atc_code",
            "label": "BIR Alphanumeric Tax Code (ATC)",
            "fieldtype": "Data",
            "insert_after": "category_name"
        }
    ],
    # ERPNext's Sales Taxes and Charges has no withholding-category field (it lives
    # on Sales Invoice Item), so a deduction row cannot otherwise be traced back to
    # the ATC. Mirrors the custom_tax_withholding_category idiom already used for
    # Purchase Order in api/procurement_hooks.py.
    "Sales Taxes and Charges": [
        {
            "fieldname": "custom_tax_withholding_category",
            "label": "Tax Withholding Category",
            "fieldtype": "Link",
            "options": "Tax Withholding Category",
            "insert_after": "description"
        }
    ],
    "Sales Invoice": [
        {
            "fieldname": "custom_eis_section",
            "label": "BIR EIS",
            "fieldtype": "Section Break",
            "insert_after": "tax_id"
        },
        {
            "fieldname": "custom_eis_qz",
            "label": "QZ Series",
            "fieldtype": "Data",
            "read_only": 1,
            "insert_after": "custom_eis_section"
        },
        {
            "fieldname": "custom_eis_atp_number",
            "label": "ATP Number",
            "fieldtype": "Int",
            "read_only": 1,
            "insert_after": "custom_eis_qz"
        },
        {
            "fieldname": "custom_ciap_due_date_submission_basis",
            "label": "CIAP Due Date (Submission Basis)",
            "fieldtype": "Date",
            "read_only": 1,
            "description": "Calculated as submitted_on + 30 days under CIAP 102. Internal additive basis; not reported.",
            "insert_after": "custom_eis_atp_number"
        }
    ],
    "Employee": [
        {
            "fieldname": "custom_dole_section",
            "label": "DOLE Construction Safety Accreditation",
            "fieldtype": "Section Break",
            "insert_after": "attendance_device_id"
        },
        {
            "fieldname": "custom_dole_so_level",
            "label": "DOLE Safety Officer Level",
            "fieldtype": "Select",
            "options": "\nSO1 (Basic OSH)\nSO2 (BOSH / COSH 40-hr)\nSO3 (Advanced OSH + 2yr Exp)\nSO4 (OSH Specialist / Consultant)",
            "insert_after": "custom_dole_section"
        },
        {
            "fieldname": "custom_prc_license_no",
            "label": "PRC Engineering / Architecture License No.",
            "fieldtype": "Data",
            "insert_after": "custom_dole_so_level"
        },
        {
            "fieldname": "custom_bosh_cosh_accredited",
            "label": "COSH / BOSH Accredited",
            "fieldtype": "Check",
            "insert_after": "custom_prc_license_no"
        }
    ]
}

def setup_custom_fields():
    """Idempotently registers all custom fields into Frappe's database"""
    frappe.flags.ignore_permissions = True
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
    frappe.db.commit()
    print(
        "Successfully provisioned custom fields for Project, Item, Supplier, PO, Warehouse, "
        "Employee, Company, Tax Withholding Category, and Sales Invoice."
    )
