# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-5: the right panel, and the rule that shapes it.

  * **An absent value renders as "Not measured", never as a number.** This panel
    sits next to a bill. `0` is a claim about the BUILDING -- "this wall encloses
    no area" -- while "not measured" is a claim about the DATA. Only one is true
    when the field is empty, and a rendered zero in a quantity-surveying tool is a
    false measurement that can reach a payment certificate. The resolver sends
    `None` plus an `unknown` list, and the panel must honour both.

  * **Issues are filtered to the SELECTION**, and with nothing selected they show
    the whole model rather than nothing -- an empty list with no selection reads as
    "no issues" when it means "no filter applied".

  * **The takeoffs card exists only in PDF mode**, per the spec.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from js_harness import module_path, require_node, run_js  # noqa: E402

MODULE = "erpnext_construction_ph/public/js/bim/right_panel.js"
EXPORTS = ["createRightPanel", "formatValue", "PROPERTY_ROWS"]


def run(body):
	return run_js(body, module_path(MODULE), EXPORTS)


class TestRightPanel(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path(MODULE)

	def test_every_unknown_value_renders_as_not_measured(self):
		"""The one rule. `None`, absent, empty and listed-as-unknown all agree."""
		result = run(
			"""
			const { formatValue, PROPERTY_ROWS } = module.exports;
			const row = (k) => PROPERTY_ROWS.find((r) => r.key === k);
			const out = {};
			for (const key of ["area", "boq_item", "cost", "progress_percent"]) {
				const r = row(key);
				out[key] = {
					nullish: formatValue(r, null, []),
					absent: formatValue(r, undefined, []),
					empty: formatValue(r, "", []),
					listedUnknown: formatValue(r, 0, [key]),   // a real 0, but flagged
				};
			}
			out.name = { given: formatValue(row("element_name"), "Wall-001", []) };
			out.cost_real = { real: formatValue(row("cost"), 125000, []) };
			out.progress = { real: formatValue(row("progress_percent"), 60, []) };
			out.zeroArea = { real: formatValue(row("area"), 0, []) };
			console.log(JSON.stringify(out));
			"""
		)
		for key in ("area", "boq_item", "cost", "progress_percent"):
			for form in ("nullish", "absent", "empty", "listedUnknown"):
				self.assertEqual(
					result[key][form], "Not measured",
					f"{key}.{form} rendered {result[key][form]!r} instead of 'Not measured'",
				)
		# A REAL zero is a measurement and must be shown as one -- WITH its unit, which
		# the old assertion ("0") predated. The intent is that a zero is never confused
		# with a blank, and "0 m2" serves that better than "0": it says what the number
		# is, and a bare "0" on a card whose other values carry units reads as an
		# unformatted placeholder. What is asserted here is the INTENT, not the old
		# string -- the string was an accident of where the unit used to be appended.
		self.assertNotEqual(result["zeroArea"]["real"], "Not measured")
		self.assertTrue(
			result["zeroArea"]["real"].startswith("0"),
			"a real zero must render as a number, got %r" % result["zeroArea"]["real"],
		)
		self.assertIn("m2", result["zeroArea"]["real"], "a zero area must still carry its unit")
		self.assertEqual(result["name"]["given"], "Wall-001")
		self.assertIn("₱", result["cost_real"]["real"])
		self.assertIn("60%", result["progress"]["real"])

	def test_issues_filter_to_the_selection_and_fall_back_to_the_model(self):
		result = run(
			"""
			const { createRightPanel } = module.exports;
			const host = document.createElement("div");
			const panel = createRightPanel(host, {});
			const topics = [
			  { topic_guid: "t1", title: "On the wall", elements: [{ global_id: "g1" }] },
			  { topic_guid: "t2", title: "On the beam",  elements: [{ global_id: "g2" }] },
			];
			panel.renderIssues(topics, "g1");
			const filtered = host.querySelectorAll(".bim-rp-issue").length;
			panel.renderIssues(topics, null);
			const all = host.querySelectorAll(".bim-rp-issue").length;
			panel.renderIssues(topics, "g-nothing");
			const none = (host.querySelector('[data-body="issues"]')||{}).innerHTML;
			console.log(JSON.stringify({ filtered, all, noneMsg: none.slice(0, 60) }));
			"""
		)
		self.assertEqual(result["filtered"], 1, "the issues card did not filter to the selection")
		self.assertEqual(result["all"], 2, "with no selection the card should show the model")
		self.assertIn("No issues on this element", result["noneMsg"])

	def test_the_takeoffs_card_exists_only_in_pdf_mode(self):
		result = run(
			"""
			const { createRightPanel } = module.exports;
			const host = document.createElement("div");
			const panel = createRightPanel(host, {});
			const hidden = (mode) => { panel.setMode(mode);
				return host.querySelector('[data-card="takeoffs"]').hidden; };
			console.log(JSON.stringify({ "3d": hidden("3d"), pdf: hidden("pdf"), split: hidden("split") }));
			"""
		)
		self.assertTrue(result["3d"])
		self.assertFalse(result["pdf"], "the takeoffs card is missing in PDF mode")
		self.assertTrue(result["split"])

	def test_a_clicked_issue_reports_its_topic(self):
		result = run(
			"""
			const { createRightPanel } = module.exports;
			const picked = [];
			const host = document.createElement("div");
			const panel = createRightPanel(host, { onIssue: (t) => picked.push(t) });
			panel.renderIssues([{ topic_guid: "t1", title: "A", elements: [{ global_id: "g1" }] }], "g1");
			host.querySelector(".bim-rp-issue").click();
			console.log(JSON.stringify({ picked }));
			"""
		)
		self.assertEqual(result["picked"], ["t1"])

	def test_properties_with_nothing_selected_invites_a_selection(self):
		result = run(
			"""
			const { createRightPanel, PROPERTY_ROWS } = module.exports;
			const host = document.createElement("div");
			const panel = createRightPanel(host, {});
			panel.renderProperties(null);
			const html = host.querySelector('[data-body="properties"]').innerHTML;
			panel.renderProperties({ element_name: "Wall-001", area: null, unknown: ["area"] });
			const rows = host.querySelectorAll(".bim-rp-key").length;
			const keys = PROPERTY_ROWS.map((r) => r.key);
			const unknownCells = Array.from(host.querySelectorAll(".is-unknown")).length;
			console.log(JSON.stringify({ empty: html.slice(0, 50), rows, keys, unknownCells }));
			"""
		)
		self.assertIn("Select an element", result["empty"])
		# A BARE COUNT WAS THE WRONG ASSERTION, and this is why. It said "eight rows
		# in the spec", which reads as a contract on the NUMBER, so adding a ninth row
		# for a measured reason looked like breaking it. What the spec actually fixes
		# is WHICH rows the card shows, and that is what is asserted now -- plus the one
		# deliberate addition, named.
		for spec_key in (
			"element_name",
			"element_type",
			"element_guid",
			"ifc_id",
			"area",
			"boq_item",
			"cost",
			"progress_percent",
		):
			self.assertIn(spec_key, result["keys"], "the spec's Properties card must still show %s" % spec_key)
		# `area_class` was added on 2026-09-29, deliberately: with 619 IFCREINFORCINGBAR
		# at 0.16-0.23 m2 against 299 IFCSLAB carrying 73.7% of the area, a user reading
		# 0.23 m2 beside 16,464 m2 with nothing to say they are different kinds of thing
		# is exactly the mistake the classification work exists to prevent.
		self.assertIn("area_class", result["keys"], "the area's class must be shown beside the number")
		self.assertEqual(result["rows"], len(result["keys"]), "every declared row must render")
		self.assertEqual(result["unknownCells"], 1)
