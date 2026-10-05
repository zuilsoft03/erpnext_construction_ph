# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH2-05 gate: the S-curve measures something.

The expected values below are **hand-computed literals**. They are deliberately
not derived from `earned_value` itself: a test whose expectations come from the
code under test agrees with it by construction and proves nothing.

The fixture is one BOQ item worth 100.00, planned across 2026-01-01 ->
2026-01-31, with actual progress 60%. At 2026-01-16 the planned window is
half elapsed, so:

    PV = 100.00 * (15 / 30) = 50.00
    EV = 100.00 * 60/100     = 60.00
    AC = 80.00
    SPI = 60 / 50 = 1.20
    CPI = 60 / 80 = 0.75

The lag/parity pair is the load-bearing part. A degenerate implementation that
read `Project.percent_complete` for both PV and EV would give SPI == 1.0 here and
would still satisfy a naive "SPI matches a fixture" assertion -- these two cases
together are what catch it.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import earned_value

ITEM_TOTAL = 100.00
PLAN_START = "2026-01-01"
PLAN_END = "2026-01-31"
ACTUAL_PERCENT = 60.0
AC = 80.00
MIDPOINT = "2026-01-16"  # 15 of 30 days elapsed


class TestEarnedValue(IntegrationTestCase):
	"""SPI/CPI arithmetic, plus the guards that make the numbers trustworthy."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
		cls.project, cls.erp_project = cls._project()
		# AC has to actually exist for CPI to be exercised. A fresh Project
		# accumulates nothing, so without this the divisor is zero, CPI is None,
		# and the test would pass while never dividing by anything.
		frappe.db.set_value("Project", cls.erp_project, "total_purchase_cost", AC)
		cls.boq = cls._boq()

	# ---- the three cases the gate names ------------------------------------

	def test_lag_case_reports_spi_above_one(self):
		"""Actual ahead of plan: PV 50.00, EV 60.00, SPI 1.20."""
		got = earned_value.curve(self.project, MIDPOINT)
		self.assertEqual(got["pv"], 50.00)
		self.assertEqual(got["ev"], 60.00)
		self.assertEqual(got["spi"], 1.20)

	def test_cpi_is_below_one_when_work_costs_more_than_it_earns(self):
		"""AC 80.00 against EV 60.00 gives CPI 0.75 -- the divisor is exercised."""
		got = earned_value.curve(self.project, MIDPOINT)
		self.assertEqual(got["ac"], AC)
		self.assertEqual(got["cpi"], 0.75)
		self.assertLess(got["cpi"], 1.0)

	def test_zero_pv_returns_none_and_does_not_raise(self):
		"""Before the first planned start there is no PV, so SPI is undefined.

		`None`, not infinity and not ZeroDivisionError. A date before any item
		starts is a normal question ("where was we in January?") and must answer.
		"""
		got = earned_value.curve(self.project, "2025-12-31")
		self.assertEqual(got["pv"], 0.00)
		self.assertIsNone(got["spi"], "SPI must be undefined, not infinite, when PV is 0")

	# ---- the negative cases the gate names --------------------------------

	def test_before_plan_reports_no_progress(self):
		"""A date before the window contributes nothing to PV or EV progress."""
		before = earned_value.planned_progress(self._item(), "2025-12-31")
		self.assertEqual(before, 0.0)

		after = earned_value.planned_progress(self._item(), "2026-03-01")
		self.assertEqual(after, 1.0, "a date past the window is fully planned")

	def test_zero_length_window_never_divides_by_zero(self):
		"""planned_end == planned_start is 0 before that day and 1 on and after."""
		item = self._item()
		item.planned_start = "2026-02-10"
		item.planned_end = "2026-02-10"
		self.assertEqual(earned_value.planned_progress(item, "2026-02-09"), 0.0)
		self.assertEqual(earned_value.planned_progress(item, "2026-02-10"), 1.0)
		self.assertEqual(earned_value.planned_progress(item, "2026-02-11"), 1.0)

	def test_unplanned_item_contributes_nothing_to_pv(self):
		"""An item with no planned window is 0 planned, not a guess."""
		item = self._item()
		item.planned_start = None
		item.planned_end = None
		self.assertEqual(earned_value.planned_progress(item, MIDPOINT), 0.0)

	def test_parity_case_gives_spi_exactly_one(self):
		"""Actual equal to plan gives SPI 1.0 -- the arithmetic is wired.

		Paired with the lag case this is what rules out the degenerate curve.
		"""
		self._set_actual_percent(ACTUAL_PERCENT)
		try:
			# On 2026-01-31 the window is complete: PV = 100.00. Set actual to
			# 100 so EV is also 100.00 and SPI is exactly 1.
			self._set_actual_percent(100.0)
			got = earned_value.curve(self.project, PLAN_END)
			self.assertEqual(got["pv"], 100.00)
			self.assertEqual(got["ev"], 100.00)
			self.assertEqual(got["spi"], 1.0)
		finally:
			self._set_actual_percent(ACTUAL_PERCENT)

	# ---- fixtures ----------------------------------------------------------


	def test_portal_payload_exposes_the_curve_to_a_permitted_client(self):
		"""A scoped client sees the S-curve in their project payload."""
		from erpnext_construction_ph.api import portal_data

		user = self._user("evm-permitted@portal.test", self.project)
		payload = portal_data.get_portal_payload(self.project, user=user)
		self.assertFalse(payload["denied"])
		self.assertIn("evm", payload)
		self.assertTrue(payload["evm"]["available"])
		self.assertIsNotNone(payload["evm"]["pv"])

	def test_portal_refusal_carries_no_curve(self):
		"""A refusal must not leak the curve, or any figure derived from it.

		The curve is computed after the `may_view` gate, so a client scoped
		elsewhere gets a payload with no `evm` key at all -- not an empty one,
		not a zeroed one. If this ever returns without asserting, the row is
		carrying a security test that tests nothing.
		"""
		from erpnext_construction_ph.api import portal_data

		# A real second project, so the outsider is genuinely scoped *elsewhere*
		# rather than unpermissioned: "no permissions" and "permissions for the
		# wrong project" are different failure modes and both must be refused.
		other, _ = self._project_named("EVM Other Project")
		outsider = self._user("evm-outsider@portal.test", other)
		payload = portal_data.get_portal_payload(self.project, user=outsider)
		self.assertTrue(payload["denied"])
		self.assertNotIn("evm", payload)
		for leak in ("pv", "ev", "ac", "spi", "cpi"):
			self.assertNotIn(leak, payload, f"the refusal leaked {leak}")

	def test_page_context_exposes_the_curve(self):
		"""`get_context` must copy `evm` onto the context the template renders.

		The payload returning a key is not enough: if `get_context` never assigns
		it, the template's `{% if not evm %}` branch is always taken and every
		client sees "no time-phased plan" for a project that has a full baseline.
		Nothing else in the suite renders the template, so this is the only thing
		that would catch it.
		"""
		project_view = self._page_view()

		user = self._user("evm-page@portal.test", self.project)
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")

		previous = dict(frappe.form_dict)
		frappe.form_dict["project"] = self.project
		try:
			context = project_view.get_context(frappe._dict())
		finally:
			frappe.form_dict.clear()
			frappe.form_dict.update(previous)

		self.assertIn("evm", context, "the page controller never assigned context.evm")
		self.assertTrue(
			context["evm"]["available"],
			"a project with a full baseline rendered as 'no plan on record'",
		)
		self.assertIsNotNone(context["evm"]["pv"])

	def test_template_only_reads_keys_the_context_assigns(self):
		"""Every top-level name the template reads must be assigned by get_context.

		A Jinja name that is never assigned does not raise -- it renders empty.
		That is the whole failure mode: a missing assignment is indistinguishable
		from legitimately empty data, which is why it has to be asserted.
		"""
		import os
		import re

		project_view = self._page_view()

		here = os.path.dirname(os.path.abspath(project_view.__file__))
		with open(os.path.join(here, "project-view.html"), encoding="utf-8") as handle:
			template = handle.read()

		# Top-level names only: first identifier of a {{ ... }} or {% if ... %}.
		names = set()
		for match in re.finditer(r"\{\{-?\s*([a-zA-Z_][a-zA-Z0-9_]*)", template):
			names.add(match.group(1))
		# Loop variables are local to their block, not context keys. `{% for b in
		# billings %}` binds b, and a naive scan would demand get_context assign
		# `b`, which is nonsense.
		for match in re.finditer(r"\{%-?\s*for\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+in\b", template):
			names.discard(match.group(1))

		assigned = set()
		with open(os.path.abspath(project_view.__file__), encoding="utf-8") as handle:
			source = handle.read()
		for match in re.finditer(r"context\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=", source):
			assigned.add(match.group(1))

		# `for`/`if` loop variables and Jinja builtins are not context keys.
		ignored = {"for", "if", "else", "endif", "endfor", "block", "endblock", "extends", "set", "length"}
		missing = sorted(n for n in names - assigned - ignored)
		self.assertEqual(
			missing,
			[],
			f"the template reads {missing} but get_context never assigns them; "
			"Jinja renders an unassigned name as empty, which is indistinguishable "
			"from real 'no data'",
		)
		"""A refusal must not leak the curve, or any figure derived from it.

		The curve is computed after the `may_view` gate, so a client scoped
		elsewhere gets a payload with no `evm` key at all -- not an empty one,
		not a zeroed one.
		"""
		from erpnext_construction_ph.api import portal_data

		# A real second project, so the outsider is genuinely scoped *elsewhere*
		# rather than unpermissioned: "no permissions" and "permissions for the
		# wrong project" are different failure modes and both must be refused.
		other, _ = self._project_named("EVM Other Project")
		outsider = self._user("evm-outsider@portal.test", other)
		payload = portal_data.get_portal_payload(self.project, user=outsider)
		self.assertTrue(payload["denied"])
		self.assertNotIn("evm", payload)
		for leak in ("pv", "ev", "ac", "spi", "cpi"):
			self.assertNotIn(leak, payload, f"the refusal leaked {leak}")

	@staticmethod
	def _page_view():
		"""Load the page controller the way production does.

		**Through `frappe.get_module`, using the exact name Frappe derives.**
		`set_pymodule` builds `self.app + "." + pymodule_path` and only sets
		`pymodule_name` if that path exists, so a controller named
		`project-view.py` left `pymodule_name` as None, `get_context` never ran,
		and the page rendered with an empty context and 500'd on its first format
		spec. The page had never rendered for anyone.

		This used to hand-derive a file path and load it with
		`spec_from_file_location`, on the reasoning -- recorded in the old
		docstring -- that "the `www/` tree is not an importable package". That was
		only ever true because of the **hyphen**: `project-view` is not a valid
		Python identifier. With the underscore Frappe requires, a dotted import
		works, and going through `get_module` means this test now exercises the
		same resolution production does -- so the next rename fails here for the
		right reason instead of silently diverging.
		"""
		return frappe.get_module("erpnext_construction_ph.www.portal.project_view")

	@staticmethod
	def _user(email, construction_project):
		"""A User scoped to exactly one Construction Project, via User Permission."""
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
					"new_password": frappe.generate_hash(length=12),
				}
			).insert(ignore_permissions=True)
		erp = frappe.db.get_value("Construction Project", construction_project, "erpnext_project")
		if not frappe.db.exists("User Permission", {"user": email, "for_value": erp}):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Project",
					"for_value": erp,
					"apply_to_all_doctypes": 1,
				}
			).insert(ignore_permissions=True)
		return email

	# ---- fixtures ----------------------------------------------------------

	def _item(self):
		return frappe._dict(
			total=ITEM_TOTAL,
			planned_start=PLAN_START,
			planned_end=PLAN_END,
			actual_percent_complete=ACTUAL_PERCENT,
		)

	def _set_actual_percent(self, value):
		frappe.db.set_value(
			"Construction BOQ Item",
			self.boq.boq_items[0].name,
			"actual_percent_complete",
			value,
		)

	@classmethod
	def _project(cls):
		return cls._project_named("EVM Project")

	@classmethod
	def _project_named(cls, name):
		erp_name = f"{name} ERP"
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = f"{name} Customer"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "Philippines",
				}
			).insert(ignore_permissions=True)
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		erp_project = hits[0] if hits else frappe.get_doc(
			{"doctype": "Project", "project_name": erp_name, "company": company}
		).insert(ignore_permissions=True).name
		if not frappe.db.exists("Construction Project", name):
			frappe.get_doc(
				{
					"doctype": "Construction Project",
					"project_name": name,
					"erpnext_project": erp_project,
					"company": company,
					"client": customer,
					"contract_date": "2026-01-01",
					"contract_amount": 1_000_000.00,
				}
			).insert(ignore_permissions=True)
		return name, erp_project

	@classmethod
	def _boq(cls):
		"""One priced item for the EVM project, created once and then reused.

		Idempotent on purpose. Deriving the BOQ number from a row count and
		inserting unconditionally left an extra 100.00 item behind on every run,
		so the suite passed once and failed forever after -- which reads like a
		regression in the arithmetic when it is really a leaking fixture.
		"""
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		uom = frappe.get_all("UOM", limit=1, pluck="name")[0]

		# Clear first, then create: exactly one priced item, whatever history the
		# site carries. Reusing an existing BOQ looked idempotent but still
		# summed every stale row, so the curve drifted upward on each run.
		for stale in frappe.get_all(
			"Construction BOQ", filters={"project": "EVM Project"}, pluck="name"
		):
			frappe.delete_doc("Construction BOQ", stale, force=True, ignore_permissions=True)

		seq = len(frappe.get_all("Construction BOQ", pluck="name")) + 1
		return frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": f"EVM-BOQ-{seq:04d}",
				"project": "EVM Project",
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": "1.01",
						"item_code": "EVM-001",
						"description": "EVM gate fixture item",
						"unit": uom,
						"quantity": 1.0,
						"direct_cost": ITEM_TOTAL,
						"ocm": 0.0,
						"profit": 0.0,
						"planned_start": PLAN_START,
						"planned_end": PLAN_END,
						"actual_percent_complete": ACTUAL_PERCENT,
					}
				],
			}
		).insert(ignore_permissions=True)
