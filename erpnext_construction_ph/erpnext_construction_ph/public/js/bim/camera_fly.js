// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// A SMOOTH camera fly-to, which is what the viewport spec asks for and what the
// shipped code does not do.
//
// `pin_overlay.flyTo` calls the engine's own `controls.setLookAt(...)` and the camera
// teleports. Measured: nothing in this app animates the camera — the only hits for
// requestAnimationFrame, setInterval, easing, tween and lerp were the word "between"
// in comments. So the spec's `camera_fly.js`, "smooth camera fly-to animation", was
// never built; the hard cut was delivered in its place.
//
// WHY IT IS NOT COSMETIC. The spec's own flow ends "Fly camera to saved viewpoint".
// A viewpoint reached by teleporting tells the reader nothing about where they have
// landed in a 134 m building: they lose the spatial relationship the flight exists to
// convey. The hard cut is right for a machine and wrong for a person.
//
// WHAT IS DELIBERATELY NOT REIMPLEMENTED. The engine owns camera control, and this
// module never touches it directly — every frame goes through the SAME `setLookAt` the
// instant path used, so the engine's controls are never fought or bypassed. Only the
// VALUES change, and only over time.
//
// THREE THINGS THIS GETS RIGHT THAT A LOOP DOES NOT GET RIGHT BY ITSELF:
//
//   * It LANDS. The final frame is the exact camera an instant `setLookAt` would have
//     produced. An animation that ends somewhere else is a new bug wearing this fix
//     as a disguise, and it would not show up in a screenshot.
//
//   * A NEW FLIGHT CANCELS THE LAST ONE. Two loops writing `setLookAt` fight, and the
//     camera lands on neither target.
//
//   * REDUCED MOTION IS HONOURED. `animate: false` jumps, and so does a reader whose
//     system asks for less movement. "Always animate" is a regression against them.

const DEFAULT_MS = 700;
// A cubic ease-in-out. Linear motion reads as mechanical and ease-out alone reads as
// a snap followed by a drift; the flight is short, so the shape is the whole thing.
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

// THE FLIGHT IN FLIGHT, module-level on purpose. Two loops writing setLookAt fight
// and the camera lands on neither target, so a new flight cancels the last. The
// first version gave every flight its OWN cancelled flag, so a second flight never
// touched the first, they interleaved, and the camera ended wherever the last
// writer left it. The gate caught that.
let inFlight = null;

function triple(value) {
	if (!Array.isArray(value) || value.length !== 3) return null;
	const out = value.map((n) => Number(n));
	return out.every(Number.isFinite) ? out : null;
}

function mix(a, b, t) {
	return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Where the camera is RIGHT NOW, or null if the engine cannot say. */
export function currentLookAt(engine) {
	const camera = engine && engine.world && engine.world.camera;
	const controls = camera && camera.controls;
	if (!controls) return null;
	// Prefer the engine's own reading; fall back to the position it is already at,
	// so a flight can start from a camera the user dragged themselves.
	if (typeof controls.getLookAt === "function") {
		const at = controls.getLookAt();
		const eye = triple(at && (at.eye || at.position));
		const target = triple(at && at.target);
		if (eye) return { eye, target: target || [0, 0, 0], up: triple(at && at.up) || [0, 1, 0] };
	}
	if (camera && triple(camera.position && camera.position.toArray ? camera.position.toArray() : camera.position)) {
		return {
			eye: triple(camera.position.toArray ? camera.position.toArray() : camera.position),
			target: [0, 0, 0],
			up: [0, 1, 0],
		};
	}
	return null;
}

/** The destination a viewpoint names, or a reason there is not one. */
export function lookAtOf(viewpoint) {
	const v = viewpoint || {};
	const eye = triple(v.eye || v.position || v.camera_position);
	if (!eye) return { ok: false, reason: "the viewpoint carries no eye point" };
	return {
		ok: true,
		eye,
		target: triple(v.target || v.look_at) || [0, 0, 0],
		up: triple(v.up) || [0, 1, 0],
	};
}

/**
 * Fly the camera to a viewpoint over `ms`.
 *
 * Returns a handle with `cancel()` and `done`, so a caller can start a second flight
 * without leaking the first one's loop. A failure is reported in the SAME shape as
 * `pin_overlay.flyTo` so a caller does not have to learn two error vocabularies.
 */
export function flyTo(engine, viewpoint, options = {}) {
	const controls = engine && engine.world && engine.world.camera && engine.world.camera.controls;
	if (!engine) return { ok: false, reason: "no engine" };
	if (!controls || typeof controls.setLookAt !== "function") {
		return { ok: false, reason: "this engine exposes no setLookAt" };
	}
	const to = lookAtOf(viewpoint);
	if (!to.ok) return to; // a flight must not paper over a viewpoint it cannot use
	// A new flight supersedes whatever is running: two loops writing setLookAt fight,
	// and the camera lands on neither target. The first version gave every flight its
	// OWN `cancelled` flag, so a second flight never touched the first, they
	// interleaved, and the camera ended wherever the last writer left it. The gate
	// caught that, and `inFlight` is the bug made impossible to reintroduce.
	if (inFlight) inFlight.cancel();
	const from = currentLookAt(engine);
	const reduced =
		options.animate === false ||
		(typeof window !== "undefined" &&
			window.matchMedia &&
			window.matchMedia("(prefers-reduced-motion: reduce)").matches);
	const ms = Number(options.ms) > 0 ? Number(options.ms) : DEFAULT_MS;

	// NO ANIMATION, OR NOWHERE TO FLY FROM. Both land on the same camera, so a caller
	// that asks for a jump gets exactly what it asked for.
	if (reduced || !from || ms <= 0) {
		controls.setLookAt(to.eye, to.target, to.up);
		return { ok: true, animated: false, eye: to.eye, target: to.target };
	}

	let cancelled = false;
	let raf = null;
	const started = now();
	let resolveDone;
	const done = new Promise((r) => (resolveDone = r));

	const finish = (animated) => {
		// The LAST frame is the destination exactly, not an interpolated approximation
		// that rounds to somewhere nearby. A flight that lands "close" is a flight that
		// has not landed.
		controls.setLookAt(to.eye, to.target, to.up);
		if (raf && typeof cancelAnimation === "function") cancelAnimation(raf);
		raf = null;
		resolveDone({ ok: true, animated, eye: to.eye, target: to.target });
	};

	const step = () => {
		if (cancelled) return;
		const t = Math.min(1, (now() - started) / ms);
		const k = easeInOut(t);
		// Every frame goes through the engine's own setLookAt. The engine keeps
		// ownership of its controls; only the values are ours.
		controls.setLookAt(mix(from.eye, to.eye, k), mix(from.target, to.target, k), mix(from.up, to.up, k));
		if (t >= 1) return finish(true);
		raf = requestAnimation(step);
	};
	// The loop starts AFTER the handle exists, so the slot holds the real handle rather
	// than a stub. A stub here was the second version of this bug: it flipped the NEW
	// flight's own `cancelled` and left the old one running.
	inFlight = null;

	const handle = {
		ok: true,
		animated: true,
		eye: to.eye,
		target: to.target,
		done,
		cancel() {
			if (cancelled) return;
			cancelled = true;
			if (raf && typeof cancelAnimation === "function") cancelAnimation(raf);
			raf = null;
			if (inFlight === handle) inFlight = null;
			resolveDone({ ok: false, animated: false, reason: "cancelled" });
		},
	};
	// Registered, THEN started: the slot must hold the real handle so a later flight
	// cancels THIS one. A stub in this position flipped the new flight's own flag and
	// left the old loop running -- the second version of that bug, caught by the gate.
	inFlight = handle;
	raf = requestAnimation(step);
	return handle;
}

function now() {
	return typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
}

function requestAnimation(fn) {
	if (typeof window !== "undefined" && window.requestAnimationFrame) return window.requestAnimationFrame(fn);
	// No rAF (a test, or a headless shell): fall back to a timer so the flight still
	// happens rather than silently becoming an instant jump.
	return setTimeout(() => fn(now()), 16);
}

function cancelAnimation(handle) {
	if (typeof window !== "undefined" && window.cancelAnimationFrame) return window.cancelAnimationFrame(handle);
	return clearTimeout(handle);
}
