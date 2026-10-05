# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""A2 -- `raise_requisition` took `project` only to resolve the company.

Measured before the fix: there was no scope check anywhere in the function. Any
user holding Material Request create rights could raise a requisition against
ANY Construction Project by naming it, and the Material Request that landed
carried that project's company -- a purchase commitment in someone else's
project, from a caller with no claim on it. Same disclosure class as A1: the
argument was a selector, not a permission.

The fix checks `portal_data.may_view` before anything is written.

`may_view` and NOT `entitled_projects`, and these tests are what make that the
checked choice rather than an accident. A requisition is raised by site staff,
so `entitled_projects` -- which demands a client-facing role -- would refuse
every estimator on the site. The role used below is therefore probed for create
rights and asserted to be outside `portal_data.READ_ROLES`; a `READ_ROLES`
member is `_unrestricted` and would see every project, which is precisely what
the negative test denies.

Ordering is asserted, not assumed: the refusal has to land BEFORE the write, so
`test_the_refusal_writes_nothing` counts Material Requests across the call. A
check placed after the insert would raise on the right project and still leave
the document behind.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import material_requisition as mr
from erpnext_construction_ph.api import portal_data

SITE_USER = "mrscope-requester@portal.test"
OTHER_USER = "mrscope-outsider@portal.test"

TAKEOFF_QTY = 100.0
RATE = 1000.0

# Roles that can read an Item. Probed in `_grant` rather than assumed, for the
# same reason `_create_role` probes: what a role holds in this ERPNext is a
# fact about the site, and a hard-coded answer would be a guess.
ITEM_READ_CANDIDATES = (
	"Construction Estimator",
	"Stock User",
	"Stock Manager",
	"Purchase User",
	"Purchase Manager",
)


class TestRaiseRequisitionScoping(IntegrationTestCase):
	"""A requisition may only be raised against a project the caller may view."""

	# ---- the hole ----------------------------------------------------------

	def test_a_caller_cannot_raise_against_a_project_it_is_not_assigned_to(self):
		"""`SITE_USER` is scoped to P and names Q. Refused.

		Against the unfixed code this SUCCEEDS and returns a Material Request
		name, which is the defect: the document lands in Q's company.
		"""
		boq_item, other_project = self._boq_item("Q")
		self._as(SITE_USER)
		try:
			with self.assertRaises(mr.RequisitionError) as caught:
				mr.raise_requisition(other_project, boq_item, qty=10.0)
		finally:
			frappe.set_user("Administrator")
		self.assertIn("not assigned to project", str(caught.exception))

	def test_the_refusal_writes_nothing(self):
		"""**Ordering.** The check must precede the insert, not follow it.

		A guard that raised after the document was created would satisfy the test
		above -- the same exception, the same message -- while still leaving an
		unwanted purchase commitment in the other project's books, which is the
		actual harm. So this counts, rather than trusting the exception.
		"""
		boq_item, other_project = self._boq_item("Q")
		before = self._mr_count(other_project)
		self._as(SITE_USER)
		try:
			with self.assertRaises(mr.RequisitionError):
				mr.raise_requisition(other_project, boq_item, qty=10.0)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(
			self._mr_count(other_project), before,
			"a Material Request was created for a project the caller may not view",
		)

	def test_a_refusal_does_not_disclose_the_other_projects_boq_lines(self):
		"""The refusal must not echo the thing the caller was not entitled to.

		`raise_requisition` used to read the BOQ line to build the request, so a
		guard placed after that read would confirm the line exists. The scope
		check runs ahead of it, so a foreign BOQ item is refused on the PROJECT
		alone and the caller learns nothing about the line.
		"""
		boq_item, other_project = self._boq_item("Q")
		self._as(SITE_USER)
		try:
			with self.assertRaises(mr.RequisitionError) as caught:
				mr.raise_requisition(other_project, boq_item, qty=10.0)
		finally:
			frappe.set_user("Administrator")
		self.assertNotIn(boq_item, str(caught.exception))
		self.assertNotIn("takeoff", str(caught.exception))

	def test_a_caller_with_no_project_scope_cannot_raise_at_all(self):
		"""`OTHER_USER` holds create rights and no User Permission: no scope."""
		boq_item, project = self._boq_item("Q")
		before = self._mr_count(project)
		self._as(OTHER_USER)
		try:
			with self.assertRaises(mr.RequisitionError):
				mr.raise_requisition(project, boq_item, qty=10.0)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self._mr_count(project), before)

	# ---- the feature still works -------------------------------------------

	def test_a_caller_can_raise_against_the_project_it_is_assigned_to(self):
		"""The refusal above is the gate working, not a broken write path."""
		boq_item, project = self._boq_item("P")
		self._as(SITE_USER)
		try:
			name = mr.raise_requisition(project, boq_item, qty=25.0)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(name)
		request = frappe.get_doc("Material Request", name)
		self.assertEqual(len(request.items), 1)
		self.assertEqual(request.items[0].custom_boq_item, boq_item)
		self.assertEqual(
			request.company,
			frappe.db.get_value("Construction Project", project, "company"),
		)

	def test_administrator_is_unaffected(self):
		"""`_unrestricted` short-circuits for Administrator, as in every other
		module in this app. Pinned because a `may_view` call with the arguments
		in the wrong order would break exactly this."""
		boq_item, other_project = self._boq_item("Q")
		name = mr.raise_requisition(other_project, boq_item, qty=10.0)
		self.assertTrue(name)

	# ---- the cap is untouched ---------------------------------------------

	def test_the_takeoff_cap_still_fires_for_an_entitled_caller(self):
		"""The scope check must not have displaced the cap check.

		Both raise `RequisitionError`, so an ordering slip that swallowed the cap
		would look like a working refusal. Asserting on the MESSAGE separates
		them: the cap says "cannot exceed", the gate says "not assigned to".
		"""
		boq_item, project = self._boq_item("P")
		self._as(SITE_USER)
		try:
			with self.assertRaises(mr.RequisitionError) as caught:
				mr.raise_requisition(project, boq_item, qty=TAKEOFF_QTY + 1)
		finally:
			frappe.set_user("Administrator")
		self.assertIn("cannot exceed", str(caught.exception))

	# ---- plumbing ----------------------------------------------------------

	@staticmethod
	def _as(user):
		frappe.set_user(user)

	@staticmethod
	def _mr_count(project):
		company = frappe.db.get_value("Construction Project", project, "company")
		return len(frappe.get_all("Material Request", filters={"company": company}, ignore_permissions=True))

	def setUp(self):
		self.project = self._project("MR Scope Shown")
		self.other_project = self._project("MR Scope Other")
		self._user(SITE_USER, self.project)
		self._user(OTHER_USER, None)

	@staticmethod
	def _project(name):
		existing = frappe.get_all("Construction Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		customer = name + " Customer"
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
		hits = frappe.get_all("Project", filters={"project_name": name + " ERP"}, pluck="name")
		erp = hits[0] if hits else frappe.get_doc(
			{"doctype": "Project", "project_name": name + " ERP", "company": company}
		).insert(ignore_permissions=True).name
		return frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": name,
				"erpnext_project": erp,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 1000000.00,
			}
		).insert(ignore_permissions=True).name

	@staticmethod
	def _item(seq):
		"""A real Item: the requisition references `item_code` as a Link, so a
		made-up code would fail validation and the positive test could not
		distinguish a permission refusal from a broken fixture."""
		code = f"MRSCOPE-MAT-{seq:03d}"
		if not frappe.db.exists("Item", code):
			groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
			group = next((g for g in ("All Item Groups", "Raw Materials") if g in groups), groups[0])
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": f"MR scope material {seq}",
					"item_group": group,
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"valuation_method": "FIFO",
					"standard_rate": RATE,
				}
			).insert(ignore_permissions=True)
		return code

	def _boq_item(self, which):
		"""A BOQ line with an uncommitted takeoff, on P or Q.

		Fresh per call so the cumulative-headroom arithmetic of a sibling test
		cannot leak into this one.
		"""
		project = {"P": self.project, "Q": self.other_project}[which]
		seq = len(frappe.get_all("Construction BOQ", filters={"project": project}, pluck="name")) + 1
		boq = frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": f"MRSCOPE-{which}-{seq:04d}",
				"project": project,
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": f"{seq}.01",
						"item_code": self._item(seq),
						"description": f"MR scope takeoff {which}",
						"unit": frappe.get_all("UOM", limit=1, pluck="name")[0],
						"quantity": TAKEOFF_QTY,
						"direct_cost": 10.0,
					}
				],
			}
		).insert(ignore_permissions=True)
		return boq.boq_items[0].name, project

	def _user(self, email, construction_project):
		"""A user who can create a Material Request and is project-scoped.

		The role is PROBED, not assumed. The positive test really writes, and a
		fixture that guessed a role without create rights would fail there for a
		reason unrelated to the fix. Candidates exclude `portal_data.READ_ROLES`:
		a member of that is `_unrestricted` and may view every project, which is
		exactly what the negative test denies, so a `READ_ROLES` fixture would
		make this module pass against the unfixed code.
		"""
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
		role = self._create_role()
		if role not in frappe.get_roles(email):
			user = frappe.get_doc("User", email)
			user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
		self.assertNotIn(
			role, portal_data.READ_ROLES,
			f"fixture role {role!r} is unrestricted and cannot prove scoping",
		)
		# ERPNext's `get_item_details` calls `item.check_permission()` while a
		# Material Request is validated, so a caller who may raise a requisition
		# must also be able to READ the Item on the BOQ line -- or the write dies
		# in ERPNext's controller with a PermissionError that has nothing to do
		# with scoping. Measured on the gate site: `Construction Site Manager`
		# holds Material Request but NOT Item (its only Custom DocPerm rows are
		# Material Request, Stock Entry, Task, Timesheet, Issue, Quality
		# Inspection and Budget), while `Construction Estimator` holds Item and
		# nothing here. The user is therefore given both, which is a realistic
		# site-crew shape and leaves the user OUTSIDE `READ_ROLES` -- so still
		# genuinely scoped, which is what the negative test depends on.
		self._grant(email, "Item", "read", ITEM_READ_CANDIDATES)
		# The User Permission is what makes `construction_project` the ONE project
		# this user may reach. Without it the user is unscoped, `may_view` refuses
		# BOTH projects, and every negative test in this module passes for the
		# wrong reason -- so it is the load-bearing half of the fixture.
		if not construction_project:
			return
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

	def _grant(self, email, doctype, permtype, candidates):
		"""Append roles until `email` holds `permtype` on `doctype`."""
		if frappe.has_permission(doctype, permtype, user=email):
			return
		for role in candidates:
			if role in portal_data.READ_ROLES or not frappe.db.exists("Role", role):
				continue
			if role not in frappe.get_roles(email):
				user = frappe.get_doc("User", email)
				user.append("roles", {"role": role})
				user.save(ignore_permissions=True)
				frappe.clear_cache()
			if frappe.has_permission(doctype, permtype, user=email):
				self.assertNotIn(
					role, portal_data.READ_ROLES,
					f"role {role!r} granted {permtype} on {doctype} but is "
					"unrestricted, so it cannot prove scoping",
				)
				return
		self.fail(
			f"no candidate role grants {permtype} on {doctype} for {email}"
		)

	def _create_role(self):
		"""First candidate role outside `READ_ROLES` that grants create.

		Resolution happens against a real user rather than the role table, because
		what matters is whether the ROLE produces the permission in this
		ERPNext's permission set -- which varies by version, and a hard-coded
		answer would be a guess dressed as a fact.
		"""
		probe = "mrscope-probe@portal.test"
		if not frappe.db.exists("User", probe):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": probe,
					"first_name": "mrscope",
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
					"new_password": frappe.generate_hash(length=12),
				}
			).insert(ignore_permissions=True)
		# The app's OWN roles lead the list, and they are the semantically right
		# ones: a `Custom DocPerm` row grants `Construction Site Manager` create
		# on Material Request, Stock Entry, Task and Timesheet -- the site
		# requisitioning role this module is written for.
		#
		# Measured on the gate site: NO ERPNext stock role carries create on
		# Material Request in this build, and `Material Request User` /
		# `Material Request Manager` do not exist here at all. A candidate list
		# built from the obvious ERPNext names therefore finds nothing and the
		# whole module fails in setUp for a reason that has nothing to do with
		# the scoping under test. The tail is kept in case an ERPNext upgrade
		# does grant it.
		for role in (
			"Construction Site Manager",
			"Construction Buyer",
			"Stock User",
			"Purchase User",
			"Stock Manager",
			"Purchase Manager",
		):
			if role in portal_data.READ_ROLES or not frappe.db.exists("Role", role):
				continue
			if role not in frappe.get_roles(probe):
				user = frappe.get_doc("User", probe)
				user.append("roles", {"role": role})
				user.save(ignore_permissions=True)
				# Not `clear_cache(user=...)`: a `Custom DocPerm` row is a
				# doctype cache, so a user-scoped clear leaves the very grant
				# under test still invisible and the probe reports no role.
				frappe.clear_cache()
			if frappe.has_permission("Material Request", "create", user=probe):
				return role
		self.fail(
			"no role outside portal_data.READ_ROLES grants create on Material "
			"Request; the fixture cannot be built"
		)
