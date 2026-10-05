# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""GATES.md PH2-04 — client portal row-level scoping.

The gate's HTTP expectations (200 / 302 / 403) are exercised by a real curl in
`ops/tmp-ph2-04/check.txt`. What this test covers is the part a curl cannot
judge: that a client scoped to project A is **refused** project B, and that the
refusal does not leak B's billings.

D-004 makes the client portal an authorization surface, so an IDOR here is a
class A defect even though the row is otherwise class B.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import portal_data

CLIENT_ROLE = "Construction Client"


class TestPortalScoping(IntegrationTestCase):
	"""A client sees only the projects they are assigned to."""

	def _role(self):
		if not frappe.db.exists("Role", CLIENT_ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": CLIENT_ROLE}).insert(
				ignore_permissions=True
			)
		return CLIENT_ROLE

	def _project(self, name, customer, amount=10_000_000.00):
		"""Create (or find) a Construction Project and its native ERPNext Project.

		Returns (construction_project_name, erp_project_name). The two names
		differ -- the native Project autonames as PROJ-000N -- and User Permission
		is granted against the native one.
		"""
		company = frappe.get_all("Company", limit=1, pluck="name")[0]

		erp_project = self._erp_project(name)
		if not erp_project:
			erp_project = frappe.get_doc(
				{
					"doctype": "Project",
					"project_name": f"{name} ERP",
					"company": company,
					"status": "Open",
				}
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
					"contract_amount": amount,
				}
			).insert(ignore_permissions=True)

		return name, erp_project


	def _erp_project(self, name):
		hits = frappe.get_all("Project", filters={"project_name": f"{name} ERP"}, pluck="name")
		return hits[0] if hits else None

	def _customer(self, name):
		if frappe.db.exists("Customer", name):
			return name
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Philippines",
			}
		).insert(ignore_permissions=True).name

	def _client(self, email, erp_project):
		"""A Portal User equivalent: a User with the client role and a User
		Permission on exactly one *native* Project."""
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
					"roles": [{"role": self._role()}],
				}
			).insert(ignore_permissions=True)
		# User Permission.for_value is the native Project; the caller supplies it
		# directly rather than re-deriving it.
		if not frappe.db.exists(
			"User Permission", {"user": email, "for_value": erp_project}
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Project",
					"for_value": erp_project,
					"apply_to_all_doctypes": 1,
				}
			).insert(ignore_permissions=True)
		return email

	def test_portal_scoping(self):
		self._role()
		customer = self._customer("Portal Test Client")
		project_a, erp_a = self._project("Portal Project A", customer)
		project_b, erp_b = self._project("Portal Project B", customer)

		# A submitted billing on B, so a leak would be visible in the payload.
		billing = frappe.get_doc(
			{
				"doctype": "Construction Progress Billing",
				"billing_number": "PORTAL-B-001",
				# billings link the native Project, not the Construction Project
				"project": erp_b,
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"client": customer,
				"posting_date": "2026-03-01",
				"billing_period_start": "2026-01-01",
				"billing_period_end": "2026-03-31",
				"contract_amount": 10_000_000.00,
				"period_accomplishment_percent": 10,
				"cumulative_accomplishment": 1_000_000.00,
			}
		)
		# The portal ledger shows *submitted* billings, so the fixture has to be
		# submitted too -- an inserted draft is correctly invisible to the client.
		billing.insert(ignore_permissions=True)
		billing.submit()

		client_a = self._client("client-a@portal.test", erp_a)
		client_b = self._client("client-b@portal.test", erp_b)

		# ---- EXPECT: the assigned project is served with its payload.
		ok = portal_data.get_portal_payload(project_a, user=client_a)
		self.assertFalse(ok["denied"], "a client must see the project they are assigned to")
		self.assertEqual(ok["project"], project_a)
		self.assertEqual(ok["available_projects"], [project_a])
		self.assertEqual(flt(ok["contract_amount"]), 10_000_000.00)

		# ---- NEGATIVE (the IDOR this row exists to prevent): the other project is
		# refused, and the refusal carries none of its data.
		denied = portal_data.get_portal_payload(project_b, user=client_a)
		self.assertTrue(denied["denied"])
		self.assertEqual(denied["reason"], "not_permitted")
		for leak in ("billings", "totals", "contract_amount", "project"):
			self.assertNotIn(leak, denied, f"the refusal leaked {leak}")

		# A project that does not exist is refused identically, so the portal
		# cannot be used to enumerate project names.
		missing = portal_data.get_portal_payload("Portal Project DOES NOT EXIST", user=client_a)
		self.assertTrue(missing["denied"])
		self.assertEqual(missing, denied, "missing and forbidden must be indistinguishable")

		# ---- NEGATIVE: an unauthenticated caller is refused.
		self.assertTrue(portal_data.get_portal_payload(project_a, user="Guest")["denied"])
		self.assertEqual(
			portal_data.get_portal_payload(project_a, user="Guest")["reason"],
			"login_required",
		)

		# ---- EXPECT: the other client sees only B.
		ok_b = portal_data.get_portal_payload(project_b, user=client_b)
		self.assertFalse(ok_b["denied"])
		self.assertEqual(ok_b["available_projects"], [project_b])
		self.assertEqual([b["billing_number"] for b in ok_b["billings"]], ["PORTAL-B-001"])

		# ---- NEGATIVE: a user with no User Permission sees nothing at all.
		stranger = "stranger@portal.test"
		if not frappe.db.exists("User", stranger):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": stranger,
					"first_name": "stranger",
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
					"new_password": frappe.generate_hash(length=12),
					"roles": [{"role": self._role()}],
				}
			).insert(ignore_permissions=True)
		self.assertEqual(portal_data.permitted_projects(stranger), [])
		self.assertTrue(portal_data.get_portal_payload(project_a, user=stranger)["denied"])
