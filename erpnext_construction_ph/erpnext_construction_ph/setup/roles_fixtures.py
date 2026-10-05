# erpnext_construction_ph/setup/roles_fixtures.py
"""
Role and Permission Fixtures for erpnext_construction_ph
Automates the creation of 10 specialized construction roles, role profiles,
and granular document permission matrices upon module initiation.
"""

import frappe

# The 10 Core Construction Roles
CONSTRUCTION_ROLES = [
    {
        "role_name": "Construction Estimator",
        "desk_access": 1,
        "description": "Quantity surveying, multi-source takeoff (PDF/CAD/BIM), DUPA rate analysis, and BOQ preparation."
    },
    {
        "role_name": "Construction Site Manager",
        "desk_access": 1,
        "description": "Day-to-day site execution, concrete pour permits, RFI coordination, daily site logs, and field material issues."
    },
    {
        "role_name": "Construction Project Manager",
        "desk_access": 1,
        "description": "Total project operational, financial, and contractual stewardship; CIAP 102 progress claims and variation approvals."
    },
    {
        "role_name": "Construction BIM Coordinator",
        "desk_access": 1,
        "description": "IFC model federation, That Open Engine fragment processing, buildingSMART BCF clash detection and coordination."
    },
    {
        "role_name": "Construction Buyer",
        "desk_access": 1,
        "description": "Material procurement, Haversine regional landed cost optimization, RFQ canvassing, and supplier PO execution."
    },
    {
        "role_name": "Construction Planner",
        "desk_access": 1,
        "description": "Project controls, master CPM scheduling, S-Curve EVM (PV/EV/AC) tracking, and CIAP 102 time extension impact analysis."
    },
    {
        "role_name": "Construction Design Lead",
        "desk_access": 1,
        "description": "Architectural and engineering submittal review, RFI resolution, design change proposal authoring, and drawing registers."
    },
    {
        "role_name": "Construction Project Accountant",
        "desk_access": 1,
        "description": "CIAP 102 progress billing verification, retention asset/liability management, BIR Form 2307 issuance, and 1601-EQ tax returns."
    },
    {
        "role_name": "Construction Contract Administrator",
        "desk_access": 1,
        "description": "CIAP 102 contract authoring, Variation Order claims governance, bond validity monitoring, and formal completion certificates."
    },
    {
        "role_name": "Construction Field Foreman",
        "desk_access": 1,
        "description": "Mobile field crew attendance, daily tool box talks (DOLE D.O. 13), consumable material pickup, and task checklist completion."
    },
    {
        # The ONE role with `desk_access: 0`. It is a portal role: a client's own
        # contact, who sees their project and nothing else. PH3-04's gate turns on
        # "an internal Desk role alone is not sufficient", so this role has to
        # exist and has to be distinguishable from the Desk ones -- and it was
        # being created inside a TEST's transaction, which meant it did not exist
        # outside one. A role the authorisation rule depends on is a role the
        # install must create. D-025: there is no `Portal User` role in this ERPNext.
        "role_name": "Construction Client",
        "desk_access": 0,
        "description": "Client-side contact for one project. Read-only, portal-scoped, no Desk access."
    }
]

# Role Profiles: Bundles of native ERPNext roles + Construction Roles
# This ensures users have underlying permissions to native ERPNext modules without permission errors.
ROLE_PROFILES = {
    "Construction Estimator Profile": [
        "Construction Estimator", "Stock User", "Sales User", "Item Manager"
    ],
    "Construction Site Manager Profile": [
        "Construction Site Manager", "Stock User", "Projects User", "Quality Manager"
    ],
    "Construction Project Manager Profile": [
        "Construction Project Manager", "Projects Manager", "Accounts User", "Purchase User", "Sales Manager"
    ],
    "Construction BIM Coordinator Profile": [
        "Construction BIM Coordinator", "Projects User"
    ],
    "Construction Buyer Profile": [
        "Construction Buyer", "Purchase User", "Stock User", "Purchase Master Manager"
    ],
    "Construction Planner Profile": [
        "Construction Planner", "Projects Manager"
    ],
    "Construction Design Lead Profile": [
        "Construction Design Lead", "Projects User"
    ],
    "Construction Project Accountant Profile": [
        "Construction Project Accountant", "Accounts User", "Accounts Manager"
    ],
    "Construction Contract Administrator Profile": [
        "Construction Contract Administrator", "Projects User", "Sales User", "Purchase User"
    ],
    "Construction Field Foreman Profile": [
        "Construction Field Foreman", "Projects User"
    ],
    # Common Philippine Construction Multi-Role Composite Profiles
    "Composite - Project In-Charge (Site + Planner)": [
        "Construction Site Manager", "Construction Planner", "Stock User", "Projects Manager", "Quality Manager"
    ],
    "Composite - Commercial Lead (Estimator + Buyer)": [
        "Construction Estimator", "Construction Buyer", "Stock User", "Purchase User", "Item Manager"
    ],
    "Composite - Project Director (PM + Contract Admin)": [
        "Construction Project Manager", "Construction Contract Administrator", "Projects Manager", "Accounts User", "Sales Manager", "Purchase User"
    ],
    "Composite - Technical Coordinator (Design + BIM)": [
        "Construction Design Lead", "Construction BIM Coordinator", "Projects User"
    ],
    "Composite - Field Operations Lead (Site Mgr + Foreman)": [
        "Construction Site Manager", "Construction Field Foreman", "Stock User", "Projects User"
    ]
}

# Segregation of Duties (SoD) Guardrails
# Identifies toxic role combinations that represent internal control or audit risks
SOD_CONFLICTS = [
    {
        "roles": {"Construction Buyer", "Construction Project Accountant"},
        "severity": "Critical",
        "reason": "Maker-Checker Violation: Sourcing and issuing Purchase Orders while simultaneously certifying Invoices and Payment Entries violates internal accounting controls."
    },
    {
        "roles": {"Construction Estimator", "Construction Project Manager"},
        "severity": "Warning",
        "reason": "Self-Approval Risk: Creating baseline BOQ estimates and having unilateral authority to freeze/submit them without secondary commercial review."
    },
    {
        "roles": {"Construction Site Manager", "Construction Project Accountant"},
        "severity": "Critical",
        "reason": "Field Inspection & Cash Disbursement Conflict: Verifying site physical accomplishment percentages and directly approving disbursement payments."
    }
]

# Granular Document Permission Matrix
# 1 = Allowed, 0 = Denied
# Fields: [Read, Write, Create, Delete, Submit, Cancel, Amend]
ROLE_PERMISSION_MATRIX = {
    "Construction Estimator": {
        "Construction BOQ": [1, 1, 1, 0, 0, 0, 0], # Draft and edit, PM must submit/freeze
        "Construction BOQ Item": [1, 1, 1, 1, 0, 0, 0],
        "Takeoff Sheet": [1, 1, 1, 1, 0, 0, 0],
        "BIM Model": [1, 0, 0, 0, 0, 0, 0],       # Read-only model inspection
        "Item": [1, 1, 1, 0, 0, 0, 0],            # Maintain DUPA items
        "Item Price": [1, 1, 1, 0, 0, 0, 0],
        "Construction Project": [1, 0, 0, 0, 0, 0, 0],
        "Supplier": [1, 0, 0, 0, 0, 0, 0],
        "Construction Variation Order": [1, 1, 1, 0, 0, 0, 0] # Can draft cost impacts
    },
    "Construction Site Manager": {
        "Construction Safety Log": [1, 1, 1, 0, 1, 0, 0],
        "Stock Entry": [1, 1, 1, 0, 1, 0, 0],      # Issue materials to site WIP
        "Material Request": [1, 1, 1, 0, 1, 0, 0],
        "Task": [1, 1, 0, 0, 0, 0, 0],             # Update progress %
        "Timesheet": [1, 1, 1, 0, 1, 0, 0],
        "Issue": [1, 1, 1, 0, 0, 0, 0],             # Site defect punch items
        "Quality Inspection": [1, 1, 1, 0, 1, 0, 0],
        "BCF Topic": [1, 1, 1, 0, 0, 0, 0],
        "Construction BOQ": [1, 0, 0, 0, 0, 0, 0], # Read-only reference
        "Construction Progress Billing": [1, 0, 0, 0, 0, 0, 0] # Read-only, cannot bill
    },
    "Construction Project Manager": {
        "Construction Project": [1, 1, 1, 1, 1, 1, 1],
        "Construction BOQ": [1, 1, 1, 0, 1, 1, 1], # Full baseline approval
        "Construction Progress Billing": [1, 1, 1, 0, 1, 1, 1],
        "Construction Subcontractor Agreement": [1, 1, 1, 0, 1, 1, 1],
        "Construction Subcontractor Claim": [1, 1, 0, 0, 1, 1, 1],
        "Construction Variation Order": [1, 1, 1, 0, 1, 1, 1],
        "Budget": [1, 1, 1, 0, 1, 0, 1],
        "Purchase Order": [1, 1, 0, 0, 1, 0, 1],   # Approve POs
        "BCF Topic": [1, 1, 1, 0, 1, 0, 0]
    },
    "Construction BIM Coordinator": {
        "BIM Model": [1, 1, 1, 1, 1, 1, 1],        # Full model management
        "BCF Topic": [1, 1, 1, 1, 1, 1, 1],        # Issue & clash management
        "BCF Viewpoint": [1, 1, 1, 1, 0, 0, 0],
        "Construction BOQ Item": [1, 1, 0, 0, 0, 0, 0], # Map GUIDs only
        "Task": [1, 1, 0, 0, 0, 0, 0]
    },
    "Construction Buyer": {
        "Request for Quotation": [1, 1, 1, 0, 1, 0, 0],
        "Supplier Quotation": [1, 1, 1, 0, 1, 0, 0],
        "Purchase Order": [1, 1, 1, 0, 0, 0, 0],   # Draft PO, PM submits
        "Supplier": [1, 1, 1, 0, 0, 0, 0],
        "Item Price": [1, 1, 1, 0, 0, 0, 0],
        "Material Request": [1, 0, 0, 0, 0, 0, 0]  # Read incoming requisitions
    },
    "Construction Planner": {
        "Project": [1, 1, 0, 0, 0, 0, 0],
        "Task": [1, 1, 1, 1, 1, 0, 1],             # Master CPM scheduling
        "Project Template": [1, 1, 1, 0, 0, 0, 0],
        "Construction BOQ Item": [1, 1, 0, 0, 0, 0, 0], # Schedule linkage
        "Construction Variation Order": [1, 1, 0, 0, 0, 0, 0] # Time extension evaluation
    },
    "Construction Design Lead": {
        "BIM Model": [1, 1, 0, 0, 0, 0, 0],
        "BCF Topic": [1, 1, 1, 0, 0, 0, 0],
        "Issue": [1, 1, 1, 0, 1, 0, 0],             # RFI answers & sign-offs
        "File": [1, 1, 1, 0, 0, 0, 0],              # Drawings & specifications
        "Construction Variation Order": [1, 1, 1, 0, 0, 0, 0] # Initiate design changes
    },
    "Construction Project Accountant": {
        "Sales Invoice": [1, 1, 1, 0, 1, 1, 1],     # Client Progress Billings
        "Purchase Invoice": [1, 1, 1, 0, 1, 1, 1],  # Subcon Claims
        "Payment Entry": [1, 1, 1, 0, 1, 1, 1],
        "Journal Entry": [1, 1, 1, 0, 1, 1, 1],
        "Construction Progress Billing": [1, 1, 0, 0, 1, 0, 1], # Post to GL
        "Construction Subcontractor Claim": [1, 1, 0, 0, 1, 0, 1],
        "Tax Withholding Category": [1, 1, 0, 0, 0, 0, 0] # WC120 / WI120
    },
    "Construction Contract Administrator": {
        "Construction Subcontractor Agreement": [1, 1, 1, 0, 1, 1, 1],
        "Construction Variation Order": [1, 1, 1, 0, 1, 1, 1],
        "Construction Document Template": [1, 1, 1, 0, 0, 0, 0], # CIAP templates
        "Construction Project": [1, 1, 0, 0, 0, 0, 0],
        "Construction Progress Billing": [1, 0, 0, 0, 0, 0, 0] # Read & compliance audit
    },
    "Construction Field Foreman": {
        "Construction Safety Log": [1, 1, 1, 0, 0, 0, 0], # Daily Tool Box talk sign
        "Timesheet": [1, 1, 1, 0, 0, 0, 0],        # Crew attendance
        "Material Request": [1, 1, 1, 0, 0, 0, 0], # Consumable pickup
        "Task": [1, 1, 0, 0, 0, 0, 0]              # View today's tasks & check off
    }
}

def setup_roles_and_permissions():
    """Idempotently creates roles, role profiles, and custom permissions"""
    frappe.flags.ignore_permissions = True
    
    # 1. Create Roles
    for role_data in CONSTRUCTION_ROLES:
        role_name = role_data["role_name"]
        if not frappe.db.exists("Role", role_name):
            doc = frappe.get_doc({
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": role_data["desk_access"],
                "description": role_data["description"]
            })
            doc.insert(ignore_permissions=True)
            print(f"Created Role: {role_name}")

    # 2. Create Role Profiles
    for profile_name, roles in ROLE_PROFILES.items():
        if not frappe.db.exists("Role Profile", profile_name):
            doc = frappe.get_doc({
                "doctype": "Role Profile",
                "role_profile": profile_name,
                "roles": [{"role": r} for r in roles]
            })
            doc.insert(ignore_permissions=True)
            print(f"Created Role Profile: {profile_name}")
        else:
            # Sync roles in existing profile
            doc = frappe.get_doc("Role Profile", profile_name)
            existing_roles = [r.role for r in doc.roles]
            for r in roles:
                if r not in existing_roles:
                    doc.append("roles", {"role": r})
            doc.save(ignore_permissions=True)

    # 3. Apply Granular Custom DocPerms
    for role_name, doctype_dict in ROLE_PERMISSION_MATRIX.items():
        for doctype, perms in doctype_dict.items():
            if frappe.db.exists("DocType", doctype):
                apply_custom_docperm(doctype, role_name, perms)

    frappe.db.commit()

def apply_custom_docperm(doctype, role, perms):
    """Inserts or updates a Custom DocPerm record for a role and DocType"""
    read, write, create, delete, submit, cancel, amend = perms
    
    existing = frappe.db.get_value(
        "Custom DocPerm",
        {"parent": doctype, "role": role},
        "name"
    )
    
    if existing:
        doc = frappe.get_doc("Custom DocPerm", existing)
        doc.read = read
        doc.write = write
        doc.create = create
        doc.delete = delete
        doc.submit = submit
        doc.cancel = cancel
        doc.amend = amend
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": "Custom DocPerm",
            "parent": doctype,
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": role,
            "read": read,
            "write": write,
            "create": create,
            "delete": delete,
            "submit": submit,
            "cancel": cancel,
            "amend": amend
        })
        doc.insert(ignore_permissions=True)

def validate_role_assignment(roles_list):
    """
    Checks for Segregation of Duties (SoD) conflicts when an employee is assigned multiple roles.
    Returns a tuple: (is_valid, warnings_list, errors_list)
    """
    assigned_set = set(roles_list)
    warnings = []
    errors = []

    for rule in SOD_CONFLICTS:
        if rule["roles"].issubset(assigned_set):
            msg = f"[{rule['severity']}] {' + '.join(rule['roles'])}: {rule['reason']}"
            if rule["severity"] == "Critical":
                errors.append(msg)
            else:
                warnings.append(msg)

    is_valid = len(errors) == 0
    return is_valid, warnings, errors

def assign_employee_roles(employee_name, roles_list, allow_critical_sod=False):
    """
    Assigns multiple construction roles to an employee's linked User account.
    Applies additive permissions (union of all assigned roles).
    Enforces Segregation of Duties (SoD) validation.
    """
    if not frappe.db.exists("Employee", employee_name):
        frappe.throw(f"Employee {employee_name} does not exist.")

    user_id = frappe.db.get_value("Employee", employee_name, "user_id")
    if not user_id:
        frappe.throw(f"Employee {employee_name} does not have a linked User ID.")

    # Check SoD conflicts
    is_valid, warnings, errors = validate_role_assignment(roles_list)
    if not is_valid and not allow_critical_sod:
        error_msg = "\n".join(errors)
        frappe.throw(f"Cannot assign roles to {employee_name} due to Segregation of Duties violations:\n{error_msg}")

    user_doc = frappe.get_doc("User", user_id)
    existing_roles = {r.role for r in user_doc.roles}

    for role in roles_list:
        if role not in existing_roles:
            user_doc.append("roles", {"role": role})

    user_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "employee": employee_name,
        "user": user_id,
        "assigned_roles": roles_list,
        "warnings": warnings
    }
