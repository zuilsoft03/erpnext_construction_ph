# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-45 gate: the additive, `submitted_on`-derived 30-day term.

This row was split out of the original CORE-45 because the original conflated
*computing* a payment term with *reporting* one. Only the first is engineering.
The second is CORE-49, and it is BLOCKED on a compliance decision that no agent
may take, so nothing in this module may change what any document says.

**THE CLAUSE THIS ROW EXISTS FOR:** `Sales Invoice.due_date` stays exactly
`add_days(posting_date, 30)`. It is what every CIAP 7 document, the Form 2307
PDF, the EIS export and the portal render. The column built here is written to a
separately-named field that nothing reads, so building it is provably free.

**A TEST THAT PASSES WHEN THE TWO BASES AGREE PROVES NOTHING.** Every fixture in
this module back-dates `posting_date` by `BACKDATE_DAYS` while leaving the
submission stamp at today, so the two readings of the same 30-day clock are
`BACKDATE_DAYS` apart on purpose. A suite that only ever saw them coincide would
pass against a build that computed the new field from `posting_date` -- which is
the exact defect this row was cut out of the combined row to expose.
"""

import importlib.util
import os
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from erpnext_construction_ph.api import billing_engine

DOCTYPE = "Construction Progress Billing"
INVOICE = "Sales Invoice"

#: The name the additive term is stored under. Deliberately NOT `due_date`: it is
#: a different reading of the same clock and a name that could be mistaken for the
#: reported one is how a compliance column gets flipped by accident.
ADDITIVE_FIELD = "custom_ciap_due_date_submission_basis"

#: How far `posting_date` is pushed into the past. Large enough that the two
#: readings can never coincide by accident, small enough to stay in period.
BACKDATE_DAYS = 40

#: Where `check.sh` leaves the byte-identical copy of the PRE-CORE-45 engine
#: (extracted from git, not retyped) so the "before" arm compares against the
#: implementation that actually shipped rather than a paraphrase of it. The repo
#: is not mounted into the container, so the gate stages it inside the bench and
#: the env var exists for anyone running this module outside `check.sh`.
BASELINE_ENGINE = "/home/frappe/frappe-bench/sites/_core45_baseline/billing_engine_baseline.py"


def _baseline_candidates():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    candidates = [os.environ.get("CORE45_BASELINE_ENGINE"), BASELINE_ENGINE,
                  os.path.join(repo, "ops", "tmp-core45", "billing_engine_baseline.py")]
    return [c for c in candidates if c]


def _load_baseline_engine():
    """Import the pre-CORE-45 `on_progress_billing_submit` from the extracted file.

    Returns None when no baseline is found. The caller then FAILS rather than
    skipping: a byte-identity comparison that did not run must not be allowed to
    read as a byte-identity comparison that passed (CORE-48's lesson).
    """
    for path in _baseline_candidates():
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("core45_baseline_engine", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


class TestCore45AdditiveTerm(IntegrationTestCase):
    """The new term is computed, stored, and reported to nobody."""

    # ---- the column exists on the live table (clause 6) -------------------

    def test_the_additive_column_exists_on_the_live_table(self):
        """`SHOW COLUMNS`, not an assumption about a migrate having been run.

        The whole row is a schema change. A schema change that exists only in
        Python fails the first time a real invoice is created, which is the worst
        moment to find out, so the column is asserted directly.
        """
        columns = {c[0]: c[1] for c in frappe.db.sql("SHOW COLUMNS FROM `tab%s`" % INVOICE)}
        self.assertIn(ADDITIVE_FIELD, columns)
        self.assertEqual(columns[ADDITIVE_FIELD], "date")
        self.assertTrue(frappe.get_meta(INVOICE).get_field(ADDITIVE_FIELD))

    def test_the_additive_field_is_not_the_reported_due_date(self):
        """Two columns, two names. One that can be mistaken for the other is a defect.

        The requirement is that nobody can read the new field as the reported due
        date, and the cheapest way to honour that is a name that cannot be:
        `custom_ciap_due_date_submission_basis` names both the standard it serves
        and the basis it is measured from.
        """
        meta = frappe.get_meta(INVOICE)
        self.assertTrue(meta.get_field("due_date"))
        self.assertNotEqual(ADDITIVE_FIELD, "due_date")
        self.assertNotEqual(meta.get_field("due_date").label, meta.get_field(ADDITIVE_FIELD).label)
        self.assertIn("submission", meta.get_field(ADDITIVE_FIELD).fieldname)

    # ---- the discriminating fixture (clause 4) ---------------------------

    def test_the_new_term_runs_from_submission_not_from_posting(self):
        """**The clause the row was split out for.**

        `posting_date` is back-dated 40 days; the billing is submitted now. The
        reported `due_date` must stay on the back-dated basis and the new field
        must carry the submission basis -- 40 days apart, both correct, neither
        borrowed from the other.
        """
        billing = self._submit_backdated_billing()
        invoice = self._invoice_for(billing)

        submitted_on = getdate(billing.submitted_on)
        posting_date = getdate(invoice.posting_date)

        # The premise, asserted: the two bases really are far apart.
        self.assertEqual((submitted_on - posting_date).days, BACKDATE_DAYS)

        # The reported term, unchanged and still on `posting_date`.
        self.assertEqual(str(invoice.due_date), str(add_days(posting_date, 30)))

        # The new term, on `submitted_on` -- never on `posting_date`.
        self.assertEqual(
            str(getdate(invoice.get(ADDITIVE_FIELD))),
            str(add_days(submitted_on, 30)),
            "the additive term must be submitted_on + 30",
        )
        self.assertNotEqual(str(invoice.get(ADDITIVE_FIELD)), str(invoice.due_date))

    def test_the_additive_term_survives_the_round_trip_to_the_database(self):
        """Stored, not merely held on an in-memory object.

        `getdate()` on a value that came back from the database is the only way
        to catch a field that is assigned on the document and then silently
        dropped -- which is what an undeclared column does.
        """
        billing = self._submit_backdated_billing()
        invoice = self._invoice_for(billing)

        stored = frappe.db.get_value(INVOICE, invoice.name, ADDITIVE_FIELD)
        self.assertIsNotNone(stored, "the additive column was not persisted")
        self.assertEqual(str(getdate(stored)), str(add_days(getdate(billing.submitted_on), 30)))

    def test_the_additive_term_is_populated_for_a_freshly_submitted_billing(self):
        """Clause 6's second half: populated, on the live row, by the real hook."""
        billing = self._submit_backdated_billing()
        row = frappe.db.sql(
            "SELECT `%s` FROM `tab%s` WHERE name = %%s" % (ADDITIVE_FIELD, INVOICE),
            (self._invoice_for(billing).name,),
            as_dict=True,
        )[0]
        self.assertEqual(str(getdate(row[ADDITIVE_FIELD])), str(add_days(getdate(billing.submitted_on), 30)))

    # ---- the reported term is byte-identical (clause 2) -------------------

    def test_the_reported_due_date_is_byte_identical_to_the_pre_core45_engine(self):
        """**The clause this row exists for: nothing reported may change.**

        The PRE-CORE-45 `on_progress_billing_submit` -- extracted from git by
        `check.sh`, not retyped -- runs against an identical second fixture, and
        the two reported `due_date` values are compared as bytes. Comparing
        `str()` values would let a type change through; comparing bytes of the
        same field on the same fixture cannot.
        """
        baseline = _load_baseline_engine()
        # FAIL CLOSED, do not skip. A comparison that did not run must not read as
        # a comparison that passed -- that is the one false green this project has
        # already paid for (CORE-48). `check.sh` stages the baseline before it runs
        # this module, so an absent baseline here means the gate was not run the
        # way it is meant to be run.
        self.assertIsNotNone(
            baseline,
            "the pre-CORE-45 baseline engine is absent, so the byte-identity arm did not run",
        )

        after_invoice = self._invoice_for(self._submit_backdated_billing())

        # Same fixture, same submission stamp, the OLD engine instead of the new one.
        before_billing = self._draft_billing()
        before_billing.run_method("before_submit")
        baseline.on_progress_billing_submit(before_billing)
        before_invoice = self._invoice_for(before_billing)

        # Printed, not merely asserted: the evidence for "byte-identical" is the
        # two values side by side, and an assertion that only reports a failure
        # leaves nobody able to see what was compared.
        print(
            "\n[CORE-45 clause 2] reported due_date  BEFORE (pre-CORE-45 engine): %r"
            % str(before_invoice.due_date)
        )
        print(
            "[CORE-45 clause 2] reported due_date  AFTER  (shipping engine):     %r"
            % str(after_invoice.due_date)
        )
        print(
            "[CORE-45 clause 2] additive term (never reported):                  %r"
            % str(after_invoice.get(ADDITIVE_FIELD))
        )
        print(
            "[CORE-45 clause 2] bases differ by %s days"
            % (
                getdate(after_invoice.get(ADDITIVE_FIELD))
                - getdate(after_invoice.due_date)
            ).days
        )

        self.assertEqual(
            before_invoice.due_date,
            after_invoice.due_date,
            "the REPORTED due_date moved: CORE-45 must not change what is reported",
        )
        self.assertEqual(
            str(before_invoice.due_date).encode(),
            str(after_invoice.due_date).encode(),
            "the reported due_date must be byte-identical before and after",
        )
        # The old engine had no such column, so this is also the additive proof:
        # the new field is new, not repurposed.
        self.assertFalse(
            frappe.db.get_value(INVOICE, before_invoice.name, ADDITIVE_FIELD),
            "the pre-CORE-45 engine cannot have written the additive term",
        )
        self.assertTrue(frappe.db.get_value(INVOICE, after_invoice.name, ADDITIVE_FIELD))

    def test_the_reported_term_is_still_computed_from_posting_date_in_source(self):
        """`inv.due_date` is one line, and it is load-bearing for every document.

        Reading the source is not a substitute for the behaviour assertions
        above; it is the cheapest possible tripwire against somebody "fixing" the
        line while fixing something else. The additive term must be written to a
        different attribute.
        """

        path = billing_engine.__file__
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()

        self.assertIn(
            "inv.due_date = frappe.utils.add_days(inv.posting_date, 30)",
            text,
            "the reported due_date line must remain exactly posting_date + 30",
        )
        self.assertIn("inv.%s = frappe.utils.add_days(" % ADDITIVE_FIELD, text)
        self.assertNotIn("inv.due_date = frappe.utils.add_days(inv.posting_date, 30) or", text)

    # ---- additive only (clause 3) ----------------------------------------

    def test_the_additive_term_appears_in_no_render_path(self):
        """The field is reported to nobody: no template, PDF, email or export reads it.

        The scan covers every file the app ships under `templates/`, `www/`,
        `construction_ph/notification/`, the print-format and export modules, and
        the client-facing `website/` surfaces. The engine and this module are the
        only places allowed to name the column.
        """
        offenders = scan_render_paths()
        self.assertEqual(
            offenders,
            [],
            "the additive term leaked into a render path: %r" % (offenders,),
        )

    # ---- fixtures --------------------------------------------------------

    def _submit_backdated_billing(self):
        """A billing whose `posting_date` is 40 days before it is submitted.

        Submitted through the real hook (`doc.submit()`), so the invoice under
        test is the one production creates -- not one a test wired up itself.
        """
        billing = self._draft_billing()
        billing.submit()
        self.assertTrue(billing.submitted_on, "submission must stamp the clock")
        return billing

    def _draft_billing(self):
        company = frappe.get_all("Company", limit=1, pluck="name")
        self.assertTrue(company, "gate site needs a Company (see D-012)")
        company = company[0]
        suffix = uuid.uuid4().hex[:8]

        project_name = "PH CORE-45 project"
        project = frappe.get_all("Project", filters={"project_name": project_name}, pluck="name")
        if project:
            project = project[0]
        else:
            project = frappe.get_doc(
                {"doctype": "Project", "project_name": project_name, "company": company}
            ).insert(ignore_permissions=True).name

        customer_name = "PH CORE-45 Customer"
        if not frappe.db.exists("Customer", customer_name):
            frappe.get_doc(
                {
                    "doctype": "Customer",
                    "customer_name": customer_name,
                    "customer_type": "Company",
                    "customer_group": "Commercial",
                    "territory": "Philippines",
                }
            ).insert(ignore_permissions=True)

        return frappe.get_doc(
            {
                "doctype": DOCTYPE,
                "billing_number": "PB-CORE45-" + suffix,
                "project": project,
                "company": company,
                "client": customer_name,
                # The 40-day gap. Everything this module proves rests on this line.
                "posting_date": add_days(nowdate(), -BACKDATE_DAYS),
                "billing_period_start": add_days(nowdate(), -BACKDATE_DAYS - 5),
                "billing_period_end": add_days(nowdate(), -BACKDATE_DAYS),
                "contract_amount": 1000000.00,
                "period_accomplishment_percent": 10.0,
                "mobilization_recoupment": 0.0,
                "cumulative_accomplishment": 100000.00,
            }
        ).insert(ignore_permissions=True)

    @staticmethod
    def _invoice_for(billing):
        """The Sales Invoice the engine created for this billing.

        Found by the billing number the engine writes into `remarks`, which is
        deterministic, rather than by "the newest invoice", which is a guess that
        can pick up a neighbour's.
        """
        number = getattr(billing, "billing_number", None) or billing.name
        rows = frappe.get_all(INVOICE, filters={"remarks": ["like", f"%{number}%"]}, pluck="name")
        assert rows, "no Sales Invoice was created referencing %s" % number
        return frappe.get_doc(INVOICE, rows[0])


#: Where the additive term is allowed to be named: the engine that writes it, the
#: setup that declares it, and this gate. Anything else naming it is a leak.
ALLOWED_MENTIONS = ("api/billing_engine.py", "setup/custom_fields.py", "test_core45_additive_term.py")


def render_path_files(app_root):
    """Every shipped file that can put a value in front of a human or an auditor.

    Deliberately broad: the standard is "cannot be read anywhere a person or a
    regulator could see it", and a file list that is merely broad is the cheap way
    to honour it. Templates, print formats, notifications, the www portal, the
    export/PDF/docx engines, and the client-facing `website/` surfaces.
    """
    # Everything under these roots is, by definition, something a person or an
    # auditor can be shown. The suffixes are therefore not a filter -- a `.js`
    # portal bundle renders a due date exactly as a template does.
    roots = (
        "erpnext_construction_ph/www",
        "erpnext_construction_ph/public",
        "erpnext_construction_ph/templates",
        "erpnext_construction_ph/construction_ph/notification",
    )
    files = []
    for rel in roots:
        base = os.path.join(app_root, rel)
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if name.endswith((".pyc", ".map")):
                    continue
                files.append(os.path.join(dirpath, name))

    # The renderers and exporters, by module name, because none of these files is
    # called a "render" and every one of them puts figures on paper or on a
    # screen: the EIS export, the Form 2307 PDF, the e-book register, the DOCX
    # certification, the portal feed, the SAWT reconciliation, the BCF XML package.
    render_modules = (
        "eis_export.py",
        "form_2307.py",
        "ebook_export.py",
        "docx_engine.py",
        "vo_docgen.py",
        "portal_data.py",
        "sawt_reconciliation.py",
        "bcf_package.py",
        "earned_value.py",
        "dole_cshp.py",
    )
    api_dir = os.path.join(app_root, "erpnext_construction_ph/api")
    for name in render_modules:
        candidate = os.path.join(api_dir, name)
        if os.path.exists(candidate):
            files.append(candidate)
    return sorted(set(files))


def scan_render_paths(app_root=None):
    """Return every render path that names the additive term.

    Prints the scan result rather than only asserting it, because the useful
    output of this check is the list of files that were looked at -- an empty
    offender list from a scan that read nothing looks identical to one that read
    everything.
    """
    if app_root is None:
        app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    files = render_path_files(app_root)
    offenders = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue
        rel = os.path.relpath(path, app_root).replace("\\", "/")
        if any(rel.endswith(allowed) for allowed in ALLOWED_MENTIONS):
            continue
        if ADDITIVE_FIELD in text or "submission_basis" in text:
            offenders.append(rel)

    print("\n[CORE-45 clause 3] render paths scanned: %d" % len(files))
    print("[CORE-45 clause 3] files naming the additive term: %d" % len(offenders))
    for rel in offenders:
        print("[CORE-45 clause 3]   LEAK: %s" % rel)
    print("[CORE-45 clause 3] files scanned:")
    for path in files:
        print("[CORE-45 clause 3]   %s" % os.path.relpath(path, app_root).replace("\\", "/"))
    return offenders