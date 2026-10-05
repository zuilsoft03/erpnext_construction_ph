# erpnext_construction_ph/setup/workspaces_fixtures.py
"""
Workspace Fixtures for erpnext_construction_ph
Defines the 10 role-tailored Frappe Workspaces complete with metric cards,
shortcuts, quick lists, and dashboard charts for Frappe v14/v15+.
"""

import json
import frappe

WORKSPACES_CONFIG = [
    {
        "title": "Estimating & Takeoff",
        "icon": "calculator",
        "role": "Construction Estimator",
        "shortcuts": [
            {"label": "BOQ Worksheet", "type": "Page", "link_to": "boq_worksheet", "color": "Orange"},
            {"label": "PDF Takeoff Studio", "type": "Page", "link_to": "takeoff_studio", "color": "Teal"},
            {"label": "New Bill of Quantities", "type": "DocType", "link_to": "Construction BOQ", "color": "Blue"},
            {"label": "DUPA Item Master", "type": "DocType", "link_to": "Item", "color": "Grey"}
        ],
        "cards": [
            {
                "label": "Bill of Quantities (BOQ)",
                "links": [
                    {"label": "Active BOQ Baseline", "type": "DocType", "link_to": "Construction BOQ"},
                    {"label": "Detailed Unit Price Analysis (DUPA)", "type": "DocType", "link_to": "Construction BOQ Item"},
                    {"label": "Takeoff Sheets & Calibrations", "type": "DocType", "link_to": "Takeoff Sheet"}
                ]
            },
            {
                "label": "Market & Cost Benchmarks",
                "links": [
                    {"label": "Item Market Rates (DTI/DPWH)", "type": "DocType", "link_to": "Item Price"},
                    {"label": "Regional Supplier Registry", "type": "DocType", "link_to": "Supplier"},
                    {"label": "Subcontractor Trade Quotations", "type": "DocType", "link_to": "Supplier Quotation"}
                ]
            }
        ]
    },
    {
        "title": "Site Management",
        "icon": "building",
        "role": "Construction Site Manager",
        "label": "Site Management",
        "shortcuts": [
            {"label": "Daily Site Log", "type": "DocType", "link_to": "Construction Safety Log", "color": "Green"},
            {"label": "Issue Material to Site", "type": "DocType", "link_to": "Stock Entry", "color": "Blue"},
            {"label": "Material Requisition", "type": "DocType", "link_to": "Material Request", "color": "Orange"},
            {"label": "Log Defect / Punch Item", "type": "DocType", "link_to": "Issue", "color": "Red"}
        ],
        "cards": [
            {
                "label": "Daily Site Execution",
                "links": [
                    {"label": "DOLE Tool Box Meetings (TBM)", "type": "DocType", "link_to": "Construction Safety Log"},
                    {"label": "Concrete Pouring QA Inspections", "type": "DocType", "link_to": "Quality Inspection"},
                    {"label": "Daily Manpower Timesheets", "type": "DocType", "link_to": "Timesheet"},
                    {"label": "Active Site Tasks", "type": "DocType", "link_to": "Task"}
                ]
            },
            {
                "label": "Site Logistics & Laydown",
                "links": [
                    {"label": "Site Laydown Warehouses", "type": "DocType", "link_to": "Warehouse"},
                    {"label": "Incoming Deliveries Check", "type": "DocType", "link_to": "Purchase Receipt"},
                    {"label": "BCF Field Coordination Issues", "type": "DocType", "link_to": "BCF Topic"}
                ]
            }
        ]
    },
    {
        "title": "Project Command Center",
        "icon": "project",
        "role": "Construction Project Manager",
        "label": "Project Command Center",
        "shortcuts": [
            {"label": "Construction Projects", "type": "DocType", "link_to": "Construction Project", "color": "Blue"},
            {"label": "Progress Billing Claim (CIAP 102)", "type": "DocType", "link_to": "Construction Progress Billing", "color": "Green"},
            {"label": "Variation Orders (COR)", "type": "DocType", "link_to": "Construction Variation Order", "color": "Orange"},
            {"label": "Subcontractor Agreements", "type": "DocType", "link_to": "Construction Subcontractor Agreement", "color": "Teal"}
        ],
        "cards": [
            {
                "label": "Commercial & Billing Governance",
                "links": [
                    {"label": "CIAP 102 Progress Claims", "type": "DocType", "link_to": "Construction Progress Billing"},
                    {"label": "Subcontractor Progress Claims", "type": "DocType", "link_to": "Construction Subcontractor Claim"},
                    {"label": "Variation Order Register", "type": "DocType", "link_to": "Construction Variation Order"},
                    {"label": "Project Budgets & Cost Centers", "type": "DocType", "link_to": "Budget"}
                ]
            },
            {
                "label": "Milestones & Delivery",
                "links": [
                    {"label": "Master Work Breakdown Structure", "type": "DocType", "link_to": "Task"},
                    {"label": "Substantial Completion Certificates", "type": "DocType", "link_to": "Construction Document Template"},
                    {"label": "Earned Value Reports (EVM)", "type": "Report", "link_to": "ciap_project_cost_summary"}
                ]
            }
        ]
    },
    {
        "title": "BIM & VDC Studio",
        "icon": "box",
        "role": "Construction BIM Coordinator",
        "label": "BIM & VDC Studio",
        "shortcuts": [
            {"label": "3D BIM Workspace", "type": "Page", "link_to": "bim_workspace", "color": "Teal"},
            {"label": "IFC Models Registry", "type": "DocType", "link_to": "BIM Model", "color": "Blue"},
            {"label": "BCF Clashes & Issues", "type": "DocType", "link_to": "BCF Topic", "color": "Orange"},
            {"label": "Camera Viewpoints", "type": "DocType", "link_to": "BCF Viewpoint", "color": "Grey"}
        ],
        "cards": [
            {
                "label": "Model Coordination (buildingSMART)",
                "links": [
                    {"label": "Federated IFC Models", "type": "DocType", "link_to": "BIM Model"},
                    {"label": "BCF Coordination Topics", "type": "DocType", "link_to": "BCF Topic"},
                    {"label": "Saved 3D Viewpoints", "type": "DocType", "link_to": "BCF Viewpoint"}
                ]
            },
            {
                "label": "Spatial Quantity Linking",
                "links": [
                    {"label": "BOQ Elements Tagged to GUID", "type": "DocType", "link_to": "Construction BOQ Item"},
                    {"label": "Common Data Environment Files", "type": "DocType", "link_to": "File"}
                ]
            }
        ]
    },
    {
        "title": "Procurement & Sourcing",
        "icon": "shopping-cart",
        "role": "Construction Buyer",
        "label": "Procurement & Sourcing",
        "shortcuts": [
            {"label": "Site Material Requests", "type": "DocType", "link_to": "Material Request", "color": "Blue"},
            {"label": "Request for Quotation (RFQ)", "type": "DocType", "link_to": "Request for Quotation", "color": "Orange"},
            {"label": "Material Purchase Orders", "type": "DocType", "link_to": "Purchase Order", "color": "Green"},
            {"label": "Regional Supplier Registry", "type": "DocType", "link_to": "Supplier", "color": "Teal"}
        ],
        "cards": [
            {
                "label": "Procurement Operations",
                "links": [
                    {"label": "Purchase Orders with 2% EWT", "type": "DocType", "link_to": "Purchase Order"},
                    {"label": "Supplier Quotation Matrix", "type": "DocType", "link_to": "Supplier Quotation"},
                    {"label": "Purchase Receipts & Weight Slips", "type": "DocType", "link_to": "Purchase Receipt"}
                ]
            },
            {
                "label": "Logistics & Geotagged Suppliers",
                "links": [
                    {"label": "Geotagged Material Catalogs", "type": "DocType", "link_to": "Item"},
                    {"label": "Supplier Scorecards & Quality", "type": "DocType", "link_to": "Supplier"}
                ]
            }
        ]
    },
    {
        "title": "Project Controls & Planning",
        "icon": "calendar",
        "role": "Construction Planner",
        "label": "Project Controls & Planning",
        "shortcuts": [
            {"label": "Project Master Gantt", "type": "DocType", "link_to": "Task", "color": "Blue"},
            {"label": "Project WBS Templates", "type": "DocType", "link_to": "Project Template", "color": "Teal"},
            {"label": "EVM Performance S-Curve", "type": "Report", "link_to": "ciap_project_cost_summary", "color": "Green"},
            {"label": "Time Extension Claims (CIAP)", "type": "DocType", "link_to": "Construction Variation Order", "color": "Orange"}
        ],
        "cards": [
            {
                "label": "Schedule & WBS Management",
                "links": [
                    {"label": "Work Breakdown Structure Tasks", "type": "DocType", "link_to": "Task"},
                    {"label": "Project Templates (DPWH/Building)", "type": "DocType", "link_to": "Project Template"},
                    {"label": "Daily Labor & Equipment Timesheets", "type": "DocType", "link_to": "Timesheet"}
                ]
            },
            {
                "label": "Delay & Time Impact Analysis",
                "links": [
                    {"label": "Variation Orders Schedule Impact", "type": "DocType", "link_to": "Construction Variation Order"},
                    {"label": "Weather Obstruction Records", "type": "DocType", "link_to": "Construction Safety Log"}
                ]
            }
        ]
    },
    {
        "title": "Design & Engineering",
        "icon": "tool",
        "role": "Construction Design Lead",
        "label": "Design & Engineering",
        "shortcuts": [
            {"label": "Open Requests for Information (RFI)", "type": "DocType", "link_to": "Issue", "color": "Red"},
            {"label": "3D BIM Coordination View", "type": "Page", "link_to": "bim_workspace", "color": "Teal"},
            {"label": "Shop Drawings & Specifications", "type": "DocType", "link_to": "File", "color": "Blue"},
            {"label": "Design Variation Proposals", "type": "DocType", "link_to": "Construction Variation Order", "color": "Orange"}
        ],
        "cards": [
            {
                "label": "Technical Submittals & RFI",
                "links": [
                    {"label": "Field RFI Tracker", "type": "DocType", "link_to": "Issue"},
                    {"label": "Architectural & MEPF BCF Topics", "type": "DocType", "link_to": "BCF Topic"},
                    {"label": "Drawing Revision Register", "type": "DocType", "link_to": "File"}
                ]
            },
            {
                "label": "Engineering Standards",
                "links": [
                    {"label": "Material Quality Specifications", "type": "DocType", "link_to": "Quality Inspection"},
                    {"label": "Federated BIM Models", "type": "DocType", "link_to": "BIM Model"}
                ]
            }
        ]
    },
    {
        "title": "Accounting & BIR Compliance",
        "icon": "bank",
        "role": "Construction Project Accountant",
        "label": "Accounting & BIR Compliance",
        "shortcuts": [
            {"label": "Progress Billings (Sales Invoices)", "type": "DocType", "link_to": "Sales Invoice", "color": "Green"},
            {"label": "Subcontractor Invoices", "type": "DocType", "link_to": "Purchase Invoice", "color": "Blue"},
            {"label": "BIR Form 2307 Register", "type": "Report", "link_to": "bir_2307_register", "color": "Orange"},
            {"label": "Payment Entries", "type": "DocType", "link_to": "Payment Entry", "color": "Teal"}
        ],
        "cards": [
            {
                "label": "CIAP 102 Billing & Retention",
                "links": [
                    {"label": "Head Contract Progress Claims", "type": "DocType", "link_to": "Construction Progress Billing"},
                    {"label": "Subcon Progress Claims", "type": "DocType", "link_to": "Construction Subcontractor Claim"},
                    {"label": "Chart of Accounts (Withholding/Retention)", "type": "DocType", "link_to": "Account"}
                ]
            },
            {
                "label": "BIR Tax Filings (RR 2-98 / RR 11-2018)",
                "links": [
                    {"label": "2% EWT ATC Codes (WC120/WI120)", "type": "DocType", "link_to": "Tax Withholding Category"},
                    {"label": "BIR Form 2307 Certificates Registry", "type": "Report", "link_to": "bir_2307_register"},
                    {"label": "General Ledger Audit", "type": "Report", "link_to": "General Ledger"}
                ]
            }
        ]
    },
    {
        "title": "Contracts & Commercial",
        "icon": "edit",
        "role": "Construction Contract Administrator",
        "label": "Contracts & Commercial",
        "shortcuts": [
            {"label": "In-App Document Author", "type": "Page", "link_to": "doc_editor", "color": "Blue"},
            {"label": "Subcontractor Agreements", "type": "DocType", "link_to": "Construction Subcontractor Agreement", "color": "Teal"},
            {"label": "Change Orders / Variations", "type": "DocType", "link_to": "Construction Variation Order", "color": "Orange"},
            {"label": "CIAP 102 Progress Claims", "type": "DocType", "link_to": "Construction Progress Billing", "color": "Green"}
        ],
        "cards": [
            {
                "label": "Contract Administration",
                "links": [
                    {"label": "CIAP 102 Standard Contracts", "type": "DocType", "link_to": "Construction Document Template"},
                    {"label": "Notice to Proceed & Award Records", "type": "DocType", "link_to": "Construction Project"},
                    {"label": "Subcontractor Trade Agreements", "type": "DocType", "link_to": "Construction Subcontractor Agreement"}
                ]
            },
            {
                "label": "Claims & Variations",
                "links": [
                    {"label": "Variation Order Log & Entitlements", "type": "DocType", "link_to": "Construction Variation Order"},
                    {"label": "30-Day Progress Billing Review Clock", "type": "DocType", "link_to": "Construction Progress Billing"}
                ]
            }
        ]
    },
    {
        "title": "Field Foreman Mobile",
        "icon": "mobile",
        "role": "Construction Field Foreman",
        "label": "Field Foreman Mobile",
        "shortcuts": [
            {"label": "Daily Tool Box Meeting (TBM)", "type": "DocType", "link_to": "Construction Safety Log", "color": "Green"},
            {"label": "Punch Crew Attendance", "type": "DocType", "link_to": "Timesheet", "color": "Blue"},
            {"label": "Consumables Pickup Request", "type": "DocType", "link_to": "Material Request", "color": "Orange"},
            {"label": "Today's Assigned Work", "type": "DocType", "link_to": "Task", "color": "Teal"}
        ],
        "cards": [
            {
                "label": "Quick Field Actions (<= 3 Clicks)",
                "links": [
                    {"label": "DOLE D.O. 13 Safety Check-in", "type": "DocType", "link_to": "Construction Safety Log"},
                    {"label": "Crew Daily Timesheet", "type": "DocType", "link_to": "Timesheet"},
                    {"label": "Material Request for Rebar/Lumber/Consumables", "type": "DocType", "link_to": "Material Request"},
                    {"label": "Task Checklist", "type": "DocType", "link_to": "Task"}
                ]
            }
        ]
    }
]

def _link_target_exists(item):
    """True when a workspace shortcut/link target is installed AND openable.

    Two ways a target can fail:
    - it does not exist yet (a DocType or Page owned by a later build row);
    - it exists but is a child table (`istable: 1`). Child tables have no list
      view and cannot be opened from Desk, so linking one ships a dead shortcut.
      Construction BOQ Item and BCF Viewpoint both hit this the moment CORE-03
      created them, and no gate counts workspace links, so nothing else catches it.

    Skipped links return when the owning row replaces them with something a user
    can actually click.
    """
    target = item.get("link_to")
    if not target:
        return False
    if not frappe.db.exists(item.get("type") or "DocType", target):
        return False
    if frappe.db.get_value("DocType", target, "istable"):
        return False
    return True


def _resolvable_links(ws_cfg):
    """Split a workspace config into the shortcuts and cards that resolve today."""
    shortcuts = [s for s in ws_cfg["shortcuts"] if _link_target_exists(s)]
    cards = []
    for card in ws_cfg["cards"]:
        links = [i for i in card["links"] if _link_target_exists(i)]
        if links:
            cards.append({**card, "links": links})
    return shortcuts, cards


def setup_workspaces():
    """Idempotently creates or updates the 10 role-tailored Frappe Workspaces"""
    frappe.flags.ignore_permissions = True

    for ws_cfg in WORKSPACES_CONFIG:
        # Workspace.autoname is "field:label", so the label is the document's real
        # identity. Existence checks must use it, not a separate slug.
        ws_name = ws_cfg["title"]
        
        shortcuts, cards = _resolvable_links(ws_cfg)

        # Build Frappe v14/v15 Content JSON block structure
        content_blocks = [
            {"id": "header_block", "type": "header", "data": {"text": ws_cfg["title"], "level": 3}},
            {"id": "shortcuts_block", "type": "shortcut", "data": {"shortcuts": [s["label"] for s in shortcuts]}},
            {"id": "cards_block", "type": "card", "data": {"cards": [c["label"] for c in cards]}}
        ]
        
        ws_doc_data = {
            "doctype": "Workspace",
            "label": ws_name,
            "title": ws_cfg["title"],
            "icon": ws_cfg["icon"],
            "module": "Construction PH",
            "category": "Modules",
            "public": 1,
            "roles": [{"role": ws_cfg["role"]}],
            "content": json.dumps(content_blocks),
            "shortcuts": shortcuts,
            "links": []
        }

        # Build card links. Workspace Link.type is the container kind, so each
        # card opens with a "Card Break" row and the target kind goes in link_type.
        for card in cards:
            ws_doc_data["links"].append({"type": "Card Break", "label": card["label"]})
            for item in card["links"]:
                ws_doc_data["links"].append({
                    "type": "Link",
                    "link_type": item["type"],
                    "link_to": item["link_to"],
                    "label": item["label"]
                })

        if not frappe.db.exists("Workspace", ws_name):
            doc = frappe.get_doc(ws_doc_data)
            doc.insert(ignore_permissions=True)
            print(f"Created Workspace: {ws_name}")
        else:
            doc = frappe.get_doc("Workspace", ws_name)
            doc.label = ws_cfg["title"]
            doc.title = ws_cfg["title"]
            doc.content = json.dumps(content_blocks)
            doc.icon = ws_cfg["icon"]
            # Assigning raw dicts to a Table field leaves plain dicts in the
            # child list; _set_defaults() then fails on d.is_new().
            doc.roles = []
            doc.append("roles", {"role": ws_cfg["role"]})
            doc.shortcuts = []
            for s in shortcuts:
                doc.append("shortcuts", s)
            doc.links = []
            for card in cards:
                doc.append("links", {"type": "Card Break", "label": card["label"]})
                for item in card["links"]:
                    doc.append("links", {
                        "type": "Link",
                        "link_type": item["type"],
                        "link_to": item["link_to"],
                        "label": item["label"]
                    })
            doc.save(ignore_permissions=True)
            print(f"Updated Workspace: {ws_name}")

    frappe.db.commit()
