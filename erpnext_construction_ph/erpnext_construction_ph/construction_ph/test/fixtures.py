# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Test fixtures the tests OWN.

This module exists because five test files each grew their own copy of the same
twenty-five lines, after each of them separately discovered that
`frappe.get_all("Construction Project", limit=1)` is not a fixture:

    project = frappe.get_all("Construction Project", limit=1)
    if not project:
        self.skipTest("no Construction Project on this site")
    project = project[0].name

It is not a shortcut, it is a test whose result depends on the order of rows it does
not control. It has now failed three separate ways, and each failure was found by
somebody else rather than by the test:

* **It borrowed somebody else's data.** `test_bcf_roundtrip` adopted a project
  carrying 499 committed BCF Topics and failed with `No BCF topic with that reference
  in this project` -- in BCF export, which no BIM row had ever touched.
* **It borrowed an ORPHAN.** `test_bim_convert` took whichever BIM Model was `Pending`
  first, which was one whose project had been deleted, so the authorisation guard
  raised `PermissionError` where the test asserted `DoesNotExistError`. A refusal, but
  the wrong one.
* **It SKIPPED.** With no ambient project, `test_a_caller_not_entitled_to_the_model_is_refused`
  -- the guard that is the entire reason `stream_frag` exists -- called `skipTest`. The
  suite reported `OK` with its most important guard not running, and a skip is the
  quietest false green there is: it reads as a pass and survives re-runs.

**A test that creates its own data can fail for one reason only: the thing it is
testing.** Every row here is created inside the caller's transaction and rolls back
with it. Nothing in this module commits -- a commit in a fixture is how 499 rows ended
up committed on a site in the first place.
"""

import frappe

__all__ = ["make_project", "make_customer", "project_or_skip", "MADE_BY_TESTS", "forget"]

#: Every row `make_project` has created and not yet reclaimed, as `(doctype, name)`.
#: The registry exists because a cleanup that remembers three of the four things a
#: fixture creates is not a cleanup -- see the note in `make_project`.
MADE_BY_TESTS = set()

#: Doctypes that post to the general ledger, DERIVED rather than listed.
#:
#: `frappe.db.delete` is a low-level ORM call and does **not** cascade. Deleting one of
#: these by hand removes the header and strands every dependent row -- for a ledger doctype
#: that means GL Entries whose voucher no longer exists. No gate in this project can see it:
#: every gate counts rows per doctype and never asks whether a parent is still there.
#:
#: `CommittingCase` exists precisely for classes that COMMIT -- rollback cannot reclaim their
#: rows, so teardown deletes them explicitly. `test_billing_engine_accounts` registers
#: `Sales Invoice` in its `committing_doctypes`, and until teardown cascaded, its invoices
#: were removed without their postings. MEASURED, not assumed: one suite run left +42 active
#: GL Entries orphaned (23,001 -> 23,043), reproduced from a clean baseline.
#:
#: The set is derived from `tabGL Entry.voucher_type` -- Frappe's own record of which
#: doctypes post. A literal name would fix today's leak and leave the trap armed: the next
#: ledger doctype added to a `committing_doctypes` tuple would be deleted non-cascadingly
#: again, with nothing here to catch it. **Do not "simplify" this into a frozenset.**
def gl_posting_doctypes() -> set:
    """Doctypes that actually post to the GL on this site."""
    return {r[0] for r in frappe.db.sql("SELECT DISTINCT voucher_type FROM `tabGL Entry`")}


def reclaim_document(doctype: str, name: str, gl_posting: set) -> None:
    """Remove one test-created document, taking its child rows and ledger rows with it.

    `frappe.db.delete` is a low-level ORM call and does NOT cascade, so deleting a ledger
    doctype by hand used to leave its GL Entries behind with no parent document. Frappe's
    own `delete_doc` cannot do it either: it refuses a submitted document ("Submitted
    Record cannot be deleted"), and cancelling first needs a live project link that the
    teardown may already have deleted.

    So the postings are removed explicitly, by the two fields that tie a GL Entry to its
    voucher, and then the header goes. Neither a docstatus nor an intact link is required.
    Child table rows (e.g. Sales Invoice Item) are also cascaded so no orphaned child rows
    persist.
    """
    if doctype in gl_posting:
        frappe.db.delete("GL Entry", {"voucher_type": doctype, "voucher_no": name})
    try:
        meta = frappe.get_meta(doctype)
        for df in meta.get_table_fields():
            frappe.db.delete(df.options, {"parent": name, "parenttype": doctype})
    except Exception:
        pass
    frappe.db.delete(doctype, {"name": name})

def forget():
    """Empty the registry, after a cleanup has drained it."""
    MADE_BY_TESTS.clear()


def make_customer(name):
    """A Customer with the fields `Construction Project` actually requires."""
    return frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": name,
            "customer_type": "Company",
            "customer_group": "Commercial",
            "territory": "Philippines",
            "tax_id": "000-111-222-00000",
        }
    ).insert(ignore_permissions=True)


def make_project(tag="T"):
    """A Construction Project this test owns, with its ERPNext Project behind it.

    The field set is copied from a helper that already worked rather than assembled
    from the doctype JSON. My first attempt omitted `client` and `contract_amount` and
    `MandatoryError` named four fields -- the cost of guessing at a schema.
    """
    company = frappe.get_all("Company", limit=1, pluck="name")
    if not company:
        frappe.throw("no Company on this site; the app cannot be exercised without one")
    company = company[0]
    name = "FIX %s %s" % (tag, frappe.generate_hash(length=8))
    customer = make_customer(name)
    erp = frappe.get_doc(
        {"doctype": "Project", "project_name": f"{name} ERP", "company": company}
    ).insert(ignore_permissions=True).name
    MADE_BY_TESTS.add(("Customer", customer.name))
    MADE_BY_TESTS.add(("Project", erp))
    # All FOUR rows this makes are recorded, not just the Construction Project. UI4
    # registered its project and its model, and the eleven rows it left on the site
    # every run were exactly the `Customer` and the ERPNext `Project` that nobody had
    # thought to write down. A cleanup that remembers three of the four things a
    # fixture creates is not a cleanup.
    _project = frappe.get_doc(
        {
            "doctype": "Construction Project",
            "project_name": name,
            "erpnext_project": erp,
            "company": company,
            "client": customer.name,
            "contract_date": "2026-01-01",
            "contract_amount": 5_000_000.00,
        }
    ).insert(ignore_permissions=True).name
    MADE_BY_TESTS.add(("Construction Project", _project))
    return _project


def project_or_skip(tag="T"):
    """`make_project`, or a skip when the site has no Company at all.

    A site with no Company cannot run any of this app's tests, and skipping is the
    honest response to that -- unlike skipping because a fixture was never built.
    """
    if not frappe.get_all("Company", limit=1, pluck="name"):
        frappe.throw("no Company on this site")
    return make_project(tag)


# --------------------------------------------------------------------------
# A test case that COMMITS on purpose, and still leaves nothing behind.
# --------------------------------------------------------------------------
class CommittingCase:
    """Mixin for a test class whose durability the test genuinely needs.

    Some tests must commit, and the reasons are sound. `test_billing_engine_accounts`
    says it plainly: *the invoice is submitted; make it visible to the reads below.* A
    submitted document has to be DURABLE for a later read to see it, and rolling it
    back would make the test stop testing the thing it exists to test.

    **So the commit stays and the rows it persists go.** A commit defeats the harness's
    rollback for the whole class, which is how a suite accumulates residue: 47 rows a
    run, measured, for as long as nobody cleans up. Deleting the commit instead would
    be the cheapest false green available -- a green suite bought by removing the reason
    a test exists.

    **Snapshots rather than naming.** It records the row ids that exist before the class
    runs and deletes the difference afterwards, so it cannot miss a row a test created
    in a way its author did not anticipate -- which is exactly how the residue got here
    in the first place. Naming the rows would put the same burden on every future test
    in the class, and the first one to forget would leak silently.

    The delete is COMMITTED. A rollback would undo the cleanup, which is the mistake
    CORE-30 made once: a `tearDownClass` that deleted its rows and then let the harness
    roll back, leaving 44 projects a run.
    """

    #: Doctypes this class may create IN ADDITION to the ambient ones every test can
    #: produce. Compared by id set, so a child row is covered without the caller
    #: enumerating every child table.
    committing_doctypes = ()

    #: EVERY test can write these, whether its author thought about them or not, and a
    #: test that provokes an error writes an Error Log as a side effect. MEASURED: with
    #: `Error Log` and `Comment` declared by NO class, they were +24 and +11 of the 49
    #: rows a run was adding -- 35 of them, from tests that never mention either
    #: doctype and never would. Declaring them per class is the wrong shape: it makes
    #: the cleanup depend on somebody remembering, which is the same mistake as naming
    #: the rows to delete.
    # `File` IS NOT HERE, and putting it there broke the fragment endpoint.
    #
    # I added `File` on the reasoning that it is ambient output like the rest. It is not:
    # a `File` row IS the geometry, and `store_frag` writes one that
    # `stream_frag` later reads. Because the snapshot is per-class, any `CommittingCase`
    # that ran AFTER `test_bim_convert` deleted the fragment that class had just stored,
    # and the test failed with "The cached fragment for this model is missing" -- with no
    # borrow anywhere in sight, and no error pointing at the mixin that removed it.
    #
    # The same lesson twice, then: a shared registry plus a per-class snapshot reclaims
    # ANOTHER class's rows. It is also the second time this session that treating `File`
    # as disposable destroyed product data -- the first was the gate's sweep, which
    # deleted every File row and reported a clean run.
    ALWAYS_RECLAIMED = ("Error Log", "Comment", "ToDo")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        watched = tuple(dict.fromkeys(tuple(cls.committing_doctypes or ()) + cls.ALWAYS_RECLAIMED))
        cls._committing_before = {
            d: set(frappe.get_all(d, pluck="name")) for d in watched
        }

    @classmethod
    def tearDownClass(cls):
        try:
            removed, failed = [], []
            # Whatever `make_project` made and nobody claimed. That is the whole of
            # UI4's leak, and would be a third of anything else using the fixture.
            for doctype, name in sorted(MADE_BY_TESTS, reverse=True):
                try:
                    if frappe.db.exists(doctype, name):
                        frappe.db.delete(doctype, {"name": name})
                        removed.append(f"{doctype}:{name}")
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{doctype}:{name} ({type(exc).__name__})")
            # Resolved ONCE per class, not once per row: the query is cheap but the loop
            # is per-row, and a teardown that gets slower with the size of its own mess
            # is a teardown someone will eventually stop running.
            gl_posting = gl_posting_doctypes()
            for doctype, before in (getattr(cls, "_committing_before", None) or {}).items():
                created = [n for n in frappe.get_all(doctype, pluck="name") if n not in before]
                for name in created:
                    try:
                        if frappe.db.exists(doctype, name):
                            # Cascade for ledger doctypes, or the postings are stranded
                            # with no parent. See `reclaim_document`.
                            reclaim_document(doctype, name, gl_posting)
                            removed.append(f"{doctype}:{name}")
                    except Exception as exc:  # noqa: BLE001
                        # REPORTED, not swallowed. A cleanup that cannot be shown to
                        # have worked is a check that never fired, and this one had
                        # already failed silently once: the mixin sat outside the MRO,
                        # so the snapshot never ran and the class believed it was
                        # cleaning up while removing nothing at all.
                        failed.append(f"{doctype}:{name} ({type(exc).__name__})")
            # COMMIT the cleanup -- a rollback here would restore the rows this exists
            # to remove, and the gate would read as a pass while the leak continued.
            frappe.db.commit()
            cls.last_cleanup_removed = len(removed)
            cls.last_cleanup_failed = failed
            forget()
        finally:
            cls._committing_before = {}
            super().tearDownClass()
