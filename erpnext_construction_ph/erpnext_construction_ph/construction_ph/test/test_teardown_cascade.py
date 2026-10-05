"""The teardown must not strand ledger rows.

`CommittingCase` exists for test classes that COMMIT: rollback cannot reclaim their rows, so
`tearDownClass` deletes them explicitly. It used `frappe.db.delete`, which is a low-level ORM
call and does **not** cascade. `test_billing_engine_accounts` registers `Sales Invoice` in its
`committing_doctypes`, so every invoice it created was removed while its GL Entries survived
with no parent document.

MEASURED: one full suite run left +42 active GL Entries orphaned, reproducibly.

Two wrong fixes were tried before the right one, and both looked correct on the orphan count
alone -- which is why this test asserts the SECOND observable as well:

1. `frappe.delete_doc(..., force=1)` refuses a submitted document. The teardown swallowed the
   ValidationError, so it deleted nothing: the orphan count went flat and read as success
   while Sales Invoice rows grew +14/run instead of +4.
2. Cancelling first needs a live project link, which the teardown's earlier loop has already
   deleted. It failed with "Project PROJ-4978 not found".

So a green orphan count is not by itself evidence of a correct fix. `test_invoices_are_not_
leaked` covers the half that fix 1 broke.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.construction_ph.test.fixtures import (
    gl_posting_doctypes,
    reclaim_document,
)


class TestTeardownCascade(IntegrationTestCase):
    """A ledger doctype removed by the teardown must take its postings with it."""

    def _gl_rows(self, voucher_no):
        return frappe.db.sql(
            "SELECT name FROM `tabGL Entry` WHERE voucher_no = %s", (voucher_no,)
        )

    def _make_submitted_invoice(self):
        """A submitted Sales Invoice, so there are GL rows that could be stranded."""
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cost_center = frappe.get_all(
            "Cost Center", filters={"company": company, "is_group": 0}, limit=1, pluck="name"
        )[0]
        si = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "customer": customer,
                "company": company,
                "posting_date": frappe.utils.nowdate(),
                "due_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
                "items": [
                    {
                        "item_code": frappe.get_all("Item", limit=1, pluck="name")[0],
                        "qty": 1,
                        "rate": 1000,
                        "cost_center": cost_center,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        si.submit()
        return si

    def test_sales_invoice_is_derived_as_ledger_posting(self):
        """The set is derived from the ledger, so it cannot miss a posting doctype."""
        self.assertIn(
            "Sales Invoice",
            gl_posting_doctypes(),
            msg="Sales Invoice must be derived as ledger-posting; if this fails the "
            "cascade branch is never taken for it",
        )

    def test_ambient_doctypes_keep_the_fast_path(self):
        """The non-cascading path must survive for doctypes nothing depends on.

        `delete_doc` on every Error Log would make teardown slower for no benefit, and the
        point of the branch is that it is narrow.
        """
        for ambient in ("Error Log", "Comment", "ToDo"):
            self.assertNotIn(
                ambient,
                gl_posting_doctypes(),
                msg=f"{ambient} is ambient and has no ledger children; it must not be "
                "routed through the cascading path",
            )

    def test_teardown_deletion_leaves_no_orphan_gl_rows(self):
        """The invariant: after reclamation, no GL Entry may point at a dead voucher."""
        si = self._make_submitted_invoice()
        name = si.name

        # Must have actually posted, or the assertion below would pass vacuously.
        self.assertTrue(
            self._gl_rows(name),
            msg="fixture did not post; the assertion after reclamation is meaningless",
        )

        # Exactly what `CommittingCase.tearDownClass` calls.
        reclaim_document("Sales Invoice", name, gl_posting_doctypes())

        self.assertFalse(
            frappe.db.exists("Sales Invoice", name),
            msg="the invoice should be gone",
        )
        self.assertFalse(
            self._gl_rows(name),
            msg="teardown deleted the invoice non-cascadingly and stranded its GL rows",
        )

    def test_invoices_are_not_leaked(self):
        """Reclamation must actually remove the invoice, not merely stop stranding rows.

        This is the half that `frappe.delete_doc(force=1)` broke: it refused the submitted
        document, the error was swallowed, nothing was deleted, and the orphan count went
        flat -- which read as a fix.
        """
        si = self._make_submitted_invoice()
        name = si.name

        reclaim_document("Sales Invoice", name, gl_posting_doctypes())

        self.assertFalse(
            frappe.db.exists("Sales Invoice", name),
            msg="reclaim_document did not remove the invoice; the ledger rows were deleted "
            "and the document left behind, which is a worse leak than the original",
        )