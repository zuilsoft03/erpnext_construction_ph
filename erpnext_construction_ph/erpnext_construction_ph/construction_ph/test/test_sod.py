# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Segregation of Duties, the class A control the spec calls a "hard block".

`docs/ARCHITECTURE.md`, Auth Model:

    "SoD hard blocks: Buyer+Accountant, Site Manager+Accountant
     (roles_fixtures.py:115-133)"

**Untested.** Fifth finding from auditing `docs/ARCHITECTURE.md` section by
section, and the most consequential one so far: the other four were an
unasserted property, an untested degradation, a missing architecture and a
stale count. This one is a *financial control with a hard block on it* -- the
"Maker-Checker Violation" that stops a Buyer also being the Accountant who
certifies the invoice -- and nothing verified that it blocks.

The line reference in the spec has also drifted (`SOD_CONFLICTS` is at 129, not
115), which is the ordinary cost of citing a line number instead of a name.

What is asserted here is the property that matters: a Critical conflict makes
the assignment INVALID, a Warning one does not, and the two are not conflated.
Collapsing them would be the quiet failure -- a control that warns where it
should refuse, and reads as working because the code runs.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.setup import roles_fixtures


class TestSegregationOfDuties(IntegrationTestCase):
	"""A hard block on a financial control, asserted rather than documented."""

	# ---- the control exists and classifies its own rules -------------------

	def test_every_sod_rule_names_real_roles_and_a_severity(self):
		"""A rule referencing a role that does not exist can never fire.

		A SoD rule is a set membership test, so a typo in a role name makes the
		rule silently dead -- and a dead SoD rule is indistinguishable from an
		absent one until someone pairs the two roles for real.
		"""
		self.assertTrue(roles_fixtures.SOD_CONFLICTS, "no SoD rules are declared at all")
		for rule in roles_fixtures.SOD_CONFLICTS:
			self.assertIn(rule.get("severity"), ("Critical", "Warning"), rule)
			self.assertTrue((rule.get("reason") or "").strip(), "a SoD rule with no reason: %s" % rule)
			for role in rule["roles"]:
				self.assertTrue(
					frappe.db.exists("Role", role),
					"SoD rule %s names a role that does not exist, so it can never fire: %s"
					% (rule["roles"], role),
				)

	def test_the_two_spec_pairs_are_declared(self):
		"""The spec names two pairs by hand; both must be present and Critical.

		`docs/ARCHITECTURE.md`: "Buyer+Accountant, Site Manager+Accountant". The
		Estimator+Project Manager pair is a Warning (self-approval risk), which is
		a different and weaker thing -- so this test pins severity too, not just
		presence.
		"""
		critical = {
			frozenset(r["roles"])
			for r in roles_fixtures.SOD_CONFLICTS
			if r["severity"] == "Critical"
		}
		for pair in (
			frozenset({"Construction Buyer", "Construction Project Accountant"}),
			frozenset({"Construction Site Manager", "Construction Project Accountant"}),
		):
			self.assertIn(
				pair, critical,
				"the spec calls %s a HARD BLOCK; it is not declared as Critical" % sorted(pair),
			)

	# ---- the behaviour ----------------------------------------------------

	def test_a_critical_pair_is_refused(self):
		"""The whole point: the pair must not validate."""
		is_valid, _warnings, errors = roles_fixtures.validate_role_assignment(
			["Construction Buyer", "Construction Project Accountant"]
		)
		self.assertFalse(is_valid, "a Buyer who is also the Accountant validated")
		self.assertTrue(errors, "it was invalid but said nothing about why")
		self.assertIn("Maker-Checker", " ".join(errors))

	def test_a_warning_pair_does_not_refuse(self):
		"""A Warning is a Warning. Conflating the two is the quiet failure.

		Refusing on a warning would make the control unusable and get disabled;
		passing a Critical would make it theatre. They are different severities
		and the code has to keep them different.
		"""
		estimator_pm = next(
			r for r in roles_fixtures.SOD_CONFLICTS
			if r["severity"] == "Warning" and len(r["roles"]) == 2
		)
		is_valid, warnings, errors = roles_fixtures.validate_role_assignment(sorted(estimator_pm["roles"]))
		self.assertTrue(is_valid, "a Warning-severity pair refused the assignment")
		self.assertFalse(errors)
		self.assertTrue(warnings, "a Warning pair produced neither a warning nor an error")

	def test_unrelated_roles_are_unaffected(self):
		"""The control must not fire on combinations it never named."""
		is_valid, warnings, errors = roles_fixtures.validate_role_assignment(
			["Construction Estimator", "Construction Buyer", "Construction Field Foreman"]
		)
		self.assertTrue(is_valid)
		self.assertEqual(errors, [])

	def test_a_single_role_never_conflicts(self):
		"""Every rule needs at least two roles, so one alone is always clean."""
		for role in ("Construction Buyer", "Construction Project Accountant", "Construction Estimator"):
			is_valid, _w, _e = roles_fixtures.validate_role_assignment([role])
			self.assertTrue(is_valid, "a lone %s was treated as a conflict" % role)
