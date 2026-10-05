// BIM geometry engine for erpnext_construction_ph.
//
// The seat of the OpenProject IFC-viewer architecture, which this file studies and
// replicates (D-043, GATES.md PH3-02). OpenProject converts in the BROWSER —
// `IfcImporter` is a client-side WASM step and the `.frag` is a *cache* so later
// sessions skip parsing. This file does the same, and the consequence is the whole
// point: the server never transcodes geometry, so there is no headless-GL stack in
// production and no second copy of the model on the server.
//
// The claim this file is careful about: **WebGL is required to RENDER, not to
// CONVERT.** `convert()` runs identically in Node and in a browser, which is what
// refuted a BLOCKED row's premise (ops/tmp-ph3-01/conversion-probe.json).
//
// Dual-mode on purpose. The two pure helpers below are `export`ed so the gate can
// import the SHIPPED code rather than parse a copy of it; the rest is attached to
// the `frappe` namespace, which is how `hooks.py` serves this to Desk. Importing
// the file under Node is safe: the namespace is only attached when a browser host
// is present.

// `@thatopen/fragments` is NOT statically imported, and must not be. A bare
// specifier is unresolvable in a file Frappe serves straight to the browser, so
// the static import made the WHOLE module fail to load -- the page reported
// "Geometry engine could not be loaded: Failed to resolve module specifier
// \"@thatopen/fragments\"" and 3D never started, while every other part of the
// page looked healthy. It is loaded through `loadModule()` like
// `@thatopen/components` already was, which resolves the vendored bundle in
// `public/js/bim/vendor/`.
//
// It was static so the PH3-02 gate could import the shipped module under Node.
// That gate only uses the PURE helpers (`globalIdOf`, `extractTreeRows`) and never
// reaches a `loadModule` call, so a lazy import costs it nothing and removes a
// bare-specifier resolution that only happened to work because node_modules
// happened to be reachable.

/**
 * Recover an element's IFC `GlobalId`.
 *
 * This is the join the whole product turns on: a click in 3D produces a GUID, the
 * server resolves that GUID to an indexed row (`api/bim_convert.resolve_element`),
 * and that row carries the BCF issues. Returning something that is not a GlobalId
 * here means the viewer works and the issues never attach — a failure that looks
 * like a BCF bug, so the gate asserts this function directly.
 *
 * @param {object} hit whatever the raycaster returned
 * @returns {string|null} 22-char GlobalId, or null when the hit carries none
 */
export function globalIdOf(hit) {
	if (!hit) return null;
	const raw =
		(typeof hit.getGlobalId === "function" ? hit.getGlobalId() : null) ||
		hit.globalId ||
		(typeof hit.getObjectInfo === "function" ? hit.getObjectInfo().globalId : null) ||
		null;
	if (raw === null || raw === undefined) return null;
	const value = String(raw).trim();
	// An IFC GlobalId is 22 chars over the 64-symbol base64 alphabet — the same shape
	// the server's own GUID_RE asserts. Anything else is an id from some other
	// addressing scheme and must not be sent to the server, which would index a GUID
	// that can never match.
	return /^[0-9A-Za-z_$]{22}$/.test(value) ? value : null;
}

/**
 * Flatten a spatial tree into rows a tree widget can render, each carrying a depth.
 * The engine stays ignorant of the DOM; the page does the rendering.
 *
 * @param {Array} nodes root items, each optionally with `.children`
 * @returns {Array<{id, name, expressID, depth, isVisible}>}
 */
export function extractTreeRows(nodes) {
	const rows = [];
	const walk = (items, depth) => {
		for (const item of items || []) {
			rows.push({
				id: item.id,
				name: item.name || "",
				expressID: item.expressID === undefined ? null : item.expressID,
				depth,
				isVisible: item.isVisible !== false,
			});
			walk(item.children, depth + 1);
		}
	};
	walk(nodes, 0);
	return rows;
}

/**
 * Convert IFC bytes to `.frag` bytes. Headless-safe: no DOM, no WebGL.
 *
 * This is the method that refuted the recorded blocker. A real 8.6 MB IFC converts
 * to a 630 KB fragment in about one second in plain Node, and the loader resolves
 * `web-ifc-node.wasm` — a CPU module. GL is needed to *draw* the result, which is
 * `mount()`'s job, not this one's.
 *
 * @param {Uint8Array} bytes raw IFC
 * @param {object} opts { wasmPath, onProgress }
 * @returns {Promise<ArrayBuffer>} the `.frag`
 */
export async function convert(bytes, opts = {}) {
	const FRAGS_LIB = await loadFragmentsLib();
	const importer = new FRAGS_LIB.IfcImporter();
	// `absolute` tells the importer the path is already a root rather than something
	// to resolve against a CDN. In the browser this is the web-ifc WASM asset; under
	// Node it resolves web-ifc-node.wasm. The loader picks the right module for the
	// runtime, which is exactly why conversion needs no GL context.
	importer.wasm = {
		absolute: true,
		path: opts.wasmPath || defaultWasmPath(),
	};
	return importer.process({
		bytes,
		progressCallback: opts.onProgress || function () {},
	});
}

/** Where web-ifc's WASM lives in each runtime. */
export function defaultWasmPath() {
	if (typeof window !== "undefined") {
		return "/assets/erpnext_construction_ph/js/bim/web-ifc/";
	}
	return "node_modules/web-ifc/";
}

// ---------------------------------------------------------------------------
// Browser-only half: the mounted viewer. Reached through the frappe namespace,
// which `hooks.py` serves to every Desk page via app_include_js.
// ---------------------------------------------------------------------------
const api = {
	// Filled in by `mount()`, so a Desk Page can drive a viewer it did not construct.
	engine: null,

	globalIdOf,
	extractTreeRows,
	convert,
	defaultWasmPath,

	/**
	 * Mount a real viewer. This is the half that genuinely needs WebGL, and the half
	 * the headless gate cannot prove — which the gate says out loud rather than
	 * implying otherwise.
	 *
	 * @param {HTMLElement} container
	 * @param {object} opts { fragUrl, modelId, onPick, onReady }
	 */
	async mount(container, opts) {
		const options = opts || {};
		const OBC = await loadModule("@thatopen/components");
		const FRAGS_LIB = await loadFragmentsLib();
		// There is deliberately no `this._initFragments(OBC)` here. It was called,
		// bound to `fragments`, and then never read: the real setup is the
		// `FragmentsModels.getWorker()` + `new FragmentsModels(workerUrl)` pair two
		// lines below, which is what the version-matched worker needs. The helper it
		// called was never defined anywhere in this file, so `mount()` threw
		// "this._initFragments is not a function" the first time it was ever reached
		// from a caller -- the canvas stayed empty with no other symptom. It only
		// became reachable once the module's exports were fixed, which is why it had
		// never been observed.

		const components = new OBC.Components();
		const worlds = components.get(OBC.Worlds);
		const world = worlds.create();
		world.scene = new OBC.SimpleScene(components);
		world.scene.setup();
		world.scene.three.background = null;
		world.renderer = new OBC.SimpleRenderer(components, container);
		world.camera = new OBC.SimpleCamera(components);
		components.init();

		// The version-matched fragments worker. Rendering is the part that genuinely
		// needs a worker; conversion above does not, which is what D-043 records.
		const workerUrl = await FRAGS_LIB.FragmentsModels.getWorker();
		const models = new FRAGS_LIB.FragmentsModels(workerUrl);
		world.camera.controls.addEventListener("update", function () {
			models.update();
		});

		// Coplanar fragments z-fight, which reads as a flickering mess. The standard
		// fix costs nothing.
		models.models.materials.list.onItemSet.add(function (event) {
			const material = event.value;
			if (!("isLodMaterial" in material && material.isLodMaterial)) {
				material.polygonOffset = true;
				material.polygonOffsetUnits = 1;
			}
		});

		const response = await fetch(options.fragUrl);
		if (!response.ok) {
			throw new Error(
				"model fetch failed: " + response.status + " " + options.fragUrl
			);
		}
		const bytes = await response.arrayBuffer();
		const modelId = options.modelId || "model";
		const model = await models.load(bytes, { modelId: modelId });
		model.useCamera(world.camera.three);
		world.scene.three.add(model.object);
		await models.update(true);

		// OpenProject's selection model: a click in select-mode yields an element.
		//
		// `Raycasters.get(world)` is the whole of it. This used to hand-build
		// `components.get(OBC.Raycasters).create(new OBC.SimpleRaycaster(components,
		// world))`, and both halves of that were wrong against @thatopen/components
		// 3.4.8: the manager exposes `get(world)`, `delete(world)` and a `list` map,
		// but no `create`; and `get` constructs and registers the raycaster itself,
		// disposing it when the world is. Hand-building one produced a duplicate that
		// the manager did not know about and would never clean up. The `world`
		// argument is likewise not optional -- the constructor reads `world.renderer`
		// for the canvas the mouse is tracked against -- which is what the earlier
		// "Cannot read properties of undefined (reading 'renderer')" was.
		const raycaster = components.get(OBC.Raycasters).get(world);
		container.addEventListener("dblclick", async function (event) {
			const hit = await raycaster.getAt({
				camera: world.camera,
				dom: container,
				x: event.offsetX,
				y: event.offsetY,
			});
			const globalId = globalIdOf(hit);
			if (globalId && options.onPick) options.onPick(globalId, hit);
		});

		const engine = {
			components: components,
			// The OBC NAMESPACE, exposed because `engine.components` is NOT it: that
			// object has 6 keys and none of the components -- not even SimpleCamera, which
			// the app demonstrably uses. The toolbar's `missing()` was reading it, so every
			// tool with a `needs` entry was reported as missing a component it had.
			OBC: OBC,
			models: models,
			world: world,
			raycaster: raycaster,
			/**
			 * Spatial rows for the model tree, via the shared pure helper.
			 *
			 * Async because `getSpatialStructure()` is. It was synchronous and it
			 * was wrong twice over: it read an API that does not exist, and it
			 * returned a promise-shaped thing to a synchronous walker.
			 */
			tree: async function () {
				return extractTreeRows(await spatialNodes(models));
			},
			/**
			 * Per-element surface area and XY footprint, in square METRES.
			 *
			 * The server cannot: it never decodes a `.frag`, and every route to
			 * per-element numbers was measured before this one.
			 *   * `getItemsVolume(ids)` returns ONE number for the whole model -- it
			 *     ignores the ids -- so per-element volume is not available from it.
			 *   * `getItemsGeometry([3 ids])` returns an Array of three NUMBERS: an
			 *     index into the model, not geometry.
			 *   * grouping triangles by the per-vertex `id` on the 17 TILE meshes is
			 *     also wrong: that attribute carries only 157 distinct values for
			 *     13,357 elements, so it identifies tiles and not elements.
			 *
			 * What works, each hop verified on the real model:
			 *   `getItemsWithGeometry(ids)` -> 1,526 Items, zero errors
			 *   `item.getGeometry()`        -> the per-ELEMENT geometry
			 *   `geo.getPositions()` and `geo.getIndices()` POPULATE `_positions` and
			 *     `_indices`, which are NULL until called. That second layer of
			 *     laziness is what made an earlier implementation read the fields
			 *     directly and report 0 of 51,884.
			 *
			 * Shapes, measured: `_positions` is `[Float32Array(492)]` (164 vertices,
			 * interleaved xyz) and `_indices` is `[Uint16Array(348)]` (116 triangles)
			 * -- each a ONE-ELEMENT ARRAY wrapping the typed array.
			 *
			 * UNITS ARE METRES, from the vertices themselves: the sampled corner is
			 * (-0.2, 3.55, 0.25) -- a 3.55 m storey height and a 0.2 m thickness.
			 * An earlier hard-coded /1e6 was wrong by a million.
			 *
			 * BOTH bases are produced because a QS "area" is a commercial quantity and
			 * the two are not interchangeable: for a 200x3000x200 wall the surface is
			 * ~2.5 m2 and the footprint ~0.06 m2. `geom_footprint_area` is a bounding
			 * box on XY, which is what a plan-area takeoff line means; summing
			 * projected triangle areas would be right for a slab and badly wrong for
			 * anything vertical.
			 */
			/**
			 * POST to a whitelisted method, carrying the CSRF token.
			 *
			 * `frappe.call` was MEASURED rejecting with HTTP 417 from this page --
			 * the server's CSRF answer -- while a `fetch` carrying `X-Frappe-CSRF-Token`
			 * returned 200 on the identical payload. The failure is the missing token,
			 * never the payload: all 2,104 rows are 174,637 bytes. Do not simplify this
			 * back to `frappe.call`; the rejection is opaque and costs a whole debug.
			 */
			post: async function (method, args) {
				const token = (frappe && frappe.csrf_token) || "";
				const r = await fetch("/api/method/" + method, {
					method: "POST",
					credentials: "include",
					headers: {
						"Content-Type": "application/json",
						"X-Frappe-CSRF-Token": token,
					},
					body: JSON.stringify(args || {}),
				});
				const text = await r.text();
				let body = null;
				try {
					body = JSON.parse(text);
				} catch (error) {
					body = null;
				}
				if (!r.ok) {
					// The server's own words, not a bare object.
					const why = (body && (body.exc_type || body.message)) || r.statusText;
					throw new Error(method + " -> HTTP " + r.status + ": " + String(why).slice(0, 200));
				}
				return body;
			},

			measureAreas: async function (opts) {
				const o = opts || {};
				const onProgress = typeof o.onProgress === "function" ? o.onProgress : null;

				// ONE call. `getItemsWithGeometry(ids)` IGNORES the slice -- measured, it
				// returns all 1,526 items every time -- so batching it re-counted
				// everything once per batch and reported 102,242 for a model with
				// 1,526 measurable elements. That number is 1526 x 67, which is how the
				// duplication was found rather than assumed away.
				const seen = new Set();
				const out = [];
				let total = 0;
				let hydrated = 0;
				let noGeometry = 0;
				let noData = 0;
				let duplicates = 0;
				let unjoinable = 0;
				let done = 0;

				for (const model of models.models.list.values()) {
					if (!model || typeof model.getItemsWithGeometry !== "function") continue;
					let items = [];
					try {
						items = (await model.getItemsWithGeometry()) || [];
					} catch (error) {
						continue; // a model that cannot be measured is skipped, not faked
					}
					total += items.length;

					for (const item of items) {
						let geometry = null;
						let guid = null;
						try {
							geometry = await item.getGeometry();
						} catch (error) {
							geometry = null;
						}
						try {
							guid = await item.getGuid();
						} catch (error) {
							guid = null;
						}
						if (!geometry) {
							noGeometry++;
							continue;
						}
						hydrated++;
						if (!guid) {
							unjoinable++;
							continue;
						}
						// A GUID twice would otherwise be uploaded twice.
						if (seen.has(guid)) {
							duplicates++;
							continue;
						}
						seen.add(guid);

						// THE POPULATING CALLS. `_positions` and `_indices` are null
						// until these run; reading the fields directly yields null and
						// the measurement comes back empty.
						try {
							await geometry.getPositions();
							await geometry.getIndices();
						} catch (error) {
							/* measured below, where the arrays are checked anyway */
						}
						const P = geometry._positions && geometry._positions[0];
						const I = geometry._indices && geometry._indices[0];
						if (!P || !I || I.length < 3) {
							noData++;
							continue;
						}
						const m = measure(P, I);
						if (!m) {
							noData++;
							continue;
						}
						out.push({
							element_guid: String(guid),
							surface_area: m.surface,
							footprint_area: m.footprint,
							// Carried so a caller -- and the gate -- can check a figure
							// against a known element instead of trusting it.
							bounding: m.bounding,
						});
						done++;
						if (onProgress) onProgress(done, total);
					}
				}

				return {
					written: out.length,
					total,
					hydrated,
					noGeometry,
					noData,
					duplicates,
					unjoinable,
					areas: out,
				};
			},

			/**
			 * Post the measurements to the server, keeping ONLY the GUIDs the index
			 * already holds.
			 *
			 * The model carries ~1,526 measurable elements against 2,103 indexed
			 * `BIM Model GUID` rows, and the intersection is what can actually be
			 * joined. Sending the rest is a few hundred kilobytes of rows nothing can
			 * resolve. The index is fetched once and used as the allow-list, and the
			 * upload is chunked so a failure costs one chunk rather than the model.
			 */
			uploadAreas: async function (opts) {
				const o = opts || {};
				const CHUNK = Number(o.chunk) || 400;
				const method = "erpnext_construction_ph.api.bim_frag.record_areas";

				// ALREADY MEASURED? MEASURED, not assumed: with 1,502 areas stored, an
				// open of the model still sent 9 `record_areas` POSTs, because nothing
				// asked whether the work had been done. Re-measuring 1,526 elements on
				// every page open is pure waste, and it also made "the areas are stored"
				// impossible to take as a fact about the database.
				if (!o.force) {
					let already = 0;
					try {
						already =
							(
								await this.post(
									"erpnext_construction_ph.api.bim_frag.measured_count",
									{ model: o.model }
								)
							).message || 0;
					} catch (error) {
						// Unreadable is NOT zero. Defaulting to 0 re-posts; the cost of
						// that is duplicated work and the cost of skipping is stale areas,
						// so an unknown count asks again next time rather than guessing.
						already = -1;
					}
					if (already > 0) {
						return {
							// A COUNT, not a flag. `skipped` below is also a count and
							// the two must not be interchangeable: a caller testing
							// `if (result.skipped)` is then true for any non-zero
							// skip and reports a normal run as an already-measured one.
							alreadyMeasured: already,
							reason: "already measured",
							measured: 0,
							sent: 0,
							skipped: 0,
						};
					}
				}

				const measured = (await this.measureAreas(o)) || {};
				const rows = measured.areas || [];

				// Which GUIDs the server can join. A name never indexed cannot be
				// resolved to an element, so storing its area is cost with no possible
				// use. An EMPTY allow-list uploads nothing, never everything: the
				// failure mode is 1,502 rows nothing can resolve.
				const known = new Set();
				try {
					const r = await this.post(
						"erpnext_construction_ph.api.bim_frag.indexed_guids",
						{ model: o.model }
					);
					for (const g of (r && r.message) || []) known.add(g);
				} catch (error) {
					/* empty allow-list, by the rule above */
				}

				const keep = rows.filter((a) => known.has(a.element_guid));
				let sent = 0;
				try {
					for (let i = 0; i < keep.length; i += CHUNK) {
						await this.post(method, {
							model: o.model,
							areas: keep.slice(i, i + CHUNK),
						});
						sent += Math.min(CHUNK, keep.length - i);
						if (typeof o.onProgress === "function") o.onProgress(sent, keep.length);
					}
				} catch (error) {
					// A PARTIAL UPLOAD IS NOT A FAILED ONE, and carrying the counter is
					// the whole point. The previous version reported "the areas were not
					// stored" while 800 of 1,502 were -- false, and a half-finished
					// measurement is indistinguishable from a finished one to anything
					// reading the database.
					const err = new Error(
						"stored " + sent + " of " + keep.length + " element areas before failing: " + error.message
					);
					err.storedSoFar = sent;
					err.attempted = keep.length;
					throw err;
				}

				return {
					measured: rows.length,
					uploadable: keep.length,
					sent: sent,
					skipped: rows.length - keep.length,
					skippedDetails: {
						noGeometry: measured.noGeometry,
						noData: measured.noData,
						duplicates: measured.duplicates,
						unjoinable: measured.unjoinable,
					},
				};
			},

			dispose: function () {
				return models.disposeModel(modelId);
			},
		};
		api.engine = engine;
		if (options.onReady) options.onReady(engine);
		return engine;
	},

	/** Lazily import a vendor bundle. One function so the asset root is written once. */
	async _load(specifier) {
		return loadModule(specifier);
	},

	/** Where the vendor bundles are served from. */
	vendorBase: function () {
		return "/assets/erpnext_construction_ph/js/bim/vendor/";
	},
};

/**
 * Collect the spatial nodes of every loaded model, in the shape `extractTreeRows`
 * expects. Split out so the DOM-free walk can be reasoned about on its own.
 */
/**
 * The spatial tree of every loaded model, in the shape `extractTreeRows` takes.
 *
 * Three wrong APIs got here first, all measured in the browser rather than
 * assumed, and all of them silent:
 *
 *   * `model.getItems()` -- a low-level edit accessor that returns a plain `{}`
 *     on a FragmentsModel. `for (const item of items || [])` threw "(items || [])
 *     is not iterable", and because the throw came out of `onReady` the page
 *     reported "Could not display the model", which was a lie: the model WAS on
 *     screen. A tree that failed to draw made the whole viewer look broken.
 *   * `model.getAttributeNames()` / `getAttributeValues()` -- both return an EMPTY
 *     array on this fragment, so there is no attribute catalogue to read.
 *
 * What works is `getSpatialStructure()` for the hierarchy ({category, localId,
 * children}) and `getItemsData(ids, config)` for the labels -- one batched call
 * for the whole model rather than one per node, because this tree is 1,549 nodes
 * and a call each would be 1,549 worker round-trips.
 *
 * It returns `{Name, LongName, _guid, _category}` per item, and `_guid` is the
 * IFC GlobalId -- the join the entire product turns on, since a click in 3D
 * produces a GUID that the server resolves to a BCF issue. The row's `id` is that
 * GUID, so the tree row and the pick carry the same key rather than two different
 * ones that have to be translated.
 */
/**
 * Surface area and XY footprint of one element's geometry, in square metres.
 *
 * A `.frag` stores its geometry as triangles carrying IFC-style length units, so a
 * metre-based result needs the unit applied rather than assumed. Anything that
 * cannot be measured returns null rather than a zero: a zero is a real
 * measurement for a degenerate element, and a reader must be able to tell the two.
 */
function areaOf(geometry) {
	try {
		if (!geometry) return null;
		// The fragments API hands back a localId plus a mesh; the shape differs by
		// version, so probe rather than assume.
		const guid = geometry.guid || (geometry._guid && geometry._guid.value) || null;
		const localId = geometry.localId != null ? geometry.localId : (geometry._localId && geometry._localId.value);
		const mesh =
			(geometry.getMesh && geometry.getMesh()) ||
			geometry.mesh ||
			(geometry.buffer && geometry.buffer());
		if (!guid || !mesh) return null;

		const index = mesh.index || mesh.indices;
		const position = mesh.position || mesh.positions;
		if (!index || !position) return null;

		// Bounding box on XY, and the sum of triangle areas on all three axes.
		let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
		const point = (k) => {
			const o = k * 3;
			return [position[o] || 0, position[o + 1] || 0, position[o + 2] || 0];
		};
		for (let t = 0; t < index.length; t += 3) {
			for (const v of [index[t], index[t + 1], index[t + 2]]) {
				const [x, y] = point(v);
				if (x < minX) minX = x;
				if (x > maxX) maxX = x;
				if (y < minY) minY = y;
				if (y > maxY) maxY = y;
			}
		}
		const footprint = (maxX - minX) * (maxY - minY);

		let surface = 0;
		for (let t = 0; t < index.length; t += 3) {
			const a = point(index[t]), b = point(index[t + 1]), c = point(index[t + 2]);
			const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
			const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
			const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
			surface += 0.5 * Math.sqrt(nx * nx + ny * ny + nz * nz);
		}
		if (!Number.isFinite(surface) || !Number.isFinite(footprint)) return null;
		return { guid, localId, surface, footprint };
	} catch (error) {
		return null;
	}
}

/**
 * The spatial tree of every loaded model. Async because `getSpatialStructure()` is
 * -- and it lost its `async` once, to an edit whose anchor matched the tail of
 * `async function spatialNodes`, which stranded the keyword and left every
 * `await` in this function reserved. The file is served straight to a browser, so
 * that was a whole-module SyntaxError, not a syntax warning.
 */
/**
 * Surface area and XY footprint of one element, in square metres.
 *
 * Takes the ALREADY-POPULATED typed arrays: `positions` is interleaved xyz and
 * `indices` steps three at a time.
 *
 * Every triangle is visited exactly once, because the index buffer is stepped
 * linearly and no de-duplication is needed. That matters: a closed mesh that stores
 * both faces of every triangle would report DOUBLE its own surface, and a takeoff
 * comparison would then be confidently wrong by a factor of two. The `bounding` is
 * returned so a caller -- and the gate -- can check a figure against a known
 * element instead of taking it on trust.
 */
function measure(positions, indices) {
	try {
		let surface = 0;
		let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
		let minZ = Infinity, maxZ = -Infinity;
		const n = indices.length - (indices.length % 3);

		const mark = (v) => {
			const x = v[0], y = v[1], z = v[2];
			if (x < minX) minX = x; if (x > maxX) maxX = x;
			if (y < minY) minY = y; if (y > maxY) maxY = y;
			if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
		};

		for (let t = 0; t < n; t += 3) {
			const oa = indices[t] * 3, ob = indices[t + 1] * 3, oc = indices[t + 2] * 3;
			const ax = positions[oa], ay = positions[oa + 1], az = positions[oa + 2];
			const bx = positions[ob], by = positions[ob + 1], bz = positions[ob + 2];
			const cx = positions[oc], cy = positions[oc + 1], cz = positions[oc + 2];
			if (!Number.isFinite(ax) || !Number.isFinite(bx) || !Number.isFinite(cx)) continue;

			const ux = bx - ax, uy = by - ay, uz = bz - az;
			const vx = cx - ax, vy = cy - ay, vz = cz - az;
			const nx = uy * vz - uz * vy;
			const ny = uz * vx - ux * vz;
			const nz = ux * vy - uy * vx;
			surface += 0.5 * Math.sqrt(nx * nx + ny * ny + nz * nz);

			mark([ax, ay, az]); mark([bx, by, bz]); mark([cx, cy, cz]);
		}
		if (!Number.isFinite(surface) || !Number.isFinite(minX) || n < 3) return null;
		const dx = maxX - minX, dy = maxY - minY, dz = maxZ - minZ;
		return {
			surface,
			footprint: dx * dy,
			bounding: { dx, dy, dz, volume: dx * dy * dz },
		};
	} catch (error) {
		return null;
	}
}

async function spatialNodes(models) {
	const nodes = [];
	for (const model of models.models.list.values()) {
		if (!model || typeof model.getSpatialStructure !== "function") continue;
		const root = await model.getSpatialStructure();

		// One pass to collect what to ask about, then ONE batched ask.
		const ids = [];
		(function collect(node) {
			if (!node) return;
			if (node.localId != null) ids.push(node.localId);
			(node.children || []).forEach(collect);
		})(root);

		const byId = new Map();
		const BATCH = 500;
		for (let i = 0; i < ids.length; i += BATCH) {
			let data;
			try {
				data = await model.getItemsData(ids.slice(i, i + BATCH), {});
			} catch (error) {
				// A model that cannot answer is labelled by category rather than
				// failing the whole tree. Half a model tree beats none.
				data = null;
			}
			for (const record of data || []) {
				const localId = record && record._localId && record._localId.value;
				if (localId == null) continue;
				byId.set(localId, record);
			}
		}

		// Measured on the real model: 1,587 rows are 1,548 named, 38 category-only
		// spine rows, and exactly ONE reading "Unnamed" -- and that one HAS a GlobalId,
		// so it is a real element whose IFC record simply carries no `Name` and no
		// `LongName`. It is not a lookup failure: the same `getItemsData` response
		// supplied its `_guid`, and it supplies `_category` too.
		//
		// The old fallback never read `_category`, so an element that had a category
		// but no name fell through to the literal word. Order matters and is: the
		// element's own name, then the spatial node's category (which is what keeps the
		// 38 spine rows reading IFCPROJECT / IFCSITE rather than being blanked), then
		// the RECORD's category, de-categorised because "Building" tells a coordinator
		// more than "IFCBUILDING" -- and only then the word "Unnamed", which should mean
		// "this element has no identity we can show", not "we did not look".
		const label = (node) => {
			const record = byId.get(node.localId);
			const name = record && ((record.Name && record.Name.value) || (record.LongName && record.LongName.value));
			if (name) return String(name);
			const spine = node && node.category;
			if (spine) return String(spine);
			const category = record && record._category && record._category.value;
			if (category) return String(category).replace(/^IFC/, "");
			// Nothing at all to go on. Rare, and said plainly rather than blanked.
			return "(no name)";
		};
		// The GUID when the fragment has one, and NOTHING when it has neither.
		//
		// This used to be `String(guid || node.localId)`, and the 38 spine nodes have
		// `localId === null` -- so every one of them was handed the literal four-
		// character string "null" as its key. A row that looks like it has an identity
		// and does not is worse than a row that plainly has none: a click sends "null"
		// to the server, which resolves no element and answers with no issues, so the
		// panel reports "this element has no issues" about a node that is not an
		// element at all. No key is the honest answer, and the tree renders grouping
		// nodes as non-selectable headers.
		// A NAMESPACED key, not `null` and not `String(null)`.
		//
		// The 38 spine nodes have `localId === null`, so `String(guid ||
		// node.localId)` handed every one of them the literal four-character string
		// "null" -- a plausible-looking `data-guid` that resolves to no element, so a
		// click answered "this element has no issues" about something that is not an
		// element. `null` is no better: it is still a value the panel can render as
		// clickable.
		//
		// `spine:<CATEGORY>` says what the row IS, and cannot collide with an IFC
		// GlobalId, which is 22 characters of base64. The prefix is the contract the
		// panel uses to render a grouping node as a non-selectable header rather than
		// an element.
		const key = (node) => {
			if (node == null || node.localId == null) {
				return `spine:${(node && node.category) || "unknown"}`;
			}
			const record = byId.get(node.localId);
			const guid = record && record._guid && record._guid.value;
			return guid ? String(guid) : String(node.localId);
		};

		const convert = (node) => ({
			id: key(node),
			name: label(node),
			expressID: node.localId == null ? null : node.localId,
			isVisible: true,
			children: (node.children || []).map(convert),
		});
		nodes.push(convert(root));
	}
	return nodes;
}
/**
 * Import a vendor bundle by URL. The bundles are built into
 * `public/js/bim/vendor/` by `ops/tmp-ph3-02/build_vendor.mjs`; until that has run
 * this throws a NAMED error rather than a bare 404, so "the viewer is blank" and
 * "the vendor bundle was never built" stay distinguishable.
 */
/**
 * `@thatopen/fragments`, resolved for whichever host is running.
 *
 * Under Node the package resolves BY NAME, and it has to: `convert()` is the method
 * that refuted a BLOCKED row, it runs in plain Node with no server, and a
 * hardcoded `/assets/...` path does not exist there. In a browser a bare specifier
 * is unresolvable in a file Frappe serves directly, so the vendored bundle is the
 * only way in. One branch, used by both call sites, so neither host can regress
 * into the other's failure.
 */
async function loadFragmentsLib() {
	return typeof window === "undefined"
		? import("@thatopen/fragments")
		: loadModule("@thatopen/fragments");
}

async function loadModule(specifier) {
	const base = api.vendorBase();
	const url = base + specifier.replace("@thatopen/", "") + ".bundle.js";
	try {
		return await import(/* webpackIgnore: true */ url);
	} catch (error) {
		throw new Error(
			specifier +
				" could not be loaded from " +
				url +
				". Run ops/tmp-ph3-02/build_vendor.mjs to build the vendor bundles. (" +
				error.message +
				")"
		);
	}
}

/**
 * THE contract: `mount` is exported, and the namespace object is the default.
 *
 * Until this, `mount` was a method on a bare `api` object that was only ever
 * published onto `window.frappe`, so an `import()` of this module got a namespace
 * with NOTHING on it. Both callers were wrong in the same way and neither agreed
 * with the other: `bim_viewer.js` reached for `module.default` (undefined -- there
 * was no default export) and fell back to `module.mount` (also undefined -- `mount`
 * was not a named export), and the page went straight to `geometry.mount`. The
 * result was `mount is not a function` on a clean, fully-loaded module, with an
 * empty canvas and no other symptom.
 *
 * Both shapes are exported so the two call sites can collapse to one. `bim_viewer.js`
 * prefers `default`; the page uses the named `mount`. One object, both names.
 */
export { api as default };
export const mount = (...args) => api.mount(...args);

// Attached to the frappe namespace only when a browser host is present, so a Node
// import of this file for its pure helpers does not explode on `window`.
if (typeof window !== "undefined" && window.frappe && window.frappe.provide) {
	window.frappe.provide("erpnext_construction_ph.bim_geometry");
	Object.assign(window.erpnext_construction_ph.bim_geometry, api);
}
