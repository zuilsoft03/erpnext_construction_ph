# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""BCF topic. Invariant: topic_guid is a unique UUID assigned on insert."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr


class BCFTopic(Document):
	def before_insert(self):
		# Only when blank. This used to assign unconditionally, which silently
		# replaced a caller-supplied GUID: a BCF topic's GUID is its identity, so
		# importing the same package twice minted a second, different topic instead
		# of being recognised as the same one. `validate` already had the guard.
		if not cstr(self.topic_guid).strip():
			self._assign_guid()

	def validate(self):
		if not cstr(self.topic_guid).strip():
			self._assign_guid()
		guard_close_requires_resolution(self)
		guard_status_transition(self)
		self._validate_viewpoints()
	# Status and author labels are display names; the standard fields store account
	# ids. Kept in sync here so an import (which sets neither) and a UI edit (which
	# sets neither either) both end up with something a person can read.
	def before_save(self):
		user = frappe.session.user
		if self.is_new() and not cstr(self.creation_author).strip():
			self.creation_author = _display_name(user)
		if not self.is_new() and not cstr(self.modified_by_label).strip():
			self.modified_by_label = _display_name(user)


	def _assign_guid(self):
		import uuid

		self.topic_guid = str(uuid.uuid4())


	def _validate_viewpoints(self):
		for vp in self.get("viewpoints") or []:
			if not cstr(vp.snapshot_image).strip():
				frappe.throw(
					_("A snapshot image is required when a viewpoint is created."),
					title=_("Snapshot Required"),
				)
			if not cstr(vp.viewpoint_guid).strip():
				import uuid

				vp.viewpoint_guid = str(uuid.uuid4())

# The six-state machine, roadmap BIM-03. `Closed` is the only state with a rule
# attached, and the rule is that a topic cannot be declared accepted until
# someone has written down what was done about it.
TERMINAL_STATES = ("Closed",)

# Roles that run coordination. Deliberately EXCLUDES the commercial and finance
# roles -- Buyer, Contract Administrator, Project Accountant -- which touch
# contracts, variations and billing but have no business advancing a clash issue.
# Set 2026-09-29.
COORDINATION_ROLES = (
	"Construction BIM Coordinator",
	"Construction Design Lead",
	"Construction Project Manager",
	"Construction Site Manager",
	"Construction Field Foreman",
	"Construction Planner",
)

# A client may confirm a fix on an issue they raised, but may not accept or
# reject it -- the verifying party has to be someone other than the reporter, or
# the issue trail proves nothing. Set 2026-09-29.
CLIENT_ROLES = ("Construction Client", "Portal User")
CLIENT_ALLOWED_STATES = ("In Progress", "In Review", "Resolved")


def _display_name(user):
	"""A person's name, not their account id."""
	if not user or user == "Guest":
		return ""
	return (frappe.db.get_value("User", user, "full_name") or user)


def guard_close_requires_resolution(doc):
	"""Refuse to close a topic with no resolution written down.

	Not a UI affordance: a state that can be reached with no record of what was
	done is a state that will be reached. The mutation for this is clearing
	`resolution` and asserting the save is refused.
	"""
	if doc.status in TERMINAL_STATES and not cstr(doc.resolution).strip():
		frappe.throw(
			_("A topic cannot be Closed without a Resolution explaining what was done."),
			title=_("Resolution Required"),
		)


def guard_status_transition(doc):
	"""Coarse authorisation on who may advance a topic's state.

	Deliberately coarse. The full role model is the user's to set and is deferred
	(plan §6), so this is the minimum that is not nothing: a client cannot accept
	or reject, and the commercial/finance roles cannot advance a coordination
	issue at all.

	An earlier position was "six states, no permissions". That was recorded as a
	known gap -- anyone who could open a topic could advance it -- and superseded
	on 2026-09-29 by adding this check.
	"""
	if doc.is_new():
		return

	old = doc.get_doc_before_save()
	if not old or old.status == doc.status:
		return  # not a transition; creation is governed by insert permission

	user = frappe.session.user
	if user == "Administrator":
		return

	roles = set(frappe.get_roles(user))

	if roles & set(COORDINATION_ROLES):
		return

	if roles & set(CLIENT_ROLES):
		# Only on an issue they raised, and never into a terminal or judgement state.
		if old.owner != user:
			frappe.throw(
				_("A client may only change the status of an issue they raised."),
				frappe.PermissionError,
			)
		if doc.status not in CLIENT_ALLOWED_STATES:
			frappe.throw(
				_("A client may move an issue to In Progress, In Review or Resolved, "
				  "but not to {0}.").format(doc.status),
				frappe.PermissionError,
			)
		return

	frappe.throw(
		_("Your role may not change the status of a BCF issue."),
		frappe.PermissionError,
	)
