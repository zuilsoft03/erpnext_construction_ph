# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The committed TipTap bundle must be the one the app's own 2.27.3 produced.

PH1-10. This app's `node_modules` carries @tiptap/core 2.27.3 while five other
apps in the same bench carry 3.20-3.27. A bundle built from the wrong tree is a
different MAJOR version of the same library, and the failure it produces --
"Schema is missing its top node type ('doc')" -- does not say which tree it came
from. So the build emits an esbuild `--metafile` and this test holds it to account.

Without this, `ops/tmp-ph1-10/meta.json` is a file nobody reads: it could go
stale, or be replaced by one from another app's tree, and nothing would notice.
That is the same "renders but does nothing" trap the row was written about,
one layer up.

The repo ships no package.json, so nothing else pins that 2.27.3. This is the
only machine-checkable record of it.
"""

import json
import os
import unittest

import frappe


# Anything under another app's node_modules is a different major version.
OWN_PREFIX = "apps/erpnext_construction_ph/"



class TestTiptapBundleProvenance(unittest.TestCase):
	"""The metafile must show the bundle came from THIS app's tree."""

	def setUp(self):
		# Both files live inside the app package, so they exist in the deployed tree and
		# not only in the working copy. A record that existed only in the repo could not
		# be checked by a test that runs inside the app.
		self.app = frappe.get_app_path("erpnext_construction_ph")
		self.meta_path = os.path.join(self.app, "tools", "tiptap.meta.json")
		self.bundle_path = os.path.join(
			self.app, "public", "js", "editor", "tiptap.bundle.js"
		)
		# Loaded here, not inside a test: unittest builds a fresh instance per test, so a
		# value assigned in a test method is absent from every other one.
		self.assertTrue(
			os.path.isfile(self.meta_path),
			"tools/tiptap.meta.json is missing. It is the only record of which @tiptap tree "
			"the committed bundle was built from, and the repo ships no package.json to say "
			"so. Re-run ops/tmp-ph1-10/build.sh.",
		)
		with open(self.meta_path, encoding="utf-8") as handle:
			self.meta = json.load(handle)
		self.assertIn("inputs", self.meta, "tools/tiptap.meta.json is not an esbuild metafile")



	def test_the_metafile_is_committed(self):
		"""Absent metafile = the provenance record was lost, which is the row's original sin."""
		self.assertTrue(
			os.path.isfile(self.meta_path),
			"tools/tiptap.meta.json is missing. It is the only record of which @tiptap tree the "
			"committed bundle was built from, and the repo ships no package.json to say so.",
		)
		with open(self.meta_path, encoding="utf-8") as handle:
			self.meta = json.load(handle)
		self.assertIn("inputs", self.meta, "not an esbuild metafile")

	def test_every_tiptap_input_came_from_this_app(self):
		"""A single foreign @tiptap input means the bundle is the wrong major version."""
		inputs = [p for p in self.meta["inputs"] if "@tiptap/" in p]
		self.assertTrue(inputs, "the metafile lists no @tiptap inputs at all")
		foreign = [p for p in inputs if not p.startswith(OWN_PREFIX)]
		self.assertEqual(
			[],
			foreign,
			"the bundle resolved @tiptap from another app's node_modules: "
			f"{foreign[:3]}. Other apps in this bench carry @tiptap 3.20-3.27, so the "
			"bundle would be a different MAJOR version than the 2.27.3 this row is about.",
		)

	def test_the_bundle_this_app_serves_exists(self):
		"""It is in app_include_js, so a missing file is a 404 on every Desk page."""
		self.assertTrue(
			os.path.isfile(self.bundle_path),
			f"{self.bundle_path} is missing but is listed in app_include_js, "
			"so every Desk page would request it and get a 404",
		)

	def test_the_metafile_describes_a_bundle_output(self):
		"""The record must actually be about this bundle, not a stale build."""
		outputs = self.meta.get("outputs", {})
		self.assertTrue(outputs, "the metafile records no outputs, so it describes no build")
		# esbuild keys outputs by the path it was given, which varies with the cwd the
		# build ran in, so match on the file name rather than a fixed path.
		names = [os.path.basename(k) for k in outputs]
		self.assertIn(
			"tiptap.bundle.js",
			names,
			f"the metafile describes {names}, not the bundle this app serves",
		)
		entry = next(v for k, v in outputs.items() if os.path.basename(k) == "tiptap.bundle.js")
		self.assertIn("bytes", entry, "the output entry carries no size, so it cannot vouch for a build")

	def test_the_committed_bundle_is_the_build_the_metafile_describes(self):
		"""The tie between the record and the artifact. Without it, meta.json goes stale silently.

		Safe to compare byte counts only because `.gitattributes` marks the bundle
		`-text`. Otherwise git converts LF to CRLF on checkout, the size shifts, and
		this would fail for a reason that has nothing to do with provenance.
		"""
		entry = next(
			v
			for k, v in self.meta["outputs"].items()
			if os.path.basename(k) == "tiptap.bundle.js"
		)
		self.assertEqual(
			entry["bytes"],
			os.path.getsize(self.bundle_path),
			"the committed bundle is not the build ops/tmp-ph1-10/meta.json describes. "
			"Either the bundle was rebuilt without refreshing the metafile, or the file was "
			"edited after the build. Re-run ops/tmp-ph1-10/build.sh and commit both.",
		)

