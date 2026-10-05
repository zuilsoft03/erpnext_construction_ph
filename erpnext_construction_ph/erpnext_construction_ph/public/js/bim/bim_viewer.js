// BIM / BCF surface for erpnext_construction_ph.
//
// Loaded on every Desk page via hooks.py app_include_js.
//
// Scope note, deliberate: the geometry engine is Phase 3 (D-009 — in-browser
// Web Worker conversion via @thatopen/fragments + web-ifc.wasm). This file owns
// everything that does NOT need the geometry engine — model inventory,
// conversion state, and the BCF issue overlay — and exposes a single
// `mountGeometry()` seam that Phase 3 replaces. It does not pretend to render a
// model it cannot: with no engine attached the surface says so plainly instead
// of showing an empty black rectangle.

frappe.provide("erpnext_construction_ph.bim_viewer");

Object.assign(window.erpnext_construction_ph.bim_viewer, {
	// The real engine (D-043), attached lazily. It was `null` for this file's whole
	// life with a comment saying Phase 3 would replace it; `mount()` therefore
	// rendered "Geometry engine not installed" on every surface, which is the
	// honest-empty-state behaviour doing its job on a seam nobody had filled.
	// `attachGeometry()` is called by the workspace page, NOT at import time, so a
	// viewer nobody opened never pulls the thatopen bundles.
	engine: null,

	/**
	 * Load the real geometry engine and install it at this seam.
	 * @param {object} opts { wasmPath }
	 * @returns {Promise<object>} the engine
	 */
	async attachGeometry(opts = {}) {
		const url =
			"/assets/erpnext_construction_ph/js/bim/geometry.js";
		try {
			const module = await import(/* webpackIgnore: true */ url);
			this.engine = {
				attach: (container, model) =>
					module.default
						? module.default.mount(container, {
								fragUrl: model.frag_url,
								modelId: model.name,
								wasmPath: opts.wasmPath,
							})
						: module.mount(container, {
								fragUrl: model.frag_url,
								modelId: model.name,
								wasmPath: opts.wasmPath,
							}),
			};
			return this.engine;
		} catch (error) {
			// Leave `engine` null so `mount()` still shows the honest empty state,
			// now naming the real cause rather than "not installed".
			this.loadError = error.message;
			return null;
		}
	},

	/** List the converted/queued models for a project, newest first. */
	getModels(project, callback) {
		frappe.call({
			method: "erpnext_construction_ph.api.bim_viewer.list_models",
			args: { project },
			callback: (r) => callback(r.message || []),
		});
	},

	/** BCF topics (issues) for a model, for the overlay. */
	getTopics(bim_model, callback) {
		frappe.call({
			method: "erpnext_construction_ph.api.bim_viewer.list_topics",
			args: { bim_model },
			callback: (r) => callback(r.message || []),
		});
	},

	/**
	 * Render the viewer surface.
	 * @param {jQuery|DOMElement} $surface container
	 * @param {object} opts { project, model }
	 */
	mount($surface, opts = {}) {
		$surface.addClass("bim-viewer-surface").empty();

		if (!this.engine) {
			// Honest empty state. The CSS ships the dark surface so the layout is
			// stable, but we say what is missing rather than implying a broken model.
			$surface.append(
				$("<div class='bim-viewer-placeholder'></div>").html(
					__("Geometry engine not installed.") +
						"<br>" +
						__("Run ops/tmp-ph3-02/build_vendor.mjs to build the thatopen bundles.")
				)
			);
			return;
		}

		// `attach` is async now that a real engine sits behind the seam. The caller
		// gets the promise rather than a fire-and-forget, because a viewer that
		// reports success before the model has loaded is the exact failure this
		// row's previous gate carried.
		const attached = this.engine.attach($surface[0], opts.model);
		this.overlayTopics($surface, opts.model);
		return Promise.resolve(attached);
	},

	/** Draw BCF issue markers from each topic's camera vectors. */
	overlayTopics($surface, model) {
		if (!model) {
			return;
		}
		this.getTopics(model.name, (topics) => {
			(topics || []).forEach((topic) => {
				const marker = $("<div class='bcf-issue-marker'></div>")
					.text(topic.priority === "High" ? "!" : "•")
					.attr("title", topic.title || topic.name)
					.css("left", `${topic.screen_x || 50}%`)
					.css("top", `${topic.screen_y || 50}%`);

				marker.on("click", () => {
					frappe.set_route("Form", "BCF Topic", topic.name);
				});
				$surface.append(marker);
			});
		});
	},

	/** Compact model inventory card, used by the BIM & VDC workspace. */
	renderModelCard(model) {
		const $card = $("<div class='bim-model-card'></div>");
		$card.append($("<div class='strong'></div>").text(model.model_name || model.name));
		$card.append(
			$("<div class='conversion-status'></div>")
				.text(model.conversion_status || "Pending")
				.attr("data-status", model.conversion_status || "Pending")
		);
		if (model.lod_level) {
			$card.append($("<div class='text-muted small'></div>").text(model.lod_level));
		}
		if (model.frag_url) {
			$card.append(
				$("<a class='small'></a>")
					.attr("href", model.frag_url)
					.attr("target", "_blank")
					.text(__("Open converted model"))
			);
		}
		return $card;
	},
});
