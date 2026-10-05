// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The issue modal, which stays inside the viewer.
//
// The spec is explicit about the part that matters: "modal stays in the viewer
// (no page navigation)". A coordinator comparing a clash against the model has
// both on screen; opening a form document throws one of them away and loses the
// camera, the selection and the render. So this is a dialog, and it captures
// everything it needs BEFORE it is opened -- snapshot, camera vectors and the
// selected GUIDs -- because by the time the user types a title they may well have
// moved the camera to frame the problem.
//
// The capture is the part that cannot be done later. `canvas.toDataURL()` reads
// the framebuffer; once the dialog opens and the engine re-renders, that frame is
// gone. So `capture()` is called on OPEN, not on SUBMIT.

/** What a captured issue carries away from the viewer. */
export function captureContext({ canvas, camera, selection, model, project }) {
	const out = { model, project, element_guids: (selection || []).filter(Boolean) };

	// The snapshot. A canvas the browser cannot read (a tainted cross-origin
	// image, or a context that was never drawn to) THROWS on toDataURL rather than
	// returning null, and an uncaught throw here would lose the whole dialog -- so
	// it is caught and reported as "no snapshot", not as a broken page.
	try {
		out.snapshot = canvas && canvas.toDataURL ? canvas.toDataURL("image/png") : null;
	} catch (_) {
		out.snapshot = null;
		out.snapshotError = "The viewer canvas could not be read; the issue will be filed without a snapshot.";
	}

	out.camera_vectors = camera
		? {
				eye: vec(camera.eye),
				target: vec(camera.target),
				up: vec(camera.up),
				// Where on screen the issue sits, so the pin has a position before
				// anyone opens the topic (UI-3).
				screen_x: pct(camera.screenX, canvas && canvas.clientWidth),
				screen_y: pct(camera.screenY, canvas && canvas.clientHeight),
		  }
		: null;
	return out;
}

function vec(v) {
	return v && [Number(v.x), Number(v.y), Number(v.z)];
}

/**
 * A pixel offset -> 0..100, and clamped: a pin outside the range is invisible AND
 * unclickable, which reads as an issue that was never filed.
 *
 * The span is per-AXIS. It used to be `clientWidth` for both, so a square canvas
 * hid the bug and a 16:9 one put every pin at the wrong height -- a pin that
 * looked right on the test's square canvas and drifted on every real viewport.
 */
function pct(value, span) {
	const n = Number(value);
	if (!Number.isFinite(n)) return 50;
	const size = Number(span) || 0;
	const raw = size ? (n / size) * 100 : n;
	return Math.min(100, Math.max(0, Math.round(raw * 100) / 100));
}

/**
 * The modal.
 *
 * @param {HTMLElement} host
 * @param {object} deps  { onCreate, onStatus }
 */
export function createIssueModal(host, deps = {}) {
	let captured = null;

	function open(context) {
		captured = context;
		host.innerHTML = `
			<div class="bim-issue-backdrop" data-close="1"></div>
			<div class="bim-issue-modal" role="dialog" aria-modal="true" aria-label="New issue">
				<div class="bim-issue-head">
					<div class="bim-issue-title">${esc(context.title || "New issue")}</div>
					<button class="bim-issue-close" title="Close">&times;</button>
				</div>
				<div class="bim-issue-preview">
					${context.snapshot
						? `<img class="bim-issue-shot" alt="Snapshot of the model where the issue was raised" src="${context.snapshot}">`
						: `<div class="bim-issue-nosnap">${
								context.snapshotError ? esc(context.snapshotError) : "No snapshot available"
						  }</div>`}
				</div>
				<div class="bim-issue-fields">
					<label>Title<input class="bim-issue-name" type="text" value=""></label>
					<label>Type
						<select class="bim-issue-type">
							${["Clash", "Design Issue", "Coordination", "General"]
								.map((t) => `<option>${t}</option>`)
								.join("")}
						</select>
					</label>
					<label>Priority
						<select class="bim-issue-priority">
							<option>Low</option><option selected>Normal</option><option>High</option>
						</select>
					</label>
					<div class="bim-issue-meta">
						<span class="bim-issue-elements">${(context.element_guids || []).length} element(s) attached</span>
						<span class="bim-issue-camera">${
							context.camera_vectors ? "Camera captured" : "No camera captured"
						}</span>
					</div>
					<div class="bim-issue-error" hidden></div>
				</div>
				<div class="bim-issue-actions">
					<button class="btn btn-xs btn-default bim-issue-cancel">Cancel</button>
					<button class="btn btn-xs btn-primary bim-issue-create">Create issue</button>
				</div>
			</div>`;

		host.querySelector(".bim-issue-close").onclick = close;
		host.querySelector(".bim-issue-cancel").onclick = close;
		host.querySelector('[data-close="1"]').onclick = close;
		host.querySelector(".bim-issue-create").onclick = submit;
		// The first field, so the dialog is usable from the keyboard immediately.
		const name = host.querySelector(".bim-issue-name");
		if (name) name.focus();
	}

	function close() {
		host.innerHTML = "";
		captured = null;
	}

	/**
	 * File the issue.
	 *
	 * The snapshot goes through `store_snapshot` FIRST and its PATH is what
	 * `add_viewpoint` is given, because that endpoint takes a path and requires one.
	 * Doing it in the other order is the shape the plan records as unimplementable.
	 */
	async function submit() {
		if (!captured) return;
		const title = (host.querySelector(".bim-issue-name").value || "").trim();
		const box = host.querySelector(".bim-issue-error");
		if (!title) {
			// Refused here rather than filed blank: a topic with no title is
			// unfindable in the issue list, and the list is how anyone finds it again.
			box.hidden = false;
			box.textContent = "Give the issue a title.";
			return;
		}
		const button = host.querySelector(".bim-issue-create");
		button.disabled = true;
		try {
			let snapshotPath = null;
			if (captured.snapshot) {
				const stored = await deps.storeSnapshot({
					project: captured.project,
					payload: captured.snapshot,
				});
				snapshotPath = stored && stored.file_url;
			}
			const created = await deps.onCreate({
				project: captured.project,
				bim_model: captured.model,
				title,
				topic_type: host.querySelector(".bim-issue-type").value,
				priority: host.querySelector(".bim-issue-priority").value,
			});
			if (created && snapshotPath) {
				await deps.addViewpoint({
					topic_guid: created.topic_guid,
					project: captured.project,
					camera_vectors: captured.camera_vectors,
					snapshot: snapshotPath,
				});
			}
			close();
			if (deps.onCreated) deps.onCreated(created);
		} catch (error) {
			box.hidden = false;
			box.textContent = String(error && error.message ? error.message : error).slice(0, 300);
		} finally {
			if (button) button.disabled = false;
		}
	}

	return {
		open,
		close,
		isOpen: () => Boolean(captured),
		context: () => captured,
	};
}

function esc(s) {
	return String(s ?? "").replace(
		/[&<>"']/g,
		(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
	);
}
