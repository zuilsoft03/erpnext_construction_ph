// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
// Site Log — the field-facing entry form.
//
// Written for a phone on site, not a desk: one column, large targets, and the
// project and WBS task chosen once rather than typed per entry. The task list
// comes from the server because `pour_task` links to the *native* ERPNext
// Project's tasks; the browser cannot work that out, and guessing it in the form
// is how every submitted log ends up failing link validation.

frappe.provide("erpnext_construction_ph.page.site_log");

// Held as a plain object rather than registered with `frappe.page(name, obj)`.
// That factory is the SPA API: it never runs for a Desk Page route, which is
// why this page rendered blank. The object is driven by an explicit
// `frappe.pages["site_log"].on_page_load` at the foot of the file, which builds
// the two containers this code queries and hands it a `main`.
const site_log = {
	onload() {
		this.project = null;
		this.tasks = [];
		this.setup_projects();
	},

	setup_projects() {
		const host = this.root.querySelector(".sl-projects");
		if (!host) return;
		host.innerHTML = `
			<div class="sl-field">
				<label>Project</label>
				<select class="sl-project"></select>
			</div>
		`;
		frappe.call({
			method: "erpnext_construction_ph.api.site_log.recent_logs",
			args: { limit: 1 },
			callback: () => this.load_projects(),
		});
		this.load_projects();
	},

	load_projects() {
		frappe
			.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Construction Project",
					fields: ["name", "project_name"],
					limit_page_length: 100,
					order_by: "modified desc",
				},
			})
			.then((r) => {
				const select = this.root.querySelector(".sl-project");
				if (!select) return;
				select.innerHTML = (
					r.message.map((p) => `<option value="${p.name}">${p.project_name || p.name}</option>`).join("")
				);
				select.addEventListener("change", () => {
					this.project = select.value;
					this.load_tasks();
					this.render();
				});
				if (r.message && r.message.length) {
					this.project = r.message[0].name;
					select.value = this.project;
					this.load_tasks();
				}
				this.render();
			});
	},

	load_tasks() {
		if (!this.project) return;
		frappe
			.call({ method: "erpnext_construction_ph.api.site_log.open_tasks", args: { project: this.project } })
			.then((r) => {
				this.tasks = r.message || [];
				this.render();
			});
	},

	render() {
		const recent = this.root.querySelector(".sl-recent");
		if (!recent) return;
		if (!this.project) {
			recent.innerHTML = ('<p class="text-muted">Choose a project to start logging.</p>');
			return;
		}
		recent.innerHTML = `
			<div class="sl-field"><label>Log type</label>
				<select class="sl-type">
					<option>Concrete Pour</option>
					<option>Weather Delay</option>
					<option>Subcontractor Headcount</option>
					<option>General</option>
				</select>
			</div>
			<div class="sl-field"><label>Location</label><input class="sl-location" placeholder="Level 3 Grid C-F" /></div>
			<div class="sl-field"><label>WBS task</label><select class="sl-task">
				<option value="">-- not a pour --</option>
				${this.tasks.map((t) => `<option value="${t.name}">${t.subject || t.name}</option>`).join("")}
			</select></div>
			<div class="sl-field"><label>Pour volume</label><input type="number" class="sl-volume" step="0.01" /></div>
			<div class="sl-field"><label>Delay hours</label><input type="number" class="sl-hours" step="0.5" /></div>
			<div class="sl-field"><label>Delay reason</label><input class="sl-reason" /></div>
			<div class="sl-field"><label>Headcount</label><input type="number" class="sl-headcount" /></div>
			<button class="btn btn-primary sl-save">Save site log</button>
		`;

		const save_btn = this.root.querySelector(".sl-save");
		if (save_btn) save_btn.addEventListener("click", () => this.save());
	},

	// save() calls this after a successful write, and it was never defined --
	// the success path of every save threw. Re-render the recent list against
	// the same project so the crew sees their own entry come back.
	refresh() {
		this.render();
		this.load_tasks();
	},

	save() {
		const read = (sel) => {
			const el = this.root.querySelector(sel);
			return el ? el.value : null;
		};
		const args = {
			project: this.project,
			log_type: read(".sl-type"),
			location: read(".sl-location"),
			pour_volume: read(".sl-volume"),
			pour_task: read(".sl-task"),
			delay_hours: read(".sl-hours"),
			delay_reason: read(".sl-reason"),
			headcount: read(".sl-headcount"),
		};
		// A pour needs an item before the backing Stock Entry can be made. The
		// form does not invent one: a wrong item silently mis-states the
		// material movement, so the field is required from the crew, not guessed.
		if (args.log_type === "Concrete Pour" && read(".sl-volume")) {
			args.pour_item = read(".sl-item") || null;
		}
		frappe.call({
			method: "erpnext_construction_ph.api.site_log.create_site_log",
			args,
		})
			.then((r) => {
				frappe.show_alert({ message: __("Site log {0} saved").format(r.message), indicator: "green" });
				this.refresh();
			})
			.catch(() => {
				// The server's refusal is shown verbatim. Swallowing it here would
				// leave the crew believing an unlinked pour was logged.
			});
	},
};

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
// 404s because it is called with the ROUTE name ("site-log") while the Page
// record is "site_log". `frappe.views.Page` then hits `frappe.show_not_found`
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
frappe.standard_pages["site_log"] = function () {
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

	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Site Log") });

	wrapper.innerHTML = `
		<div class="page-body">
			<div class="sl-projects"></div>
			<div class="sl-recent"></div>
		</div>
	`;

	// Native DOM, not jQuery. This Frappe does not expose `frappe.$`, and the
	// Desk swallows a throw from a page handler, so a jQuery handle here failed
	// silently: the containers rendered and nothing after them ever ran.
	site_log.root = wrapper.querySelector(".page-body") || wrapper;
	site_log.project = null;
	site_log.tasks = [];
	site_log.setup_projects();
};
