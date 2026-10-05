# erpnext_construction_ph/api/billing_engine.py
"""
CIAP Document 102 Billing Engine & ERPNext Sales Invoice Integration
Generates official ERPNext Sales Invoices and General Ledger entries from Progress Billings:
  - Deducts pro-rata mobilization advance recoupment
  - Deducts 10% retention money (stopped at 50% milestone)
  - Adds 12% Output VAT
  - Records 2% Creditable Withholding Tax (BIR Form 2307 / ATC WC120)
"""

import frappe
from frappe import _

def on_progress_billing_submit(doc, method=None):
    """
    Triggered when Construction Progress Billing is submitted.
    Creates an ERPNext Sales Invoice with automatic GL account mapping.
    """
    proj = frappe.get_doc("Project", doc.project) if frappe.db.exists("Project", doc.project) else None
    
    # 1. Fetch Company & Accounts
    company = doc.company or (proj.company if proj else frappe.defaults.get_user_default("Company"))
    
    income_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Construction Contract Revenue - Progress Billings"},
        "name"
    ) or frappe.db.get_value("Company", company, "default_income_account")

    retention_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Retention Receivable from Project Owners"},
        "name"
    )

    cwt_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": "Prepaid Tax - Creditable Withholding Tax 2307"},
        "name"
    )

    # 2. Build Sales Invoice
    inv = frappe.new_doc("Sales Invoice")
    inv.customer = doc.client
    inv.company = company
    inv.project = doc.project
    inv.posting_date = doc.posting_date or frappe.utils.today()
    # ERPNext overwrites posting_date with *today* unless set_posting_time is set
    # (erpnext/utilities/transaction_base.py:34). Without this a backdated CIAP
    # claim is silently filed in the wrong SAWT period and its 30-day payment
    # clock starts on the day the invoice happened to be created, not the billing
    # date. That is a compliance defect, not a cosmetic one.
    inv.set_posting_time = 1
    inv.posting_time = frappe.utils.nowtime()
    inv.due_date = frappe.utils.add_days(inv.posting_date, 30) # 30-day CIAP 102 term
    # CORE-45 (ADDITIVE HALF ONLY -- CORE-49 owns the switch that is BLOCKED on a
    # compliance decision). The reported term on the line above stays on
    # `posting_date` and must not move: it is what every CIAP 7 document, PDF,
    # email and export renders. What is computed HERE is the other reading of the
    # same 30-day clock -- the one measured from when the billing was actually
    # submitted rather than from the period it claims -- and it is written to a
    # separately-named column that nothing reads. Deriving it is engineering;
    # reporting it is a decision a person has not made.
    #
    # Read from `submitted_on`, never `posting_date`: `submitted_on` is stamped
    # in `before_submit` and guarded immutable after (CORE-07). `posting_date` is
    # caller-supplied and routinely back-dated, which is exactly the divergence
    # the two bases exist to expose.
    if doc.submitted_on:
        inv.custom_ciap_due_date_submission_basis = frappe.utils.add_days(
            frappe.utils.getdate(doc.submitted_on), 30
        )
    inv.remarks = f"CIAP Document 102 Progress Claim #{doc.billing_number or doc.name} for {doc.project}."

    # Item row for Net Progress Billing Accomplishment
    taxable_accomplishment = (doc.gross_accomplishment_period or 0.0) - (doc.mobilization_recoupment or 0.0)
    
    inv.append("items", {
        "item_name": f"Progress Accomplishment: {doc.project}",
        "description": f"Accomplishment Period {doc.billing_period_start or ''} to {doc.billing_period_end or ''}",
        "qty": 1,
        "rate": taxable_accomplishment,
        "income_account": income_account
    })

    # Taxes and Charges (12% Output VAT)
    if doc.vat_amount and doc.vat_amount > 0:
        vat_account = output_vat_account(company)
        if vat_account:
            inv.append("taxes", {
                "charge_type": "On Net Total",
                "account_head": vat_account,
                "description": "Output Value Added Tax (12%)",
                "rate": 12.0,
                "tax_amount": doc.vat_amount
            })
        else:
            # ARCHITECTURE.md §Failure Behavior Table: an unresolvable account
            # must not abort the billing. The invoice is still created without the
            # VAT row and the omission is logged for the accountant to fix.
            frappe.log_error(
                title="Construction: output VAT account not found",
                message=(
                    "No output VAT account for {0}; Sales Invoice {1} was created "
                    "without its 12% VAT row. Fix the account mapping before filing."
                ).format(company, inv.name)
            )

    # Deductions: Retention Money & 2% EWT
    # In ERPNext, deductions can be recorded as negative tax rows or payment deductions
    if doc.retention_amount and doc.retention_amount > 0 and retention_account:
        inv.append("taxes", {
            "charge_type": "Actual",
            "account_head": retention_account,
            "description": "Less: 10% Retention Withheld (CIAP 102)",
            "tax_amount": -abs(doc.retention_amount)
        })

    if doc.cwt_withheld and doc.cwt_withheld > 0 and cwt_account:
        # The category is stamped on the row so the EIS exporter can resolve the
        # deduction's ATC. Without it the row is only identifiable by its account,
        # and the account (11050 CWT asset) is not the one the Tax Withholding
        # Category is bound to (21070 EWT payable).
        cwt_category = _cwt_category_for(doc.client)
        inv.append("taxes", {
            "charge_type": "Actual",
            "account_head": cwt_account,
            "description": "Less: 2% Creditable Withholding Tax (BIR 2307)",
            "tax_amount": -abs(doc.cwt_withheld),
            "custom_tax_withholding_category": cwt_category,
        })

    inv.insert(ignore_permissions=True)
    inv.submit()

    # Link back to progress billing document
    frappe.db.set_value("Construction Progress Billing", doc.name, "sales_invoice", inv.name)
    frappe.msgprint(_("Created and submitted Sales Invoice {0}").format(inv.name), indicator="green")


def output_vat_account(company):
    """Return the company's output VAT account, or None if it cannot be resolved.

    ERPNext v17's Company doctype has no `default_tax_account` field -- only
    `tax_id` -- so the account has to be found in the chart. Look for an account
    named "Output Tax" first (used by some CoA templates), then a VAT-named tax
    liability under "Duties and Taxes", which is what the standard template
    creates. Never construct the name by hand: accounts are "<name> - <abbr>".
    """
    for account_name in ("Output Tax", "VAT", "Output VAT"):
        name = frappe.db.get_value(
            "Account", {"account_name": account_name, "company": company}, "name"
        )
        if name:
            return name

    duties = frappe.db.get_value(
        "Account", {"account_name": "Duties and Taxes", "company": company}, "name"
    )
    if duties:
        candidates = frappe.get_all(
            "Account",
            filters={"parent_account": duties, "account_type": "Tax"},
            pluck="name",
        )
        if len(candidates) == 1:
            return candidates[0]
    return None


def _cwt_category_for(client):
    """WC120 for a juridical client, WI120 for an individual one.

    Falls back to WC120 (the corporate rate) when the client carries no tax
    withholding category, which is the conservative default: an over-withheld
    amount is creditable, a under-withheld one is a penalty.
    """
    category = frappe.db.get_value("Customer", client, "tax_withholding_category")
    if category and frappe.db.exists("Tax Withholding Category", category):
        return category
    return "WC120 - 2% Contractors (Corporate)"
