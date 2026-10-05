// Copyright (c) 2026, Philippine AEC Engineering
// License: AGPL-3.0
// Read-only BIM surface for the `www` client portal.
//
// A `www` portal page is served WITHOUT Desk's boot: no `frappe.provide`, no
// jQuery, no `frappe.call`. So `public/js/bim/bim_viewer.js` — which is a Desk
// asset and does both — cannot load here, and loading it would throw on a page
// every client sees. `public/js/bim/geometry.js` CAN: it is a plain ES module and
// its only `frappe` touch is guarded by `typeof window !== "undefined" &&
// window.frappe`, so it exports `mount` and attaches nothing when there is no
// Desk. That is why the Desk path and this one can share one engine.
//
// WHY THE BYTES COME FROM AN ENDPOINT, NOT A URL
//
// The portal's payload hands the browser `frag_url`, which is
// `/private/files/bim-frag-<model>.frag` -- and a `Construction Client` entitled to
// that very model gets **403** on it. Frappe's `/private/files` authorisation wants
// READ on `File` and on the doctype the file hangs off, and a client role has
// neither. That is file DELIVERY, not WebGL: no amount of rendering work would have
// produced geometry, and the surface would have mounted and shown an empty canvas.
//
// So the bytes are fetched from `bim_frag.stream_frag`, which authorises through
// the same `_require_model_access` as `get_frag_url` and `store_frag`, turned into
// a blob URL, and handed to the engine. Read-only by construction: there is no
// pick handler and no toolbar, so a client cannot select, annotate or mutate.

const GEOMETRY = "/assets/erpnext_construction_ph/js/bim/geometry.js";
const STREAM = "/api/method/erpnext_construction_ph.api.bim_frag.stream_frag";

function say(el, message) {
	if (el) el.textContent = message;
}

async function mountPortalModel(surface) {
	const status = document.getElementById("bim-status");
	const model = surface.getAttribute("data-model");
	const fragUrl = surface.getAttribute("data-frag");

	if (!model) {
		say(status, "No model is shared with you for this project.");
		return;
	}

	// The fallback: the payload's own URL, if there is one. It is tried because a
	// Desk-role session CAN read /private/files, and because if this endpoint ever
	// stops answering, the page should still say something true rather than fail
	// silently in the console.
	let source = fragUrl;
	try {
		say(status, "Loading geometry…");
		const response = await fetch(`${STREAM}?model=${encodeURIComponent(model)}`, {
			credentials: "include",
		});
		if (!response.ok) {
			throw new Error(`the fragment endpoint answered ${response.status}`);
		}
		const bytes = await response.arrayBuffer();
		if (!bytes.byteLength) {
			throw new Error("the fragment endpoint returned an empty body");
		}
		// A blob URL rather than the bytes themselves: `mount` fetches, and this
		// keeps the engine's contract unchanged instead of adding a second way in.
		source = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
	} catch (error) {
		// Say what happened. A portal that silently shows an empty box is
		// indistinguishable from a portal with nothing to show.
		say(
			status,
			`The model could not be loaded for viewing: ${error.message}. The issues below are unaffected.`
		);
		return;
	}

	// The blob URL is NOT revoked here. `mount()` fetches it ASYNCHRONOUSLY, so a
	// revoke in a `finally` runs while that fetch is still in flight -- which showed
	// up as `net::ERR_FILE_NOT_FOUND` against the blob URL on every load, while the
	// canvas still appeared, which is the worst combination: a working-looking viewer
	// and a console full of failures. It is released on `pagehide`, which is the
	// only moment nothing can still be reading it.
	if (source !== fragUrl) {
		window.addEventListener("pagehide", () => URL.revokeObjectURL(source), { once: true });
	}

	try {
		const mod = await import(/* webpackIgnore: true */ GEOMETRY);
		const engine = (mod.default || mod).mount(surface, {
			fragUrl: source,
			// No `onPick`: this is the read-only surface, and a client who selects an
			// element must not be offered anything to do with it.
		});
		surface.classList.add("bim-surface--live");
		if (status) status.textContent = "";
		return engine;
	} catch (error) {
		say(status, `The viewer engine could not start: ${error.message}`);
	}
}

function initPortalBim() {
	const surface = document.getElementById("bim-surface");
	if (!surface) return;
	mountPortalModel(surface);
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", initPortalBim);
} else {
	initPortalBim();
}
