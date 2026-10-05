# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH4-01 gate: the field write path, and the scoping of its read path.

Scope, per the gate partition: the service-worker and offline-sync half is a
browser assertion and is **not** claimed here. What this covers is the
server-side contract — a pour creates its log and the Material Issue that backs
it, the link holds both ways, an unlinked pour is refused, and the two read
endpoints do not hand out projects the caller cannot see.

The scoping tests exist because the first version of both read endpoints did
exactly that: `open_tasks` accepted any project name and `recent_logs` with no
filter returned every project's site activity. Any authenticated user could
enumerate the portfolio.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import site_log


class TestSiteLogApi(IntegrationTestCase):
	"""Field write path, link integrity, and read-path scoping."""

	# ---- the write path ----------------------------------------------------

	def test_a_pour_creates_its_log_and_a_matching_material_issue(self):
		"""One call, two records, linked both ways."""
		project = self._project()
		task = self._task(project)
		item = self._item("Ready-mix concrete PH401")

		name = site_log.create_site_log(
			project=project,
			log_type="Concrete Pour",
			pour_item=item,
			pour_volume=12.5,
			pour_task=task.name,
			location="Level 3 Grid C-F",
		)

		log = frappe.get_doc("Construction Site Log", name)
		self.assertEqual(log.log_type, "Concrete Pour")
		self.assertEqual(log.pour_task, task.name)
		self.assertTrue(log.stock_entry, "the pour created no Stock Entry")

		entry = frappe.get_doc("Stock Entry", log.stock_entry)
		self.assertEqual(entry.purpose, "Material Issue")
		self.assertEqual(entry.stock_entry_type, "Material Issue")
		self.assertIn(item, [row.item_code for row in entry.items])
		self.assertIn(name, (entry.remarks or ""))

	def test_an_unlinked_pour_is_refused_by_the_server(self):
		"""A pour with a volume and no WBS task never reaches the database.

		The refusal must reach the caller verbatim. Swallowing it in the page
		controller would leave the crew believing an unreconcilable pour was
		logged, which is the exact failure the guard prevents.
		"""
		project = self._project()
		with self.assertRaises(frappe.ValidationError) as caught:
			site_log.create_site_log(
				project=project,
				log_type="Concrete Pour",
				pour_item=self._item("Ready-mix concrete PH401"),
				pour_volume=5.0,
			)
		self.assertIn("WBS Task", str(caught.exception))

	def test_a_non_pour_creates_no_stock_entry(self):
		"""A weather delay is a log, not a material movement."""
		project = self._project()
		name = site_log.create_site_log(
			project=project,
			log_type="Weather Delay",
			delay_hours=4.0,
			delay_reason="Heavy monsoon",
		)
		log = frappe.get_doc("Construction Site Log", name)
		self.assertFalse(log.stock_entry)
		self.assertEqual(log.delay_hours, 4.0)

	def test_an_unknown_project_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			site_log.create_site_log(project="No Such Project", log_type="General")

	def test_a_user_with_the_write_role_cannot_post_for_a_project_they_are_not_scoped_to(self):
		"""The load-bearing case, and the one that proves the check is not redundant.

		`create_site_log` creates a Stock Entry, which moves stock and posts to
		the GL. A user with *no* roles is refused by Frappe's own permission model
		anyway, so testing one proves nothing about this module's check -- an
		earlier version of this test did exactly that and stayed green with the
		guard deleted.

		So the user here is given the real write role (`Construction Site
		Manager`). Frappe's model is now satisfied -- the insert would succeed --
		and the only thing that can refuse is the project-scope check. That makes
		the check load-bearing rather than a redundant second opinion.
		"""
		project = self._project()
		other = self._other_project()
		task = self._task(project)
		self._user_scoped_to("ph4-client@portal.test", other, roles=("Construction Site Manager",))

		previous = frappe.session.user
		frappe.set_user("ph4-client@portal.test")
		try:
			with self.assertRaises(frappe.PermissionError) as caught:
				site_log.create_site_log(
					project=project,
					log_type="Concrete Pour",
					pour_item=self._item("Ready-mix concrete PH401"),
					pour_volume=2.0,
					pour_task=task.name,
				)
			# Assert OUR message, not merely that something refused.
			self.assertIn("do not have access", str(caught.exception))
		finally:
			frappe.set_user(previous)

		self.assertEqual(
			frappe.db.count("Stock Entry", {"docstatus": 0, "remarks": ["like", "%PH401%"]}),
			0,
			"a refused write left a Stock Entry behind",
		)

	def test_a_user_with_the_write_role_and_no_project_at_all_is_refused(self):
		"""The second distinct refusal: a role with no project scope whatsoever.

		Companion to the wrong-project case. One refusal could be satisfied by a
		check that only notices a *mismatch*; a check that refuses a role-holder
		with no projects at all is refusing the absence of scope, which is a
		separate property. Both are needed before the control can be called
		load-bearing rather than incidentally correct.
		"""
		project = self._project()
		task = self._task(project)
		self._user_scoped_to("ph4-noscope@portal.test", project, roles=("Construction Site Manager",))
		# Drop the permission, leaving the role intact.
		frappe.db.delete("User Permission", {"user": "ph4-noscope@portal.test"})

		previous = frappe.session.user
		frappe.set_user("ph4-noscope@portal.test")
		try:
			self.assertIn("Construction Site Manager", frappe.get_roles("ph4-noscope@portal.test"))
			with self.assertRaises(frappe.PermissionError) as caught:
				site_log.create_site_log(
					project=project,
					log_type="Concrete Pour",
					pour_item=self._item("Ready-mix concrete PH401"),
					pour_volume=1.0,
					pour_task=task.name,
				)
			self.assertIn("do not have access", str(caught.exception))
		finally:
			frappe.set_user(previous)

	# ---- the read path must be scoped --------------------------------------

	def test_open_tasks_refuses_a_project_the_caller_cannot_see(self):
		"""The regression this gate exists to prevent.

		`open_tasks` used to accept any project name, so an authenticated user
		could read the WBS structure of a project they were not assigned to.
		"""
		mine = self._project()
		theirs = self._other_project()
		self._user_scoped_to("ph4-scoped@portal.test", mine)
		self._user_scoped_to("ph4-outsider@portal.test", theirs)

		previous = frappe.session.user
		frappe.set_user("ph4-outsider@portal.test")
		try:
			with self.assertRaises(frappe.PermissionError) as caught:
				site_log.open_tasks(project=mine)
			self.assertIn("do not have access", str(caught.exception))
			# Their own project still works, so the refusal is scoping and not a
			# blanket denial.
			self.assertTrue(isinstance(site_log.open_tasks(project=theirs), list))
		finally:
			frappe.set_user(previous)

	def test_recent_logs_without_a_project_returns_only_permitted_ones(self):
		"""`recent_logs()` with no filter must not return the whole portfolio."""
		mine = self._project()
		theirs = self._other_project()
		self._user_scoped_to("ph4-scope2@portal.test", mine)

		# Write a log for the project the caller must NOT see. Without one the
		# assertion passes vacuously: "not in the result" is trivially true of a
		# project that has never logged anything, so the test would prove nothing
		# about the scoping.
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Construction Site Log",
				"log_number": "PH401-SCOPE-OTHER",
				"project": theirs,
				"log_type": "General",
				"log_date": "2026-01-20 09:00:00",
			}
		).insert(ignore_permissions=True)

		previous = frappe.session.user
		frappe.set_user("ph4-scope2@portal.test")
		try:
			rows = site_log.recent_logs()
			seen = {row.project for row in rows}
			self.assertIn(mine, seen, "the fixture did not produce a visible log; the test proves nothing")
			self.assertNotIn(theirs, seen, "a scoped user saw another project's site activity")
		finally:
			frappe.set_user(previous)

	def test_a_user_scoped_to_nothing_is_answered_with_nothing(self):
		"""Not with everything -- the fallback is the permitted set, which is empty."""
		self._user_scoped_to("ph4-nobody@portal.test", self._project())
		previous = frappe.session.user
		frappe.set_user("ph4-nobody@portal.test")
		try:
			# No commit. The delete is already visible to the call below inside the
			# same transaction, and committing would push the whole test
			# transaction -- including the Item this class created -- out as real
			# rows, which is the exact leak removed from project-view.py.
			frappe.db.delete("User Permission", {"user": "ph4-nobody@portal.test"})
			self.assertEqual(site_log.recent_logs(), [])
		finally:
			frappe.set_user(previous)

	# ---- the PWA shell is present, even though it is not exercised ---------

	def test_the_pwa_manifest_declares_the_app(self):
		"""The manifest exists and is coherent.

		Its *behaviour* -- install prompt, offline caching -- needs a browser and
		is not claimed. That the shell is well-formed and served is checkable.
		"""
		import json
		import os

		root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
		path = os.path.join(root, "public", "pwa", "manifest.json")
		self.assertTrue(os.path.exists(path), f"no PWA manifest at {path}")
		with open(path, encoding="utf-8") as handle:
			manifest = json.load(handle)
		for key in ("name", "short_name", "start_url", "display"):
			self.assertIn(key, manifest)
		self.assertEqual(manifest["display"], "standalone")

	# ---- fixtures ----------------------------------------------------------

	@classmethod
	def _user_scoped_to(cls, email, construction_project, roles=()):
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
					"roles": [{"role": r} for r in roles],
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

	@classmethod
	def _project_named(cls, name):
		existing = frappe.get_all("Construction Project", filters={"project_name": name}, pluck="name")
		if existing:
			return existing[0]
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
		erp_name = f"{name} ERP"
		hits = frappe.get_all("Project", filters={"project_name": erp_name}, pluck="name")
		erp_project = hits[0] if hits else frappe.get_doc(
			{"doctype": "Project", "project_name": erp_name, "company": company}
		).insert(ignore_permissions=True).name
		return frappe.get_doc(
			{
				"doctype": "Construction Project",
				"project_name": name,
				"erpnext_project": erp_project,
				"company": company,
				"client": customer,
				"contract_date": "2026-01-01",
				"contract_amount": 1_000_000.00,
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _project(cls):
		return cls._project_named("PH4-01 Project")

	@classmethod
	def _other_project(cls):
		return cls._project_named("PH4-01 Other Project")

	@classmethod
	def _task(cls, project):
		erp = frappe.db.get_value("Construction Project", project, "erpnext_project")
		existing = frappe.get_all("Task", filters={"project": erp}, limit=1, pluck="name")
		if existing:
			return frappe.get_doc("Task", existing[0])
		return frappe.get_doc(
			{"doctype": "Task", "subject": "PH4-01 pour task", "project": erp, "status": "Open"}
		).insert(ignore_permissions=True)

	@staticmethod
	def _fractional_uom():
		"""A UOM that permits fractions.

		`Nos` is must-be-whole, so a 12.5 m3 pour raises
		UOMMustBeIntegerError. That constraint is real and is exactly why concrete
		is measured in m3 rather than in pieces.
		"""
		fractional = frappe.get_all("UOM", filters={"must_be_whole_number": 0}, pluck="name")
		for preferred in ("Cubic Meter", "M3", "Cubic m"):
			if preferred in fractional:
				return preferred
		return frappe.get_doc(
			{"doctype": "UOM", "uom_name": "Cubic Meter", "uom": "m3", "must_be_whole_number": 0}
		).insert(ignore_permissions=True).uom

	@staticmethod
	def _item(name):
		"""Find-or-create by `item_code`, which is the actual primary key.

		Looking up by `item_name` and deriving the code from the first word of
		the name is how two different items collide on one code while neither
		finds the other.
		"""
		code = "".join(ch for ch in name.upper() if ch.isalnum())[:18] or "ITEM-PH401"
		existing = frappe.db.exists("Item", code)
		if existing:
			return existing
		groups = frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name")
		group = next((g for g in ("All Item Groups", "Raw Materials") if g in groups), groups[0] if groups else None)
		uom = TestSiteLogApi._fractional_uom()
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": name,
				"item_group": group,
				"stock_uom": uom,
				"is_stock_item": 1,
			}
		).insert(ignore_permissions=True).name
