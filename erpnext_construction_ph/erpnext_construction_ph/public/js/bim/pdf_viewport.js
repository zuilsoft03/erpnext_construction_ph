// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// The PDF takeoff viewport: a tab, not a page.
//
// The spec is explicit that this is an EXTRACTION, not a rewrite -- "extract
// takeoff_studio.js into the tab, not a rewrite" -- and that one thing in the
// original must survive verbatim: "Your existing to_page_units() flips Y because
// PDF origin is bottom-left but canvas origin is top-left. When merging into the
// unified workspace, make sure this flip is preserved -- it's correct."
//
// It is correct, and it is only HALF the story. to_page_units() converts a click
// into PDF user space by flipping Y; drawing those points back onto the canvas has
// to flip it AGAIN, or the marker appears vertically mirrored from where it was
// clicked. The original did not: its `to_canvas` was `([x, y]) => [x, (y / h) * h]`,
// which is `[x, y]` -- the flip undone by algebra rather than by a flip. A surveyor
// clicks the end of a wall, sees the dot at the top of the sheet, and cannot
// calibrate; and because both points move together the error is invisible until
// someone checks the resulting area against a known one.
//
// So: one pair of functions, `to_page_units` and `to_canvas_point`, that are exact
// inverses of each other, and a test that asserts a round trip through both. The
// warning in the spec points at a real defect, and it was on the other side of the
// conversion from the one it named.

/** The measurement modes the spec's tools table lists. */
export const MODES = [
	{ id: "area", label: "Area", unit: "m2", needs: "polygon" },
	{ id: "length", label: "Length", unit: "m", needs: "segment" },
	{ id: "count", label: "Count", unit: "no", needs: "point" },
	{ id: "perimeter", label: "Perimeter", unit: "m", needs: "polygon" },
];

/**
 * The click/pixel conversion pair.
 *
 * Kept as free functions, not methods, so they can be tested against a real page
 * box without constructing a viewport -- the arithmetic is where the bug lived.
 */
export function makeConverter(pageWidth, pageHeight) {
	/**
	 * A click position -> PDF user space. PDF's origin is the BOTTOM-left; the
	 * canvas's is the top-left. This is the flip the spec calls correct.
	 */
	function to_page_units(clientX, clientY, rect) {
		const x = ((clientX - rect.left) / rect.width) * pageWidth;
		const y = pageHeight - ((clientY - rect.top) / rect.height) * pageHeight;
		return [x, y];
	}

	/** PDF user space -> canvas pixels. The INVERSE of `to_page_units`. */
	function to_canvas_point(point, pageHeightPx) {
		const [x, y] = point;
		return [x, pageHeightPx - y];
	}

	return { to_page_units, to_canvas_point };
}

/**
 * An undo/redo stack over the click history.
 *
 * The spec is blunt about why this exists: "Current takeoff_studio.js has no undo
 * -- the QS has to clear and restart on a misclick." Clearing is destructive: it
 * throws away a calibration AND every captured vertex, which on a large plan is
 * a dozen clicks of work.
 *
 * It is a command stack rather than a snapshot stack, because a snapshot of the
 * whole polygon on every click is O(n^2) memory over a long capture, and because
 * undo should be able to say WHICH click it removed.
 */
export function createHistory(limit = 200) {
	let past = [];
	let future = [];
	const listeners = [];

	function notify() {
		listeners.forEach((fn) => fn({ canUndo: past.length > 0, canRedo: future.length > 0 }));
	}

	return {
		/** Record a reversible action. `undo` is what puts the world back. */
		push(entry) {
			past.push(entry);
			if (past.length > limit) past.shift();
			// A new action invalidates the redo branch, as everywhere else. Leaving
			// it would let a user undo past a decision and then redo INTO a future
			// that no longer follows from what they did.
			future = [];
			notify();
		},
		undo() {
			const entry = past.pop();
			if (!entry) return null;
			future.push(entry);
			notify();
			return entry;
		},
		redo() {
			const entry = future.pop();
			if (!entry) return null;
			past.push(entry);
			notify();
			return entry;
		},
		canUndo: () => past.length > 0,
		canRedo: () => future.length > 0,
		depth: () => ({ past: past.length, future: future.length }),
		clear() {
			past = [];
			future = [];
			notify();
		},
		onChange: (fn) => {
			listeners.push(fn);
			return () => {
				const i = listeners.indexOf(fn);
				if (i >= 0) listeners.splice(i, 1);
			};
		},
	};
}

/**
 * Create the PDF viewport.
 *
 * @param {HTMLElement} host
 * @param {object} deps  { loadPdf, onCalibrated, onCaptured, onStatus }
 */
export function createPdfViewport(host, deps = {}) {
	host.innerHTML = `
		<div class="bim-pdf">
			<div class="bim-pdf-toolbar">
				<select class="bim-pdf-mode">
					${MODES.map((m) => `<option value="${m.id}">${m.label}</option>`).join("")}
				</select>
				<button class="btn btn-xs btn-default bim-pdf-prev" title="Previous page">&#8592;</button>
				<span class="bim-pdf-page">-</span>
				<button class="btn btn-xs btn-default bim-pdf-next" title="Next page">&#8594;</button>
				<button class="btn btn-xs btn-default bim-pdf-undo" title="Undo (Ctrl+Z)">Undo</button>
				<button class="btn btn-xs btn-default bim-pdf-redo" title="Redo (Ctrl+Y)">Redo</button>
				<button class="btn btn-xs btn-default bim-pdf-clear" title="Clear">Clear</button>
				<label class="bim-pdf-file btn btn-xs btn-default">Open PDF<input type="file" accept="application/pdf" hidden></label>
			</div>
			<div class="bim-pdf-stage"><canvas class="bim-pdf-canvas"></canvas></div>
			<div class="bim-pdf-status"></div>
		</div>`;

	const canvas = host.querySelector(".bim-pdf-canvas");
	const statusEl = host.querySelector(".bim-pdf-status");
	const pageLabel = host.querySelector(".bim-pdf-page");
	const modeSelect = host.querySelector(".bim-pdf-mode");
	const ctx = canvas.getContext ? canvas.getContext("2d") : null;

	const state = {
		doc: null,
		page: 1,
		pageCount: 0,
		pageWidth: 1,
		pageHeight: 1,
		mode: "area",
		// Calibration is per DOCUMENT, not per page: a plan's scale does not change
		// between its first floor and its second, and re-clicking two points on
		// every page is work the spec explicitly does not want.
		calibration: null,
		polygon: [],
		counts: [],
		// A calibration gesture in progress. The spec puts the Calibrate TOOL in
		// the left panel and the two clicked points here, so the two halves have to
		// be able to meet: the panel starts the gesture, this collects the points,
		// and the panel turns them into a scale with a known length.
		calibrating: false,
		calibrationPoints: [],
		pageCounts: {}, // page -> how many points were counted on it
	};

	const history = createHistory();
	let converter = makeConverter(1, 1);
	let active = false;

	function setStatus(message) {
		if (statusEl) statusEl.textContent = message || "";
		if (deps.onStatus) deps.onStatus(message || "");
	}

	// ------------------------------------------------------------------ pdf.js
	//
	// `deps.loadPdf` exists so a test can hand in a stand-in document. It is
	// expected to RETURN a document object. The default path goes through pdf.js
	// itself, and it must be `getDocument({ data }).promise` -- calling the
	// pdf.js MODULE as a function fails with "load is not a function", which names
	// neither the cause nor the file.
	async function loadPdf(file) {
		if (deps.loadPdf) return deps.loadPdf(file);
		const pdfjs = await defaultLoader();
		const data = await file.arrayBuffer();
		// The bytes are handed over and then RELEASED, because pdf.js takes
		// ownership of the buffer and will not copy it; leaving a detached view
		// alive keeps the whole file in memory for the life of the document.
		return pdfjs.getDocument({ data }).promise;
	}

	// pdf.js 4.x is ESM, so it is imported by URL rather than through
	// `frappe.require` -- which cannot resolve a bare specifier, and which named a
	// path (`pdfjs-dist/build/pdf.min.js`) that does not exist in pdfjs 4 at all.
	// The old call 404'd, which is why the PDF viewport has never rendered a plan.
	// Both files are built from THIS app's node_modules by
	// ops/tmp-ph3-05/build_pdf.sh into public/js/bim/vendor/pdf/.
	const PDFJS_URL = "/assets/erpnext_construction_ph/js/bim/vendor/pdf/pdf.js";
	const PDF_WORKER_URL = "/assets/erpnext_construction_ph/js/bim/vendor/pdf/pdf.worker.js";

	let _pdfjs;
	async function defaultLoader() {
		if (_pdfjs) return _pdfjs;
		_pdfjs = deps.loadPdfModule
			? await deps.loadPdfModule()
			: await import(/* webpackIgnore: true */ PDFJS_URL);
		// The worker is a separate file in a separate thread, so it cannot be
		// inlined; pdf.js is told where to find it. Set on every load, because a
		// cached module may be reused across documents.
		_pdfjs.GlobalWorkerOptions.workerSrc = PDF_WORKER_URL;
		return _pdfjs;
	}

	// ------------------------------------------------------------------ pages
	async function showPage(n) {
		if (!state.doc) return;
		const target = Math.min(Math.max(1, n), state.doc.numPages);
		state.page = target;
		const page = await state.doc.getPage(target);
		const viewport = page.getViewport({ scale: 1 });
		state.pageWidth = viewport.width;
		state.pageHeight = viewport.height;
		canvas.width = viewport.width;
		canvas.height = viewport.height;
		converter = makeConverter(state.pageWidth, state.pageHeight);
		await page.render({ canvasContext: ctx, viewport }).promise;
		if (pageLabel) pageLabel.textContent = `${target} / ${state.doc.numPages}`;
		draw();
	}

	async function open(file) {
		if (!file) return;
		active = true;
		setStatus("Reading the plan…");
		try {
			state.doc = await loadPdf(file);
			state.pageCount = state.doc.numPages;
			// A new document resets the measurements but NOT the calibration, which
			// the surveyor may have set once and is reusing deliberately.
			state.polygon = [];
			state.counts = [];
			history.clear();
			await showPage(1);
			setStatus(
				state.calibration
					? `Calibrated at ${state.calibration.metres_per_unit.toFixed(6)} m per unit.`
					: "Not calibrated. Click two points on a known dimension, then calibrate."
			);
		} catch (error) {
			setStatus(`The plan could not be read: ${error.message}`);
		}
	}

	// ------------------------------------------------------------------ input
	function toPageUnits(clientX, clientY, rect) {
		return converter.to_page_units(clientX, clientY, rect || canvas.getBoundingClientRect());
	}

	function onCanvasClick(event) {
		if (!active || !state.doc) return;
		const rect = canvas.getBoundingClientRect();
		const point = toPageUnits(event.clientX, event.clientY, rect);

		if (state.calibrating) {
			state.calibrationPoints.push(point);
			// Two points is the whole gesture. Handed over immediately rather than
			// on a third click, because a third point is a mistake and silently
			// swallowing it is how a surveyor ends up calibrating to the wrong pair.
			if (state.calibrationPoints.length === 2) {
				const points = state.calibrationPoints;
				state.calibrating = false;
				state.calibrationPoints = [];
				draw();
				if (deps.onCalibrateRequest) deps.onCalibrateRequest(points);
			} else {
				draw();
			}
			return;
		}

		if (state.mode === "count") {
			// Counts belong to the page they were made on, so navigating away and
			// back shows the tally for THAT page rather than the whole document.
			state.counts.push({ page: state.page, point });
			state.pageCounts[state.page] = (state.pageCounts[state.page] || 0) + 1;
			history.push({ kind: "count", point, page: state.page });
			draw();
			return;
		}

		state.polygon.push(point);
		history.push({ kind: "point", point, page: state.page });
		draw();
	}

	// ------------------------------------------------------------------ tools
	function undo() {
		const entry = history.undo();
		if (!entry) return;
		if (entry.kind === "point") {
			state.polygon = state.polygon.filter((p) => p !== entry.point);
		} else if (entry.kind === "count") {
			const i = state.counts.findIndex((c) => c.point === entry.point && c.page === entry.page);
			if (i >= 0) state.counts.splice(i, 1);
			state.pageCounts[entry.page] = Math.max(0, (state.pageCounts[entry.page] || 1) - 1);
		}
		draw();
	}

	function redo() {
		const entry = history.redo();
		if (!entry) return;
		if (entry.kind === "point") state.polygon.push(entry.point);
		else if (entry.kind === "count") {
			state.counts.push({ point: entry.point, page: entry.page });
			state.pageCounts[entry.page] = (state.pageCounts[entry.page] || 0) + 1;
		}
		draw();
	}

	function clearAll() {
		state.polygon = [];
		state.counts = [];
		state.pageCounts = {};
		history.clear();
		draw();
	}

	function setCalibration(calibration) {
		state.calibration = calibration;
		draw();
	}

	// ------------------------------------------------------------------ draw
	function draw() {
		if (!ctx) return;
		const h = state.pageHeight;
		ctx.clearRect(0, 0, state.pageWidth, h);
		const toCanvas = (p) => converter.to_canvas_point(p, h);

		// The polygon, as the spec asks: orange stroke with a fill.
		if (state.polygon.length) {
			const pts = state.polygon.map(toCanvas);
			ctx.beginPath();
			pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
			if (pts.length > 2) {
				ctx.closePath();
				ctx.fillStyle = "rgba(232, 89, 12, 0.18)";
				ctx.fill();
			}
			ctx.strokeStyle = "#e8590c";
			ctx.lineWidth = 2;
			ctx.stroke();
			pts.forEach(([x, y]) => {
				ctx.beginPath();
				ctx.arc(x, y, 3, 0, Math.PI * 2);
				ctx.fillStyle = "#e8590c";
				ctx.fill();
			});
		}

		// Count marks, in the mode's own ink.
		state.counts
			.filter((c) => c.page === state.page)
			.forEach((c, i) => {
				const [x, y] = toCanvas(c.point);
				ctx.beginPath();
				ctx.arc(x, y, 6, 0, Math.PI * 2);
				ctx.strokeStyle = "#1c7ed6";
				ctx.lineWidth = 2;
				ctx.stroke();
				ctx.fillStyle = "#fff";
				ctx.font = "10px sans-serif";
				ctx.fillText(String(i + 1), x + 8, y - 6);
			});

		// The in-progress calibration gesture, so the first point is visible while
		// the user goes looking for the second.
		if (state.calibrationPoints.length) {
			ctx.fillStyle = "#2f9e44";
			state.calibrationPoints.forEach((p, i) => {
				const [x, y] = toCanvas(p);
				ctx.beginPath();
				ctx.arc(x, y, 4, 0, Math.PI * 2);
				ctx.fill();
				ctx.fillStyle = "#fff";
				ctx.font = "10px sans-serif";
				ctx.fillText(`P${i + 1}`, x + 6, y - 6);
			});
		}

		// Calibration: the two clicked points and the green dashed line between.
		if (state.calibration && state.calibration.points) {
			const pts = state.calibration.points.map(toCanvas);
			ctx.strokeStyle = "#2f9e44";
			ctx.lineWidth = 2;
			ctx.setLineDash([4, 3]);
			if (pts.length === 2) {
				ctx.beginPath();
				ctx.moveTo(pts[0][0], pts[0][1]);
				ctx.lineTo(pts[1][0], pts[1][1]);
				ctx.stroke();
			}
			ctx.setLineDash([]);
			pts.forEach(([x, y], i) => {
				ctx.beginPath();
				ctx.arc(x, y, 4, 0, Math.PI * 2);
				ctx.fillStyle = "#2f9e44";
				ctx.fill();
				ctx.fillStyle = "#fff";
				ctx.font = "10px sans-serif";
				ctx.fillText(`P${i + 1}`, x + 6, y - 6);
			});
		}
	}

	// ------------------------------------------------------------------ wiring
	modeSelect.onchange = () => {
		state.mode = modeSelect.value;
		draw();
	};
	host.querySelector(".bim-pdf-prev").onclick = () => showPage(state.page - 1);
	host.querySelector(".bim-pdf-next").onclick = () => showPage(state.page + 1);
	host.querySelector(".bim-pdf-undo").onclick = undo;
	host.querySelector(".bim-pdf-redo").onclick = redo;
	host.querySelector(".bim-pdf-clear").onclick = clearAll;
	const fileInput = host.querySelector("input[type=file]");
	fileInput.onchange = () => open(fileInput.files && fileInput.files[0]);
	host.querySelector(".bim-pdf-file").onclick = () => fileInput.click();
	if (canvas.addEventListener) {
		canvas.addEventListener("click", onCanvasClick);
	}
	// Ctrl+Z / Ctrl+Y, as the spec asks for. Bound on the host rather than the
	// document so the shortcut follows the viewport the user is actually in.
	if (host.addEventListener) {
		host.addEventListener("keydown", (event) => {
			if (!(event.ctrlKey || event.metaKey)) return;
			if (event.key === "z" || event.key === "Z") {
				event.preventDefault();
				undo();
			} else if (event.key === "y" || event.key === "Y") {
				event.preventDefault();
				redo();
			}
		});
	}

	return {
		open,
		showPage,
		undo,
		redo,
		clear: clearAll,
		setCalibration,
		/** Start a two-point calibration gesture. The next two clicks are its points. */
		beginCalibration: () => {
			state.calibrating = true;
			state.calibrationPoints = [];
			draw();
			setStatus("Click two points on a known dimension.");
		},
		calibrating: () => state.calibrating,
		setMode: (m) => {
			state.mode = m;
			modeSelect.value = m;
			draw();
		},
		getState: () => JSON.parse(JSON.stringify({ ...state, doc: undefined })),
		getConverter: () => converter,
		canvas,
		history,
	};
}
