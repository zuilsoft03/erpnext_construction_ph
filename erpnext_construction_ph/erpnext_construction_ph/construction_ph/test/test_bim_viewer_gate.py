# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH3-04 gate, the claimable half: the viewer embed and who may see it.

The gate has three clauses. **One is not claimed here, and the reason is NOT a missing
browser** -- this line used to say "needs a browser, and this environment has none",
which was false and sent the next reader after a browser that was there all along. A
browser IS available and capable: `typeof Worker` is a function and
`canvas.getContext("webgl2")` returns a context on ANGLE/NVIDIA RTX 4070 Ti
(ops/tmp-ph3-01/webgl-probe.json). The real chain, measured on the gate site: the one
BIM Model is `conversion_status = 'Pending'`; `bim_context()` keeps only `Ready`
models; so `bim.available` is False, the template takes the withheld branch, and
`bim-surface` is never emitted -- **there is no context to initialise.** The blocker
is PH3-01's `.frag` converter, which `default_converter` deliberately refuses to fake.
What these tests cover is the other two clauses, and the second turned out to be the
real work:

- "the portal renders the read-only viewer for a Portal User" -- a client-facing
  role, for a project they are scoped to.
- "an internal Desk role alone is not sufficient" -- the lower bound, and the
  reason `READ_ROLES` must not satisfy the viewer gate.

**`api/bim_viewer.list_models` was unscoped.** It was `@frappe.whitelist()`,
`project` was a filter and not a permission, and with no project named it
returned *every* BIM Model in the installation. A page-level role gate in front
of it would have been decorative: any session holder could call the method
directly. Closing that is what these tests hold shut.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import bim_viewer, portal_data

CLIENT = "ph304-client@portal.test"
STAFF = "ph304-staff@portal.test"
OTHER_CLIENT = "ph304-other@portal.test"


class TestBimViewerGate(IntegrationTestCase):
	"""The viewer is a client surface, and the gate is on the data, not the page."""

	# ---- who may see a model ----------------------------------------------

	def test_a_client_with_project_access_may_view(self):
		self.assertTrue(portal_data.may_view_bim(CLIENT, self.project))

	def test_an_internal_desk_role_alone_is_not_sufficient(self):
		"""**The gate's own lower bound.**

		`STAFF` holds a role in `READ_ROLES`, so they can see the billing ledger,
		and they are scoped to this project -- and they still may not see the
		client's model. Sharing one predicate between the Desk and the portal is
		what would make the read-only viewer a staff surface instead of a client
		one.
		"""
		frappe.set_user(STAFF)
		try:
			self.assertTrue(
				portal_data.may_view(STAFF, self.project),
				"precondition: staff can see the ledger for this project",
			)
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(
			portal_data.may_view_bim(STAFF, self.project),
			"an internal Desk role must not by itself grant the client model",
		)

	def test_a_client_scoped_to_another_project_may_not_view(self):
		self.assertFalse(portal_data.may_view_bim(CLIENT, self.other_project))

	def test_no_project_is_not_a_pass(self):
		"""A falsy project must not slip through as 'unrestricted'."""
		for empty in (None, "", 0):
			self.assertFalse(portal_data.may_view_bim(CLIENT, empty))

	# ---- the endpoint, which is what actually holds the control ------------

	def test_list_models_refuses_a_project_the_caller_may_not_view(self):
		"""A client scoped elsewhere gets nothing, not a refusal page.

		Runs as `OTHER_CLIENT`, who is scoped to `other_project` and holds a
		client role, and asks for `self.project`. Calling it as the session user
		would have been meaningless: that user is Administrator, who is entitled
		to every project, so the row coming back is the CORRECT answer and the
		test would have been asserting the wrong thing.
		"""
		frappe.set_user(OTHER_CLIENT)
		try:
			self.assertEqual(bim_viewer.list_models(project=self.project), [])
		finally:
			frappe.set_user("Administrator")

	def test_list_models_is_not_a_cross_project_enumeration(self):
		"""**The hole this row was really about.**

		Called with no project, the old implementation returned every model in the
		installation. It now returns only models on projects the caller is
		entitled to -- and for a client that is at most the one they are scoped to.
		"""
		frappe.set_user(CLIENT)
		try:
			rows = bim_viewer.list_models()
			for row in rows:
				self.assertEqual(
					row.get("project"), self.project,
					"a model from a project this client is not scoped to leaked",
				)
		finally:
			frappe.set_user("Administrator")

	def test_an_internal_desk_role_gets_nothing_from_the_endpoint(self):
		"""The endpoint enforces the same rule the page does."""
		frappe.set_user(STAFF)
		try:
			self.assertEqual(bim_viewer.list_models(project=self.project), [])
			self.assertEqual(bim_viewer.list_models(), [])
		finally:
			frappe.set_user("Administrator")

	def test_a_permitted_client_sees_the_project_models(self):
		frappe.set_user(CLIENT)
		try:
			rows = bim_viewer.list_models(project=self.project)
			self.assertTrue(rows, "a permitted client saw no model at all")
		finally:
			frappe.set_user("Administrator")

	# ---- the page context, which is what the embed renders -----------------

	def test_the_context_withholds_the_model_without_leaking_its_existence(self):
		"""Withheld must read as 'nothing to show', not 'something is hidden'.

		An embed that rendered "access denied" on one branch and a model on the
	other would let a caller enumerate which projects have models.
		"""
		frappe.set_user(STAFF)
		try:
			bim = self._bim_context()
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(bim["available"])
		self.assertEqual(bim["models"], [])
		self.assertIsNone(bim["model"])
		# No key anywhere in the withheld payload names a model or its geometry.
		for key in ("name", "model_name", "frag_url", "conversion_status"):
			self.assertNotIn(key, str(bim), "withheld context leaked %s" % key)

	def test_the_context_carries_a_model_for_a_permitted_client(self):
		frappe.set_user(CLIENT)
		try:
			bim = self._bim_context()
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(bim["available"])
		self.assertTrue(bim["model"].get("name"))

	def test_the_context_always_carries_an_available_key(self):
		"""The template checks `bim.available`; a missing key renders as
		undefined, which is how this page 500'd before its controller was found."""
		frappe.set_user(STAFF)
		try:
			self.assertIn("available", self._bim_context())
		finally:
			frappe.set_user("Administrator")

	def _bim_context(self):
		"""The same expression the page controller uses."""
		from erpnext_construction_ph.www.portal import project_view

		return project_view.bim_context(self.project)

	# ---- fixtures ---------------------------------------------------------

	@property
	def project(self):
		return self._project("PH3-04 shown")

	@property
	def other_project(self):
		return self._project("PH3-04 other")

	def _project(self, name):
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

	def setUp(self):
		# A Ready model on EACH project, because the context only surfaces
		# converted ones -- and, more importantly, because a leak test needs
		# **something there to leak**. With a model only on the shown project, the
		# enumeration assertion passed even with the project filter REMOVED
		# entirely: there was no second project to return. A test that cannot fail
		# is not a test, and this one was passing for the wrong reason.
		self._model("PH3-04 model", self.project)
		self._model("PH3-04 OTHER model", self.other_project)
		self._user(CLIENT, self.project, roles=("Construction Client",))
		self._user(STAFF, self.project, roles=("Construction Project Manager",))
		self._user(OTHER_CLIENT, self.other_project, roles=("Construction Client",))
		self._topic("PH3-04 shown issue", self._model_name("PH3-04 model"))
		self._topic("PH3-04 OTHER issue", self._model_name("PH3-04 OTHER model"))

	@staticmethod
	def _topic(title, model):
		"""A BCF topic on `model`. Without one on each, a leak test has nothing to leak."""
		if not model or frappe.db.exists("BCF Topic", {"title": title}):
			return
		frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"title": title,
				"bim_model": model,
				# BCF Topic requires  too, and it must AGREE with the
				# model's -- a topic claiming a different project is the shape a
				# leak would take, so the fixture must not create one by accident.
				"project": frappe.db.get_value("BIM Model", model, "project"),
				"topic_guid": frappe.generate_hash(length=32),
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	@staticmethod
	def _model(name, project):
		"""A Ready model, if one is not already there.

		`Ready` is a CLAIM that the cache behind it exists, so this fixture has to
		make it true rather than assert it. It used to write a `frag_url` pointing
		at a path that was never created, which is the exact dishonest state
		`list_models` downgrades: the model said Ready, `get_frag_url` answered
		`cache_missing`, and the dropdown would have offered a model the viewer
		cannot load. With that fixed, three authorisation tests here failed -- not
		because authorisation broke, but because the fixture was claiming a
		convertibility it had not earned, and the reconciliation correctly
		refused to take its word for it.

		So the fragment is really stored. These tests are about WHO sees a model,
		and they can only measure that once a model genuinely exists to see.
		"""
		if frappe.get_all("BIM Model", filters={"model_name": name}, pluck="name"):
			return
		doc = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": name,
				"project": project,
				# Pending, because the doctype refuses to let a model be marked Ready
				# without a .frag URL -- and the point of this fixture is that the
				# Ready state is reached the way production reaches it: convert, then
				# mark. Setting Ready up front is what made the old fixture a lie.
				"conversion_status": "Pending",
				"lod_level": "LOD200",
			}
		).insert(ignore_permissions=True)
		import base64

		from erpnext_construction_ph.api import bim_frag

		bim_frag.store_frag(doc.name, base64.b64encode(b"\x00" * 64).decode())
		frappe.db.set_value(
			"BIM Model", doc.name, "conversion_status", "Ready", update_modified=False
		)

	@staticmethod
	def _user(email, construction_project, roles):
		"""A user scoped to exactly one Construction Project, with given roles."""
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
		# `Has Role` is a CHILD TABLE of User, so it cannot be inserted as a
		# doctype in its own right -- doing so fails on parent/parenttype. It has
		# to be appended to the User document, which is also the only way the
		# change persists to the role cache frappe.get_roles() reads.
		held = frappe.get_roles(email)
		needed = [r for r in roles if r not in held]
		if needed:
			user = frappe.get_doc("User", email)
			for role in needed:
				user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
		if not frappe.db.exists(
			"User Permission", {"user": email, "for_value": frappe.db.get_value("Construction Project", construction_project, "erpnext_project")}
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Project",
					"for_value": frappe.db.get_value("Construction Project", construction_project, "erpnext_project"),
					"apply_to_all_doctypes": 1,
				}
			).insert(ignore_permissions=True)

	# ---- the topic overlay, which had the identical hole ---------------------

	def test_list_topics_refuses_a_model_the_caller_may_not_view(self):
		"""**`list_topics` was the same hole, and worse.**

		Whitelisted, no authorisation, and with no model named it returned *every
		BCF Topic in the installation* with `limit_page_length=0` -- no limit at
		all. BCF topics carry issue TEXT and camera vectors, not just an inventory
		row, so it leaked more than the model list did. The embed's own
		`getTopics` calls exactly this method, so closing `list_models` alone left
		the overlay's second call wide open.
		"""
		frappe.set_user(OTHER_CLIENT)
		try:
			self.assertEqual(bim_viewer.list_topics(bim_model=self.shown_model), [])
		finally:
			frappe.set_user("Administrator")

	def test_list_topics_is_not_a_cross_project_enumeration(self):
		"""With no model named, topics come only from entitled projects."""
		frappe.set_user(CLIENT)
		try:
			seen = {row.get("bim_model") for row in bim_viewer.list_topics()}
			self.assertNotIn(
				self.other_model, seen,
				"a topic on a model from another project leaked to this client",
			)
		finally:
			frappe.set_user("Administrator")

	def test_an_internal_desk_role_gets_no_topics(self):
		frappe.set_user(STAFF)
		try:
			self.assertEqual(bim_viewer.list_topics(bim_model=self.shown_model), [])
			self.assertEqual(bim_viewer.list_topics(), [])
		finally:
			frappe.set_user("Administrator")

	def test_a_permitted_client_sees_their_own_topics(self):
		"""Proves the refusals above are the gate working, not an empty table."""
		frappe.set_user(CLIENT)
		try:
			self.assertTrue(bim_viewer.list_topics(bim_model=self.shown_model))
		finally:
			frappe.set_user("Administrator")

	def test_topics_are_bounded(self):
		"""`limit_page_length=0` was unbounded as well as unauthorised."""
		import erpnext_construction_ph.api.bim_viewer as bv

		self.assertGreater(bv.TOPIC_LIMIT, 0)
		frappe.set_user(CLIENT)
		try:
			self.assertLessEqual(len(bim_viewer.list_topics()), bv.TOPIC_LIMIT)
		finally:
			frappe.set_user("Administrator")

	@property
	def shown_model(self):
		return self._model_name("PH3-04 model")

	@property
	def other_model(self):
		return self._model_name("PH3-04 OTHER model")

	@staticmethod
	def _model_name(name):
		rows = frappe.get_all("BIM Model", filters={"model_name": name}, pluck="name")
		return rows[0] if rows else None

	# ---- the page itself, rendered -------------------------------------------

	def _context_for(self, project):
		"""Run the real controller for `project`, restoring `form_dict` after."""
		from erpnext_construction_ph.www.portal import project_view

		previous = dict(frappe.form_dict)
		frappe.form_dict["project"] = project
		try:
			return project_view.get_context(frappe._dict())
		finally:
			frappe.form_dict.clear()
			frappe.form_dict.update(previous)

	def test_the_viewer_template_compiles(self):
		"""A syntax error in the template is a 500 for every client, and JUnit shows nothing.

		Compiling the template is as far as this harness can go and it catches the
		whole class of "the file is there but malformed". What it CANNOT do is render
		the full page: `project-view.html` extends `templates/web.html`, which needs
		Frappe's web bootstrap (`frappe.website`, boot dict, the site's JS bundle) and
		renders `UndefinedError` in a unit test. So the rendered-HTML half of the
		gate is NOT covered here and is not claimed; the browser CHECK is where it
		belongs. The controller half IS covered, by the two tests below.
		"""
		import frappe

		import os

		# get_app_path(app) alone resolves correctly; the multi-argument form does
		# not join the way it reads, so the path is built here.
		template = os.path.join(
			frappe.get_app_path("erpnext_construction_ph"), "www", "portal", "project-view.html"
		)
		with open(template, encoding="utf-8") as handle:
			source = handle.read()
		frappe.get_jenv().parse(source)
		# A stray `{% extends %}`-shaped typo would parse and still be wrong, so
		# assert the two things the page is actually built from.
		self.assertIn("bim-surface", source, "the viewer surface is gone from the template")
		self.assertIn(
			"No model is shared with you",
			source,
			"the withheld branch is gone, so an unauthorised caller would get a blank "
			"section rather than a refusal",
		)

	def test_the_page_gives_a_portal_user_the_viewer_payload(self):
		"""The gate's first clause, on the context the page renders from."""
		frappe.set_user(self._user_name(CLIENT))
		self.addCleanup(frappe.set_user, "Administrator")
		context = self._context_for(self.project)
		self.assertTrue(
			context["bim"]["available"],
			"a permitted client was shown the withheld branch for a project with a Ready model",
		)
		self.assertEqual(context["bim"]["model"]["model_name"], self._model_name("PH3-04 model"))

	def test_the_page_gives_an_internal_desk_role_no_model_and_no_trace_of_one(self):
		"""The gate's second clause: withheld, and not even a hint at what exists.

		WHAT THIS DOES AND DOES NOT PROVE, since the distinction matters. It proves the
		OUTCOME: no model reaches an internal Desk role. It does NOT attribute that to
		the controller's `may_view_bim` guard, because removing that guard leaves the
		test green -- `bim_viewer.list_models` scopes independently through
		`entitled_projects`, so the defence is layered and either layer alone holds.
		That is the right design and it was not built by accident: `list_models` was
		unscoped once and returned every model in the installation.

		THE TEST IS PROVEN TO SEE A LEAK, by bypassing the layering rather than
		unpicking it: replacing `bim_context`'s body with one that hands every caller
		a FOREIGN model fails this module 5 times, and restoring it is green. A
		single-line mutation cannot demonstrate that -- the defence is layered, so
		removing one layer correctly changes nothing. What matters is the outcome,
		and the outcome is what these tests assert.
		"""
		frappe.set_user(self._user_name(STAFF))
		self.addCleanup(frappe.set_user, "Administrator")
		context = self._context_for(self.project)
		self.assertFalse(context["bim"]["available"])
		self.assertIsNone(context["bim"]["model"])
		# A withheld branch that NAMED a model would let a caller enumerate which
		# projects have one, which is the whole reason the payload shape withholds.
		blob = str(context["bim"])
		for leak in (self._model_name("PH3-04 model"), self._model_name("PH3-04 OTHER model")):
			self.assertNotIn(leak, blob, f"the withheld payload leaked {leak!r}")

	@staticmethod
	def _user_name(email):
		return frappe.db.get_value("User", email, "name")

	# ---- the clash list, which is the delegated-geometry reading made real ---

	# The access control on the clash list is LAYERED, and the scope of what these
	# tests prove is recorded rather than overclaimed:
	#   - removing `list_topics`' OWN scoping fails 4 of these tests -- that is the
	#     layer carrying the disclosure, and it is the one that matters;
	#   - removing the controller's `may_view_bim` guard leaves them GREEN, because
	#     `list_topics` scopes independently through `entitled_projects`.
	# That is defence in depth and it is deliberate: `list_topics` was itself unscoped
	# once and returned every BCF Topic in the installation -- issue TEXT and camera
	# vectors, with no limit at all. So these tests prove the OUTCOME (no issue
	# reaches a caller who may not see it), not that any single line is load-bearing.
	# No one-layer mutation can demonstrate otherwise, and claiming one would be the
	# same false "mutation-proven" this project has produced by accident more than once.

	def test_a_portal_user_is_shown_the_clashes_on_their_own_project(self):
		"""Detecting clashes is delegated to the partner tool; SHOWING them is ours.

		This is deliverable without a `.frag`, which is why it ships while the geometry
		stays delegated: a clash list needs no rendered model to be useful, and a
		client with a fully converted model was previously shown a viewer surface and
		a list of nothing.
		"""
		frappe.set_user(self._user_name(CLIENT))
		self.addCleanup(frappe.set_user, "Administrator")
		context = self._context_for(self.project)
		topics = context["bim"]["topics"]
		self.assertTrue(topics, "a client with a Ready model and a filed issue saw no list")
		titles = [t.get("title") for t in topics]
		self.assertIn("PH3-04 shown issue", titles)

	def test_the_clash_list_does_not_cross_projects(self):
		"""The other project's issue must not appear, or a client enumerates others."""
		frappe.set_user(self._user_name(CLIENT))
		self.addCleanup(frappe.set_user, "Administrator")
		topics = self._context_for(self.project)["bim"]["topics"]
		for topic in topics:
			self.assertNotEqual(
				topic.get("title"),
				"PH3-04 OTHER issue",
				"a client scoped to one project was shown another project's issue",
			)

	def test_an_internal_desk_role_gets_no_clashes(self):
		"""BCF topics carry issue TEXT, not just an inventory row, so this leaks more."""
		frappe.set_user(self._user_name(STAFF))
		self.addCleanup(frappe.set_user, "Administrator")
		context = self._context_for(self.project)
		self.assertFalse(context["bim"]["available"])
		self.assertEqual(
			context["bim"]["topics"], [], "an internal Desk role was handed the clash list"
		)

	def test_the_payload_always_carries_a_topics_key(self):
		"""A missing key renders as `undefined`, which is how this page 500'd before."""
		frappe.set_user(self._user_name(CLIENT))
		self.addCleanup(frappe.set_user, "Administrator")
		self.assertIn("topics", self._context_for(self.project)["bim"])
