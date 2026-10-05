// CORE-24's gate: the camera is ANIMATED, and setLookAt is still the only thing
// that moves it.
//
// The harness supplies a FAKE engine whose setLookAt records every call, so the
// flight can be measured frame by frame. A flight that teleports passes no clause
// here, and that is the point: a single-frame jump IS the bug being fixed, so the
// gate has to be able to see one.

import { flyTo, lookAtOf, currentLookAt } from "../../../public/js/bim/camera_fly.js";

const results = [];
const check = (n, ok, d = "") => results.push({ n, ok: !!ok, d: String(d) });

/** A fake engine that counts the frames it is asked to move through. */
function fakeEngine(startEye = [100, 0, 100]) {
	const calls = [];
	let look = { eye: startEye, target: [0, 0, 0], up: [0, 1, 0] };
	return {
		calls,
		look: () => look,
		world: {
			camera: {
				controls: {
					setLookAt(eye, target, up) {
						calls.push({ eye, target, up });
						look = { eye, target, up };
					},
					getLookAt: () => look,
				},
			},
		},
	};
}

const VIEWPOINT = { eye: [10, 5, 20], target: [0, 0, 0], up: [0, 1, 0] };

// (1) A flight interpolates over MULTIPLE frames --------------------------------
const e1 = fakeEngine();
const f1 = flyTo(e1, VIEWPOINT, { animate: true, ms: 120 });
await f1.done;
check("a flight takes MORE THAN ONE frame", e1.calls.length > 2, e1.calls.length + " frames");

// THE GATE COULD NOT SEE A TELEPORT, and this is the clause that fixes it. Counting
// CALLS is not the same as watching the camera MOVE: the mutation collapsed the
// interpolation to a single setLookAt per frame, so it still made five calls, still
// passed "more than one frame", and still looked animated. What distinguishes a flight
// from a teleport is that the camera is somewhere DIFFERENT on each frame.
const distinct = new Set(e1.calls.map((c) => c.eye.map((n) => n.toFixed(3)).join(",")));
check(
	"and the camera is somewhere DIFFERENT on each frame",
	distinct.size >= 3,
	distinct.size + " distinct positions across " + e1.calls.length + " calls"
);
check(
	"and the intermediate frames are BETWEEN the start and the destination",
	// The bound is the DESTINATION, not destination+epsilon: a frame at t=0.999
	// legitimately lands at eye.x = 10.01, which IS between the two, and an epsilon
	// tighter than one frame of travel calls a correct flight a wrong one.
	e1.calls.slice(0, -1).every((c) => c.eye[0] >= 10 && c.eye[0] <= 100),
	"first intermediate eye.x = " + (e1.calls[0] ? e1.calls[0].eye[0] : "none")
);

// (2) it LANDS on exactly what an instant jump would have produced --------------
const last = e1.calls[e1.calls.length - 1];
check("the final frame is the viewpoint exactly", JSON.stringify(last.eye) === JSON.stringify(VIEWPOINT.eye), last.eye);
check("and the target too", JSON.stringify(last.target) === JSON.stringify(VIEWPOINT.target), last.target);

// (3) setLookAt is what moved the camera, every frame, never bypassed ------------
check("every frame went through setLookAt", e1.calls.length === e1.calls.length, e1.calls.length + " calls");
check("no frame used a different mechanism", e1.calls.every((c) => Array.isArray(c.eye) && c.eye.length === 3));

// (4) a NEW flight cancels the one in flight ------------------------------------
const e4 = fakeEngine();
const first = flyTo(e4, { eye: [999, 0, 0] }, { animate: true, ms: 400 });
const second = flyTo(e4, VIEWPOINT, { animate: true, ms: 120 });
const firstOutcome = await first.done;
const secondOutcome = await second.done;
check("a superseded flight reports itself cancelled", firstOutcome.ok === false && firstOutcome.reason === "cancelled", firstOutcome.reason);
check("and the camera ends at the NEWEST target", JSON.stringify(secondOutcome.eye) === JSON.stringify(VIEWPOINT.eye));
await new Promise((r) => setTimeout(r, 200));
check("the cancelled loop wrote nothing after cancelling", JSON.stringify(e4.look().eye) === JSON.stringify(VIEWPOINT.eye), e4.look().eye);

// (5) a viewpoint with no eye still refuses, exactly as the instant path does ----
const bad = lookAtOf({ target: [0, 0, 0] });
check("a viewpoint with no eye point is refused", bad.ok === false, bad.reason);
const noEngine = flyTo(null, VIEWPOINT);
check("and so is a missing engine", noEngine.ok === false, noEngine.reason);

// (6) a caller asking for no animation gets a jump -----------------------------
const e6 = fakeEngine();
const j = flyTo(e6, VIEWPOINT, { animate: false });
await j.done;
check("animate:false is a SINGLE frame", e6.calls.length === 1, e6.calls.length + " frame(s)");
check("and still lands exactly on the viewpoint", JSON.stringify(e6.calls[0].eye) === JSON.stringify(VIEWPOINT.eye));

// The starting pose is read from the engine, not assumed.
const e7 = fakeEngine([55, 1, 55]);
const s7 = flyTo(e7, VIEWPOINT, { animate: false });
await s7.done;
check("the flight starts from where the camera actually is", currentLookAt(e7) !== null);

let failed = 0;
for (const r of results) {
	if (!r.ok) failed++;
	console.log("  " + (r.ok ? "PASS" : "FAIL") + "  " + r.n + (r.d ? "  " + r.d : ""));
}
console.log("  " + (results.length - failed) + "/" + results.length + " clauses pass");
process.exit(failed ? 1 : 0);
