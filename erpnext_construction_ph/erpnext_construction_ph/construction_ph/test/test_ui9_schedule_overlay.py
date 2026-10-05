# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""UI-9: the 4D schedule overlay.

`scheduleState` is pure, so it is tested hard and without a viewer. The cases
that matter are the boundaries and the refusals:

  * a line is active ON its start day and ON its end day -- an exclusive end shows
    a one-day task as never active and a task as finished the moment it begins;
  * a one-day task (start == end) is active rather than dividing by zero;
  * dates the wrong way round are REFUSED, not swapped -- two dates reversed is a
    data error, and normalising it draws a task that runs backwards;
  * a line with no dates is `unscheduled`, and is never coloured as if it were
    complete. An element shown as done because nothing said otherwise is a claim
    about the BUILDING;
  * `actual_percent_complete` never decides the state. A line the database says
    is finished before its plan starts is a real discrepancy and the overlay's
    whole purpose is to show it, so it is flagged `ahead` rather than resolved.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from js_harness import module_path, require_node, run_js  # noqa: E402

MODULE = "erpnext_construction_ph/public/js/bim/schedule_overlay.js"
EXPORTS = ["scheduleState", "scheduleSpan", "createScheduleOverlay", "STATES"]


def run(body):
	return run_js(body, module_path(MODULE), EXPORTS)


class TestScheduleOverlay(unittest.TestCase):
	def setUp(self):
		require_node(self)
		self.module = module_path(MODULE)

	def test_the_boundaries_of_a_task_are_inclusive(self):
		result = run(
			"""
			const { scheduleState } = module.exports;
			const item = { planned_start: "2026-03-01", planned_end: "2026-03-10" };
			const at = (d) => scheduleState(item, d).state;
			console.log(JSON.stringify({
				dayBefore: at("2026-02-28"),
				startDay: at("2026-03-01"),
				middle: at("2026-03-05"),
				endDay: at("2026-03-10"),
				dayAfter: at("2026-03-11"),
			}));
			"""
		)
		self.assertEqual(result["dayBefore"], "future")
		self.assertEqual(result["startDay"], "active", "a task is not active on the day it starts")
		self.assertEqual(result["middle"], "active")
		self.assertEqual(result["endDay"], "active", "a task is already complete on its end day")
		self.assertEqual(result["dayAfter"], "complete")

	def test_a_one_day_task_is_active_rather_than_dividing_by_zero(self):
		result = run(
			"""
			const { scheduleState } = module.exports;
			const one = { planned_start: "2026-03-01", planned_end: "2026-03-01" };
			console.log(JSON.stringify({
				onTheDay: scheduleState(one, "2026-03-01"),
				before: scheduleState(one, "2026-02-28").state,
			}));
			"""
		)
		self.assertEqual(result["onTheDay"]["state"], "active")
		self.assertEqual(result["onTheDay"]["fraction"], 1)
		self.assertEqual(result["before"], "future")

	def test_reversed_dates_are_refused_rather_than_swapped(self):
		result = run(
			"""
			const { scheduleState } = module.exports;
			const bad = { planned_start: "2026-03-10", planned_end: "2026-03-01" };
			console.log(JSON.stringify(scheduleState(bad, "2026-03-05")));
			"""
		)
		self.assertEqual(result["state"], "unscheduled")
		self.assertIn("before the planned start", result["reason"])

	def test_an_unscheduled_line_is_never_shown_as_complete(self):
		result = run(
			"""
			const { scheduleState } = module.exports;
			console.log(JSON.stringify({
				noDates: scheduleState({}, "2026-03-05"),
				oneDate: scheduleState({ planned_start: "2026-03-01" }, "2026-03-05"),
				noDateGiven: scheduleState({ planned_start: "2026-03-01", planned_end: "2026-03-10" }, null),
			}));
			"""
		)
		for key in ("noDates", "oneDate", "noDateGiven"):
			self.assertEqual(result[key]["state"], "unscheduled", f"{key} was not unscheduled")

	def test_actual_progress_flags_a_discrepancy_without_deciding_the_state(self):
		"""A line the database says is finished before its plan starts is a REAL
		discrepancy. Showing it is the overlay's purpose; resolving it silently is
		not."""
		result = run(
			"""
			const { scheduleState } = module.exports;
			const item = { planned_start: "2026-03-01", planned_end: "2026-03-10", actual_percent_complete: 100 };
			console.log(JSON.stringify({
				ahead: scheduleState(item, "2026-03-02"),
				behind: scheduleState({ ...item, actual_percent_complete: 20 }, "2026-03-20"),
				planned: scheduleState({ ...item, actual_percent_complete: 50 }, "2026-03-20"),
			}));
			"""
		)
		self.assertEqual(result["ahead"]["state"], "active", "actual progress overrode the plan")
		self.assertTrue(result["ahead"].get("ahead"), "the finished-early case was not flagged")
		self.assertTrue(result["behind"].get("behind"), "the unfinished-ontime case was not flagged")
		self.assertEqual(result["planned"]["state"], "complete")

	def test_the_scrubber_spans_every_scheduled_line(self):
		result = run(
			"""
			const { scheduleSpan, createScheduleOverlay } = module.exports;
			const items = [
			  { planned_start: "2026-01-10", planned_end: "2026-02-10" },
			  { planned_start: "2026-03-01", planned_end: "2026-04-01" },
			  { item_code: "no dates" },
			];
			const host = document.createElement("div");
			const overlay = createScheduleOverlay(host, {});
			const loaded = overlay.load(items, []);
			const span = scheduleSpan(items);
			console.log(JSON.stringify({
				from: new Date(span.from).toISOString().slice(0, 10),
				to: new Date(span.to).toISOString().slice(0, 10),
				loaded,
				coverageText: host.querySelector(".bim-sched-coverage").textContent,
			}));
			"""
		)
		self.assertEqual(result["from"], "2026-01-10")
		self.assertEqual(result["to"], "2026-04-01")
		self.assertEqual(result["loaded"]["total"], 3)
		self.assertEqual(result["loaded"]["scheduled"], 2, "the unscheduled line was counted as scheduled")
		# And the coverage line says so on screen, rather than the viewer assuming
		# the whole model is scheduled.
		self.assertIn("2 of 3", result["coverageText"])

	def test_scrubbing_moves_the_date_and_recounts(self):
		result = run(
			"""
			const { createScheduleOverlay } = module.exports;
			const items = [
			  { planned_start: "2026-01-01", planned_end: "2026-01-10" },
			  { planned_start: "2026-01-01", planned_end: "2026-01-10" },
			];
			const host = document.createElement("div");
			const overlay = createScheduleOverlay(host, {});
			overlay.load(items, []);
			const atStart = { date: host.querySelector(".bim-sched-date").textContent, states: overlay.coverage().states };
			overlay.scrubTo(1000);
			const atEnd = { date: host.querySelector(".bim-sched-date").textContent, states: overlay.coverage().states };
			console.log(JSON.stringify({ atStart, atEnd }));
			"""
		)
		self.assertEqual(result["atStart"]["date"], "2026-01-01")
		self.assertEqual(result["atStart"]["states"]["active"], 2)
		self.assertEqual(result["atEnd"]["date"], "2026-01-10")
		self.assertEqual(result["atEnd"]["states"]["active"], 2, "the last day should still be active")
