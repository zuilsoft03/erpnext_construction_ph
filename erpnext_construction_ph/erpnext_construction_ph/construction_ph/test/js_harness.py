# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Shared plumbing for the JavaScript-module tests.

Four things every one of these tests needs, and each of which was written by hand
in every test until the copies disagreed:

  * **node resolution.** `node` is installed in this bench but is NOT on the PATH a
    `bench run-tests` subprocess gets -- it lives under /home/frappe/.nvm. An
    earlier version hardcoded `"node"`, so the tests passed when run by hand on the
    host and died with a bare `OSError: 2` inside the container: a green/red split
    that depended on who ran them.
  * **the DOM shim path**, which lives beside this file rather than in `public/`.
  * **`run_js`**, which loads a module by stripping `export` and evaluating it, so
    no bundler is needed.
  * **the async IIFE wrapper.** Bodies that `await` make node treat the eval as an
    ES module, which then collides with `require` in the harness and fails with
    ERR_AMBIGUOUS_MODULE_SYNTAX -- an error naming neither the test nor the cause.

One copy, so the next module test inherits all four rather than rediscovering them.
"""

import glob
import json
import os
import pathlib
import shutil
import subprocess
import textwrap


def _app_root():
	"""The bench `apps/` directory, found by looking rather than by counting.

	Counting parents is wrong for this app in two different directions: on the
	host the repo nests one level deeper than it does in the bench, and inside the
	bench the app itself is a nested directory (D-011). So walk up until the
	directory that actually contains `erpnext_construction_ph/public` is found.
	"""
	here = pathlib.Path(__file__).resolve()
	for parent in here.parents:
		# The rule has to hold in BOTH layouts: the host repo nests one level
		# deeper than the bench, and inside the bench the app is itself a nested
		# directory (D-011). Keying on `public` is not enough -- the inner app
		# directory has a `public` of its own, so the search stops there and every
		# module path comes out doubled. Keying on the app's OWN test directory
		# matches exactly one ancestor in each layout.
		if (parent / "erpnext_construction_ph" / "construction_ph" / "test" / "dom_shim.js").is_file():
			return parent
	raise AssertionError(f"could not locate the apps root above {here}")


APP_ROOT = _app_root()
import textwrap

SHIM = pathlib.Path(__file__).resolve().parent / "dom_shim.js"


def find_node():
	"""The `node` binary, wherever this environment actually keeps it."""
	found = shutil.which("node") or shutil.which("nodejs")
	if found:
		return found
	for pattern in (
		"/home/frappe/.nvm/versions/node/*/bin/node",
		"/usr/local/n/versions/node/*/bin/node",
	):
		hits = sorted(glob.glob(pattern))
		if hits:
			return hits[-1]
	return None


NODE = find_node()


def require_node(testcase):
	"""Skip honestly when node is absent.

	Skipped, never silently passed. The browser probes under
	`ops/tmp-ph3-05/` are the primary evidence for these rows; this harness is what
	runs on every `bench run-tests`, and a test that quietly passes without running
	would report coverage it does not have.
	"""
	if not NODE:
		testcase.skipTest(
			"node is not available in this runtime; the behaviour is asserted in a "
			"real browser by the probes under ops/tmp-ph3-05/"
		)
	if not SHIM.exists():
		testcase.skipTest(f"the shared DOM shim is missing: {SHIM}")


def module_path(*parts):
	"""A module under the app, resolved by SEARCH rather than by arithmetic.

	Counting parents to find the app root is fragile here, and it failed in a way
	worth designing out rather than debugging again: this app's layout differs
	between the host repo and the bench, the app is itself a nested directory
	(D-011), and the two runners resolve the import root differently. The result was
	a module path with one extra `erpnext_construction_ph` in it, from a source file
	that was provably correct.

	So: try the natural join, and if that misses, walk APP_ROOT and its parents
	until one produces a file that exists. Every candidate is checked for existence,
	so this cannot resolve to a plausible-looking wrong path -- which is the failure
	mode that matters, because a wrong path that exists is a test measuring the wrong
	file.
	"""
	direct = APP_ROOT.joinpath(*parts)
	if direct.exists():
		return direct
	for base in (APP_ROOT, *APP_ROOT.parents):
		candidate = base.joinpath(*parts)
		if candidate.exists():
			return candidate
	raise AssertionError(
		f"module not found: {parts!r}\n  tried under: {APP_ROOT} and its parents"
	)


def run_js(body, module, exports, *, env=None):
	"""Run `body` in Node against `module`, with the shared DOM shim installed.

	:param module:    path to the ES module under test
	:param exports:   names to pull out of it
	"""
	require_node_guard = (NODE and SHIM.exists())
	if not require_node_guard:
		raise AssertionError("run_js called without node or the shim; call require_node() in setUp")

	harness = (
		"const { installDom, loadModule } = require(process.env.SHIM_PATH);\n"
		"installDom();\n"
		f"module.exports = loadModule(process.env.MODULE_PATH, {json.dumps(list(exports))});\n"
	)
	# Wrapped, because these bodies await. Without the wrapper node sees top-level
	# `await`, decides the eval is an ES module, and then chokes on `require`.
	wrapped = "(async () => {\n" + harness + textwrap.dedent(body) + "\n})();"
	proc = subprocess.run(
		[NODE, "-e", wrapped],
		capture_output=True,
		text=True,
		timeout=60,
		env={
			**os.environ,
			"MODULE_PATH": str(module),
			"SHIM_PATH": str(SHIM),
			**(env or {}),
		},
	)
	if proc.returncode != 0:
		raise AssertionError(f"harness failed:\n{proc.stderr[-2500:]}")
	out = proc.stdout.strip().splitlines()
	if not out:
		raise AssertionError(f"harness produced no output:\n{proc.stderr[-1500:]}")
	return json.loads(out[-1])
