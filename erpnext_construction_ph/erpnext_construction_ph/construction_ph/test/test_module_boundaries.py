# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The dependency rules in `docs/ARCHITECTURE.md`, asserted rather than hoped for.

    "api/ may import from setup/ only for shared constants; never call setup
     functions at runtime."
    "billing_engine.py must never bypass Frappe's Document.submit() flow."
    "All Frappe Desk pages use frappe.call() -- no direct SQL from JS."

**They all hold today, and nothing was keeping them there.** These are the rules
that decay silently: someone reaches for a provisioning helper because it is
right there and already idempotent, and the dependency direction inverts
permanently with a green suite. Found by auditing `docs/ARCHITECTURE.md` section
by section -- the same sweep that found CORE-17, the untested account
degradation, and the unexercised install idempotency.

This is a STATIC check over the source tree rather than a behavioural one, and
that is the point: a boundary is a property of the code's shape, and no runtime
test can observe it.
"""

import pathlib
import re

import frappe
from frappe.tests import IntegrationTestCase

APP = pathlib.Path(frappe.get_app_path("erpnext_construction_ph"))
JS_DIRS = ("public/js", "construction_ph/page")


class TestModuleBoundaries(IntegrationTestCase):
	"""The dependency directions the spec fixes, checked as shapes not behaviours."""

	def _py_files(self, *subdirs):
		for sub in subdirs:
			for path in sorted((APP / sub).rglob("*.py")):
				yield path

	@staticmethod
	def _is_test(path):
		return "test" in path.parts

	# ---- rule 1: api/ must not CALL setup/ at runtime ----------------------

	def test_no_api_module_calls_a_setup_function(self):
		"""`setup/` is provisioning, not a runtime library.

		An import is not the violation -- a *call* is. Tests legitimately import
		`setup/` to exercise the install chain, and `api/` may take shared
		constants. What is forbidden is `api/` reaching into a provisioning
		function at request time, because `after_install` is idempotent for a
		reason that has nothing to do with serving a request.
		"""
		violations = []
		for path in self._py_files("api", "construction_ph/doctype", "construction_ph/page", "www"):
			if self._is_test(path):
				continue
			source = path.read_text(encoding="utf-8")
			if "from erpnext_construction_ph.setup" not in source and "from ..setup" not in source:
				continue
			# Collect the imported setup symbols, then look for a CALL to one.
			names = re.findall(
				r"from\s+(?:erpnext_construction_ph\.)?setup(?:\.\w+)*\s+import\s+([^\n]+)", source
			)
			called = []
			for group in names:
				# Strip a trailing comment and whitespace. Without this the symbol
				# comes back as "name  # comment", the call search never matches,
				# and the guard passes while the violation is in the tree -- a
				# boundary check that cannot fail is the same defect as a blocklist
				# too narrow to close its class.
				cleaned = group.split("#", 1)[0]
				for raw in cleaned.split(","):
					sym = raw.strip().split(" as ")[0].strip()
					if sym and re.search(r"\b%s\s*\(" % re.escape(sym), source):
						called.append(sym)
			if called:
				violations.append("%s calls %s" % (path.relative_to(APP), ", ".join(sorted(set(called)))))
		self.assertEqual(
			violations, [],
			"api/ calling setup/ functions at runtime inverts the dependency the "
			"spec fixes: %s" % violations,
		)

	# ---- rule 2: billing_engine must not bypass submit() -------------------

	def test_billing_engine_does_not_bypass_the_submit_flow(self):
		"""No `db_insert`, no `flags.ignore_permissions` to force a write.

		`inv.submit()` is the legal path and is expected; what is forbidden is
		short-circuiting it, which is how a Sales Invoice ends up visible without
		its GL entries -- and the spec's audit-trail rule says every submitted doc
		produces them.
		"""
		source = (APP / "api" / "billing_engine.py").read_text(encoding="utf-8")
		for forbidden in ("db_insert", "flags.ignore_permissions", "flags.ignore_mandatory"):
			self.assertNotIn(
				forbidden, source,
				"billing_engine uses %s, which bypasses the Document.submit() flow "
				"the spec forbids bypassing" % forbidden,
			)
		self.assertIn(".submit()", source, "the invoice should be submitted through Frappe, not written directly")

	# ---- rule 4: no direct SQL from JS ------------------------------------

	def test_no_assets_reach_the_database_directly(self):
		"""Desk pages go through `frappe.call()`.

		Direct SQL from a browser asset bypasses permissions entirely, so this is
		the rule with the widest blast radius: a single `frappe.db.sql` in a
		workspace JS file hands any logged-in user whatever it selects.
		"""
		offenders = []
		patterns = (
			re.compile(r"frappe\.db\.(sql|get_value|set_value|get_list|get_all)"),
			re.compile(r"new\s+frappe\.DBRequest"),
			re.compile(r"\bconn\s*\("),
		)
		for sub in JS_DIRS:
			root = APP / sub
			if not root.exists():
				continue
			for path in sorted(root.rglob("*.js")):
				if path.name.endswith(".min.js"):
					continue
				source = path.read_text(encoding="utf-8", errors="replace")
				for pattern in patterns:
					if pattern.search(source):
						offenders.append("%s matches %s" % (path.relative_to(APP), pattern.pattern))
		self.assertEqual(
			offenders, [],
			"Desk/portal assets must use frappe.call(), not the database directly: %s" % offenders,
		)

	# ---- rule 3: geometry stays out of MariaDB ----------------------------

	def test_the_geometry_rule_is_covered_elsewhere(self):
		"""Point at the test that owns it, so the rule is not read as unowned.

		"BIM geometry (.frag) stored on S3 only; IFC GUIDs and properties in
		MariaDB" is asserted by `test_geometry_bytes_never_reach_mariadb` in
		`test_bim_convert.py`. Naming it here is worth more than restating the
		rule, because a boundary with no owner is a boundary that decays.
		"""
		source = (APP / "construction_ph" / "test" / "test_bim_convert.py").read_text(encoding="utf-8")
		self.assertIn(
			"test_geometry_bytes_never_reach_mariadb", source,
			"the geometry boundary lost its test; .frag bytes must never reach MariaDB",
		)
