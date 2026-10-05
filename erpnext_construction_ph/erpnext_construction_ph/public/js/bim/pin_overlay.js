// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// BCF issue pins, and the camera jump when one is clicked.
//
// The spec's shape, with one thing changed on purpose:
//
//     pin.style.left = `${topic.screen_x}%`;
//     pin.style.top  = `${topic.screen_y}%`;
//
// The spec positions pins in PERCENTAGES. That is only correct while the viewport
// is exactly the size the percentages were computed against -- and they are not:
// `screen_x`/`screen_y` come from the server, computed against whatever canvas the
// issue was raised from, which is generally not the one now on screen. A pin at
// "60%" of a 1600px viewport and the "same" pin at 60% of a 900px viewport are
// different elements, so the pin drifts away from the thing it names the moment
// anyone resizes or opens the panel.
//
// So the module stores the pin's NORMALISED position (0..1) and re-derives pixels
// on every resize. The percentages are a display detail of one moment, not a
// coordinate; treating one as the other is how a marker ends up confidently
// pointing at the wrong wall.
//
// A second decision: pins are created ONCE and re-positioned, never re-created.
// Re-creating them on every render loses the focus the user had on the pin they
// were about to activate, and re-binds every handler each time.

/** Pin colour by status, per the spec's key. Unknown statuses are not hidden. */
export const PIN_COLOURS = {
	Open: "#e03131",
	"In Progress": "#f08c00",
	"In Review": "#7048e8",
	Resolved: "#2f9e44",
	Closed: "#868e96",
	Rejected: "#c92a2a",
};

const DEFAULT_COLOUR = "#495057";

export function pinColour(status) {
	return PIN_COLOURS[status] || DEFAULT_COLOUR;
}

/**
 * Create the pin overlay over a viewport host.
 *
 * @param {HTMLElement} host  the positioned container the canvas sits in
 * @param {(topic) => void} onSelect
 */
export function createPinOverlay(host, onSelect) {
	// The overlay is its own layer, so pins are not clipped by the canvas and a
	// pin can be clicked without the canvas' own pick handler seeing it first.
	const layer = document.createElement("div");
	layer.className = "bim-pin-layer";
	host.appendChild(layer);

	/** topic_guid -> { el, nx, ny } */
	const pins = new Map();
	/** The topic whose pin has focus, so a keyboard user can see where they are. */
	let focused = null;

	function setTopics(topics) {
		const seen = new Set();
		(topics || []).forEach((topic, index) => {
			const guid = topic.topic_guid || topic.name;
			if (!guid) return;
			seen.add(guid);

			let entry = pins.get(guid);
			if (!entry) {
				const el = document.createElement("button");
				el.type = "button";
				el.className = "bim-pin";
				el.dataset.guid = guid;
				layer.appendChild(el);
				entry = { el, nx: 0.5, ny: 0.5 };
				pins.set(guid, entry);
			}
			// Rebound every render, so a refreshed topic (a status that changed
			// while the panel was open) updates the pin in place. `onclick` ONLY:
			// binding both addEventListener and onclick made one click fire the
			// handler twice, so a single click flew to the viewpoint twice.
			entry.el.onclick = (event) => {
				event.stopPropagation();
				if (onSelect) onSelect(topic);
			};
			entry.el.textContent = String(index + 1);
			entry.el.title = `${topic.title || "Issue"} — ${topic.status || "Open"}`;
			entry.el.style.background = pinColour(topic.status);
			entry.el.setAttribute("aria-label", `Issue ${index + 1}: ${topic.title || ""}, ${topic.status || "Open"}`);

			// Server percentages -> normalised. Clamped, because a percentage
			// outside 0..100 would place a pin outside the viewport entirely, where
			// it is invisible AND unclickable -- an issue that looks unreported.
			entry.nx = clamp01(Number(topic.screen_x) / 100) || 0;
			entry.ny = clamp01(Number(topic.screen_y) / 100) || 0;
			entry.el.dataset.status = topic.status || "Open";
		});

		// Drop pins for topics that are gone, so a closed-then-reopened panel
		// cannot accumulate ghosts that still respond to clicks.
		pins.forEach((entry, guid) => {
			if (seen.has(guid)) return;
			entry.el.remove();
			pins.delete(guid);
		});
		layout();
	}

	function clamp01(value) {
		return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : null;
	}

	/** Re-derive pixel positions from the CURRENT viewport size. */
	function layout() {
		const width = host.clientWidth || 0;
		const height = host.clientHeight || 0;
		pins.forEach((entry) => {
			entry.el.style.left = `${(entry.nx * width).toFixed(1)}px`;
			entry.el.style.top = `${(entry.ny * height).toFixed(1)}px`;
		});
	}

	if (host.addEventListener) {
		host.addEventListener("resize", layout);
	}
	if (typeof ResizeObserver !== "undefined") {
		// `resize` on the host only fires for window resizes in some engines; the
		// panel opening changes the canvas without the window changing at all.
		new ResizeObserver(layout).observe(host);
	}

	return {
		setTopics,
		layout,
		count: () => pins.size,
		get: (guid) => pins.get(guid),
		focus: (guid) => {
			if (focused && pins.has(focused)) pins.get(focused).el.classList.remove("is-focused");
			focused = guid;
			if (pins.has(guid)) pins.get(guid).el.classList.add("is-focused");
		},
		focused: () => focused,
		element: layer,
	};
}

/**
 * Move the camera to a stored viewpoint, and highlight what it points at.
 *
 * `camera.controls.setLookAt(eye, target, up)` is the engine's own API, so this
 * does not reimplement camera control. What it adds is the failure reporting: a
 * viewpoint with no usable camera is a real state (an issue raised from a sheet
 * rather than from the model), and silently doing nothing makes a pin look broken.
 */
export function flyTo(engine, viewpoint, { highlight } = {}) {
	if (!engine) return { ok: false, reason: "no engine" };
	const controls = engine.world && engine.world.camera && engine.world.camera.controls;
	if (!controls || typeof controls.setLookAt !== "function") {
		return { ok: false, reason: "this engine exposes no setLookAt" };
	}
	const v = viewpoint || {};
	const eye = triple(v.eye || v.position || v.camera_position);
	const target = triple(v.target || v.look_at) || [0, 0, 0];
	const up = triple(v.up) || [0, 1, 0];
	if (!eye) return { ok: false, reason: "the viewpoint carries no eye point" };

	controls.setLookAt(eye, target, up);
	if (highlight && typeof engine.highlight === "function" && (v.element_guids || []).length) {
		engine.highlight(v.element_guids, "red");
	}
	return { ok: true, eye, target };
}

function triple(value) {
	if (!Array.isArray(value) || value.length !== 3) return null;
	const out = value.map((n) => Number(n));
	return out.every(Number.isFinite) ? out : null;
}
