# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""PH5-01: the server half of the editor's document round-trip.

The gate's claim is that **a document typed in the editor round-trips through
`frappe.call` without losing markup**. This file is the half a browser cannot
prove, and it is deliberately stateless.

**Why stateless.** ``ConstructionDocumentTemplate.before_insert`` stamps
``issued_on = now()`` on every new document -- deliberately, because stamping it
only on a later transition left the lock disengaged for a brand-new template. A
template body is therefore frozen from the moment it exists, and there is no
editable window to hang a save endpoint off. Building one would mean either
weakening that invariant or inventing a revision workflow the gate never asked
for. The gate asks about the *round-trip*, so that is what this provides.

**What the client is protected from.** A whitelisted document endpoint is the
natural place for a "helpful" tidy-up -- stripping attributes, collapsing
whitespace, escaping entities. Every one of those loses markup, and the loss
shows up as a document that renders slightly wrong weeks later with no trace of
where it went. So ``normalize_document`` returns the markup **unchanged**, and
the test proves that by asserting the output equals the input.

**Except for two things, and they are not tidying-up.** ``<script>`` and inline
event handlers (``onclick``, ``onerror``, ...) are **refused**, not stripped.
This endpoint's output becomes a ``template_html`` that is immutable from the
moment it is created (see D-033), so a script tag saved by anyone with editor
access is stored XSS against everyone who ever opens that contract template --
frozen, permanent, and executed in a page that renders it. Silently removing the
tag instead of refusing would be worse than useless: the author would believe
their document was saved intact while the damage was hidden. Refusal is the only
answer that tells the truth.
"""

import re

import frappe
from frappe import _

# Elements the editor's model owns. A body containing something outside this set
# is reported rather than quietly stored, because an unrecognised tag is usually
# the sign that markup is about to be dropped somewhere downstream.
KNOWN_TAGS = {
	"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
	"ul", "ol", "li", "blockquote", "pre",
	"strong", "b", "em", "i", "u", "s", "del", "code", "a",
	# `div` is the client's fallback container for a block outside its schema:
	# `renderNode`'s default branch emits it so an unrecognised block is
	# PRESERVED rather than dropped. It is here because the server must accept
	# everything the client can emit -- when it did not, a document carrying an
	# unknown block round-tripped to a hard refusal, which is the round-trip loss
	# this gate exists to forbid. `test_the_client_schema_is_accepted_by_the_server`
	# is what keeps the two lists from drifting apart again.
	"div",
}


# The editor's schema is an ALLOWLIST. A blocklist of executable tags and
# named event handlers is bypassable by construction: there are ~80 `on*`
# handlers, and naming eleven of them is not a control, it is a list of the
# ones somebody remembered. Anything not defined here is refused.
#
# `KNOWN_TAGS` is the same set the editor model parses and serialises, so the
# server cannot accept markup the editor would immediately destroy on the next
# round-trip -- which would be the quietest possible way to lose a document.
KNOWN_TAGS = {
	"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
	"ul", "ol", "li", "blockquote", "pre",
	"strong", "b", "em", "i", "u", "s", "del", "code", "a",
	# `div` is the client's fallback container for a block outside its schema:
	# `renderNode`'s default branch emits it so an unrecognised block is
	# PRESERVED rather than dropped. It is here because the server must accept
	# everything the client can emit -- when it did not, a document carrying an
	# unknown block round-tripped to a hard refusal, which is the round-trip loss
	# this gate exists to forbid. `test_the_client_schema_is_accepted_by_the_server`
	# is what keeps the two lists from drifting apart again.
	"div",
}

# Attributes the editor's schema defines. Everything else is refused, which
# covers every `on*` handler at once instead of enumerating them.
KNOWN_ATTRS = {"href", "class", "id", "title", "lang", "dir"}

# `\bon` + a name + `=`, i.e. ANY event handler. This is the class-closing rule:
# it does not care which of the ~80 handlers is used, because there is no
# enumeration to be incomplete.
EVENT_ATTR_RE = re.compile(r"\bon[a-z]+\s*=", re.I)

# A URL scheme that carries a payload rather than a location.
DANGEROUS_URL_RE = re.compile(r"\b(href|src)\s*=\s*[\"\']?\s*(javascript|vbscript|data)\s*:", re.I)


@frappe.whitelist()
def normalize_document(html):
	"""Pass a document through the server and return it **unchanged**.

	This is the ``frappe.call`` boundary the gate names. The round-trip is the
	whole point: whatever the editor produced must arrive here and go back out
	byte for byte, so a defect in transit is attributable to the client model
	rather than to the server quietly editing the document.

	Returns the body it was given, plus what it found. It is a fixed point:
	``normalize_document(normalize_document(x)) == normalize_document(x)``.
	"""
	if html is None:
		html = ""
	if not isinstance(html, str):
		frappe.throw(_("A document body must be a string."), frappe.ValidationError)
	unsafe = disallowed_markup(html)
	if unsafe:
		frappe.throw(
			_("This document contains markup that is not part of the editor's schema ({0}) "
			  "and cannot be stored. A template body is immutable once issued, so anything "
			  "accepted here is permanent: a script or an event handler would run for everyone "
			  "who ever opens it.").format(", ".join(unsafe)),
			title=_("Unsafe Markup"),
		)
	return {
		"html": html,
		"empty": not html.strip(),
		"unknown_tags": unknown_tags(html),
	}


def unknown_tags(html):
	"""Tags present in the body that the editor's schema does not define.

	Reported, not removed. Silently dropping an unknown element is the exact
	failure this endpoint exists to prevent, so the caller is told instead.
	"""
	import re

	return sorted({t.lower() for t in re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)", html or "")} - KNOWN_TAGS)


def disallowed_markup(html):
	"""Markup the editor's schema does not define, named so the author can fix it.

	Refused, never stripped: stripping would let the save succeed while the
	author believes their document was stored intact.
	"""
	html = html or ""
	found = []

	# Scan each tag's attribute region separately. A flat scan of the whole
	# document for `name="..."` misses UNQUOTED attribute values, which are
	# perfectly valid HTML and are exactly how a payload gets written:
	# `<img src=x onerror=alert(1)>` yields nothing at all under a
	# quote-requiring pattern, so neither the on* rule nor the allowlist could
	# fire and the markup passed through untouched. Scanning the attribute
	# region and taking only the NAME before `=` is immune to the quoting.
	# No whitespace allowed between `<` and the name: per the HTML spec `< b` is
	# literal text, not a tag. Without that rule the scanner reads prose --
	# `a < b && c >` in a rate clause -- as a `<b>` tag with `b=` and `c=`
	# attributes, and refuses an entire contract for a stray less-than sign.
	TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)((?:\s[^<>]*?)?)\s*/?>", re.S)
	# The match MUST consume its value, or the scanner finds `key=value` pairs
	# inside a quoted value: `href="https://example.com/a?b=1&c=2"` yields `b=`
	# and `c=` as if they were attributes, and a perfectly ordinary link with a
	# query string is refused. Consuming the value makes this a tokeniser
	# rather than a pattern match, and it is also what makes the unquoted form
	# work: the value alternative is `[^\s>]*`, so no quoting is required.
	ATTR_RE = re.compile(
		r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s>]*)"""
	)

	for match in TAG_RE.finditer(html):
		tag = match.group(1).lower()
		if tag not in KNOWN_TAGS:
			found.append("<%s>" % tag)
		region = match.group(2) or ""
		# finditer, not findall: the pattern has two groups, so findall hands
		# back (name, value) tuples and every one of them raises on .lower().
		for attr in sorted({m.group(1).lower() for m in ATTR_RE.finditer(region)}):
			# Any event handler, by shape rather than by name.
			if attr.startswith("on"):
				found.append("%s=" % attr)
			elif attr not in KNOWN_ATTRS:
				found.append("%s=" % attr)

	if DANGEROUS_URL_RE.search(html):
		found.append("javascript:/vbscript:/data: URL")
	return found
