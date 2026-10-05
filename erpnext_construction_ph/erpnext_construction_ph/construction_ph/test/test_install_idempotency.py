# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""CORE-01's reliability claim, asserted rather than assumed.

`docs/ARCHITECTURE.md` states two things about the install path:

    "Idempotency | All setup/* guard via `frappe.db.exists`; safe to re-run on
     migrate"

    "duplicate install-app | `after_install` skips existing fixtures via
     `frappe.db.exists` checks"

**Neither was tested.** The suite never re-ran the install, so "safe to re-run"
was a comment rather than a property. That is the same gap the ARCHITECTURE audit
found in the account degradation: a behaviour the spec blesses, which nobody
exercises precisely because exercising it looks like asserting a bug.

It matters more than a routine test, because a non-idempotent install is the
classic way a deployment fails *on the box that matters* -- the second run either
raises on a duplicate or, worse, silently doubles every permission row, so a
user gains access they were never granted.

So: run the install twice, and assert the site is identical afterwards. Counted
per object, not by "it did not crash" -- a duplicate that overwrites rather than
raises is the worse half and a smoke check would miss it.
"""

import frappe
from erpnext_construction_ph.construction_ph.test import fixtures
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.setup import install as install_module
from erpnext_construction_ph.setup import roles_fixtures


class TestInstallIdempotency(fixtures.CommittingCase, IntegrationTestCase):
	"""`after_install` may be re-run; the site must not change."""

	# Re-running an install chain is meaningless unless the previous run is durable --
	# the whole assertion is that running it again changes nothing, which cannot be
	# checked against rows that rolled back. So the commits stay, and
	# `CommittingCase` removes what they persist.
	committing_doctypes = ("Project", "Customer", "Construction Project", "Property Setter")

	def _snapshot(self):
		"""Counts of everything the install chain is supposed to create."""
		company = frappe.get_all("Company", limit=1, pluck="name")[0]
		return {
			"construction roles": frappe.db.count(
				"Role", {"name": ["in", [r["role_name"] for r in roles_fixtures.CONSTRUCTION_ROLES]]}
			),
			"role profiles": frappe.db.count(
				"Role Profile", {"name": ["in", list(roles_fixtures.ROLE_PROFILES)]}
			),
			"construction workspaces": frappe.db.count(
				"Workspace", {"name": ["like", "Construction%"]}
			),
			"docperms on Construction Progress Billing": frappe.db.count(
				"Custom DocPerm", {"parent": "Construction Progress Billing"}
			),
			"custom fields": frappe.db.count("Custom Field", {"fieldtype": "Data"}),
			"gl accounts in the company": frappe.db.count("Account", {"company": company}),
			"eis export accounts": frappe.db.count("Account", {"company": company, "account_type": "Tax"}),
		}

	def test_rerunning_the_role_fixtures_changes_nothing(self):
		"""The claim the spec makes most strongly, on the part that creates the most rows.

		Roles, profiles and Custom DocPerms are the ones that double silently: a
		second run that appends instead of skipping leaves a user with two
		identical permission grants and nothing to show for it.
		"""
		before = self._snapshot()
		roles_fixtures.setup_roles_and_permissions()
		frappe.db.commit()
		after = self._snapshot()
		self.assertEqual(
			before, after,
			"re-running the role fixtures changed the site, so `after_install` is "
			"NOT idempotent and a second install would either duplicate rows or fail",
		)

	def test_rerunning_the_whole_after_install_changes_nothing(self):
		"""The full chain, twice, as `bench migrate` and a re-install would do it."""
		before = self._snapshot()
		install_module.after_install()
		frappe.db.commit()
		after = self._snapshot()
		self.assertEqual(
			before, after,
			"a second after_install changed the site; the spec says it is safe to "
			"re-run on migrate",
		)

	def test_three_runs_still_change_nothing(self):
		"""Idempotence is about the steady state, not about surviving one retry.

		A guard that skips the second run but not the third -- a counter, an
		off-by-one in a range, a fixture keyed on a name that changes -- passes a
		two-run test and fails in production on the third.
		"""
		before = self._snapshot()
		for _ in range(3):
			install_module.after_install()
		frappe.db.commit()
		self.assertEqual(before, self._snapshot())

	def test_every_declared_role_exists_after_a_rerun(self):
		"""The spec's own words: the fixtures are "skipped", not "half-applied".

		A skip that drops one role is indistinguishable from a skip that works
		unless the roles are counted by name afterwards.
		"""
		install_module.after_install()
		frappe.db.commit()
		for role in roles_fixtures.CONSTRUCTION_ROLES:
			self.assertTrue(
				frappe.db.exists("Role", role["role_name"]),
				"after a re-run, role missing: %s" % role["role_name"],
			)
		for profile in roles_fixtures.ROLE_PROFILES:
			self.assertTrue(
				frappe.db.exists("Role Profile", profile),
				"after a re-run, profile missing: %s" % profile,
			)
