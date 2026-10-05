# erpnext_construction_ph/setup/accounts.py
"""
Chart of Accounts Provisioning for Philippine Construction
Provisions construction-specific General Ledger accounts across all registered Companies:
  - Account 11050: Prepaid Tax - Creditable Withholding Tax (BIR Form 2307)
  - Account 11060: Retention Receivable from Project Owners (Asset)
  - Account 21060: Retention Payable to Trade Subcontractors (Liability)
  - Account 21070: Expanded Withholding Tax Payable (BIR Form 1601-EQ)
  - Account 41000: Construction Contract Revenue - Progress Billings (Income)
"""

import frappe

CONSTRUCTION_ACCOUNTS = [
    {
        "account_name": "Prepaid Tax - Creditable Withholding Tax 2307",
        "parent_type": "Current Assets",
        "root_type": "Asset",
        "account_type": "Tax",
        "description": "2% CWT withheld by project owners documented on BIR Form 2307."
    },
    {
        "account_name": "Retention Receivable from Project Owners",
        "parent_type": "Current Assets",
        "root_type": "Asset",
        "account_type": "Current Asset",
        "description": "10% retention money held by clients until substantial completion and final acceptance."
    },
    {
        "account_name": "Retention Payable to Subcontractors",
        "parent_type": "Current Liabilities",
        "root_type": "Liability",
        "account_type": "Current Liability",
        "description": "10% retention money withheld from trade subcontractor progress claims."
    },
    {
        "account_name": "Expanded Withholding Tax Payable 1601-EQ",
        "parent_type": "Current Liabilities",
        "root_type": "Liability",
        "account_type": "Tax",
        "description": "2% EWT withheld from subcontractor billings under ATC WC120/WI120 for remittance to BIR."
    },
    {
        "account_name": "Construction Contract Revenue - Progress Billings",
        "parent_type": "Direct Income",
        "root_type": "Income",
        "account_type": "Income Account",
        "description": "Recognized revenue from certified CIAP 102 progress accomplishment."
    }
]

def setup_construction_accounts():
    """Provisions construction accounts for all companies in ERPNext"""
    frappe.flags.ignore_permissions = True
    companies = frappe.get_all("Company", fields=["name"])

    for comp in companies:
        company_name = comp["name"]
        for acc_spec in CONSTRUCTION_ACCOUNTS:
            # Account names are "<account_name> - <company_abbr>" (see
            # Account.autoname), not the full company name, so existence has to
            # be tested by field filters. A name-based check never matches and
            # makes this function non-idempotent.
            if not frappe.db.exists(
                "Account", {"account_name": acc_spec["account_name"], "company": company_name}
            ):
                # Find appropriate parent account under root_type
                parent_acc = frappe.db.get_value(
                    "Account",
                    {
                        "company": company_name,
                        "account_name": acc_spec["parent_type"],
                        "is_group": 1
                    },
                    "name"
                )
                
                # Fallback to root account if specific group not found
                if not parent_acc:
                    parent_acc = frappe.db.get_value(
                        "Account",
                        {
                            "company": company_name,
                            "root_type": acc_spec["root_type"],
                            "is_group": 1
                        },
                        "name"
                    )

                if parent_acc:
                    doc = frappe.get_doc({
                        "doctype": "Account",
                        "account_name": acc_spec["account_name"],
                        "company": company_name,
                        "parent_account": parent_acc,
                        "root_type": acc_spec["root_type"],
                        "account_type": acc_spec["account_type"],
                        "is_group": 0
                    })
                    doc.insert(ignore_permissions=True)
                    print(f"Created GL Account: {doc.name}")

    frappe.db.commit()
