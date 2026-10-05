// Standalone node harness — no Frappe, no browser — that loads boq_worksheet.js
// with a stubbed `frappe` global and a minimal in-memory DOM, then proves:
//
//   BEFORE the fix: the Import Rows button handler throws
//       ReferenceError: import_rows is not defined
//   AFTER  the fix: the handler reads the DOM and calls
//       erpnext_construction_ph.api.boq_matrix.bulk_insert_boq_items(boq, rows, replace)
//       with the exact tab-separated string the page collected, and renders the
//       server's success (or error) into #import_result.
//
// The "before" source is reproduced by stripping the `import_rows` function
// declaration out of the current file — yielding the pre-fix code state where
// the button references a name defined nowhere.
//
// Run:  node test_boq_worksheet_import_rows.mjs boq_worksheet.js

import fs from "node:fs";
import vm from "node:vm";

const file = process.argv[2] || "boq_worksheet.js";
const src = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");

// ---------------------------------------------------------------------------
// Minimal DOM shim — shared elements map so querySelector finds what innerHTML sets
// ---------------------------------------------------------------------------

const elements = new Map();

function parseIds(html, registry) {
	let m;
	const re = /id="([^"]+)"/g;
	while ((m = re.exec(html)) !== null) {
		registry(m[1]);
	}
}

// Every element — whether created by the template or by the list_boqs callback's
// `$select.innerHTML = '<select id="boq">…'` — gets the same innerHTML setter
// that registers any id="..." it contains.
function makeElement(id) {
	const obj = Object.create(elementProto);
	obj.id = id;
	obj.value = "";
	obj.checked = false;
	obj._html = "";
	elements.set(id, obj);
	return obj;
}

const elementProto = {
	set innerHTML(v) {
		const str = String(v);
		this._html = str;
		parseIds(str, (name) => { if (!elements.has(name)) makeElement(name); });
	},
	get innerHTML() { return this._html || ""; },
};

function el(id) {
	if (!elements.has(id)) makeElement(id);
	return elements.get(id);
}

function freshWrapper() {
	const obj = Object.create(elementProto);
	obj._html = "";
	obj.querySelector = (sel) => {
		const id = sel.replace(/^#/, "");
		return elements.get(id) || null;
	};
	return obj;
}

const document = {
	querySelector(sel) { return sel === "body > .page-body" ? freshWrapper() : null; },
	createElement(tag) {
		const obj = Object.create(elementProto);
		obj.tagName = tag;
		obj.className = "";
		obj._html = "";
		obj.appendChild = () => {};
		return obj;
	},
	body: { appendChild() {} },
};

const esc = (s) => {
	const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
	return String(s).replace(/[&<>"']/g, (c) => map[c]);
};

// Frappe's __() returns a String that also carries a .format() printf method.
const __ = (s, ...a) => {
	const base = a.length ? s.replace(/{(\d+)}/g, (_, i) => String(a[+i] ?? "")) : s;
	const obj = new String(base);
	obj.format = (...b) => String(obj).replace(/{(\d+)}/g, (_, i) => String(b[+i] ?? ""));
	return obj;
};
const _ = __;

function makeFrappe(harness) {
	const pageObj = { set_primary_action(label, handler) { harness.handler = handler; } };
	return {
		provide() {},
		standard_pages: {},
		utils: { escape_html: esc },
		ui: { make_app_page() { return pageObj; } },
		call(opts) {
			harness.calls.push(opts);
			if (opts && opts.method && opts.method.endsWith("list_boqs")) {
				(opts.callback || (() => {}))({ message: [{ name: "CP-BOQ-001" }] });
			}
		},
	};
}

function buildPage(source) {
	const harness = { calls: [], handler: null };
	const frappe = makeFrappe(harness);
	const ctx = vm.createContext({ frappe, document, __, _, console });
	vm.runInContext(source, ctx);
	ctx.frappe.standard_pages.boq_worksheet();
	return harness;
}

function seedDom() {
	elements.clear();
	el("boq").value = "CP-BOQ-001";
	el("tsv").value = "1\tSKU-001\tConcrete pad\tm3\t10\t500\t0.1\t0.15";
	el("replace").checked = false;
	el("import_result").innerHTML = "";
}

// ---------------------------------------------------------------------------
// 1. BEFORE: strip import_rows, reproduce the ReferenceError
// ---------------------------------------------------------------------------

const beforeSrc = src.replace(
	/\t\/\/ -{5,}\n\t\/\/ import_rows[\s\S]*?\n\t}\n/,
	"\n"
);
if (beforeSrc.includes("function import_rows")) {
	console.error("FAIL: before-stripping did not remove import_rows");
	process.exit(1);
}

const beforeHarness = buildPage(beforeSrc);
seedDom();

let refError = null;
try {
	beforeHarness.handler(null);
} catch (e) {
	refError = e;
}
// vm runs in its own realm, so instanceof fails; check by name.
if (!refError || refError.name !== "ReferenceError") {
	console.error("FAIL: pre-fix did not throw ReferenceError, got:", refError);
	process.exit(1);
}
console.log("BEFORE: Import Rows button throws  " + refError.name + ": " + refError.message);

// ---------------------------------------------------------------------------
// 2. AFTER: the real source must wire the button to bulk_insert_boq_items
// ---------------------------------------------------------------------------

const afterHarness = buildPage(src);
seedDom();

if (afterHarness.handler === null) {
	console.error("FAIL: set_primary_action was never called on the fixed page");
	process.exit(1);
}

try {
	afterHarness.handler(null);
} catch (e) {
	console.error("FAIL: post-fix handler threw:", e);
	process.exit(1);
}

const bulkCall = afterHarness.calls.find(
	(c) => c && c.method && c.method.endsWith("bulk_insert_boq_items")
);
if (!bulkCall) {
	console.error("FAIL: after fix, bulk_insert_boq_items was NOT called");
	console.error("  frappe.call methods:", afterHarness.calls.map((c) => c.method));
	process.exit(1);
}

const expectedTsv = "1\tSKU-001\tConcrete pad\tm3\t10\t500\t0.1\t0.15";

if (bulkCall.method !== "erpnext_construction_ph.api.boq_matrix.bulk_insert_boq_items") {
	console.error("FAIL: wrong method:", bulkCall.method);
	process.exit(1);
}
if (bulkCall.args.boq !== "CP-BOQ-001") {
	console.error("FAIL: boq arg =", bulkCall.args.boq);
	process.exit(1);
}
if (bulkCall.args.rows !== expectedTsv) {
	console.error("FAIL: rows arg mismatch:\n  expected: " + JSON.stringify(expectedTsv) + "\n  got:      " + JSON.stringify(bulkCall.args.rows));
	process.exit(1);
}
if (bulkCall.args.replace !== false) {
	console.error("FAIL: replace arg =", bulkCall.args.replace);
	process.exit(1);
}
console.log(
	"AFTER:  Import Rows -> bulk_insert_boq_items(boq=CP-BOQ-001, " +
	"rows=<" + expectedTsv.length + "-byte TSV, 8 cols>, replace=false)"
);

// ---------------------------------------------------------------------------
// 3. Success callback renders into #import_result
// ---------------------------------------------------------------------------

bulkCall.callback({
	message: { boq: "CP-BOQ-001", inserted: 1, header_skipped: true, created_uoms: ["m3"] },
});
const html1 = el("import_result")._html;
if (!html1.includes("Imported 1 row")) {
	console.error("FAIL: result div did not show success:", html1);
	process.exit(1);
}
console.log("AFTER:  success renders ->", html1.replace(/\s+/g, " ").slice(0, 110));

// ---------------------------------------------------------------------------
// 4. Error callback (e.g. wrong column count) renders a message
// ---------------------------------------------------------------------------

el("import_result").innerHTML = "";
if (typeof bulkCall.error !== "function") {
	console.error("FAIL: no error callback registered on frappe.call");
	process.exit(1);
}
bulkCall.error({ message: "Line 2 has 7 columns; expected 8." });
const html2 = el("import_result")._html;
if (!html2.includes("Line 2 has 7 columns")) {
	console.error("FAIL: error callback did not render:", html2);
	process.exit(1);
}
console.log("AFTER:  error renders   ->", html2.replace(/\s+/g, " ").slice(0, 110));

// ---------------------------------------------------------------------------
// 5. Missing BOQ: client-side guard, no server call
// ---------------------------------------------------------------------------

const guardHarness = buildPage(src);
seedDom();
el("boq").value = "";
el("tsv").value = "1\tx\ty\tm3\t1\t1\t0\t0";
el("replace").checked = false;
guardHarness.handler(null);
const guardCalls = guardHarness.calls.filter(
	(c) => c.method && c.method.endsWith("bulk_insert_boq_items")
);
if (guardCalls.length) {
	console.error("FAIL: should NOT call bulk_insert when no BOQ selected");
	process.exit(1);
}
const html3 = el("import_result")._html;
if (!html3.includes("select a Bill of Quantities")) {
	console.error("FAIL: expected client-side guard message, got:", html3);
	process.exit(1);
}
console.log("AFTER:  empty BOQ -> client guard, no server call");

console.log("\nALL CHECKS PASSED");