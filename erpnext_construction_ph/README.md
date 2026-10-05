# erpnext_construction_ph

Philippine Construction ERPNext Custom App with BIM, Multi-Takeoff, CIAP 102,
and BIR 2307 Compliance.

- **Publisher:** Philippine AEC Engineering
- **License:** AGPL-3.0
- **Frappe module:** `construction_ph` (see `modules.txt`)
- **App title:** Construction PH

## Layout

| Path | Purpose |
|---|---|
| `hooks.py` | App metadata, `after_install` / `after_migrate`, Desk assets, DocType events |
| `setup/` | `install.py` lifecycle + idempotent fixtures (roles, workspaces, custom fields, GL accounts) |
| `api/` | Runtime hooks: `billing_engine`, `stock_hooks`, `procurement_hooks`, `boq_matrix` |
| `construction_ph/doctype/` | App DocTypes (built under Phase 0 rows CORE-02/CORE-03) |

## Install

```bash
bench --site <sitename> install-app erpnext_construction_ph
```

`after_install` seeds the ten construction roles, the workspaces, the custom
fields, and the five GL accounts. The script is idempotent, so re-running it is
safe. See `BUILD_TRACKER.md` and `GATES.md` for the rows that gate each phase.

## Dependencies

`frappe`, `erpnext`, `ezdxf>=1.1.0`, `python-docx>=1.1.0`, `openpyxl>=3.1.0`
