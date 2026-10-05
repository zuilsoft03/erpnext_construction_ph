# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH5-02 gate: the documents are valid OOXML and carry live values.

Two halves, asserted separately (D-031's split, applied to documents):

- **the package** — declared content types, a well-formed relationship graph, and
  a body containing the parts the template declares. That is what Word's schema
  requires, and it is checkable headlessly. "Opens without a repair prompt" is a
  desktop-application assertion and is **not** claimed.
- **the values** — the figures come from the `Construction Project` and
  `Construction Progress Billing` the caller names. A template that renders but
  ignores its inputs is the failure this half exists to catch.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from erpnext_construction_ph.api import docx_engine

CONTRACT_SUM = 4_500_000.00
GROSS = 1_125_000.00
NET = 1_180_925.00


class TestDocxEngine(IntegrationTestCase):
	"""Structure of the package, and the values inside it."""

	# ---- the package is what Word's schema requires -----------------------

	def test_the_output_is_a_docx_package(self):
		"""A `.docx` is a ZIP starting with the OOXML marker."""
		payload = docx_engine.render("contract", self._project())
		self.assertTrue(payload.startswith(b"PK"), "the output is not a ZIP package")
		self.assertGreater(len(payload), 2000, "suspiciously small for a document")

	def test_the_package_declares_the_required_parts(self):
		"""Content types and the main document part must be present.

		Word refuses a package without `[Content_Types].xml` or without the
		document part declared as the officeDocument relationship -- this is the
		structural equivalent of "opens without a repair prompt", and it is
		checkable here.
		"""
		import zipfile
		import io

		payload = docx_engine.render("contract", self._project())
		with zipfile.ZipFile(io.BytesIO(payload)) as zf:
			names = set(zf.namelist())
			self.assertIn("[Content_Types].xml", names)
			self.assertIn("word/document.xml", names)
			types = zf.read("[Content_Types].xml").decode("utf-8")
			self.assertIn(
				"wordprocessingml.document.main+xml",
				types,
				"the main document part is not declared in [Content_Types].xml",
			)
			rels = zf.read("_rels/.rels").decode("utf-8")
			self.assertIn("officeDocument", rels)

	def test_the_package_has_no_broken_relationship_targets(self):
		"""Every internal relationship must resolve to a part that exists.

		A dangling relationship is exactly what produces Word's "unreadable
		content" repair prompt, so this is the closest headless equivalent.
		"""
		import io
		import re
		import zipfile

		payload = docx_engine.render("contract", self._project())
		with zipfile.ZipFile(io.BytesIO(payload)) as zf:
			names = set(zf.namelist())
			for rels_name in [n for n in names if n.endswith(".rels")]:
				base = rels_name.rsplit("_rels/", 1)[0]
				targets = re.findall(r'Target="([^"]+)"', zf.read(rels_name).decode("utf-8"))
				for target in targets:
					if target.startswith("http") or target.startswith("#"):
						continue
					parts = (base + target).split("/")
					resolved = []
					for part in parts:
						if part == "..":
							if resolved:
								resolved.pop()
						elif part not in ("", "."):
							resolved.append(part)
					self.assertIn(
						"/".join(resolved),
						names,
						f"{rels_name} points at {target}, which is not in the package",
					)

	# ---- the values are live ------------------------------------------------

	def test_the_contract_carries_the_projects_figures(self):
		"""Contract sum, client and date come from the project named."""
		project = self._project()
		doc = docx_engine.build_contract(project)
		text = docx_engine.document_text(doc)
		row = frappe.db.get_value("Construction Project", project, ["contract_amount", "client"], as_dict=True)

		self.assertIn("{:,.2f}".format(flt(row.contract_amount)), text)
		self.assertIn(row.client, text)
		self.assertIn("CONTRACT AGREEMENT", text)
		self.assertIn("AGREED", text)

	def test_no_placeholder_or_None_survives_into_the_document(self):
		"""A contract reading `None` is a defect, and a surviving `{{ }}` too."""
		project = self._project()
		for kind, builder in (
			("contract", lambda: docx_engine.build_contract(project)),
			("ntp", lambda: docx_engine.build_notice_to_proceed(project)),
			("cc", lambda: docx_engine.build_completion_certificate(project)),
		):
			text = docx_engine.document_text(builder())
			self.assertNotIn("{{", text, f"{kind} left a template marker")
			self.assertNotIn("}}", text, f"{kind} left a template marker")
			self.assertNotIn("None", text, f"{kind} rendered a literal None")

	def test_a_missing_value_becomes_an_em_dash_not_None(self):
		"""The rendering rule itself, on the value it is most likely to hit."""
		self.assertEqual(docx_engine._value(None), "—")
		self.assertEqual(docx_engine._value(""), "—")
		self.assertEqual(docx_engine._value("   "), "—")
		self.assertEqual(docx_engine._value("real"), "real")

	def test_a_billings_figures_reach_the_contract(self):
		"""The 'live values' half with a real submitted billing attached."""
		project = self._project()
		billing = self._billing(project)
		text = docx_engine.document_text(docx_engine.build_contract(project, billing=billing))
		self.assertIn(billing, text)
		self.assertIn("{:,.2f}".format(GROSS), text)
		self.assertIn("FIRST PROGRESS BILLING", text)

	def test_an_unknown_document_kind_is_refused(self):
		"""`render` dispatches through a fixed table, not getattr on the module."""
		with self.assertRaises(frappe.ValidationError) as caught:
			docx_engine.render("__class__", self._project())
		self.assertIn("Unknown document", str(caught.exception))

	def test_a_project_the_caller_cannot_see_is_refused(self):
		"""A rendered contract carries the contract sum, so scoping applies.

		`portal_data.may_view` is the same gate the portal uses; skipping it here
		would be a disclosure path around it.
		"""
		mine = self._project()
		theirs = self._other_project()
		self._user_scoped_to("docx-outsider@portal.test", theirs)

		previous = frappe.session.user
		frappe.set_user("docx-outsider@portal.test")
		try:
			with self.assertRaises(frappe.PermissionError):
				docx_engine.render("contract", mine)
		finally:
			frappe.set_user(previous)

	# ---- fixtures ----------------------------------------------------------

	@staticmethod
	def _user_scoped_to(email, construction_project):
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

	@classmethod
	def _project_named(cls, name, amount):
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
				"contract_date": "2026-01-15",
				"contract_amount": amount,
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _project(cls):
		return cls._project_named("DOCX Project", CONTRACT_SUM)

	@classmethod
	def _other_project(cls):
		return cls._project_named("DOCX Other Project", 1_000_000.00)

	@classmethod
	def _billing(cls, project):
		erp = frappe.db.get_value("Construction Project", project, "erpnext_project")
		seq = len(frappe.get_all("Construction Progress Billing", pluck="name")) + 1
		doc = frappe.get_doc(
			{
				"doctype": "Construction Progress Billing",
				"billing_number": f"DOCX-BILL-{seq:04d}",
				"project": erp,
				"company": frappe.get_all("Company", limit=1, pluck="name")[0],
				"client": frappe.db.get_value("Construction Project", project, "client"),
				"posting_date": "2026-03-01",
				"billing_period_start": "2026-02-01",
				"billing_period_end": "2026-02-28",
				"contract_amount": CONTRACT_SUM,
				"period_accomplishment_percent": 25.0,
				"cumulative_accomplishment": GROSS,
			}
		).insert(ignore_permissions=True)
		doc.submit()
		return doc.name
