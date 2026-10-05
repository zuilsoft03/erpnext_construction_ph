// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
// BIM Workspace — the Desk Page that surfaces OpenProject's IFC-viewer.
//
// Layout is OpenProject's, not mine: a model tree down the left, the interactive
// model in the middle, properties and issues down the right. That grouping is the
// part worth copying, because a viewer with a model and nothing else leaves the
// coordinator navigating blind — they can see a wall but cannot say which one.
//
// Not copied: OpenProject's Angular addon shell, its issue tracker, and its view
// cube. This is a Frappe Desk Page inside an accounting system, and re-implementing
// a work-management product is not the ask. Their boundary on clash detection is
// adopted deliberately (D-041): the app renders the ISSUES a partner tool found and
// does not claim to find any itself.
//
// THIS FILE IS THE ONE THAT RUNS. It lives at public/js/pages/ because that is the
// path Frappe serves a Desk Page's script from. An identical copy used to sit
// beside the Page record at construction_ph/page/bim_workspace/bim_workspace.js,
// and that copy was NEVER SERVED (404) -- it was a shadow: every UI-0 edit went
// into the file the browser never read, and the page kept rendering the old build
// with no error anywhere. The shadow has been deleted. The controller
// (bim_workspace.py) still lives in the page folder, because THAT is where Frappe
// looks for get_context. Do not move this file.
//
// Registration note, learned the hard way on PH1-08/PH1-09: this is a plain object
// driven by an explicit `frappe.pages[...]` hook at the foot, NOT `frappe.page(name, obj)`.
// The factory is the SPA API and never runs for a Desk Page route, which is exactly
// why those pages once rendered blank.

frappe.provide("erpnext_construction_ph.page.bim_workspace");

/**
 * The PAYLOAD of a `frappe.call` response.
 *
 * `frappe.call`'s `callback` receives the whole RESPONSE ENVELOPE, `{message: ...}`,
 * not the value the method returned. Every call site in this file read the argument
 * as the payload itself, so `models.length` was `undefined` -- falsy -- and
 * `load_models` took its empty-state branch WITH FOUR MODELS IN `r.message`. The
 * page rendered "No BIM models for this project yet", which is not only false but
 * indistinguishable from a project that genuinely has none: the same code path, the
 * same sentence, whether the API returned data, returned nothing, or was refused.
 *
 * That is the second defect behind the 403s. Fixing the session made the API answer
 * 200 with data, and the page STILL showed nothing, because it was reading the wrong
 * level of the response. A green API call is not a working page.
 *
 * One unwrap, here, so the shape cannot drift per call site again. It is a helper
 * rather than six inline `r.message`s because the trap is invisible: nothing throws,
 * the array methods just quietly stop finding anything.
 *
 * Note `get_frag_url` RETURNS an object that itself has a `message` key (the human
 * text). That is not a conflict: the envelope's `message` holds the whole returned
 * object, so `payload(r).message` is the human string and `payload(r).available` is
 * the flag -- exactly what the call site wants.
 */
function payload(r) {
	return r && r.message !== undefined ? r.message : r;
}

const workspace = {
	MODEL: null,
	ENGINE: null,
	SELECTED: null,

	onload() {
		this.MODEL = null;
		this.ENGINE = null;
		this.SELECTED = null;
		this.TABS = null;
		this.build_shell();
		// The canvas lives INSIDE the 3D tab's host, so it is created after the
		// tabs exist -- otherwise the engine would mount into a node that the tab
		// bar then replaces, and the model would render into detached DOM.
		// A handle for diagnostics and for the browser gate, which needs to read
		// the LIVE viewport rather than a re-implementation of it.
		window.__bimws = this;

		// Which project is this workspace FOR? It was never answered, and every
		// symptom followed from that: `list_models` was called with
		// `project: undefined`, the server answered 400, and the page reported
		// "No BIM models for this project yet" -- false, and indistinguishable from
		// a project that genuinely has none. `?project=` is honoured first so a
		// coordinator can open a specific one; the server decides the default, and
		// it decides with the same entitlement check everything else uses.
		// ONE chain, in ORDER. These were two independent promise chains and
		// `load_models()` was fired from BOTH: the first ran `resolve_project` ->
		// `init_panel` -> `load_models`, the second created the canvas. Whichever
		// resolved first won, so `mount_engine` could run while `this.$canvas` was
		// still null -- and the guard there answers "The viewport is not available",
		// which is a plausible message for a user and says nothing about a race. The
		// canvas is built first, deliberately, because the model has nowhere to go
		// without it.
		this.init_tabs().then(() => {
			const host = this.TABS && this.TABS.host("3d");
			if (!host) return;
			// `bim-ws-stage-wrap` is the POSITIONED ancestor the pin layer needs.
			// Absolute pins inside a static parent resolve against the page, so a
			// pin lands somewhere unrelated to the model the moment the page scrolls
			// or the panel opens.
			host.innerHTML = `<div class="bim-ws-stage-wrap">
				<div class="bim-ws-canvas"></div>
				<div class="bim-ws-toolbar-slot"></div>
				<div class="bim-ws-schedule-slot"></div>
			</div>
			<div class="bim-ws-modalhost"></div>`;
			this.$stage = host.querySelector(".bim-ws-stage-wrap");
			this.$canvas = host.querySelector(".bim-ws-canvas");
			this.init_pins();
			this.init_toolbar();
			this.init_right_panel();
			this.init_issue_modal();
			this.init_sync();
			this.init_schedule();
		})
			.then(() => this.resolve_project())
			.then(() => this.init_panel())
			.then(() => this.load_models())
			// A rejected promise in a chain is INVISIBLE: no status, no console
			// error, and the page simply never populates -- the same silence a
			// genuinely broken product has, and the reason a regression here looks
			// exactly like a page with no data. Every failure in this chain now says
			// what failed. The message is deliberately specific about being a page
			// fault, because "nothing happened" is what this used to report.
			.catch((error) => {
				this.status(
					`The workspace could not finish starting up: ${
						(error && (error.message || error)) || "unknown error"
					}`
				);
				if (window.console) console.error("bim-workspace startup failed", error);
			});
	},

	build_shell() {
		this.root.innerHTML = `
			<div class="bim-ws">
				<div class="bim-ws-side bim-ws-side-left"></div>
				<div class="bim-ws-stage">
					<!-- The tab bar is built by viewport_tabs.js and inserted here;
					     every viewport host below it is created ONCE and kept. -->
					<div class="bim-ws-tabslot"></div>
					<div class="bim-ws-toolbar">
						<button class="btn btn-xs btn-default bim-ws-fit" title="Reset view">Reset view</button>
						<span class="bim-ws-hint">Double-click an element to select it</span>
					</div>
					<div class="bim-ws-status"></div>
				</div>
				<div class="bim-ws-side bim-ws-right"></div>
			</div>
		`;

		// NOTE: `$canvas` is deliberately NOT queried here. The canvas is created
		// inside the 3D tab's host by `init_tabs`, which runs after this. Querying
		// it now would bind `null` and quietly succeed -- the query never throws on
		// a missing node, so the breakage would surface later as
		// "Cannot read properties of null (reading 'innerHTML')" inside
		// `load_model`, pointing at the wrong file entirely.
		// `$tree` and `$model` are gone from this file: the left panel module now
		// owns both the model dropdown and the spatial tree, and builds them
		// itself. Querying for them here would bind null (a query never throws on a
		// missing node) and the failure would surface two functions later as
		// "Cannot read properties of null (reading 'innerHTML')" -- naming the wrong
		// file entirely. The fallback render below still uses `$tree` if the panel
		// module is absent, so it is re-resolved lazily rather than stored.
		this.PANEL_RIGHT = this.root.querySelector(".bim-ws-right");
		this.$status = this.root.querySelector(".bim-ws-status");
		// (the model dropdown is built by left_panel.js; see the note above)
		this.$tabslot = this.root.querySelector(".bim-ws-tabslot");
		// The panel is built into this host, which sits INSIDE the left column, so
		// the spec's fixed 260px survives and the panel fills only what is left.
		this.PANEL_HOST = this.root.querySelector(".bim-ws-side-left");

		// The dropdown's own onchange is wired by left_panel.js, next to the
		// element it belongs to, rather than from here where it would be null.
		this.root.querySelector(".bim-ws-fit").onclick = () => this.reset_view();
	},

	/**
	 * The layout spine: one tab bar, three persistent viewports, no remount.
	 *
	 * `mount` is called once per viewport and refuses a second call, so a later
	 * wave that wires UI-1 (PDF) or UI-6 (split) cannot accidentally tear the 3D
	 * context down by re-mounting it. The spec's 2-3 second penalty is a UX
	 * regression that is invisible in a code review, which is exactly why it is
	 * made structurally impossible here rather than left to discipline.
	 */
	async init_toolbar() {
		const slot = this.root.querySelector(".bim-ws-toolbar-slot");
		if (!slot) return;
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/toolbar.js"
		).catch((error) => {
			this.status(`The toolbar could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod) return;
		this.TOOLBAR = mod.createToolbar(slot, {
			getEngine: () => this.ENGINE,
			getSelection: () => (this.SELECTION ? [this.SELECTION] : []),
			getAllGuids: () => (this.ENGINE && this.ENGINE.tree ? [] : []),
			onStatus: (message, tone) => {
				// The toolbar has its own status line for TOOL feedback; a failure the
				// user must not miss also goes to the page status.
				if (tone === "error") this.status(message);
			},
		});
	},

	async init_pins() {
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/pin_overlay.js"
		).catch((error) => {
			this.status(`Issue pins could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod || !this.$stage) return;
		this.PINS = mod.createPinOverlay(this.$stage, (topic) => this.jump_to_viewpoint(topic));
		this.pin_api = mod;
		// Deliberately NOT loading here: the pins belong to a MODEL, and no model
		// has been chosen yet. `load_model` calls this once it has one. Fetching
		// here returned early on a null model every time, and an empty overlay
		// reads as "this model has no issues" rather than as "never asked".
	},

	/** Pins for the model currently open, refreshed whenever the model changes. */
	load_pins() {
		if (!this.PINS || !this.MODEL) return;
		frappe.call({
			method: "erpnext_construction_ph.api.bim_viewer.list_topics",
			args: { bim_model: this.MODEL.name },
			callback: (r) => {
				const topics = payload(r) || [];
				if (this.PINS) this.PINS.setTopics(topics || []);
				// A count on the tab, so a coordinator can see there are issues
				// without opening the panel.
				this.TABS && this.TABS.setBadge("3d", (topics || []).length || null);
			},
		});
	},

	/**
	 * The camera jump (UI-3): a pin's viewpoint moves the camera, and the elements
	 * it points at are highlighted.
	 */
	jump_to_viewpoint(topic) {
		if (this.PINS) this.PINS.focus(topic.topic_guid);
		const raw = topic.camera_vectors;
		const vectors = raw && (typeof raw === "string" ? frappe.parse_json(raw) : raw);
		if (!this.pin_api || !this.ENGINE) {
			this.status("The model is not loaded yet, so the viewpoint cannot be shown.");
			return;
		}
		const outcome = this.pin_api.flyTo(
			this.ENGINE,
			{
				eye: vectors && (vectors.eye || vectors.position),
				target: vectors && vectors.target,
				up: vectors && vectors.up,
				element_guids: (topic.elements || []).map((e) => e.global_id).filter(Boolean),
			},
			{ highlight: true }
		);
		if (!outcome.ok) {
			// An issue raised from a sheet rather than the model has no camera. That
			// is a real state, and saying so is what stops the pin looking broken.
			this.status(`This issue has no camera viewpoint yet: ${outcome.reason}`);
		}
	},

	async resolve_project() {
		const fromUrl = new URLSearchParams(window.location.search).get("project");
		if (fromUrl) {
			this.project = fromUrl;
			return this.project;
		}
		try {
			const r = await frappe.call({
				method: "erpnext_construction_ph.api.bim_viewer.default_project",
			});
			this.project = r.message || null;
		} catch (error) {
			this.project = null;
		}
		if (!this.project) {
			// Said plainly. A workspace with no project is not an empty workspace,
			// and "you have no project" is a different message from "this project
			// has no models" -- the first is a permissions or setup answer.
			this.status(
				"You do not have a project to open. Add a ?project= parameter, or ask an administrator for access."
			);
		}
		return this.project;
	},

	async init_panel() {
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/left_panel.js"
		).catch((error) => {
			this.status(`The left panel could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod) return;
		this.PANEL = mod.createLeftPanel(this.PANEL_HOST, {
			onModelChange: (name) => this.load_model(name),
			onIsolate: (guid) => this.isolate(guid),
			onSheetOpen: (sheet) => this.open_sheet(sheet),
			onNewSheet: () => this.new_sheet(),
			onCalibrate: (points, known) => this.calibrate(points, known),
			onCapture: (payload) => this.capture(payload),
		});
		return this.PANEL;
	},

	/** Isolate a spatial node: the spec's "click -> engine.isolate(globalId)". */
	isolate(guid) {
		if (this.ENGINE && typeof this.ENGINE.isolate === "function") {
			this.ENGINE.isolate(guid);
			return;
		}
		// The engine gains `isolate` in a later task. Saying so beats doing nothing:
		// a control that silently does nothing is worse than one that explains.
		this.status("Isolating a spatial node arrives with the viewer controls.");
	},

	open_sheet(name) {
		frappe.set_route("Form", "Takeoff Sheet", name);
	},

	new_sheet() {
		if (!this.project) {
			this.status("A project is required before a takeoff sheet can be created.");
			return;
		}
		frappe.model.open_mDoc("Takeoff Sheet", {
			project: this.project,
			sheet_name: "New Takeoff Sheet",
		});
	},

	/**
	 * The four panels whose modules exist and were never wired.
	 *
	 * `init_toolbar` and `init_pins` were written; these were not, and the calls to
	 * them sat in the startup chain, so the first one THREW. A throw there ended the
	 * whole chain with no status and no console error, so the page simply never
	 * populated -- indistinguishable from a page with no data. It stayed hidden
	 * because `load_models()` was ALSO fired from a second, independent promise
	 * chain, and that one happened to survive the first one's failure. Sequencing
	 * the chain properly (canvas before models) removed the accidental masking and
	 * exposed this. The modules are complete and have passing unit tests; only the
	 * wiring was missing, and deleting the calls would have made the page load while
	 * silently dropping four product features.
	 */
	async init_right_panel() {
		const root = this.PANEL_RIGHT;
		if (!root) return;
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/right_panel.js"
		).catch((error) => {
			this.status(`The issues panel could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod) return;
		this.RIGHT = mod.createRightPanel(root, {
			onIssue: (topic) => this.jump_to_viewpoint(topic),
			onNewIssue: () => this.new_issue(),
			onViewpoint: (topic) => this.jump_to_viewpoint(topic),
		});
	},

	async init_issue_modal() {
		const host = this.root.querySelector(".bim-ws-modalhost");
		if (!host) return;
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/issue_modal.js"
		).catch((error) => {
			this.status(`The issue dialog could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod) return;
		this.MODAL = mod.createIssueModal(host, {
			// Each of these is a real endpoint, and the argument names line up. The
			// modal's `topic_type` is the topic's STATUS here: a clash is filed Open,
			// and the modal's control is the one asking.
			// Each of these resolves to `r.message`, not `r`. The modal reads
			// `stored.file_url` and `created.topic_guid` straight off what it is given,
			// and handing it the response ENVELOPE -- the mistake this file already
			// made six times and fixed in `payload()` above -- would make both reads
			// undefined and drop the viewpoint on the floor.
			storeSnapshot: (args) =>
				frappe
					.call({
						method: "erpnext_construction_ph.api.bcf.store_snapshot",
						args: { project: args.project, payload: args.payload },
					})
					.then(payload),
			onCreate: (args) =>
				frappe
					.call({
						method: "erpnext_construction_ph.api.bcf.create_topic",
						args: {
							project: args.project,
							bim_model: args.bim_model,
							title: args.title,
							// The modal names this `topic_type`; the endpoint calls it
							// `status`. Forwarded verbatim it is not a parameter at all, so
							// the call would 400 -- the silent-failure shape this session
							// has been removing.
							status: args.topic_type,
							priority: args.priority,
						},
					})
					.then(payload),
			addViewpoint: (args) =>
				frappe
					.call({
						method: "erpnext_construction_ph.api.bcf.add_viewpoint",
						args: {
							topic_guid: args.topic_guid,
							project: args.project,
							camera_vectors: args.camera_vectors,
							snapshot: args.snapshot,
						},
					})
					.then(payload),
			// A new topic has to appear in the pins and the issues list without a
			// reload, or the user files one and watches it not show up.
			onCreated: () => {
				this.load_pins();
				if (this.SELECTION) this.load_issues(this.SELECTION);
			},
		});
	},

	async init_sync() {
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/sync_engine.js"
		).catch((error) => {
			this.status(`The sync engine could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod) return;
		// Built with the module's own registry and resolver. The engine compares a
		// 3D element's measured value against the takeoff's, and nothing on this page
		// consumes it yet -- it is created, held on `this.SYNC`, and ready. It is
		// wired rather than skipped so the chain reflects what the product has, and
		// the missing consumer is a product gap, not a missing module.
		// THE READ HALF OF THE ELEMENT->REGION MAP, and it was simply absent.
		// MEASURED: `defaultResolve` reads `getMap` and nothing ever assigned it, so the
		// engine resolved null for EVERY element and the comparison never ran once. The
		// map was unwritable AND unreadable, which is a tooling gap wearing a data gap's
		// clothes. Cached because the engine asks per element and a round trip per ask is
		// not shippable; a miss is cached AS A MISS, so an element someone deliberately
		// left unmapped is asked about exactly once.
		const cache = new Map();
		mod.defaultResolve.getMap = (guid) => {
			if (cache.has(guid)) return cache.get(guid);
			if (!this.MODEL) return null;
			fetch(
				"/api/method/erpnext_construction_ph.api.bim_convert.element_map?model=" +
					encodeURIComponent(this.MODEL.name) + "&element_guid=" + encodeURIComponent(guid),
				{ credentials: "include", headers: { Accept: "application/json" } }
			)
				.then((r) => r.json())
				.then((body) => {
					const row = (body && body.message) || null;
					const mapped = row && row.linked ? row : null;
					cache.set(guid, mapped);
					return mapped;
				})
				.catch(() => {
					cache.set(guid, null); // a failed read must not be retried per frame
					return null;
				});
			return cache.get(guid) || null;
		};
		// Mapping an element invalidates the cache, or the change would not be visible
		// until the page reloaded -- which would make the button look broken.
		this.ELEMENT_MAP_CACHE = cache;
		this.SYNC = mod.createSyncEngine();
		// The split view's consumer, mounted HERE rather than in init_tabs. Both are
		// statements, because a method declaration at class level was MEASURED to break
		// this file's parse. And it must live where SYNC is created: init_sync is async,
		// so a statement in init_tabs runs before this.SYNC exists, and the consumer is
		// skipped SILENTLY -- the page renders with the shell and nothing in it.
		const splitHost = this.TABS && this.TABS.host("split");
		if (splitHost && !this.SPLIT) {
			import(
				/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/split_view.js"
			).then(
				(view) => {
					this.SPLIT = view.createSplitView(splitHost, this.SYNC, {
						takeoffClassFor: () => null,
					});
					if (this.SPLIT) this.SPLIT.render();
				}
			).catch((error) => {
				this.status(`The split view could not be loaded: ${error.message}`);
			});
		}
	},

	async init_schedule() {
		const slot = this.root.querySelector(".bim-ws-schedule-slot");
		if (!slot) return;
		const mod = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/schedule_overlay.js"
		).catch((error) => {
			this.status(`The schedule could not be loaded: ${error.message}`);
			return null;
		});
		if (!mod) return;
		this.SCHEDULE = mod.createScheduleOverlay(slot, {
			// Scrubbing the schedule moves the viewer to that point in time. There is
			// no 4D model behind it yet, so the honest action is to say where the user
			// is rather than to pretend the geometry moved.
			onScrub: (at) => this.status(`Schedule position: ${String(at)}`),
		});
	},

	async init_tabs() {
		const tabs = await import(
			/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/viewport_tabs.js"
		).catch((error) => {
			this.status(`Viewport tabs could not be loaded: ${error.message}`);
			return null;
		});
		if (!tabs) return;
		this.TABS = tabs.createTabs(this.$tabslot, (mode) => this.on_mode(mode));
		this.tabs_api = tabs;
		// The 3D tab is the landing tab: it is the one with content today, and
		// landing on an empty PDF or split viewport would read as a broken page.
		this.TABS.show("3d");
		// The PDF viewport is real (UI-1). The split view is not yet, and says so
		// rather than rendering blank -- a blank panel is indistinguishable from a
		// broken one, and this one genuinely is not built.
		const split = this.TABS.host("split");
		if (split && !split.innerHTML.trim()) {
			// The PDF viewport is MOUNTED HERE too, not remounted on tab switches --
			// the same rule the 3D tab follows, and the same reason.
			split.innerHTML = `
				<div class="bim-split-sync">
					<button class="btn btn-xs btn-default bim-split-toggle" title="Sync selection between the model and the sheet">Sync Selection</button>
					<span class="bim-split-hint"></span>
				</div>
				<div class="bim-split-panes">
					<div class="bim-split-pane" data-pane="3d"><div class="bim-split-label">3D Model</div></div>
					<div class="bim-split-pane" data-pane="pdf"><div class="bim-split-label">PDF Plan</div></div>
				</div>`;
		}
		this.mount_pdf();
	},

	/**
	 * Mount the PDF viewport into its tab host, ONCE.
	 *
	 * `TABS.mount` refuses a second call, so this cannot be reintroduced as a
	 * remount by a later wave -- the same guarantee the 3D viewport has.
	 */
	async mount_pdf() {
		const host = this.TABS.host("pdf");
		if (!host) return;
		let mod;
		try {
			mod = await import(
				/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/pdf_viewport.js"
			);
		} catch (error) {
			host.innerHTML = `<div class="bim-ws-empty">The PDF viewport could not be loaded: ${frappe.utils.escape_html(
				error.message
			)}</div>`;
			return;
		}
		const viewport = mod.createPdfViewport(host, {
			onCalibrateRequest: (points) => this.calibrate(points, this.KNOWN_LENGTH_M),
		});
		this.PDF = viewport;
		this.TABS.mount("pdf", {
			setActive: (on) => {
				// Nothing to suspend yet: pdf.js holds no animation loop. Kept so
				// the tab contract is the same for every viewport.
				viewport.setActive && viewport.setActive(on);
			},
		});
	},

	/**
	 * The left panel's calibration card collects the KNOWN LENGTH; the viewport
	 * collects the two clicked POINTS. This is where the two meet and become a
	 * scale, and the result is pushed back to both halves so neither has to know
	 * about the other.
	 */
	async calibrate(points, known_length_m) {
		if (!points || points.length !== 2) return;
		if (!Number.isFinite(known_length_m) || known_length_m <= 0) {
			this.status("Enter the length you are calibrating against, in metres.");
			return;
		}
		const r = await frappe.call({
			method: "erpnext_construction_ph.api.takeoff_geometry.calibrate",
			args: { p1: points[0], p2: points[1], known_length_m: known_length_m },
		});
		const result = r.message || {};
		if (this.PDF) {
			this.PDF.setCalibration({
				metres_per_unit: result.metres_per_unit,
				known_length_m: known_length_m,
				points: points,
			});
		}
		if (this.PANEL) this.PANEL.setCalibration({
			metres_per_unit: result.metres_per_unit,
			known_length_m: known_length_m,
			points: points,
		});
		this.status(`Calibrated at ${Number(result.metres_per_unit).toFixed(6)} m per unit.`);
	},

	capture(payload) {
		this.status("Open a plan on the PDF tab, then capture an area from here.");
	},

	TABS_ACTIVE_LABEL(mode) {
		return { pdf: "The PDF takeoff viewport", split: "The split view" }[mode] || mode;
	},

	on_mode(mode) {
		// The spec: "The left panel adapts its content based on the active viewport
		// tab." Without this line the panel showed the 3D card set on EVERY tab --
		// which the unit tests could not catch, because they drive the panel module
		// directly and never exercise this wiring. The browser probe caught it.
		if (this.PANEL) {
			this.PANEL.setMode(mode);
		}
		// The takeoffs card is a PDF-mode card; the rest are always visible.
		if (this.RIGHT) this.RIGHT.setMode(mode);

		// The RIGHT panel describes the 3D model specifically. Leaving it populated
		// and undimmed while the user reads a sheet would be a lie about what it
		// refers to: the selection on it is an element that is not on screen.
		const three_d = mode === "3d";
		this.root.querySelectorAll(".bim-ws-right").forEach((node) => {
			node.classList.toggle("bim-ws-dimmed", !three_d);
		});
	},

	status(message) {
		// Never throws. `status()` is how this page REPORTS a failure, so a throw
		// from inside it converts a visible error into silence -- and it can be
		// called from a `catch`, where a throw is swallowed by the promise chain and
		// the user sees a page that simply never populated. Losing the status
		// element is not a reason to lose the message.
		if (this.$status) {
			this.$status.textContent = message;
			return;
		}
		if (window.console) console.warn("bim-workspace status (no element):", message);
	},

	load_models() {
		frappe.call({
			// The real method name. An earlier draft of this page called
			// `get_topics`/`get_model`, neither of which exists -- a Desk Page that
			// calls a phantom method renders a blank panel and reports nothing, which
			// is how a missing endpoint hides until someone opens the page.
			method: "erpnext_construction_ph.api.bim_viewer.list_models",
			callback: (r) => {
				const models = payload(r);
				if (!models || !models.length) {
					// An empty state that says what to do, not a blank panel. A viewer
					// showing nothing with no explanation is indistinguishable from a
					// broken one.
					this.status("No BIM models for this project yet. Upload an .ifc to a BIM Model record to begin.");
					return;
				}
				if (this.PANEL) {
					this.PANEL.setModels(models, models[0].name);
				} else {
					// The panel failed to load. Saying so beats rendering a dropdown
					// that drives nothing.
					this.status(
						'The left panel could not be loaded, so models cannot be chosen.'
					);
					return;
				}
				this.load_model(models[0].name);
				this.load_model(models[0].name);
			},
		});
	},

	load_model(name) {
		if (!name) return;
		this.status(`Loading ${name}…`);
		frappe.call({
			// `list_models` filtered client-side, not a `get_model` that does not
			// exist: adding a new endpoint for one row would be new surface to
			// authorise and test, when the list already returns the fields.
			method: "erpnext_construction_ph.api.bim_viewer.list_models",
			args: { project: this.project },
			callback: (r) => {
				const rows = payload(r) || [];
				// list_models returns a LIST, so pick the row rather than treating the
				// response as a single document -- the shape mismatch is silent and
				// would leave this.MODEL undefined.
				const model = (rows || []).find((r) => r.name === name);
				if (!model) return;
				this.MODEL = model;
				// The issues are fetched HERE, with the model, and not inside the
				// geometry branch below. A model whose .frag has not been converted
				// answers `get_frag_url` with an error, and the pin fetch sat inside
				// that success path -- so a model with no geometry yet showed no
				// issues at all, and an empty overlay is indistinguishable from a
				// model that genuinely has none. The issues exist whether or not the
				// geometry renders.
				this.load_pins();
				// Ask the server whether geometry is actually servable, rather than
				// inferring it from two fields. `get_frag_url` distinguishes "never
				// converted" from "the cached file is gone", and a page that guesses
				// shows the same blank canvas for both -- and for a third cause, a
				// permission refusal, which is not this caller's to diagnose.
				frappe.call({
					method: "erpnext_construction_ph.api.bim_frag.get_frag_url",
					args: { model: name },
					callback: (r) => {
						const outcome = payload(r);
						if (!outcome || !outcome.available) {
							this.status((outcome && outcome.message) || "No geometry available.");
							this.show_canvas_message(
								"No geometry to display.",
								(outcome && outcome.reason) || ""
							);
							return;
						}
						this.MODEL = model;
						this.mount_engine(model, outcome.frag_url);
					},
				});
			},
		});
	},

	/**
	 * Put a message where the model would be, if the canvas exists.
	 *
	 * The canvas is created by `init_tabs`, so it is legitimately absent when a
	 * tab host is missing or the dynamic import failed. Writing to it unguarded
	 * throws `Cannot read properties of null`, which names this function rather
	 * than the real fault.
	 */
	show_canvas_message(message, detail) {
		if (!this.$canvas) return;
		this.$canvas.innerHTML =
			`<div class="bim-ws-empty">${frappe.utils.escape_html(message)}<br><small>${frappe.utils.escape_html(
				detail || ""
			)}</small></div>`;
	},

	async mount_engine(model, fragUrl) {
		if (!this.$canvas) {
			this.status("The viewport is not available, so the model cannot be shown.");
			return;
		}
		// Loaded on demand, not from the geometry namespace at page load. The engine
		// is an ES module under /assets, and importing it eagerly would pull the
		// whole thatopen bundle onto the accounting pages.
		let geometry;
		try {
			geometry = await import(
				/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/geometry.js"
			);
		} catch (error) {
			this.status(`Geometry engine could not be loaded: ${error.message}`);
			return;
		}
		// `mount` is a method on the namespace object this module attaches, not a
		// named export, so the module object is not where it lives. Calling
		// `geometry.mount(...)` therefore threw "geometry.mount is not a function"
		// and the canvas stayed empty -- while the engine had loaded cleanly and
		// every other part of the page looked healthy. `bim_viewer.js` already
		// resolved this correctly with `module.default || module`; the page did not,
		// and the two disagreed about the same module.
		const engineApi = geometry.default || geometry;
		try {
			// `$canvas` is a raw DOM element from `querySelector`, NOT a jQuery
			// collection, so it is passed through as-is. Indexing it -- `this.$canvas[0]`
			// -- handed `mount()` `undefined`, and thatopen refused with "This renderer
			// needs to have an HTML container!" after three earlier failures had
			// already been fixed in this function. The `$` in the name is what invites
			// the mistake; every other use of it here (`innerHTML`, the falsy guard) is
			// consistent with a plain element.
			this.ENGINE = await engineApi.mount(this.$canvas, {
				fragUrl: fragUrl,
				// `mount` calls `onReady(engine)` from INSIDE itself, before it returns.
				// Assigning from the callback rather than from the awaited result is
				// what makes `this.ENGINE` exist when `render_tree` runs -- otherwise
				// the guard there returns early and the spatial tree silently never
				// renders, with no error to explain it.
				onReady: (engine) => {
					this.ENGINE = engine;
					// Contained deliberately. `render_tree` is called from INSIDE
					// `mount`, so anything it throws propagates out and the page
					// reports "Could not display the model" while the model is on
					// screen. That is worse than a missing tree: it tells the user the
					// opposite of what is true, and the canvas is fine.
					this.render_tree().catch((error) => {
						this.status(
							`The model is shown, but its spatial tree could not be drawn: ${
								(error && error.message) || "unknown error"
							}`
						);
					});
				},
				onPick: (globalId) => this.on_pick(globalId),
			});
			this.status("");
			// The areas post WITH the model, in the same visit, rather than needing a
			// second action. Fire-and-forget on purpose: the model is already on
			// screen and the areas are a background concern, so nothing here may delay
			// or interrupt what the user came for.
			this.measure_areas_background();
		} catch (error) {
			// The engine's own error names the missing vendor bundle; surfacing it
			// beats a blank canvas that looks like a model that failed to load.
			//
			// The STACK goes to the console as well. The message alone produced four
			// wrong hypotheses in a row during this work -- each plausible, each
			// refuted by the next measurement -- because a bare message names a
			// symptom and not a line.
			this.status(`Could not display the model: ${error.message}`);
			if (window.console) console.error("bim-workspace mount failed", error && (error.stack || error));
		}
	},

	/**
	 * Measure and upload the per-element areas for the model on screen.
	 *
	 * Fire-and-forget, and the reasons are learned in this file rather than assumed.
	 * The model is ALREADY RENDERED by the time this runs, so a failure here must not
	 * surface as a failure to show the model -- the canvas is fine and saying otherwise
	 * tells the user the opposite of what is true. It is also not awaited by the
	 * caller: awaiting would hold the page in a "loading" state for the length of a
	 * 1,502-element measurement, and the user would watch a working model sit there
	 * apparently still loading.
	 *
	 * `status()` takes ONE argument and this file's convention is that the wording
	 * carries the severity -- "the model is shown, but..." rather than a tone flag. An
	 * earlier draft passed "info" and "warning" as a second argument, which JavaScript
	 * silently discards: the code looked deliberate and the severity was simply lost.
	 */
	measure_areas_background() {
		if (!this.ENGINE || !this.MODEL) return;
		// ONE MEASUREMENT AT A TIME. `mount_engine` runs more than once during a single
		// page load -- MEASURED, 20 `record_areas` POSTs where 4 chunks of data were
		// expected -- and they all raced. A "have the areas been measured" check cannot
		// settle that, because while the first run is still writing, the others each see
		// an incomplete count and post anyway. The work is therefore CLAIMED before it
		// starts, not recognised after it finishes.
		//
		// The claim is RELEASED in a `finally`. Set-once would mean the first
		// measurement disables every later one for the life of the page, and a FAILED
		// one would lock the page out of ever measuring again -- the exact silent
		// permanent failure this whole change exists to remove.
		if (this._measuringAreas) return;
		this._measuringAreas = true;

		const engine = this.ENGINE;
		const modelName = this.MODEL.name;

		Promise.resolve()
			.then(() =>
				engine.uploadAreas({
					model: modelName,
					onProgress: (done, total) => {
						// Only for a model big enough to notice. Rewriting the status line
						// every few rows flickers and is noise rather than information.
						if (total > 200 && done % 200 === 0) {
							this.status(`Measuring areas: ${done} of ${total}…`);
						}
					},
				})
			)
			.then((result) => {
				// The already-measured path returns before any work happens. Reporting
				// it as "measured 0, stored 0" would be a false statement about a model
				// holding 1,502 areas, so it says plainly that nothing was needed --
				// which is what happened.
				// `alreadyMeasured`, NOT `skipped`. Both are counts and only one of
				// them is ever set on a given run; testing the wrong one reads a
				// normal run that skipped 24 elements as an already-measured one and
				// prints "already measured for undefined elements".
				if (result && result.alreadyMeasured) {
					this.status(`Areas were already measured for ${result.alreadyMeasured} elements.`);
					return;
				}
				const sent = (result && result.sent) || 0;
				const measured = (result && result.measured) || 0;
				const skipped = (result && result.skipped) || 0;
				// Skipped is REPORTED, never hidden. Those are elements measured but not
				// joinable to a row, and a count that quietly omits them is how a
				// shortfall becomes invisible.
				this.status(
					`Measured areas for ${measured} elements and stored ${sent}` +
						(skipped ? ` (${skipped} not attached to a named element).` : ".")
				);
			})
			.catch((error) => {
				// The model is on screen and stays there. Say only that the areas did not
				// complete, and why, without implying the model failed.
				//
				// `storedSoFar` IS the whole point. `uploadAreas` puts it on the error when a
				// chunk fails partway, and without reading it here the counter is discarded
				// at the boundary and the user is told "its areas were not stored" while 800
				// of 1,502 are. An accurate number computed and then thrown away is the same
				// false statement with extra work behind it.
				const done = error && error.storedSoFar;
				const total = error && error.attempted;
				this.status(
					typeof done === "number"
						? `The model is shown, but only part of its areas was stored: ${done} of ${total} elements before the step failed. ${
								(error && error.message) || "unknown error"
							}`
						: `The model is shown, but its areas were not stored: ${
								(error && error.message) || "unknown error"
							}`
				);
				if (window.console) {
					console.error("bim-workspace area measurement failed", error && (error.stack || error));
				}
			})
			.finally(() => {
				// Release the claim on EVERY path, including a throw. Set-once would make
				// the first measurement the last one for the life of the page, and a
				// FAILED one would lock the page out permanently -- so a transient error
				// would quietly become a permanent one. The comment above promises this;
				// without it, the promise is a lie.
				this._measuringAreas = false;
			});
	},

	async render_tree() {
		if (!this.ENGINE) return;
		// Awaited: the engine's `tree()` is async because the spatial structure is.
		// It was called synchronously, so `rows` was a Promise and every `.length`
		// and `.map` below was undefined -- which surfaced as "No spatial tree in
		// this model" for a model that has one.
		const rows = await this.ENGINE.tree();
		if (this.PANEL) {
			this.PANEL.setTree(rows);
			return;
		}
		if (!rows.length) {
			const host = this.root.querySelector(".bim-ws-tree") || this.PANEL_HOST;
		if (host) {
			host.innerHTML = `<div class="bim-ws-empty">No spatial tree in this model.</div>`;
		}
			return;
		}
		const host = this.root.querySelector(".bim-ws-tree") || this.PANEL_HOST;
		if (host) host.innerHTML = rows
			.map(
				(r) =>
					`<div class="bim-ws-node" style="padding-left:${r.depth * 12}px" data-guid="${r.id}">` +
					`<span class="bim-ws-dot"></span>${frappe.utils.escape_html(r.name || r.id)}</div>`
			)
			.join("");
	},

	/**
	 * The host a panel's content goes into, or `null` when there is none.
	 *
	 * THE BUG THIS EXISTS TO PREVENT, because it is the product's central action and it
	 * has never worked. `on_pick` read `this.$new_issue`, `this.$props` and
	 * `this.$issues` directly. A search of the whole codebase for an assignment to any
	 * of them returns NOTHING, and at runtime all three are `undefined`; the markup
	 * they name is absent from the DOM, because the page moved to a right panel and
	 * these three lines were left addressing the old one. So `on_pick` threw
	 * `Cannot set properties of undefined (setting 'disabled')` on its SECOND
	 * statement, before resolving anything -- and 483 green tests never noticed,
	 * because every one of them asserts on a MODULE and none drives this function.
	 *
	 * So nothing here dereferences a panel host directly. A missing host is a state to
	 * report, never a crash: a user who clicks an element and sees nothing cannot tell
	 * that from a broken page, and cannot tell that from a bug they should report.
	 */
	props_host() {
		return this.$props || null;
	},

	issues_host() {
		return this.$issues || null;
	},

	/** Put content in the properties panel, or say plainly that there is no panel. */
	set_props(html) {
		if (this.RIGHT) {
			// The right panel renders from a resolved ELEMENT, not from HTML, and it
			// already handles the empty case with its own message. Handing it `null` is
			// meaningful and safe; see `right_panel.renderProperties`.
			return;
		}
		const host = this.props_host();
		if (host) host.innerHTML = html;
	},

	/**
	 * A pick is the moment the product is for: a GlobalId comes back from the
	 * engine, and the server resolves it to the indexed element and its issues.
	 */
	on_pick(globalId) {
		this.SELECTED = globalId;
		// Guarded, because the button this enables may not exist on this page build.
		if (this.$new_issue) this.$new_issue.disabled = false;
		// The loading state goes through the right panel, which has its own.
		this.set_props(
			`<div class="bim-ws-loading">Resolving ${frappe.utils.escape_html(globalId)}…</div>`
		);
		frappe.call({
			method: "erpnext_construction_ph.api.bim_convert.resolve_element",
			args: { model: this.MODEL.name, element_guid: globalId },
			callback: (r) => {
				const element = payload(r);
				// `not_indexed` is checked BEFORE `!element`. The endpoint now RETURNS an
				// unindexed element instead of throwing, so the payload is truthy and a
				// bare `!element` test would walk straight past this branch into
				// `render_props`, which would render a row of em-dashes and read as a
				// successfully-resolved element with no data.
				if (!element || element.not_indexed) {
					// The element is not in the server index. That is a real and
					// reportable state (the IFC and the index disagree), so it is
					// named rather than rendered as an empty panel -- AND the notice is
					// passed to the panel explicitly, because `renderProperties(null)`
					// on its own says "Select an element", which is a different claim
					// entirely: something WAS selected. My own guard introduced that
					// regression by routing the message to a host that does not exist
					// on this page, and the gate caught it.
					const notice =
						`This element is not in the server index. The IFC and the index disagree.<br><small>${frappe.utils.escape_html(
							globalId
						)}</small>`;
					if (this.RIGHT) this.RIGHT.renderProperties(null, notice);
					this.set_props(`<div class="bim-ws-empty">${notice}</div>`);
					const issues = this.issues_host();
					if (issues) issues.innerHTML = "";
					if (this.RIGHT) this.RIGHT.renderIssues([], globalId);
					// The status line too, so the state is visible wherever the user is
					// looking -- a message that only exists in one panel is a message
					// that is missed whenever the user is in another tab.
					this.status("That element is not in this model's index, so it has no properties or issues.");
					return;
				}
				this.render_props(element);
				this.load_issues(globalId);
			},
		});
	},

	render_props(element) {
		if (this.RIGHT) {
			this.RIGHT.renderProperties(element);
			return;
		}
		if (!element) return;
		const rows = [
			["Name", element.element_name],
			["Type", element.element_type],
			["GlobalId", element.element_guid],
			["IfcId", element.ifc_id],
		];
		this.set_props(
			`<table class="bim-ws-props-table">` +
				rows
					.filter((r) => r[1])
					.map((r) => `<tr><td>${r[0]}</td><td>${frappe.utils.escape_html(String(r[1]))}</td></tr>`)
					.join("") +
				`</table>`
		);
	},

	load_issues(globalId) {
		frappe.call({
			method: "erpnext_construction_ph.api.bim_viewer.list_topics",
			args: { bim_model: this.MODEL.name },
			callback: (r) => {
				const topics = payload(r) || [];
				this.TOPICS = topics || [];
				if (this.RIGHT) this.RIGHT.renderIssues(this.TOPICS, this.SELECTION);
				// A BCF topic's elements live in a CHILD table (`BCF Element
				// Reference.global_id`), not in a top-level `element_guid` column.
				// Filtering on a field that does not exist yields [], which renders
				// "no issues on this element" on EVERY element and reads as a
				// truthful answer.
				const mine = (topics || []).filter((t) =>
					(t.elements || []).some((e) => e.global_id === globalId)
				);
				// The same missing-host hazard as `$props`, in the issues column. The
				// right panel already rendered `this.TOPICS` on the line above, so this
				// legacy path is a fallback -- and a fallback that throws is worse than
				// no fallback, because it aborts the callback AFTER the panel is already
				// correct and the user sees the error for no visible reason.
				const host = this.issues_host();
				if (!host) return;
				if (!mine.length) {
					host.innerHTML = `<div class="bim-ws-empty">No issues on this element.</div>`;
					return;
				}
				host.innerHTML = mine
					.map(
						(t) =>
							`<div class="bim-ws-issue" data-topic="${t.name}">` +
							`<span class="bim-ws-issue-title">${frappe.utils.escape_html(t.title || "")}</span>` +
							`<span class="bim-ws-issue-meta">${frappe.utils.escape_html(
								t.status || ""
							)}</span></div>`
					)
					.join("");
			},
		});
	},

	new_issue() {
		if (!this.SELECTED) return;
		// Pre-fill the GUID so the coordinator records the element they clicked rather
		// than typing a 22-character id by hand — the error this whole join exists to
		// prevent.
		frappe.model.open_mDoc("BCF Topic", {
			element_guid: this.SELECTED,
			bim_model: this.MODEL.name,
		});
	},

	reset_view() {
		if (this.ENGINE && this.ENGINE.world) {
			const camera = this.ENGINE.world.camera;
			if (camera && camera.controls && camera.controls.fitToCenter) {
				camera.controls.fitToCenter();
			}
		}
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
// 404s because it is called with the ROUTE name ("bim-workspace") while the Page
// record is "bim_workspace". `frappe.views.Page` then hits `frappe.show_not_found`
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
frappe.standard_pages["bim-workspace"] = function () {
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
	// WHERE THE PAGE GOES, and it is not `document.body`.
	//
	// Frappe's desk sets `body { display: flex }` and fills it with SIBLINGS:
	// `.dock`, `.body-sidebar-contain`, `.main-section`, modals. Appending a
	// `.page-body` to `body` therefore does not put the page inside the desk -- it
	// puts it BESIDE the entire desk, as one more flex item competing for width.
	// Measured: the workspace got 519px of a 1280px viewport, with `.main-section`
	// taking the rest, and the model viewport 0px wide.
	//
	// So: mount into the Desk's own content container when there is one, and only
	// fall back to creating a `.page-body` on `body` when the desk shell is absent
	// (a cold load, which is what the original code was written against and why
	// it looked correct then).
	const desk = document.querySelector(
		".layout-main-section, .main-section, .page-container .page-body"
	);
	let wrapper;
	if (desk) {
		wrapper = desk;
	} else {
		wrapper = document.createElement("div");
		wrapper.className = "page-body";
		// The desk shell is absent, so `body` is not yet a competing flex row --
		// but it will become one. Grow explicitly rather than relying on that.
		wrapper.style.flex = "1 1 auto";
		wrapper.style.minWidth = "0";
		document.body.appendChild(wrapper);
	}
	wrapper.innerHTML = "";
	wrapper.innerHTML = `<div class="bim-ws-root"></div>`;
	workspace.root = wrapper.querySelector(".bim-ws-root");
	workspace.onload();
};
