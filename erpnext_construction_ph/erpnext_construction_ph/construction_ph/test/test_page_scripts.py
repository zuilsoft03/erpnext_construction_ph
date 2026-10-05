"""Every Desk Page in this app must be REACHABLE, not merely present.

**All five pages rendered blank, with no error anywhere, behind gates ticked
`[x]`.** Two independent defects stacked:

1. A page's script lives at `construction_ph/page/<name>/<name>.js`, OUTSIDE
   `public/`. `assets/erpnext_construction_ph` is a symlink to `public/`, so
   Frappe cannot serve it, and this Frappe's Page doctype has no
   `content`/`script`/`style` fields (D-039) to inject it another way. The
   scripts are now COPIES under `public/js/pages/`, kept in step by
   `ops/sync_page_scripts.py`.

2. The registration targeted an API this Desk does not use. It now goes through
   `frappe.standard_pages[<route>]`, the FIRST branch of
   `frappe.views.pageview.with_page` -- see the test docstring for the full chain.

Neither defect raised an error. #1 is a 404 on an asset nothing references; #2 is
an early `return` in the Desk router. Both look identical to a user: a blank page
with a clean console.

Found by driving a real browser as an authenticated Administrator, which is the
only way to see either. Every server-side test was green throughout, and two gate
CHECK strings said to "browse to ... and assert" -- so those gates carried
closures nothing had performed.

These checks are the cheap proxy, not a substitute for the browser.
"""
import os
import re

import frappe
from frappe.tests import IntegrationTestCase

APP = frappe.get_app_path("erpnext_construction_ph")
PAGES = os.path.join(APP, "construction_ph", "page")
PUBLIC_PAGES = os.path.join(APP, "public", "js", "pages")


def page_names():
	if not os.path.isdir(PAGES):
		return []
	return sorted(
		d for d in os.listdir(PAGES)
		if os.path.isdir(os.path.join(PAGES, d))
		and os.path.isfile(os.path.join(PAGES, d, f"{d}.js"))
	)


class TestDeskPageReachability(IntegrationTestCase):
	def test_every_page_registers_in_standard_pages_under_its_route_name(self):
		"""The defect this replaces: five pages rendered blank, with no error.

		`frappe.views.pageview.with_page(name)` resolves a route through four
		branches (read out of the served desk bundle):

		  1. `frappe.standard_pages[name]`        <- the one that works here
		  2. `locals.Page[name] && locals.Page[name].script`
		  3. `localStorage["_page:"+name]`, ONLY when `developer_mode != 1`
		     (this site has developer_mode = 1, so branch 3 is dead)
		  4. `frappe.call("frappe.desk.desk_page.getpage", {name})`

		The old code targeted none of them. Branch 4 is called with the ROUTE name
		("bim-workspace") while the Page record is "bim_workspace", so it 404s --
		measured: `getpage?name=bim-workspace` -> 404, `?name=bim_workspace` -> 200.
		`frappe.views.Page` then hits `frappe.show_not_found` and RETURNS, which
		renders an empty page with NO console error.

		`frappe.pages[name]` cannot be used either: the Desk OVERWRITES that key
		with its own wrapper (`frappe.pages[window.page_name] = this.wrapper`),
		destroying any handler placed there. The old defensive `|| {}` guard
		prevented a TypeError and nothing more, which is why it sat in the tree for
		sessions while every page stayed blank.
		"""
		offenders = []
		for name in page_names():
			src = open(os.path.join(PAGES, name, f"{name}.js"), encoding="utf-8").read()
			route = name.replace("_", "-")
			entry = f'frappe.standard_pages["{route}"]'
			if entry not in src:
				offenders.append(f"{name} (expected {entry})")
		self.assertEqual(
			offenders, [],
			f"pages not registered in standard_pages: {offenders}. The Desk resolves "
			"a page route through frappe.standard_pages first; nothing else in this "
			"app is reachable, and a page that is not reachable renders blank with "
			"no console error. See the module docstring.",
		)

	def test_every_page_script_is_served_from_public(self):
		"""The defect: the script exists but no URL can reach it."""
		hooks = open(os.path.join(APP, "hooks.py"), encoding="utf-8").read()
		offenders = []
		for name in page_names():
			entry = f"/assets/erpnext_construction_ph/js/pages/{name}.js"
			if entry not in hooks:
				offenders.append(f"{name} (not in app_include_js)")
				continue
			if not os.path.isfile(os.path.join(PUBLIC_PAGES, f"{name}.js")):
				offenders.append(f"{name} (no copy under public/js/pages/)")
		self.assertEqual(
			offenders, [],
			f"pages that cannot load: {offenders}. `assets/erpnext_construction_ph` "
			"symlinks to `public/`, and a page script outside it is unservable. Copy "
			"it with `python ops/sync_page_scripts.py` and add it to app_include_js.",
		)

	def test_the_served_copies_have_not_drifted(self):
		"""A copy that silently diverges reproduces the original bug one level down."""
		drift = []
		for name in page_names():
			src = os.path.join(PAGES, name, f"{name}.js")
			dst = os.path.join(PUBLIC_PAGES, f"{name}.js")
			if not os.path.isfile(dst):
				drift.append(f"{name} (missing)")
			elif open(src, "rb").read() != open(dst, "rb").read():
				drift.append(f"{name} (differs)")
		self.assertEqual(
			drift, [],
			f"public/js/pages copies have drifted: {drift}. The served copy is what "
			"runs, so the page you read in the source is not the page that loads. "
			"Re-run `python ops/sync_page_scripts.py`.",
		)

	def test_every_page_has_a_page_record_with_a_role(self):
		"""A page folder with no Page record resolves to a permission error."""
		for name in page_names():
			self.assertTrue(
				frappe.db.exists("Page", name),
				f"no Page record for {name}; the route cannot resolve",
			)
			page = frappe.get_doc("Page", name)
			self.assertTrue(
				page.roles, f"{name} has no roles, so nobody can open it"
			)


class TestBOQListing(IntegrationTestCase):
	def test_list_boqs_returns_only_draft_or_approved(self):
		"""The BOQ Worksheet page needs a list; there was no endpoint to call.

		`new frappe.ui.LinkField(...)` -- the only way the page could choose a BOQ --
		does not exist in this Frappe, so the page had no way to name its target and
		no server call to fall back on. `list_boqs` is that call.
		"""
		from erpnext_construction_ph.api import boq_matrix

		rows = boq_matrix.list_boqs()
		self.assertIsInstance(rows, list)
		allowed = {"Draft", "Approved"}
		for row in rows:
			self.assertIn(
				row.get("status"), allowed,
				f"list_boqs returned a {row.get('status')!r} BOQ; rows cannot be "
				"pasted into a Frozen one",
			)
			self.assertIn("name", row)


class TestDeskPageRuntimeAPIs(IntegrationTestCase):
	"""APIs this Frappe does not have. Calling one is a page-killing TypeError.

	Measured in Chromium on this bench, not inferred:
	  * `jquery.min.js` 404s and `frappe.$` is undefined, so every `$()` throws.
	  * `frappe.ui` exposes no `LinkField`, `DataField`, `TextInput`, `Select`,
	    `Checkbox` or `FileInput`.

	And the Desk SWALLOWS a throw from a page builder, so each of these produced a
	page that rendered minus its only two controls, with a clean-looking console.
	"""

	MISSING_CTORS = (
		"LinkField", "DataField", "TextInput", "Select", "Checkbox", "FileInput",
	)

	def _sources(self):
		for name in page_names():
			yield name, open(os.path.join(PAGES, name, f"{name}.js"), encoding="utf-8").read()

	def _code(self, src):
		"""Strip // and /* */ comments so prose about an API is not a use of it."""
		src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
		return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())

	def test_no_page_uses_jquery(self):
		offenders = [
			name for name, src in self._sources()
			if re.search(r"[^\w.$]\$\(", self._code(src))
		]
		self.assertEqual(
			offenders, [],
			f"pages calling $(): {offenders}. jquery.min.js 404s on this bench and "
			"`frappe.$` is undefined, so each call throws and the control it was "
			"about to read is never read. Use document.getElementById.",
		)


	def test_no_page_falls_back_to_writing_document_body(self):
		"""`document.body` is the whole Desk, not a page container.

		Measured: this Desk renders NO `.page-container`, NO `.layout-main` and NO
		`.layout-main-section`; the only page container is `.page-body`. A chain
		ending `|| document.body` therefore resolved all the way down, and
		`wrapper.innerHTML = ...` replaced the entire layout -- leaving three
		`Cannot read properties of null (reading 'insertAdjacentHTML')` errors from
		Frappe's own Desk code running on DOM that had just been removed.

		Each candidate must be checked for EXISTENCE before being written to, so a
		class rename degrades to "the page did not render" instead of "the Desk is
		destroyed".
		"""
		offenders = []
		for name, src in self._sources():
			code = self._code(src)
			if not code.strip():
				continue
			# A bare `|| document.body` / `?? document.body` fallback for the wrapper.
			if re.search(r"\|\|\s*document\.body|\?\?\s*document\.body", code):
				offenders.append(f"{name}: falls back to document.body")
		self.assertEqual(
			offenders, [],
			f"pages falling back to document.body: {offenders}. Writing innerHTML "
			"to document.body replaces the whole Desk. Select an existing page "
			"container instead, and verify each candidate exists before writing.",
		)

	def test_no_page_constructs_an_api_this_frappe_lacks(self):
		offenders = []
		for name, src in self._sources():
			code = self._code(src)
			for ctor in self.MISSING_CTORS:
				if f"new frappe.ui.{ctor}" in code:
					offenders.append(f"{name}: new frappe.ui.{ctor}()")
		self.assertEqual(
			offenders, [],
			f"pages constructing absent APIs: {offenders}. `frappe.ui` does not "
			"expose these in this Frappe; the call throws, Desk swallows it, and the "
			"page renders without the control it was supposed to build.",
		)
