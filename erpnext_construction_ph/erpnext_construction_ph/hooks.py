# erpnext_construction_ph/hooks.py
"""
Frappe App Configuration Hooks for erpnext_construction_ph
"""

# ONE LINE, run once before the suite, and it removes 17 of the 19 Error Logs a run
# leaves behind. See api/test_bootstrap.py for the measurement and for the two earlier
# shapes that both failed -- per-class declaration, and an app-owned base class whose
# teardown committed before the harness rollback and pushed the leak from +31 to +123.
before_tests = "erpnext_construction_ph.api.test_bootstrap.before_tests"

app_name = "erpnext_construction_ph"
app_title = "Construction PH"
app_publisher = "Philippine AEC Engineering"
app_description = "Philippine Construction ERPNext Custom App with BIM, Multi-Takeoff, CIAP 102, and BIR 2307 Compliance"
app_icon = "octicon octicon-tools"
app_color = "orange"
app_email = "engineering@construction.ph"
app_license = "AGPL-3.0"

# Module Initiation Lifecycle Hooks
after_install = "erpnext_construction_ph.setup.install.after_install"
after_migrate = "erpnext_construction_ph.setup.install.after_migrate"
before_uninstall = "erpnext_construction_ph.setup.install.before_uninstall"
after_uninstall = "erpnext_construction_ph.setup.install.after_uninstall"

# Include custom assets in Frappe Desk
app_include_js = [
    "/assets/erpnext_construction_ph/js/bim/bim_viewer.js",
    # Desk Page scripts. WITHOUT THESE, EVERY PAGE IN THIS APP IS DEAD IN THE
    # BROWSER, and nothing reports it.
    #
    # `assets/erpnext_construction_ph` is a symlink to the app's `public/`, so
    # only what is under `public/` can be served. A Desk Page's own script lives at
    # `construction_ph/page/<name>/<name>.js` -- OUTSIDE `public/` -- so Frappe
    # cannot serve it, and since this Frappe's `Page` doctype has no `content` /
    # `script` / `style` fields (D-039), nothing else injects it either. Measured
    # 2026-09-29: with an authenticated Administrator session, `/app/site_log` and
    # `/app/bim-workspace` both loaded, both showed `frappe.pages` EMPTY, and both
    # rendered a blank page with ZERO console errors. A blank page with no error is
    # the worst shape of failure: it looks like a layout bug, not a missing script.
    #
    # `public/js/pages/*.js` are COPIES of the page scripts, synced by
    # `ops/sync_page_scripts.py`, which also fails if a copy has drifted. A copy
    # rather than a symlink because Windows cannot follow relative symlinks without
    # elevated privileges, and the target machines are Windows.
    "/assets/erpnext_construction_ph/js/pages/bim_workspace.js",
    "/assets/erpnext_construction_ph/js/pages/site_log.js",
    "/assets/erpnext_construction_ph/js/pages/doc_editor.js",
    "/assets/erpnext_construction_ph/js/pages/takeoff_studio.js",
    "/assets/erpnext_construction_ph/js/pages/boq_worksheet.js",
    # tiptap.js defines window.ConstructionEditor. Without it the doc_editor page
    # mounts no editor and its round-trip silently reports success on an empty
    # body -- a worse failure than a blank page, because it looks like it worked.
    #
    # LISTED FIRST: tiptap.bundle.js puts a real `window.TipTap` on the page.
    # Without it `mount()` returns null, so doc_editor shows no editor and its
    # round-trip silently reports success on an empty body -- a worse failure than
    # a blank page, because it looks like it worked. Order matters: the bundle
    # must exist before tiptap.js runs.
    #
    # Built by `ops/tmp-ph1-10/build.sh` from THIS app's own node_modules
    # (@tiptap/core 2.27.3, StarterKit 2.27.3), not from the 3.26.0/3.27.x copies
    # other apps in this bench carry. The build input is
    # `erpnext_construction_ph/tools/tiptap_bundle_entry.js` -- under `tools/`, not
    # `public/`, because everything in `public/` is served and that file's bare
    # specifiers mean nothing to a browser.
    #
    # The bundle is MINIFIED: 724,111 -> 321,168 bytes. This list loads on every Desk
    # page, so the size is a per-page-load cost for every user of the app, not a
    # one-off. `.gitattributes` marks it `-text` so git cannot rewrite its bytes.
    #
    # PROVENANCE, because the repo ships no package.json to say where 2.27.3 came
    # from. The build emits an esbuild `--metafile` to
    # `erpnext_construction_ph/tools/tiptap.meta.json`, committed alongside the
    # bundle, and `test_tiptap_bundle.py` holds it to account: all 32 `@tiptap`
    # inputs must resolve under `apps/erpnext_construction_ph/`, and the bundle's
    # byte count must match the metafile or the record is stale. That test was
    # verified to fail on a foreign input, on an edited bundle, and on an
    # unminified one. A rebuild from a fresh clone still needs the dependency
    # installed at 2.27.3 first; that is recorded on the PH1-10 row.
    #
    # PH1-10 found the cause of the original failure, and it was never a bundler
    # problem: `mount()` called `new T.Editor(element, config)`, the TipTap **v1**
    # two-argument signature. From v2 on the constructor is `constructor(options =
    # {})` and never reads a second argument, so `config` was silently discarded,
    # `this.options.extensions` fell back to its `[]` default, and
    # createExtensionManager() built a schema with no `doc` node. mount() now passes
    # ONE options object: `new T.Editor(Object.assign({ element: element }, config))`.
    # Proved both ways: the fixed call reaches the constructor as argc=1 with 3
    # extensions; the v1 call as argc=2 with 0. EVIDENCE: ops/tmp-ph1-10/check.txt
    #
    # Two earlier leads on that row were wrong, and are recorded so they are not
    # repeated. (1) "TipTap ships prebuilt index.umd.js dists" -- FALSE: there is no
    # .umd file anywhere in @tiptap for this install; dist/ holds index.cjs and an
    # ESM index.js only. (2) bare specifier imports -- TRUE but a red herring; every
    # dist is bare-specifier ESM, and esbuild resolves those normally.
    "/assets/erpnext_construction_ph/js/editor/tiptap.bundle.js",
    "/assets/erpnext_construction_ph/js/editor/tiptap.js"
]

app_include_css = [
    "/assets/erpnext_construction_ph/css/construction_desk.css",
    # Layout for the BIM workspace three-column viewer (D-043). CSS is safe to
    # include app-wide -- unlike geometry.js, which is an ES module and must NOT go
    # in app_include_js, or every Desk page raises a SyntaxError on `export`.
    "/assets/erpnext_construction_ph/css/bim_workspace.css",
]

# Standard DocType Event Hooks
doc_events = {
    "Stock Entry": {
        "on_submit": "erpnext_construction_ph.api.stock_hooks.update_wbs_task_material_actuals"
    },
    "Purchase Order": {
        "before_submit": "erpnext_construction_ph.api.procurement_hooks.enforce_bir_ewt_withholding"
    },
    # ERPNext v17's TaxService never creates a withholding row, so the app appends
    # the 2% EWT deduction itself. See api/ebook_export.py.
    "Purchase Invoice": {
        "before_validate": "erpnext_construction_ph.api.ebook_export.apply_ewt_to_purchase_invoice",
        "before_submit": "erpnext_construction_ph.api.ebook_export.finalise_ewt_on_purchase_invoice",
        "on_submit": "erpnext_construction_ph.api.ebook_export.post_ewt_journal"
    },
    "Comment": {
        # A mention is free text from a phone. The resolver tells a real person and
        # REPORTS the ones that resolve to nobody, so the two are never confused.
        "after_insert": "erpnext_construction_ph.api.bcf_mentions.on_bcf_topic_comment"
    },
    "Construction Progress Billing": {
        "on_submit": "erpnext_construction_ph.api.billing_engine.on_progress_billing_submit"
    }
}
