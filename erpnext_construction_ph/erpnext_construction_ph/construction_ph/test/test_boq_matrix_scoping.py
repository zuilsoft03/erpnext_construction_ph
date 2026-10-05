# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""A1 -- `boq_matrix.list_boqs` carried no project scoping at all.

Measured before the fix: an unentitled portal client called
`erpnext_construction_ph.api.boq_matrix.list_boqs` and received BOTH BOQs on
the site, including one belonging to a project they cannot see. `project` was a
filter, not a permission, and nothing consulted the caller's scope -- so the
BOQ Worksheet's own project control was decorative, and any page-level gate in
front of this method would have changed nothing.

The fix filters on `portal_data.permitted_projects`. These assert the OUTCOME
on BOTH paths the endpoint offers:

- with no project named, and
- with a FOREIGN project named by hand.

The second is a separate implementation, not a variation of the first. "Filter
by what the caller may see" and "return nothing at all for a project they may
not see" are different code, and only the second is safe -- a caller who can
name a project must not thereby reach it.

`test_a_desk_role_still_gets_the_worksheet_list` is here to catch the opposite
mistake: scoping this through `entitled_projects` instead of
`permitted_projects` would close the hole and simultaneously hand every
estimator an empty dropdown, because `entitled_projects` demands a client-facing
role that site staff do not hold.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import boq_matrix, portal_data

CLIENT = "boqscope-client@portal.test"
DESK = "boqscope-desk@portal.test"
STRANGER = "boqscope-stranger@portal.test"

CLIENT_ROLE = "Construction Client"
DESK_ROLE = "Construction Project Manager"


class TestListBoqsScoping(IntegrationTestCase):
	"""A BOQ list is scoped to the caller's projects, on both call paths."""

	# ---- the hole: cross-project enumeration -------------------------------

	def test_list_boqs_is_not_a_cross_project_enumeration(self):
		"""**The hole itself.** No project named, called as a client scoped to
		one project. Every row must belong to THAT project.

		A BOQ exists on each project in `setUp`, and that is load-bearing: with
		BOQs only on the entitled project, this assertion also passes against the
		unfixed code, because there is nothing to leak. A test that cannot fail
		proves nothing.
		"""
		self._as(CLIENT)
		try:
			rows = boq_matrix.list_boqs()
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(
			self.other_boq_name, "precondition: a BOQ exists on the other project"
		)
		for row in rows:
			self.assertEqual(
				row.get("project"), self.project,
				f"a BOQ on project {row.get('project')!r} leaked to a client "
				"scoped elsewhere",
			)
		self.assertNotIn(
			self.other_boq_name, [r.get("name") for r in rows],
			"the other project's BOQ was returned to a client who cannot see it",
		)

	def test_list_boqs_refuses_a_foreign_project_named_explicitly(self):
		"""Naming a project must not be a way to reach it.

		A different code path from the row above, and the one an attacker would
		actually try: pass the other project's name and ask for it directly.
		"""
		self._as(CLIENT)
		try:
			rows = boq_matrix.list_boqs(project=self.other_project)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(
			rows, [],
			"naming a project the caller may not view returned its BOQs",
		)

	def test_a_client_with_no_project_scope_gets_nothing(self):
		"""Client role, no User Permission: no scope, so no rows.

		`may_view`-shaped authorisation has to fail CLOSED on an empty
		permission set, or the absence of scoping reads as "unrestricted" -- which
		is exactly the state the unfixed code was permanently in.
		"""
		self.assertEqual(portal_data.permitted_projects(STRANGER), [])
		self._as(STRANGER)
		try:
			self.assertEqual(boq_matrix.list_boqs(), [])
			self.assertEqual(boq_matrix.list_boqs(project=self.project), [])
		finally:
			frappe.set_user("Administrator")

	# ---- the feature still works -------------------------------------------

	def test_an_entitled_client_still_sees_their_own_boq(self):
		"""The refusals above are the gate working, not an empty site."""
		self._as(CLIENT)
		try:
			rows = boq_matrix.list_boqs(project=self.project)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(rows, "a client scoped to a project with a BOQ saw no BOQ")
		self.assertEqual(
			[r["name"] for r in rows], [self.shown_boq_name],
			"the entitled client's own BOQ was not the one returned",
		)

	def test_an_entitled_client_sees_their_boq_without_naming_a_project(self):
		"""The unfiltered call must still return their own rows.

		Guards against "fix" by returning `[]` from the no-project branch
		entirely, which would satisfy every negative test above while leaving the
		worksheet page with nothing to choose from.
		"""
		self._as(CLIENT)
		try:
			rows = boq_matrix.list_boqs()
		finally:
			frappe.set_user("Administrator")
		self.assertIn(self.shown_boq_name, [r.get("name") for r in rows])

	def test_a_desk_role_still_gets_the_worksheet_list(self):
		"""`list_boqs` is a DESK surface, so a Desk role must not be emptied.

		`DESK` holds a `READ_ROLES` role, so `may_view` treats it as unrestricted
		and it legitimately sees every project. Scoping this through
		`entitled_projects` -- which requires a client-facing role -- would return
		`[]` here and silently break the BOQ Worksheet for every estimator, while
		passing all three negative tests above.
		"""
		self._as(DESK)
		try:
			rows = boq_matrix.list_boqs()
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(rows, "a Desk role got an empty BOQ list; the worksheet is broken")
		projects = {r.get("project") for r in rows}
		self.assertIn(self.project, projects)
		self.assertIn(self.other_project, projects)

	def test_administrator_is_unaffected(self):
		rows = boq_matrix.list_boqs()
		self.assertIn(self.shown_boq_name, [r.get("name") for r in rows])
		self.assertIn(self.other_boq_name, [r.get("name") for r in rows])

	def test_status_filter_still_applies(self):
		"""The pre-existing guarantee survives the scoping change.

		Boards that are not Draft/Approved cannot take pasted rows, so listing
		them is a correctness bug the original docstring already called out.
		"""
		rows = boq_matrix.list_boqs()
		for row in rows:
			self.assertIn(
				row.get("status"), {"Draft", "Approved"},
				f"list_boqs returned a {row.get('status')!r} BOQ",
			)

	# ---- plumbing ----------------------------------------------------------

	@staticmethod
	def _as(user):
		frappe.set_user(user)

	def setUp(self):
		# A BOQ on EACH project. See the cross-project test's docstring: with only
		# one, the leak assertion passes against the unfixed code.
		self.shown_boq_name = self._boq("BOQ Scope Shown", "A")
		self.other_boq_name = self._boq("BOQ Scope Other", "B")
		self._user(CLIENT, self.project, (CLIENT_ROLE,))
		self._user(STRANGER, None, (CLIENT_ROLE,))
		self._user(DESK, None, (DESK_ROLE,))

	@property
	def project(self):
		return self._project("BOQ Scope Shown")

	@property
	def other_project(self):
		return self._project("BOQ Scope Other")

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

	@classmethod
	def _boq(cls, label, suffix):
		hits = frappe.get_all("Construction BOQ", filters={"boq_number": f"BOQSCOPE-{suffix}"}, pluck="name")
		if hits:
			return hits[0]
		return frappe.get_doc(
			{
				"doctype": "Construction BOQ",
				"boq_number": f"BOQSCOPE-{suffix}",
				"project": cls._project(label),
				"boq_date": "2026-01-01",
				"boq_items": [
					{
						"pay_item_number": "1",
						# `item_code` is Data on the BOQ line, so this validates
						# without an Item existing. Nothing here issues stock.
						"item_code": f"BOQSCOPE-{suffix}-01",
						# Mandatory on the line. Omitting it fails the WHOLE
						# fixture inside setUp, which surfaces as eight broken
						# tests rather than one broken field.
						"unit": frappe.get_all("UOM", limit=1, pluck="name")[0],
						"description": f"scope probe line {suffix}",
						"quantity": 10.0,
						"direct_cost": 1.0,
					}
				],
			}
		).insert(ignore_permissions=True).name
	@staticmethod
	def _user(email, construction_project, roles):
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
		held = frappe.get_roles(email)
		missing = [r for r in roles if r not in held]
		if missing:
			user = frappe.get_doc("User", email)
			for role in missing:
				user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
		# No User Permission at all when `construction_project` is None: that is
		# how STRANGER and DESK are built, and it is the point of each.
		if not construction_project:
			return
		erp = frappe.db.get_value("Construction Project", construction_project, "erpnext_project")
		if frappe.db.exists("User Permission", {"user": email, "for_value": erp}):
			return
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "Project",
				"for_value": erp,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
