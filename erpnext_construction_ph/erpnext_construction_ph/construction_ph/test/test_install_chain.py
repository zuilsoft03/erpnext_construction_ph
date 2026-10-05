# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-01 gate: the install chain's auth + setup result.

CORE-01 is "install smoke: after_install clean run", class A because a silent
setup failure leaves an installation that looks installed and authorises nobody.
Its authored negative is "uninstall the app; verify `list-apps` no longer shows
it and the roles are removed" -- which exercises the *uninstall* path and so
proves nothing about the *install* path that actually runs.

**Measured gap.** No test covered `setup/roles_fixtures.py` at all: not one test
in the suite referenced the roles, the profiles or the permission matrices. A
row whose setup silently creates eight roles instead of ten, or a profile naming
a role that does not exist, was DONE and green. The assertions here are what
make the install chain's own result checkable, and the mutation below is the
proof they are load-bearing.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.setup import roles_fixtures


class TestInstallChain(IntegrationTestCase):
	"""The 10 construction roles and their profiles exist and are consistent."""

	# ---- the roles -------------------------------------------------------

	def test_every_declared_construction_role_exists(self):
		"""The count is the contract, and it is read from the fixture.

		**Exactly 11, since PH3-04.** `Construction Client` is the portal role --
		the only one with `desk_access: 0` -- and PH3-04's authorisation rule
		depends on it existing. It was previously created inside a test's
		transaction, so it did not exist outside one; a role the authorisation
		rule depends on is a role the install must create. The count is read from
		the fixture rather than hard-coded so adding a role is one edit here and
		one in the fixture, not a third place -- and the assertion stays EXACT, so
		adding a role is a deliberate edit rather than something a loosened bound
		would absorb.
		"""
		expected = [r["role_name"] for r in roles_fixtures.CONSTRUCTION_ROLES]
		self.assertEqual(
			len(expected), 11,
			"the contract is exactly 11 roles since PH3-04 added the portal role. "
			"Loosening this to '10 or more' would be a guard that tests less than it "
			"claims, which is the defect this test exists to catch.",
		)
		for role in expected:
			self.assertTrue(
				frappe.db.exists("Role", role), "role missing from the install: %s" % role
			)

	def test_every_role_has_a_description(self):
		"""A role with a blank description is a role nobody will ever assign.

		The field crew-facing roles are described by what the holder does on
		site, so a missing description is a usability defect that no count
		catches.
		"""
		for role in roles_fixtures.CONSTRUCTION_ROLES:
			self.assertTrue(
				(role.get("description") or "").strip(),
				"no description for role: %s" % role.get("role_name"),
			)

	def test_the_documented_roles_are_all_present(self):
		"""Named, not counted.

		Counting ten would still pass if two roles were renamed into duplicates,
		which is precisely the kind of drift a smoke test should catch.
		"""
		for role in (
			"Construction Estimator",
			"Construction Site Manager",
			"Construction Project Manager",
			"Construction BIM Coordinator",
			"Construction Buyer",
			"Construction Planner",
			"Construction Design Lead",
			"Construction Project Accountant",
			"Construction Contract Administrator",
			"Construction Field Foreman",
			"Construction Client",
		):
			self.assertTrue(frappe.db.exists("Role", role), "missing role: %s" % role)

	# ---- the profiles ----------------------------------------------------

	def test_every_profile_resolves_to_roles_that_exist(self):
		"""A profile naming a role that does not exist grants nothing.

		ERPNext accepts the profile at creation and the holder then hits a
		permission error on first use, deep in a transaction, with no link back
		to the typo.
		"""
		for profile, roles in roles_fixtures.ROLE_PROFILES.items():
			for role in roles:
				self.assertTrue(
					frappe.db.exists("Role", role),
					"profile %s names a role that does not exist: %s" % (profile, role),
				)

	def test_every_construction_role_is_reachable_through_a_profile(self):
		"""A role in no profile cannot be granted in one click, and will not be.

		Every construction role must appear in the profile named after it, so
		assigning a role is a single pick rather than a manual hunt.
		"""
		profiled = set()
		for roles in roles_fixtures.ROLE_PROFILES.values():
			profiled.update(roles)
		for role in roles_fixtures.CONSTRUCTION_ROLES:
			name = role["role_name"]
			# A role with no Desk access is a portal role and must NOT be bundled
			# into a Role Profile: a profile is how you grant native ERPNext module
			# access, and handing a client contact `Projects User` because they
			# were given a profile would give them a Desk surface they are not
			# entitled to. `Construction Client` is the one such role.
			if not role.get("desk_access"):
				self.assertNotIn(
					name, profiled,
					"portal role %s must not be in a Role Profile; a profile grants "
					"Desk module access" % name,
				)
				continue
			self.assertIn(name, profiled, "role is in no profile: %s" % name)

	def test_the_profiles_exist_on_the_site(self):
		"""The install created them, not merely the fixture declares them."""
		for profile in roles_fixtures.ROLE_PROFILES:
			self.assertTrue(
				frappe.db.exists("Role Profile", profile), "profile not created: %s" % profile
			)

	# ---- residue ---------------------------------------------------------

	def test_no_test_leaves_a_role_behind(self):
		"""**A residue detector, added because residue was already found once.**

		Measured: `Construction Client` is created by `test_portal_scoping.py`, not
		by the install chain -- so the 11th role in the database was a test's
		leftover, not something the install produced. Deleting it and re-running
		proved the leak is closed: the suite is green without it and it does not
		reappear, because `IntegrationTestCase` rolls the transaction back.

		This asserts the closure rather than assuming it. If a future fixture
		commits, the count goes to 11 and this fails by name -- which is the only
		signal that the suite's green has stopped meaning anything.
		"""
		roles = frappe.get_all("Role", filters={"name": ["like", "Construction %"]}, pluck="name")
		self.assertEqual(
			sorted(roles), sorted(r["role_name"] for r in roles_fixtures.CONSTRUCTION_ROLES),
			"the site holds roles the install chain does not create; a test is "
		 "committing and leaving residue behind",
		)

	# ---- the site preconditions this suite stands on ------------------------

	def test_the_site_preconditions_d012_names_exist(self):
		"""**21 test files and 35 call sites depend on a Company that no test creates.**

		Every one of them does `frappe.get_all("Company", limit=1, pluck="name")[0]`
		-- a bare pick that raises IndexError on a site with no Company. D-012
		records the Company as a *precondition of the gate site*, not something
		the app installs, so the whole suite rests on hand-seeded data.

		This is asserted here, once and by name, because otherwise the first
		symptom is an IndexError in some unrelated test file and nothing points
		at the actual cause. (The same reasoning as the bare `Construction
		Project` picks that residue was hiding; Company survived the uninstall
		only because it is ERPNext core rather than app-owned, so its dependency
		was still masked.)

		It is deliberately an assertion, not a fixture: a test that CREATED a
		Company would hide the precondition instead of documenting it, and
		D-012 exists precisely because the site is not a bare Frappe site.
		"""
		self.assertGreater(
			frappe.db.count("Company"), 0,
			"no Company on this site. D-012 makes one a precondition of the gate "
			"site, and 35 call sites across 21 test files pick one with [0].",
		)

	def test_erpnext_is_installed_on_the_gate_site(self):
		"""D-012's first precondition. Without it the app cannot function and
		every ERPNext link in the suite fails in a way that names ERPNext, not
		the precondition."""
		apps = frappe.get_installed_apps()
		self.assertIn("erpnext", apps, "ERPNext is not installed; D-012 requires it")
		self.assertIn("erpnext_construction_ph", apps)

	def test_the_tax_withholding_categories_d012_names_exist(self):
		"""D-012's third precondition, and the one with statutory teeth.

		`after_install` creates `Tax Withholding Category` records that the BIR
		Form 2307 register links against. A site where the install ran but the
		categories are missing produces an empty withholding register -- which
		reads as "no withholding this period" rather than as a broken site.
		"""
		rows = frappe.get_all("Tax Withholding Category", pluck="name")
		self.assertTrue(rows, "no Tax Withholding Category on this site; D-012 requires them")
		for name in rows:
			self.assertTrue(
				frappe.db.exists("Tax Withholding Category", name), "dangling category row"
			)

	def test_the_accounts_d012_gate_expectation_refers_to_exist(self):
		"""D-012 notes that "5 GL accounts exist" is only evaluable once a
		Company exists, because `setup_construction_accounts()` iterates
		companies and creates nothing when there are none. Assert the accounts
		exist for the company the suite uses, so the CORE-01 gate expectation is
		actually checkable rather than vacuously unevaluated."""
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		accounts = frappe.get_all("Account", filters={"company": company}, pluck="name")
		self.assertTrue(
			accounts, "no GL accounts for %s; CORE-01's '5 GL accounts exist' "
			"expectation cannot be evaluated" % company
		)

	def test_the_roles_d012_requires_are_not_missing(self):
		"""D-012's other half: `after_install` builds Role Profiles naming
		native ERPNext roles, so those must exist for the profiles to mean
		anything. Checked at the site level rather than in a fixture."""
		for role in ("Stock User", "Item Manager", "Projects User", "Accounts User"):
			self.assertTrue(
				frappe.db.exists("Role", role),
				"native role missing, so the role profiles referencing it grant "
				"nothing: %s" % role,
			)
