# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH5-01 gate, server half: a document round-trips through ``frappe.call``.

The editor asset's lossless round-trip is proven on the host by
``ops/tmp-ph5-01/roundtrip.js`` (17 cases, idempotent *and* lossless). This
file proves the other half: **the server does not change the document in
transit.**

Every case here is a shape that a "helpful" server-side tidy-up would damage --
nested marks, entities, a bare ``&``, text between an open mark and a new one,
Unicode currency and names. Those are exactly the documents a Philippine
construction contract actually contains, and they are what the gate is about.
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.api import doc_editor

# Realistic contract-template markup.
RICH = (
	"<h2>3.1 Bill of Quantities</h2>"
	"<p>This Contract is governed by <strong>Republic Act No. 11032 "
	'<a href="https://www.dpwh.gov.ph/ciap102">CIAP 102</a>.</p>'
	"<p><strong>Scope <em>and rate</em></strong> are fixed at issuance.</p>"
	"<ul><li>Cement</li><li>Sand</li></ul>"
	"<p>Price &lt; 5 &amp; quantity &gt; 0.</p>"
	"<blockquote>As-built quantities govern.</blockquote>"
)

# Each of these has broken under some plausible "cleanup" at least once.
TRICKY = {
	"nested marks": "<p><strong>a<em>b<u>c</u></em></strong></p>",
	"text between marks": "<p><strong>Scope <em>and rate</em></strong></p>",
	"entities": "<p>Price &lt; 5 &amp; qty &gt; 0</p>",
	"bare ampersand": "<p>Terms & Conditions</p>",
	"link with query": '<p><a href="https://example.com/a?b=1&amp;c=2">x</a></p>',
	"self closing hr": "<p>a</p><hr/><p>b</p>",
	"unicode": "<p>Total ₱1,250,000.00 — Guardián, Andrés</p>",
	"pre with markup chars": "<pre>if (a &lt; b &amp;&amp; c &gt; d)</pre>",
	"unmatched close": "<p>a</strong>b</p>",
	"empty body": "",
	"whitespace only": "   \n\t ",
}


class TestDocEditorRoundTrip(IntegrationTestCase):
	"""Markup survives the trip to the server and back, byte for byte."""

	# ---- the round-trip ---------------------------------------------------

	def test_the_body_survives_the_round_trip(self):
		"""The gate's claim, stated directly."""
		self.assertEqual(doc_editor.normalize_document(RICH)["html"], RICH)

	def test_every_tricky_shape_survives(self):
		"""The shapes a cleanup would damage, one assertion each.

		Loop rather than a pile of near-identical methods, because a loop reports
		which shape broke -- a stack of identical assertions reports only that
		something did.
		"""
		for name, body in sorted(TRICKY.items()):
			with self.subTest(shape=name):
				self.assertEqual(doc_editor.normalize_document(body)["html"], body)

	def test_the_round_trip_is_a_fixed_point(self):
		"""Twice through is the same as once.

		A server pass that is not idempotent is a pass that accumulates damage
		across saves, and it only shows up on the third revision.
		"""
		once = doc_editor.normalize_document(RICH)["html"]
		twice = doc_editor.normalize_document(once)["html"]
		thrice = doc_editor.normalize_document(twice)["html"]
		self.assertEqual(once, twice)
		self.assertEqual(twice, thrice)

	def test_a_string_is_not_coerced_into_something_else(self):
		"""The endpoint returns a dict, so the body must not be the whole reply.

		Guards the shape: a client reading `message` as the body would otherwise
		silently store a dict's repr.
		"""
		result = doc_editor.normalize_document("<p>x</p>")
		self.assertIsInstance(result, dict)
		self.assertEqual(result["html"], "<p>x</p>")

	# ---- reporting, not removal ------------------------------------------

	def test_an_unknown_tag_is_refused(self):
		"""**INVERTED DELIBERATELY 2026-09-28 -- and this is the record of why.**

		This test used to assert that an unrecognised tag was *reported and passed
		through unchanged*, on the reasoning that dropping it would hide a loss.
		That reasoning was right about a lossy round-trip and wrong about what
		that endpoint is: its output becomes a ``template_html`` that is
		immutable from creation, so "pass it through and mention it" means
		storing a ``<marquee>`` -- or a ``<script>`` -- permanently, in a contract
		template, for everyone. Reporting and continuing is not a safe middle
		ground; it is the worst of both, because the caller is told and proceeds
		anyway.

		So the model is inverted: the schema is an ALLOWLIST, and anything outside
		it is refused by name. This is a deliberate change to a passing assertion,
		not an edit to make a test go green, and the control is proven by mutation
		below.
		"""
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document("<p>a</p><marquee>b</marquee>")
		self.assertIn("marquee", str(caught.exception))

	def test_known_tags_are_accepted(self):
		"""The allowlist must accept everything the editor model produces.

		Every tag in RICH is one the model serialises, so if the server refused
		any of them the editor would store less than the user typed -- the
		over-filtering half of the same control.
		"""
		self.assertEqual(doc_editor.normalize_document(RICH)["html"], RICH)

	def test_a_void_tag_does_not_look_unknown(self):
		"""`</br>` is sloppy but not an unknown element."""
		self.assertEqual(doc_editor.normalize_document("<p>a<br/>b</p>")["unknown_tags"], [])

	def test_emptiness_is_reported(self):
		"""A blank draft is a legitimate state, not an error."""
		self.assertTrue(doc_editor.normalize_document("")["empty"])
		self.assertTrue(doc_editor.normalize_document("   \n\t ")["empty"])
		self.assertFalse(doc_editor.normalize_document("<p>x</p>")["empty"])

	def test_a_none_body_is_treated_as_empty(self):
		"""An editor that never fired must not produce a 500."""
		self.assertEqual(doc_editor.normalize_document(None)["html"], "")

	# ---- inputs -----------------------------------------------------------

	def test_a_non_string_body_is_refused(self):
		"""Guessing at a dict or a list would be worse than refusing."""
		for bad in ({"html": "<p>x</p>"}, ["<p>x</p>"], 42, True):
			with self.assertRaises(frappe.ValidationError):
				doc_editor.normalize_document(bad)

	# ---- executable markup is refused, not stripped ------------------------

	def test_a_script_tag_is_refused(self):
		"""**The body is immutable once stored, so a script is permanent.**

		`template_html` is frozen at creation (D-033). A script tag saved by
		anyone with editor access is stored XSS against everyone who ever opens
		that contract template, and it cannot be removed afterwards.
		"""
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document("<p>ok</p><script>fetch('/api/x')</script>")
		self.assertIn("script", str(caught.exception))

	def test_an_event_handler_attribute_is_refused(self):
		"""`onerror` on an image is the payload that does not need a script tag."""
		with self.assertRaises(frappe.ValidationError):
			doc_editor.normalize_document('<p><img src="x" onerror="steal()"></p>')

	def test_a_javascript_url_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			doc_editor.normalize_document('<p><a href="javascript:steal()">click</a></p>')

	def test_the_refusal_names_what_it_found(self):
		"""A refusal that says only "unsafe" leaves the author guessing."""
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document("<script>a</script><p onclick='b'>c</p>")
		message = str(caught.exception)
		self.assertIn("script", message)
		self.assertIn("onclick", message)

	def test_ordinary_markup_is_still_accepted(self):
		"""The refusal must not become a general-purpose filter.

		Everything in TRICKY -- nested marks, entities, links, lists, Unicode --
		is legitimate contract text and must still round-trip. A sanitiser that
		quietly dropped real content would be a different defect.
		"""
		for name, body in sorted(TRICKY.items()):
			with self.subTest(shape=name):
				self.assertEqual(doc_editor.normalize_document(body)["html"], body)

	def test_the_word_script_in_prose_is_not_a_tag(self):
		"""Refusing on the substring would refuse real contract language.

		Construction contracts talk about scripts, sub-scripts and scripts
		required. The check looks at tags, not at text.
		"""
		body = "<p>The Contractor shall submit the script of works within 7 days.</p>"
		self.assertEqual(doc_editor.normalize_document(body)["html"], body)

	# ---- the allowlist closes the class, it does not enumerate it -----------

	def test_an_unlisted_event_handler_is_refused(self):
		"""**The gap a blocklist cannot see.**

		An earlier version named eleven handlers -- onerror, onclick, onload and
		so on -- and the mutation proved those eleven were load-bearing while
		proving nothing about the other ~69. `onmouseenter` sailed straight
		through. The rule is now "any attribute whose name starts with on",
		matched by shape rather than by name, so there is no list to be
		incomplete. This test uses a handler that was NOT in the old list.
		"""
		for handler in ("onmouseenter", "ondblclick", "onpointerdown", "ontoggle", "onpaste", "ondrop"):
			with self.subTest(handler=handler):
				with self.assertRaises(frappe.ValidationError) as caught:
					doc_editor.normalize_document('<p><img src="x" %s="steal()"></p>' % handler)
				self.assertIn(handler, str(caught.exception))

	def test_an_unlisted_attribute_is_refused(self):
		"""Attributes are allowlisted too, not just tags."""
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document('<p style="position:fixed">x</p>')
		self.assertIn("style", str(caught.exception))

	def test_a_known_attribute_is_accepted(self):
		"""`href` is how a link works; refusing it would break the document."""
		body = '<p><a href="https://example.com/ciap">CIAP 102</a></p>'
		self.assertEqual(doc_editor.normalize_document(body)["html"], body)

	# ---- unquoted attribute values ----------------------------------------

	def test_an_unquoted_event_handler_is_refused(self):
		"""**The hole a quote-requiring scanner leaves open.**

		`<img src=x onerror=alert(1)>` is valid HTML and is exactly how a
		payload is written by hand. The earlier attribute pattern required a
		quoted value, so this markup matched nothing at all -- not the `on*`
		rule, not the allowlist -- and passed through untouched. The scanner now
		reads each tag's attribute region and takes only the name before `=`,
		which is immune to how the value is quoted.
		"""
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document("<img src=x onerror=alert(1)>")
		self.assertIn("onerror", str(caught.exception))

	def test_an_unquoted_attribute_outside_the_allowlist_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document("<p style=color:red>x</p>")
		self.assertIn("style", str(caught.exception))

	def test_an_unquoted_handler_is_refused_without_touching_the_allowlist(self):
		"""Proves the `on*` rule fires on its own, quoted or not.

		The two checks overlap by design, so this pair is what shows the `on*`
		rule is reachable and not merely shadowed by the allowlist.
		"""
		with self.assertRaises(frappe.ValidationError) as caught:
			doc_editor.normalize_document("<p onmouseover=go() class=lead>x</p>")
		message = str(caught.exception)
		self.assertIn("onmouseover", message)
		self.assertNotIn("class", message)

	# ---- the client and the server must agree ------------------------------

	def test_the_client_schema_is_accepted_by_the_server(self):
		"""**The two sides share one schema, and nothing enforced that.**

		`public/js/editor/tiptap.js` parses and serialises a block/mark schema,
		and `KNOWN_TAGS` here is the server's copy of it. Two hand-maintained
		lists drift, and the failure mode is the worst kind: the client's
		`renderNode` default branch emits `<div>` for a block outside its
		schema, `div` was absent here, and **any document carrying an unknown
		block round-tripped to a hard refusal** -- precisely the round-trip loss
		this gate forbids.

		So the check is behavioural rather than a literal comparison of two
		lists: every tag the client's serialiser can actually EMIT is fed to
		`normalize_document`, and any refusal fails here. Adding `<mark>` to the
		editor without touching the server breaks this test the same day.
		"""
		import pathlib
		import re

		js = (
			pathlib.Path(__file__).resolve().parents[2]
			/ "public"
			/ "js"
			/ "editor"
			/ "tiptap.js"
		).read_text(encoding="utf-8")
		# Tags appear as literal open/close tags in the serialiser.
		emitted = set(re.findall(r'"</?([a-z][a-z0-9]*)[ >]', js))
		# **Fail closed.** A cross-check that silently shrinks is a check that
		# passes while testing nothing -- the exact failure the comment in
		# ops/ledger_check.py warns about. So the extraction is itself asserted:
		# a refactor of the client's serialiser that empties or thins the set must
		# fail HERE rather than leaving a green guard over an empty list.
		self.assertGreaterEqual(
			len(emitted), 12,
			"only %d tags extracted from tiptap.js; the pattern is stale and this "
			"guard would pass while testing nothing" % len(emitted),
		)
		# `div` is the one that drifted, and it is the client's fallback for a
		# block outside its schema -- anchor on it explicitly.
		self.assertIn("div", emitted, "the client's fallback container vanished; the pattern is stale")
		for tag in sorted(emitted):
			with self.subTest(tag=tag):
				doc_editor.normalize_document(f"<{tag}>body</{tag}>")  # must not raise
