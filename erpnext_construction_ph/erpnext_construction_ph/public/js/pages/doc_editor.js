// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// PH5-01 Desk Page. Wires the editor asset to the server round-trip.
//
// The one rule this file exists to keep: **the markup sent to the server is the
// markup the editor holds.** No client-side "cleanup". Every normalisation
// belongs in the shared model, where a test can reach it, or on the server.

frappe.provide("erpnext_construction_ph.page.doc_editor");

Object.assign(window.erpnext_construction_ph.page.doc_editor, {
	// Where the last round-trip failed, if it did. A page that swallows the
	// failure reports "saved" while nothing was stored.
	lastUnknownTags: [],

	// The TipTap controller from ConstructionEditor.mount(), or null when TipTap
	// is not on this page. mount() is documented never to throw, so a null here
	// is a normal state, not an error.
	controller: null,

	setup(wrapper) {
		const root = wrapper ? wrapper : document.body;
		this.editor_area = root.querySelector(".editor-body");
		this.roundTrip = root.querySelector("[data-roundtrip]");
		if (!this.editor_area || !this.roundTrip) return;
		this.controller = window.ConstructionEditor
			? window.ConstructionEditor.mount(this.editor_area, {})
			: null;
		this.roundTrip.addEventListener("click", () => this.commit());
	},

	/** Current editor HTML, preferring the controller's own view of it. */
	read() {
		if (this.controller && this.controller.getHTML) return this.controller.getHTML();
		return this.editor_area ? this.editor_area.innerHTML : "";
	},

	/** Write server-rendered HTML back, through the controller when there is one. */
	write(html) {
		if (this.controller && this.controller.setHTML) {
			this.controller.setHTML(html);
			return;
		}
		if (this.editor_area) this.editor_area.innerHTML = html;
	},

	async commit() {
		const model = window.ConstructionEditor;
		const html = this.read();
		const r = await frappe.call({
			method: "erpnext_construction_ph.api.doc_editor.normalize_document",
			args: { html: html },
		});
		const body = r.message;
		// The server returns the body unchanged. Render what it actually
		// returned rather than what we hoped it would keep, so any future
		// server-side transform becomes visible on the page immediately.
		this.write(model ? model.roundTrip(body.html) : body.html);
		this.lastUnknownTags = body.unknown_tags || [];
		if (this.lastUnknownTags.length) {
			frappe.msgprint({
				title: __("Unrecognised markup"),
				message: __("The server reported tags this editor does not know: {0}", [
					this.lastUnknownTags.join(", "),
				]),
				indicator: "orange",
			});
		} else {
			frappe.show_alert({ message: __("Round-tripped without loss"), indicator: "green" });
		}
	},
});

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
// 404s because it is called with the ROUTE name ("doc-editor") while the Page
// record is "doc_editor". `frappe.views.Page` then hits `frappe.show_not_found`
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
frappe.standard_pages["doc_editor"] = function () {
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

	const editor = window.erpnext_construction_ph.page.doc_editor;
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Document Editor") });
	page.set_primary_action(__("Round-trip"), () => editor.commit());

	wrapper.innerHTML = `
		<div class="page-body">
			<p class="help text-muted">
				${__("The markup sent to the server is the markup this editor holds. No client-side cleanup.")}
			</p>
			<div class="row">
				<div class="col-12">
					<div class="editor-body" data-editor-body></div>
				</div>
			</div>
			<div class="row" style="margin-top: 12px;">
				<div class="col-12">
					<button class="btn btn-secondary" data-roundtrip>${__("Round-trip through the server")}</button>
					<p class="help text-muted" data-editor-status></p>
				</div>
			</div>
		</div>
	`;

	editor.setup(wrapper);
};
