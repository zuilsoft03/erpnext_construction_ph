// Build the thatopen vendor bundles the BIM workspace loads at runtime.
//
// Why a build step at all: geometry.js does `import("@thatopen/components")` from
// the browser, and npm packages are not directly servable — they are un-bundled ESM
// with bare specifiers (`three`, `web-ifc`) that a browser cannot resolve. esbuild
// flattens each into a single file under `public/js/bim/vendor/`, which is what
// `loadModule()` in geometry.js fetches.
//
// This is deliberately NOT an npm script. `package.json` already carries that
// warning about `build.sh` for the tiptap bundle, and the reason applies here too:
// a script that looks runnable from the host and is not sends the next person
// chasing a failed `npm run`. This one DOES run from the host -- it resolves esbuild
// from node_modules and needs no container -- but it is still a script you invoke
// directly, so its failure mode is the failure mode you see.
//
// Usage:  node erpnext_construction_ph/erpnext_construction_ph/tools/build_bim_vendor.mjs
// Output: public/js/bim/vendor/  (sibling of this file's parent)

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// This file lives at <app>/tools/, so the package root is its PARENT. It started in
// ops/tmp-ph3-02/ and was moved here for a reason worth recording: .gitignore
// excludes `ops/tmp-*/*`, so three SHIPPED files (geometry.js, bim_viewer.js and the
// page) told users to run a script git would never carry. A build step nobody can
// obtain is not a build step.
const APP = path.resolve(HERE, "..");
const OUT = path.join(APP, "public/js/bim/vendor");

// Resolved from the APP tree, not as a bare specifier: this script lives in ops/,
// and Node resolves bare imports from the importing file's own directory, so a bare
// "esbuild" looks in ops/tmp-ph3-02/node_modules and reports MODULE_NOT_FOUND for a
// package that is installed. This is the second time in this row that a bare
// specifier sent me looking for a dependency that was present all along.
//
// pathToFileURL because a bare "C:\..." is read as URL scheme "c:" by the ESM
// loader on Windows.
let esbuild;
try {
	esbuild = await import(
		pathToFileURL(path.join(APP, "node_modules/esbuild/lib/main.js")).href
	);
} catch (error) {
	console.error(
		"esbuild is not resolvable from the app tree. Run `npm install` in " +
			APP +
			" first.\n(" +
			error.message +
			")"
	);
	process.exit(1);
}
const TARGETS = [
	{ entry: "@thatopen/components", out: "components.bundle.js" },
	{ entry: "@thatopen/fragments", out: "fragments.bundle.js" },
	// pdf.js, for the PDF takeoff viewport (UI-1). The shipped Takeoff Studio
	// asked for `pdfjs-dist/build/pdf.min.js` through `frappe.require`, which 404s:
	// pdfjs-dist was not a dependency, and pdfjs 4.x ships `.mjs` rather than that
	// filename anyway. So the plan has never rendered a document.
	//
	// `.js` and not `.mjs`, which is load-bearing rather than cosmetic: this nginx
	// serves `.mjs` as `application/octet-stream` with `X-Content-Type-Options:
	// nosniff`, and a browser then refuses to execute it, reporting "Failed to fetch
	// dynamically imported module" -- which names the import, not the MIME type, and
	// reads exactly like a missing file. The content is still an ES module; the
	// extension only picks what the server's mime map says.
	{ entry: "pdfjs-dist/build/pdf.mjs", out: "pdf.js" },
];

// pdf.js loads its worker in a SEPARATE thread, so it cannot be inlined into the
// bundle above. Copied verbatim: it is already minified and already standalone.
const COPIES = [{ from: "pdfjs-dist/build/pdf.worker.min.mjs", to: "pdf.worker.js" }];

// web-ifc's WASM has to be servable too: `IfcImporter` points the loader at a
// directory of `.wasm` files. Copying them keeps the app self-contained instead of
// reaching for a CDN at runtime, which is the difference between working offline and
// working on a good day.
const WASM_FILES = ["web-ifc.wasm", "web-ifc-mt.wasm", "web-ifc-node.wasm"];

async function bundle({ entry, out }) {
	const entryPath = path.join(APP, "node_modules", entry);
	await esbuild.build({
		entryPoints: [entryPath],
		bundle: true,
		format: "esm",
		platform: "browser",
		target: "es2022",
		outfile: path.join(OUT, out),
		logLevel: "warning",
		// `three` reaches for node builtins in a few places; stubbing them is the
		// documented approach for a browser bundle and costs nothing here because
		// the viewer never takes those paths.
		external: [],
	});
	const bytes = fs.statSync(path.join(OUT, out)).size;
	console.log(`  built ${out} -- ${(bytes / 1024).toFixed(0)} KB`);
}

function copyWasm() {
	const src = path.join(APP, "node_modules/web-ifc");
	if (!fs.existsSync(src)) {
		console.log("  web-ifc not installed; skipping WASM copy");
		return;
	}
	for (const name of WASM_FILES) {
		const from = path.join(src, name);
		if (!fs.existsSync(from)) continue;
		fs.copyFileSync(from, path.join(OUT, name));
		console.log(`  copied ${name} -- ${(fs.statSync(from).size / 1024).toFixed(0)} KB`);
	}
}

function copyExtras() {
	for (const { from, to } of COPIES) {
		const src = path.join(APP, "node_modules", from);
		if (!fs.existsSync(src)) {
			console.log(`  ${from} not installed; skipping`);
			continue;
		}
		fs.copyFileSync(src, path.join(OUT, to));
		console.log(`  copied ${to} -- ${(fs.statSync(src).size / 1024).toFixed(0)} KB`);
	}
}

fs.mkdirSync(OUT, { recursive: true });
console.log(`Building vendor bundles -> ${OUT}`);
for (const target of TARGETS) {
	await bundle(target);
}
copyWasm();
copyExtras();
console.log("Done. geometry.js and pdf_viewport.js will now find these via loadModule().");
