# erpnext_construction_ph/setup/install.py
"""
Automated Module Initiation Hooks for erpnext_construction_ph
Executed automatically by Frappe Bench during:
  - bench --site [sitename] install-app erpnext_construction_ph
  - bench --site [sitename] migrate
"""

import frappe
from erpnext_construction_ph.setup.roles_fixtures import (
    CONSTRUCTION_ROLES,
    setup_roles_and_permissions,
)
from erpnext_construction_ph.setup.workspaces_fixtures import setup_workspaces
from erpnext_construction_ph.setup.custom_fields import setup_custom_fields
from erpnext_construction_ph.setup.accounts import setup_construction_accounts

def after_install():
    """
    Master initialization hook executed when the module is first installed.
    Creates 10 construction roles, assigns granular Custom DocPerms,
    assembles Role Profiles, and deploys 10 role-tailored Workspaces.
    """
    frappe.log("Starting erpnext_construction_ph module initiation...")
    print("================================================================")
    print("Initiating Philippine Construction ERPNext Module (MVP)")
    print("================================================================")

    # 1. Setup Roles, Profiles, and Document Permissions
    print("Step 1/5: Installing Construction Roles & Permission Matrix...")
    setup_roles_and_permissions()

    # 2. Setup Custom Fields extending native ERPNext DocTypes
    print("Step 2/5: Provisioning Custom Fields for Project, Item, Supplier, PO, etc...")
    setup_custom_fields()

    # 3. Setup Construction Chart of Accounts
    print("Step 3/5: Provisioning Construction GL Accounts (Retention, CWT 2307)...")
    setup_construction_accounts()

    # 4. Setup Role-Specific Workspaces
    print("Step 4/5: Installing Role-Tailored Frappe Workspaces...")
    setup_workspaces()

    # 5. Setup Default Tax Withholding Categories (BIR WC120 & WI120)
    print("Step 5/5: Configuring BIR Tax Withholding Categories (WC120/WI120)...")
    setup_bir_withholding_categories()
    print("================================================================")
    print("erpnext_construction_ph initiation completed successfully!")
    print("================================================================")

def after_migrate():
    """
    Migration hook executed during `bench migrate`.
    Ensures that updated role matrices and workspace dashboards stay synchronized
    without overwriting user custom data.
    """
    print("Synchronizing Construction Roles, Custom Fields, Accounts, and Workspaces...")
    setup_roles_and_permissions()
    setup_custom_fields()
    setup_construction_accounts()
    setup_workspaces()
    setup_bir_withholding_categories()
    normalize_bcf_topic_guids()


EWT_ACCOUNT_NAME = "Expanded Withholding Tax Payable 1601-EQ"


def normalize_bcf_topic_guids():
    """Rewrite legacy 32-hex `topic_guid` values to the canonical hyphenated UUID.

    Called on every migrate, and idempotent: the patch only writes rows whose
    spelling actually changes, so a migrated site does no work on the next run.

    This exists because the BCF EXPORT cannot work without it. BCF 2.1 names a
    topic's folder after the topic's UUID, and `bcf/writer.py` refuses anything
    else, so every topic minted by an earlier revision -- which used
    `uuid4().hex` -- was unexportable. The same UUID written without hyphens is
    the same identifier, so restoring the canonical spelling is lossless.
    """
    from erpnext_construction_ph.patches.v0_1.normalize_bcf_topic_guids import (
        execute as run_patch,
    )

    try:
        changed, _unrecognised = run_patch()
    except Exception as error:  # noqa: BLE001
        # A migrate must not abort over a data-normalisation nicety; the export
        # reports the offending topic by name if this did not run.
        frappe.log_error(
            title="BCF topic GUID normalisation failed",
            message=frappe.get_traceback(),
        )
        print(f"BCF topic GUID normalisation skipped: {error}")

def setup_bir_withholding_categories():
    """Idempotently provisions BIR Withholding Tax Categories for Contractors.

    ERPNext requires a Tax Withholding Account row per Company plus a deduction
    basis, so each category is bound to the EWT payable account that
    setup_construction_accounts() creates for every company on the site.
    """
    # Creditable Withholding Tax (CWT) on prime contractors and subcontractors
    # (juridical entities) and on individual contractors / trade specialists,
    # both at 2%, per BIR RR 2-98 and BIR RR 11-2018.
    categories = [
        {
            "category_name": "WC120 - 2% Contractors (Corporate)",
            "tax_withholding_rate": 2.0,
            "atc_code": "WC120",
        },
        {
            "category_name": "WI120 - 2% Contractors (Individual)",
            "tax_withholding_rate": 2.0,
            "atc_code": "WI120",
        },
    ]

    accounts = []
    for company in frappe.get_all("Company", fields=["name"]):
        account = frappe.db.get_value(
            "Account",
            {"company": company["name"], "account_name": EWT_ACCOUNT_NAME},
            "name"
        )
        if account:
            accounts.append({"company": company["name"], "account": account})

    for cat in categories:
        existing = frappe.db.get_value(
            "Tax Withholding Category",
            cat["category_name"],
            ["name", "custom_atc_code"],
            as_dict=True,
        )
        if existing:
            # The category exists; keep its ATC in step with the fixture so an
            # older install that predates the field is corrected on migrate.
            if existing.get("custom_atc_code") != cat["atc_code"]:
                frappe.db.set_value(
                    "Tax Withholding Category", existing["name"], "custom_atc_code", cat["atc_code"]
                )
            continue
        if not accounts:
            print(f"Skipped Tax Withholding Category: {cat['category_name']} (no '{EWT_ACCOUNT_NAME}' account)")
            continue
        doc = frappe.get_doc({
            "doctype": "Tax Withholding Category",
            "name": cat["category_name"],
            "category_name": cat["category_name"],
            "round_off_tax_amount": 1,
            "tax_deduction_basis": "Gross Total",
            "custom_atc_code": cat["atc_code"],
            "rates": [
                {
                    "from_date": "2020-01-01",
                    "to_date": "2099-12-31",
                    "tax_withholding_rate": cat["tax_withholding_rate"],
                }
            ],
            "accounts": [dict(a) for a in accounts]
        })
        doc.insert(ignore_permissions=True)
        print(f"Created Tax Withholding Category: {cat['category_name']}")

    frappe.db.commit()


def before_uninstall():
    """
    Removes the roles and role profiles this app provisioned.

    Frappe's uninstall only drops the app's own DocTypes and modules. Roles and
    Role Profiles are standard doctypes, so without this hook the ten
    construction roles survive uninstall and keep dangling profile references.
    """
    frappe.flags.ignore_permissions = True

    # The fixture is the single source of truth; a "Construction%" prefix match
    # would silently miss any role that is not named that way. Intersect with
    # what is actually on the site — deleting a missing Role raises DoesNotExistError.
    construction_roles = [
        r["role_name"] for r in CONSTRUCTION_ROLES if frappe.db.exists("Role", r["role_name"])
    ]
    if not construction_roles:
        return

    for profile in frappe.get_all("Role Profile", pluck="name"):
        roles = [r.role for r in frappe.get_doc("Role Profile", profile).roles]
        if any(role in construction_roles for role in roles):
            frappe.delete_doc("Role Profile", profile, force=True, ignore_permissions=True)
            print(f"Removed Role Profile: {profile}")

    for role in construction_roles:
        frappe.delete_doc("Role", role, force=True, ignore_permissions=True)
        print(f"Removed Role: {role}")

    # No explicit commit: frappe.installer.remove_app() commits the uninstall
    # transaction itself, and committing here desynchronises the app registry.


def after_uninstall():
    """Drop this app's row from the `Installed Applications` Single.

    **Why this hook and not `before_uninstall`.** Frappe's `remove_app` runs, in
    order: the `before_uninstall` hooks, then `remove_from_installed_apps()`,
    then `Installed Applications.update_versions()`, then the commit, and only
    then the `after_uninstall` hooks. `update_versions()` rebuilds the Single
    from `get_installed_apps_info()`, which reads the `installed_apps` global --
    so anything a `before_uninstall` hook writes to that Single is rewritten
    moments later. `after_uninstall` is the first point after that rewrite.

    **Why it matters.** `bench list-apps` reads this Single FIRST and only falls
    back to `get_installed_apps()` when it is empty
    (`frappe/commands/site.py:621`). So while the stale row survives, `list-apps`
    reports an app that is not installed, and CORE-01's own gate negative --
    "uninstall the app; verify `list-apps` no longer shows it" -- cannot pass on
    a correct uninstall.

    **Committing here is correct, unlike in `before_uninstall`.** That hook runs
    INSIDE the uninstall transaction, and committing there desynchronised the
    app registry (see the CORE-01 journal). By the time this runs, `remove_app`
    has already committed, so this is a separate, ordinary write.
    """
    frappe.flags.ignore_permissions = True
    app = "erpnext_construction_ph"

    single = frappe.get_single("Installed Applications")
    if not single.installed_applications:
        return
    remaining = [row for row in single.installed_applications if row.app_name != app]
    if len(remaining) == len(single.installed_applications):
        return  # already gone; do not churn the document
    single.set("installed_applications", [])
    for row in remaining:
        single.append(
            "installed_applications",
            {
                "app_name": row.app_name,
                "app_version": row.app_version,
                "git_branch": row.git_branch,
                "has_setup_wizard": row.has_setup_wizard,
                "is_setup_complete": row.is_setup_complete,
                "disabled": row.disabled,
            },
        )
    single.save()
    frappe.db.commit()
    frappe.clear_cache()

