# erpnext_construction_ph

A Frappe/ERPNext custom application for the **Philippine construction sector** — progress
billing under **CIAP Document 102**, creditable withholding tax under the **BIR**, **BIM/BCF**
coordination, and **DOLE D.O. 13** site safety.

> ### ⚠️ Status: work in progress, and one thing is genuinely unresolved
>
> This is a working application, not a released product. **546 tests pass
> (`failures=0, errors=0`)**, but arithmetic correctness is a separate claim from test
> correctness, and on the most consequential figure it does not yet hold:
>
> **The base the 10% CIAP 102 retention is charged on is unresolved.** This codebase
> computes it two ways, 12% apart:
>
> | | basis | 10% of a ₱1,000,000 claim at 10% cumulative |
> |---|---|---|
> | billing controller | `net_before_retention` = taxable **+ VAT** | **₱8,960.00** |
> | shadow-run harness | `taxable_base` = taxable, **excl. VAT** | **₱8,000.00** |
>
> The 12% is exactly the VAT rate. **No one knows which is correct** — CIAP 102's retention
> base is a question for the DPWH-published reference workbook, which this project has
> never had. Both readings are pinned in the test suite as `UNRESOLVED_CORE16_*` so the
> disagreement stays visible rather than being quietly resolved by whoever edits next. The
> affected behaviour is confined to the **0–50%** cumulative band; past the milestone both
> compute zero and agree to the peso.
>
> **Do not use this to bill a contractor until that is settled.**

## What's here

- **CIAP 102 progress billing** — certification, retention, creditable withholding tax,
  advance/mobilization recoupment, and the 50% retention milestone.
- **BIR localisation** — Form 2307 certificates, 1601-EQ, e-bookkeeping export.
- **BIM/BCF** — IFC parsing and in-browser viewing (web-ifc / That Open), BCF 2.1/3.0
  topics and packages, issue-from-viewer with camera viewpoints.
- **Takeoff** — multi-source quantity takeoff from PDF drawings and 3D models.
- **DOLE site safety** — CSHP daily toolbox meetings, SO1/SO4 ratio compliance, and the
  WAIR 24-hour statutory incident report.
- **Client portal** — row-level-scoped project views, S-curve and earned value.

## Stack

Frappe 17 / ERPNext 17, Python, MariaDB. Front end is server-rendered with a
Three.js/`<model-viewer>` BIM surface and a TipTap document editor. See the app's own
`hooks.py` and `setup/` for the authoritative dependency set.

## Running it

The app expects a Frappe bench with `frappe`, `erpnext`, and `erpnext_construction_ph`
installed on `test.localhost` or your own site. It is a standard Frappe app — no custom
build step beyond `bench build` for assets.

```sh
bench --site <your-site> install-app erpnext_construction_ph
bench --site <your-site> run-tests --app erpnext_construction_ph
```

**Always pass `--site`.** Without it `bench` defaults to `localhost` and silently points
at the wrong database — a real failure mode on this project, and the reason the test
invocations above name the site explicitly.

## Layout

```
erpnext_construction_ph/
├── api/                     # billing engine, BCF, BIM convert, DOLE, portal, exports
├── construction_ph/
│   ├── doctype/             # 18 custom doctypes (progress billing, BOQ, BCF, safety…)
│   ├── page/                # Desk pages
│   └── test/                # 70 modules, 546 cases
├── public/js/               # BIM viewport, editor, portal client
├── setup/                   # custom fields and install hooks
├── www/portal/              # client portal templates
└── hooks.py                 # app wiring
```

## Licence and provenance

Internal commercial work. The CIAP 102, BIR, DOLE and PCAB references in the code are
statutory requirements of the Philippine construction sector; nothing here is an
endorsement or an official interpretation of any of them.