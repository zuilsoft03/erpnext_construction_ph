// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The viewport tab bar, and the one rule it exists to enforce.
//
// Spec: "Each tab swaps the viewport content **without destroying state** — the 3D
// engine stays mounted, the PDF canvas stays rendered. This is critical: remounting
// the WebGL context on every tab switch costs 2-3 seconds and loses camera position."
//
// So this module is built around a single structural decision: ALL viewports exist
// in the DOM at once, and switching changes which one is VISIBLE. It never removes
// a node, never tears down a context, and never re-runs a mount.
//
// The alternative — unmount the old one, mount the new one — is what the spec is
// warning against, and it is also the thing that is easiest to write by accident,
// because it is how a framework router behaves by default. Hence the deliberate
// awkwardness below: `hidden` and a class, never `innerHTML = ""` and never
// `dispose()`.
//
// Camera position is the other half of the contract, and it survives for free
// because the camera object is never recreated. What does NOT survive for free is
// the RENDER LOOP: a WebGL context whose canvas is `display:none` is not presented,
// and a paused context that is simply un-paused keeps its depth buffer, but one
// that was disposed and rebuilt does not. So the tabs also carry the per-viewport
// `activate`/`deactivate` hooks the engine needs to keep rendering the visible one.

/** The three viewports, in the order the spec's tab bar lists them. */
export const MODES = [
	{ id: "3d", label: "3D Model", title: "The IFC model, with issue pins and the floating toolbar" },
	{ id: "pdf", label: "PDF Takeoff", title: "Sheets for measuring and calibrating against the model" },
	{ id: "split", label: "Split View", title: "The model beside the sheet, with synchronised cameras" },
];

/**
 * Build the tab bar and the three viewport hosts.
 *
 * Every host is created up front and kept. `show()` only toggles visibility, so a
 * viewport that has been built once is never rebuilt: that is the entire point.
 *
 * @param {HTMLElement} root  where to build
 * @param {(mode: string) => void} [onChange]  called when the active mode changes
 * @returns {{el: HTMLElement, show: Function, active: Function, setBadge: Function,
 *            mount: Function, host: Function, isMounted: Function}}
 */
export function createTabs(root, onChange) {
	const el = document.createElement("div");
	el.className = "bim-ws-tabs";
	el.setAttribute("role", "tablist");

	// One host per mode, created ONCE and kept for the life of the page. This is the
	// line the whole module turns on: there is no code path that removes a host.
	const hosts = {};
	MODES.forEach((mode) => {
		const button = document.createElement("button");
		button.className = "bim-ws-tab";
		button.dataset.mode = mode.id;
		button.setAttribute("role", "tab");
		button.title = mode.title;
		button.innerHTML = `<span class="bim-ws-tab-label">${mode.label}</span><span class="bim-ws-tab-badge"></span>`;
		el.appendChild(button);

		const host = document.createElement("div");
		host.className = "bim-ws-viewport";
		host.dataset.mode = mode.id;
		// `hidden` rather than a display class, because `hidden` is honoured by the
		// flex layout too and cannot be accidentally outranked by a later rule.
		host.hidden = true;
		root.appendChild(host);

		hosts[mode.id] = { host, button, mounted: false, payload: null };
	});

	let current = null;

	/**
	 * Show one mode. Idempotent, and safe to call for a mode nobody has built yet:
	 * the host exists and is empty, which is a truthful "nothing here yet" rather
	 * than a crash.
	 */
	function show(modeId) {
		const target = hosts[modeId];
		if (!target) return;
		if (current === modeId) return;
		let changed = false;
		MODES.forEach((mode) => {
			const entry = hosts[mode.id];
			const active = mode.id === modeId;
			// Visibility only. Nothing is unmounted, disposed, or emptied.
			entry.host.hidden = !active;
			entry.button.classList.toggle("active", active);
			entry.button.setAttribute("aria-selected", active ? "true" : "false");
			if (active !== (current === mode.id)) changed = true;
		});
		current = modeId;
		// Only the visible viewport needs to keep presenting frames; the others are
		// suspended rather than destroyed, which is the same distinction this whole
		// module is about, one level down.
		MODES.forEach((mode) => {
			const entry = hosts[mode.id];
			if (typeof entry.payload?.setActive === "function") {
				entry.payload.setActive(mode.id === modeId);
			}
		});
		if (changed && onChange) onChange(modeId);
	}

	el.addEventListener("click", (event) => {
		const button = event.target.closest(".bim-ws-tab");
		if (button && button.dataset.mode) show(button.dataset.mode);
	});

	// Arrow-key navigation, because a tab bar that only responds to a mouse is not
	// reachable without one — and this is a keyboard-heavy tool.
	el.addEventListener("keydown", (event) => {
		if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
		const index = MODES.findIndex((m) => m.id === current);
		const step = event.key === "ArrowRight" ? 1 : -1;
		const next = MODES[(index + step + MODES.length) % MODES.length];
		show(next.id);
		hosts[next.id].button.focus();
		event.preventDefault();
	});

	// The bar appends ITSELF, and it goes FIRST so the hosts follow it. Returning
	// `el` for the caller to place was the original shape, and it is a trap: a
	// caller that forgets -- or a second caller added later -- gets three working
	// viewport hosts and no way to switch between them, with no error anywhere.
	root.appendChild(el);

	return {
		el,
		show,
		active: () => current,
		host: (modeId) => hosts[modeId]?.host || null,
		/**
		 * Hand a viewport its built content ONCE. A second call is refused rather
		 * than silently replacing the first, because replacing it is the remount
		 * this module exists to prevent -- and doing it quietly would hide the bug.
		 */
		mount: (modeId, payload) => {
			const entry = hosts[modeId];
			if (!entry) throw new Error(`Unknown viewport mode: ${modeId}`);
			if (entry.mounted) {
				console.warn(`[bim] viewport "${modeId}" is already mounted; ignoring re-mount`);
				return entry.payload;
			}

			entry.mounted = true;
			entry.payload = payload;
			// A viewport built while its tab is hidden must still know it is hidden,
			// or it starts a render loop nobody is watching.
			if (typeof payload?.setActive === "function") payload.setActive(modeId === current);
			return payload;
		},
		isMounted: (modeId) => Boolean(hosts[modeId]?.mounted),
		/** A short count on a tab, e.g. issues picked up for the 3D tab. Empty clears it. */
		setBadge: (modeId, count) => {
			const entry = hosts[modeId];
			if (!entry) return;
			const badge = entry.button.querySelector(".bim-ws-tab-badge");
			badge.textContent = count === null || count === undefined || count === "" ? "" : String(count);
			badge.hidden = !badge.textContent;
		},
	};
}

/**
 * The camera state a tab switch must preserve, and how to verify it did.
 *
 * `viewPosition` is the point the camera looks at. If a switch loses it, the user
 * comes back to a model framed somewhere else and has to re-find where they were --
 * which is the cost the spec names, arrived at without the 2-3 second remount.
 */
export function cameraSignature(engine) {
	const camera = engine?.world?.camera?.three;
	if (!camera) return null;
	const target = engine.world.camera.controls?.target;
	return {
		position: [camera.position.x, camera.position.y, camera.position.z],
		target: target ? [target.x, target.y, target.z] : null,
	};
}

/** True when two camera signatures describe the same view. */
export function sameView(a, b, epsilon = 1e-6) {
	if (!a || !b) return false;
	const close = (p, q) =>
		p && q && p.length === q.length && p.every((v, i) => Math.abs(v - q[i]) < epsilon);
	return close(a.position, b.position) && (!a.target || !b.target || close(a.target, b.target));
}
