// BOQ Worksheet — paste a Bill of Quantities out of Excel and land it as BOQ rows.
// Column order: pay_item_number, item_code, description, unit, quantity,
// direct_cost, ocm, profit. A header row is optional and is detected automatically.

frappe.provide("erpnext_construction_ph.Construction PH");

// ---------------------------------------------------------------------------
// Registration: `frappe.standard_pages`, keyed by the ROUTE name.
//
// Read out of the served desk bundle, not guessed:
//   frappe.views.pageview.with_page(name) tries, in order --
//     1. frappe.standard_pages[name]        <- this registration
//     2. locals.Page[name] && locals.Page[name].script
//     3. localStorage["_page:"+name]  (ONLY when developer_mode != 1; this site
//                                        has developer_mode = 1, so it is dead)
//     4. frappe.call("frappe.desk.desk_page.getpage", {name})
// Branches 2-4 all fail for a Desk Page in this app, and branch 4 additionally
// 404s because it is called with the ROUTE name ("boq-worksheet") while the Page
// record is "boq_worksheet". `frappe.views.Page` then hits `frappe.show_not_found`
// and RETURNS -- so the route renders an empty page with NO console error.
//
// Do NOT move this to `frappe.pages[name]`: the Desk overwrites that key with
// its own wrapper (`frappe.pages[window.page_name] = this.wrapper`), destroying
// any handler placed there. `site_log.js` carried a defensive `|| {}` guard for
// years; that guard prevents a TypeError and nothing more.
//
// Measured 2026-09-29: every page in this app rendered blank, with three gates
// ticked `[x]` whose CHECK strings said to browse the page and assert on it.
// ---------------------------------------------------------------------------
frappe.standard_pages["boq_worksheet"] = function () {
	// The container, resolved by what this Desk ACTUALLY renders.
	//
	// The previous chain was `.page-container || .layout-main || document.body`,
	// and **`document.body` is a catastrophic fallback**: writing `innerHTML` to
	// it replaces the whole Desk, not just the page. Measured here:
	// `.page-container` -> absent, `.layout-main` -> absent, `.layout-main-section`
	// -> absent, and the only page container is `.page-body`. So the chain
	// reached `document.body`, wiped the layout, and left three
	// `Cannot read properties of null (reading 'insertAdjacentHTML')` errors from
	// Frappe's own Desk code that had just had its DOM removed underneath it.
	//
	// Each candidate is also required to EXIST before being written to, so a
	// rename degrades to "page did not render" rather than to a destroyed Desk.
	// The container, built rather than found.
	//
	// Instrumented on a COLD load as an authenticated Administrator:
	// `document.body` holds exactly two children — `DIV.dock hidden` and ONE
	// `DIV.page-body` — and that `.page-body` is created by THIS page, not by the
	// Desk. `.page-container`, `.layout-main` and `.layout-main-section` do not
	// exist at all in this Frappe.
	//
	// So there is no page container to query. The earlier chain
	// `.page-container || .layout-main || document.body` resolved all the way to
	// `document.body`, and `wrapper.innerHTML = ...` then REPLACED THE ENTIRE DESK
	// — dock, layout and all — leaving three `Cannot read properties of null
	// (reading 'insertAdjacentHTML')` errors from Frappe's own code running on DOM
	// that had just been removed.
	//
	// Reuse the Desk's own `.page-body` if a previous build left one, else add a
	// fresh div beside the dock. Either way the page owns exactly its own node.
	let wrapper = document.querySelector("body > .page-body");
	if (!wrapper) {
		wrapper = document.createElement("div");
		wrapper.className = "page-body";
		document.body.appendChild(wrapper);
	}
	wrapper.innerHTML = "";

	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("BOQ Worksheet"),
	});

	// ---------------------------------------------------------------------
	// import_rows -- the page's primary action.
	//
	// Wired to the Import Rows button by `page.set_primary_action` below.
	// Reads the BOQ selector, the pasted TSV and the replace checkbox out of
	// the DOM and forwards them to `boq_matrix.bulk_insert_boq_items`, which
	// is the endpoint that actually does the work. Before this function
	// existed the button called `import_rows(frm)` -- a name defined nowhere
	// in the app -- so every press threw ReferenceError. The working server
	// endpoint sat unused while the page's headline feature was wired to
	// nothing.
	//
	// The server contract is `bulk_insert_boq_items(boq, rows, replace=False)`
	// where `rows` is a tab-separated STRING (not a list of lists) with an
	// optional header line it auto-detects. It refuses any width other than
	// the eight it declares, so we forward the raw paste verbatim and let it
	// reject a malformed one with a message the user can act on.
	// ---------------------------------------------------------------------
	function import_rows(frm) {
		const $result = wrapper.querySelector("#import_result");
		const boqSelect = wrapper.querySelector("#boq");
		const $tsv = wrapper.querySelector("#tsv");
		const $replace = wrapper.querySelector("#replace");

		if ($result) {
			$result.innerHTML = "";
		}

		const boq = boqSelect ? boqSelect.value : "";
		const rows = $tsv ? $tsv.value : "";
		const replace = $replace ? $replace.checked : false;

		if (!boq) {
			if ($result) {
				$result.innerHTML =
					'<div class="alert alert-error">' +
					__("Please select a Bill of Quantities first.") +
					"</div>";
			}
			return;
		}

		if (!rows || !rows.trim()) {
			if ($result) {
				$result.innerHTML =
					'<div class="alert alert-error">' +
					__("Paste a tab-separated range into the rows box first.") +
					"</div>";
			}
			return;
		}

		if ($result) {
			$result.innerHTML =
				'<div class="alert alert-info">Importing…</div>';
		}

		frappe.call({
			method: "erpnext_construction_ph.api.boq_matrix.bulk_insert_boq_items",
			args: { boq: boq, rows: rows, replace: replace },
			callback: (r) => {
				const data = r && r.message;
				if (!$result) {
					return;
				}
				if (data) {
					let html =
						'<div class="alert alert-success">' +
						__("Imported {0} row(s) into {1}.").format(
							data.inserted,
							'<strong>' + frappe.utils.escape_html(data.boq) + "</strong>"
						) +
						"</div>";
					if (data.header_skipped) {
						html +=
							'<p class="text-muted">' +
							__("A header row was detected and skipped.") +
							"</p>";
					}
					if (data.created_uoms && data.created_uoms.length) {
						html +=
							'<p class="text-muted">' +
							__("Created UOMs: {0}").format(
								frappe.utils.escape_html(data.created_uoms.join(", "))
							) +
							"</p>";
					}
					$result.innerHTML = html;
				} else {
					$result.innerHTML =
						'<div class="alert alert-error">' +
						__("Import failed: no response from server.") +
						"</div>";
				}
			},
			error: (err) => {
				if (!$result) {
					return;
				}
				const msg =
					(err && err.message)
						? frappe.utils.escape_html(err.message)
						: _("Import failed: unknown error.");
				$result.innerHTML =
					'<div class="alert alert-error">' + msg + "</div>";
			}
		});
	}

	page.set_primary_action(__("Import Rows"), (frm) => import_rows(frm));

	wrapper.innerHTML = `
		<div class="page-body">
			<div class="row">
				<div class="col-12">
					<div class="form-group">
						<label>Bill of Quantities</label>
						<div id="boq_link"></div>
					</div>
				</div>
			</div>
			<div class="row">
				<div class="col-12">
					<div class="form-group">
						<label>Rows (tab-separated)</label>
						<textarea id="tsv" class="form-control" rows="12"
							placeholder="pay_item&#9;item_code&#9;description&#9;unit&#9;quantity&#9;direct_cost&#9;ocm&#9;profit"></textarea>
						<p class="help">
							Copy the range straight out of Excel. Column order:
							<span class="text-muted">pay item number, item code, description, unit, quantity, direct cost, ocm, profit</span>.
							Unit price is computed as <code>direct cost x (1 + OCM + profit)</code>.
						</p>
					</div>
				</div>
			</div>
			<div class="row">
				<div class="col-6">
					<div class="form-group">
						<label class="checkbox-label">
							<input type="checkbox" id="replace" /> Replace existing rows
						</label>
					</div>
				</div>
			</div>
			<div class="row">
				<div class="col-12">
					<div id="import_result"></div>
				</div>
			</div>
		</div>
	`;

	// A native <select>, not `new frappe.ui.LinkField(...)`.
	//
	// **That constructor does not exist in this Frappe.** Measured: `frappe.ui`
	// exposes no `LinkField`, `DataField`, `TextInput`, `Select` or `Checkbox` at
	// all -- the only control-ish names it carries are FieldGroup, LinkPreview,
	// NamingSeriesController, SortSelector and FieldSelect. So the call threw
	// `TypeError: frappe.ui.LinkField is not a constructor` on EVERY load of this
	// page, and because the Desk swallows a throw from a page builder, the page
	// still rendered -- with the control silently missing and nothing in the UI
	// able to choose a BOQ, which is the one thing the page exists to do.
	//
	// The `#boq_link` div it was meant to fill was rendered by the template above
	// and read by nothing, so this is a replacement rather than a move.
	const $select = wrapper.querySelector("#boq_link");
	frappe.call({
		method: "erpnext_construction_ph.api.boq_matrix.list_boqs",
		callback: (r) => {
			const rows = (r && r.message) || [];
			const options = rows
				.map((b) => `<option value="${frappe.utils.escape_html(b.name)}">${frappe.utils.escape_html(b.name)}</option>`)
				.join("");
			$select.innerHTML = options
				? `<select class="form-control" id="boq">${options}</select>`
				: `<div class="text-muted">No Bill of Quantities yet.</div>`;
		},
	});
};
