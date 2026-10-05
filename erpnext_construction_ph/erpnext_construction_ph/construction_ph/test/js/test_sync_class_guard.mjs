// The comparison must refuse a class mismatch, not diff two bare numbers.
//
// The case that matters is measured, not hypothetical: the live model carries 619
// `IFCREINFORCINGBAR` at 0.16-0.23 m2 each, and 299 `IFCSLAB` carrying 73.7% of all
// surface. Pick a bar, link it to a region that was mapped from a slab, and the old
// `compare()` returned a confident `delta_percent` between a reinforcing bar and a
// floor. The arithmetic was fine and the two numbers meant different things.

import { compare, areaClassOf, createLinkRegistry, createSyncEngine, defaultResolve } from "../../../public/js/bim/sync_engine.js";

const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok: !!ok, detail });
const N = (c) => String.fromCharCode(0);

console.log("=== areaClassOf ===");
check("rebar is REINFORCEMENT", areaClassOf("IFCREINFORCINGBAR") === "REINFORCEMENT");
check("slab is SURFACE", areaClassOf("IFCSLAB") === "SURFACE");
check("an unlisted type is UNCLASSIFIED, never SURFACE", areaClassOf("IFCSOMETHINGNEW") === "UNCLASSIFIED");
check("case does not decide", areaClassOf("ifcreinforcingbar") === "REINFORCEMENT");

console.log("=== compare refuses a class mismatch ===");
const cross = compare(0.23, 16464.7, { modelClass: "REINFORCEMENT", takeoffClass: "SURFACE" });
check("bar against slab is NOT comparable", cross.comparable === false);
check("it names both classes", cross.model_class === "REINFORCEMENT" && cross.takeoff_class === "SURFACE");
check("it gives no delta", cross.delta === undefined && cross.delta_percent === undefined);
check("the reason says they measure different things", /different things/.test(cross.reason || ""));

const same = compare(100, 100.2, { modelClass: "SURFACE", takeoffClass: "SURFACE" });
check("slab against slab still compares", same.comparable === true);
check("and still computes a delta", typeof same.delta_percent === "number");

console.log("=== an unknown class, and the opt-in strictness ===");
const unclassified = compare(10, 10, { modelClass: "UNCLASSIFIED", takeoffClass: "SURFACE" });
check("UNCLASSIFIED against SURFACE refuses -- they are different classes", unclassified.comparable === false);
// requireClass is OPT-IN. A class MISMATCH always refuses; demanding a class from
// every caller broke the existing contract, where a linked element with a
// measurement on each side is comparable and a caller not yet wired to a takeoff
// cannot supply one. So the default stays permissive and the strict mode is the
// caller's to choose.
check("a matching pair still compares without requireClass", compare(10, 10, { modelClass: "SURFACE", takeoffClass: "SURFACE" }).comparable === true);
check("no class at all still compares by default", compare(10, 10, {}).comparable === true);
const strict = compare(10, 10, { modelClass: null, takeoffClass: "SURFACE", requireClass: true });
check("requireClass refuses a missing class when the caller asks for it", strict.comparable === false);
check("and names which side is unknown", /takeoff|element/.test(strict.reason || ""));

console.log("=== the registry carries the class ===");
const links = createLinkRegistry();
const entry = links.link("GUID0000000000000001", {
	sheet: "PLAN-01",
	region: { x: 1, y: 2, w: 3, h: 4 },
	area: 12.5,
	elementType: "IFCREINFORCINGBAR",
});
check("the link stores a class", entry.element_class === "REINFORCEMENT");
check("an unlisted type links as UNCLASSIFIED", links.link("G2", { sheet: "S", elementType: "IFCWHAT" }).element_class === "UNCLASSIFIED");

console.log("=== the engine passes the class through ===");
const engine = createSyncEngine({ links });
engine.select("GUID0000000000000001", "3d");
const viaEngine = engine.comparison(0.23, { takeoffClass: "SURFACE" });
check("the engine refuses a bar against a surface line", viaEngine.comparable === false);
check("and names the classes", viaEngine.model_class === "REINFORCEMENT" && viaEngine.takeoff_class === "SURFACE");
const matching = engine.comparison(0.23, { takeoffClass: "REINFORCEMENT" });
check("a bar against a reinforcement line compares", matching.comparable === true);
// The existing contract this change nearly broke: a linked element with a
// measurement on each side and no class supplied anywhere still compares.
const noClass = engine.comparison(0.23, {});
check("no class supplied still compares, as the existing contract requires", noClass.comparable === true);
const strictEngine = engine.comparison(0.23, { requireClass: true });
check("but the caller can opt into strictness", strictEngine.comparable === false);

console.log("=== the PERSISTED path, through the REAL defaultResolve ===");
// The previous version of this block passed a CUSTOM `resolve` that built the entry
// inline -- including the `element_class` line under test. Deleting that line from
// the real function left this test green, because it never called it. `defaultResolve`
// is exported now so the guard is proven on the path the product actually runs.
const rows = {
	BAR: { drawing: "PLAN-01", drawing_page: "A1", drawing_region: "1,2,3,4", mapped_area: 0.2, element_type: "IFCREINFORCINGBAR" },
	SLAB: { drawing: "PLAN-01", drawing_page: "A1", drawing_region: "5,6,7,8", mapped_area: 900, element_type: "IFCSLAB" },
	ODD: { drawing: "PLAN-01", drawing_page: "A1", drawing_region: "9,9,1,1", mapped_area: 5, element_type: "IFCWHAT" },
	NODRAW: { drawing_page: "A1", mapped_area: 5, element_type: "IFCSLAB" },
};
defaultResolve.getMap = (g) => rows[g] || null;

const bar = defaultResolve("BAR");
check("the real resolver classifies a persisted bar", bar.element_class === "REINFORCEMENT");
check("the real resolver classifies a persisted slab", defaultResolve("SLAB").element_class === "SURFACE");
check("the real resolver classifies an unlisted type UNCLASSIFIED", defaultResolve("ODD").element_class === "UNCLASSIFIED");
check("a row with no drawing still resolves to null", defaultResolve("NODRAW") === null);
check("a guid the map does not hold resolves to null", defaultResolve("MISSING") === null);

// And through the engine, on the real resolver -- no custom resolve at all.
const persisted = createSyncEngine();
persisted.select("BAR", "3d");
const barVsSlab = persisted.comparison(0.2, { takeoffClass: "SURFACE" });
check("a persisted bar against a surface line refuses", barVsSlab.comparable === false);
check("and the class came from the real resolver", barVsSlab.model_class === "REINFORCEMENT");
persisted.select("SLAB", "3d");
check("a persisted slab against a surface line compares", persisted.comparison(900, { takeoffClass: "SURFACE" }).comparable === true);
persisted.select("ODD", "3d");
const odd = persisted.comparison(5, { takeoffClass: "SURFACE" });
check("a persisted unlisted type refuses", odd.comparable === false && odd.model_class === "UNCLASSIFIED");

let failed = 0;
for (const r of results) {
	if (!r.ok) failed++;
	console.log("  " + (r.ok ? "PASS" : "FAIL") + "  " + r.name + (r.detail ? "  " + r.detail : ""));
}
console.log("  " + (results.length - failed) + "/" + results.length + " pass");
process.exit(failed ? 1 : 0);
