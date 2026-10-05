// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// Takeoff Studio is now a SHIM over `bim/pdf_viewport.js`.
//
// It was a standalone Desk Page with its own canvas, its own calibration, its own
// polygon capture, and no undo. The unified BIM Workspace (UI-1) needed all of that
// inside a tab, and the plan's instruction is explicit: "extract takeoff_studio.js
// into the tab, not a rewrite". So the arithmetic, the coordinate conversion and
// the drawing now live in ONE module, and this page mounts it.
//
// Keeping this page at all is deliberate. It is a working route that people have
// bookmarked, and removing it would break them for no gain; the alternative --
// leaving a second full implementation here -- is how the two drifted before, and
// how the Y-flip defect survived in this file while the spec described it as
// correct.
//
// THIS FILE IS THE ONE THAT RUNS. It lives at public/js/pages/ because that is
// where Frappe serves a Desk Page's script from. An identical copy used to sit at
// construction_ph/page/takeoff_studio/takeoff_studio.js; that path returns 404 and
// is not served, and a shadow copy is exactly how an edit lands somewhere the
// browser never reads. The copy has been deleted.

frappe.provide("erpnext_construction_ph.page.takeoff_studio");

const SHIM_MARKER = "bim-pdf"; // the class the mounted module renders into

frappe.standard_pages["takeoff_studio"] = function () {
	// Built rather than found, and only ever its own node. The chain
	// `.page-container || .layout-main || document.body` this app used to carry
	// resolves to `document.body` in this Frappe -- there is no `.page-container`
	// and no `.layout-main` -- and writing innerHTML to it REPLACES the whole Desk.
	// See the same note at length in bim_workspace.js.
	let wrapper = document.querySelector("body > .page-body");
	if (!wrapper) {
		wrapper = document.createElement("div");
		wrapper.className = "page-body";
		document.body.appendChild(wrapper);
	}
	wrapper.innerHTML = "";

	wrapper.innerHTML = `
		<div class="bim-ws-root">
			<div class="bim-ts-side">
				<div class="bim-ws-block">
					<div class="bim-ws-label">${frappe.utils.escape_html(__("Takeoff Sheet"))}</div>
					<div id="takeoff-sheet"></div>
				</div>
				<div class="bim-ws-block">
					<div class="bim-ws-label">${frappe.utils.escape_html(__("Known length (m)"))}</div>
					<div id="known-length-wrap"></div>
				</div>
				<div class="bim-ws-block">
					<div class="bim-ws-label">${frappe.utils.escape_html(__("BOQ Item"))}</div>
					<div id="boq-item-wrap"></div>
				</div>
			</div>
			<div class="bim-ts-main" id="${SHIM_MARKER}-host"></div>
		</div>`;

	// Native <input>s, not `frappe.ui.LinkField` / `frappe.ui.FileInput`: neither
	// constructor exists in this Frappe, and `new frappe.ui.FileInput(...)` throws
	// `TypeError: frappe.ui.FileInput is not a constructor` on every load, leaving
	// the controls the page cannot work without empty. Measured in the browser.
	wrapper.querySelector("#takeoff-sheet").innerHTML =
		`<input type="text" class="form-control" id="takeoff-sheet-input" placeholder="${frappe.utils.escape_html(
			__("Takeoff Sheet")
		)}">`;
	wrapper.querySelector("#known-length-wrap").innerHTML =
		`<input type="number" step="0.001" class="form-control" id="known-length" placeholder="10.000">`;
	wrapper.querySelector("#boq-item-wrap").innerHTML =
		`<input type="text" class="form-control" id="boq-item" placeholder="${frappe.utils.escape_html(
			__("BOQ Item")
		)}">`;

	import(/* webpackIgnore: true */ "/assets/erpnext_construction_ph/js/bim/pdf_viewport.js")
		.then((mod) => {
			const host = wrapper.querySelector(`#${SHIM_MARKER}-host`);
			// The page owns the known length; the module owns the two clicked
			// points. `onCalibrateRequest` is the seam between them -- the module
			// has no business reading a form field it does not render, and the page
			// has no business knowing what PDF user space is.
			async function onCalibrateRequest(points) {
				const raw = (wrapper.querySelector("#known-length") || {}).value || "";
				const known = parseFloat(raw);
				if (!Number.isFinite(known) || known <= 0) {
					frappe.msgprint({
						title: __("Known Length Required"),
						message: __("Enter the length you are calibrating against, in metres."),
						indicator: "orange",
					});
					return;
				}
				const r = await frappe.call({
					method: "erpnext_construction_ph.api.takeoff_geometry.calibrate",
					args: { p1: points[0], p2: points[1], known_length_m: known },
				// `json: true` is not a style choice; it is the difference between a
				// working page and one that cannot calibrate at all, and the reason was
				// MEASURED in the browser rather than inferred from the status code.
				//
				// `frappe.call` builds the request in `frappe/public/js/frappe/request.js`,
				// which picks the body encoding with
				//     let json = opts.json != null ? opts.json : frappe.request.app_uses_json(cmd);
				// `app_uses_json` is false for every app that has not set
				// `use_json_request_body = True` in its hooks.py, and this app has not.
				// So the default is the legacy form encoding, and `frappe.request.prepare`
				// begins with, verbatim:
				//     // stringify args if required (skipped when sending a native JSON body)
				//     if (!opts.use_json) { ... opts.args[key] = JSON.stringify(opts.args[key]); }
				// An ARRAY argument is therefore replaced by its own text before it ever
				// leaves the page. What the browser put on the wire, captured:
				//     POST /api/method/erpnext_construction_ph.api.takeoff_geometry.calibrate
				//     Content-Type: application/x-www-form-urlencoded; charset=UTF-8
				//     p1=%5B80%2C300.20001220703125%5D&p2=%5B320%2C300.20001220703125%5D&known_length_m=10
				// The server did not misread that. `_point()` accepts a dict or a
				// len-2 list/tuple, and a STRING is neither, so it refused:
				//     HTTP 417 -- "TakeoffError: Point [80,300.20001220703125] is not an
				//     (x, y) pair."
				// 417 rather than 500 because `TakeoffError` subclasses
				// `frappe.ValidationError`, whose `http_status_code` is 417.
				// `json: true` makes the same call carry a native JSON body, so the server
				// receives `p1` as the list `[80, 300.2]` the surveyor actually clicked,
				// and the arithmetic -- which was never wrong -- runs.
				//
				// Dropping the argument or stringifying it here instead would hide the
				// fault rather than fix it: the calibration is measured in the field, and a
				// scale computed from something other than the two clicked points is worse
				// than a refusal.
				json: true,
				});
				const result = r.message || {};
				viewport.setCalibration({
					metres_per_unit: result.metres_per_unit,
					known_length_m: known,
					points,
				});
				frappe.show_alert({
					message: __("Calibrated: {0} m per unit", [result.metres_per_unit.toFixed(6)]),
					indicator: "green",
				});
			}


			const viewport = mod.createPdfViewport(host, {
				onCalibrateRequest,
				onStatus: (message) => {
					const status = wrapper.querySelector(".bim-pdf-status");
					if (status) status.textContent = message;
				},
			});

			// The module owns the canvas, the tools, undo and page navigation. What
			// stays here is the page's OWN job: which sheet, which BOQ line, and the
			// known dimension -- the three values the module deliberately does not
			// know about, because they belong to the page's chrome.
			// The Known length field starts the gesture, so the surveyor types the
			// dimension and then clicks its two ends -- which is the order the spec's
			// sequence describes.
			const knownField = wrapper.querySelector("#known-length");
			if (knownField) {
				knownField.addEventListener("change", () => viewport.beginCalibration());
			}
		})
		.catch((error) => {
			wrapper.querySelector(`#${SHIM_MARKER}-host`).innerHTML =
				`<div class="bim-ws-empty">The takeoff viewport could not be loaded: ${frappe.utils.escape_html(
					error.message
				)}</div>`;
		});
};
