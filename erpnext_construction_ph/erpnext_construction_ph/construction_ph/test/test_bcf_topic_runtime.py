# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Runtime integration test for BCF Topic controller.

Exercises doctype lifecycle directly:
- Automatic topic_guid (UUIDv4) minting on insert when not supplied
- Preservation of caller-supplied topic_guid on insert
- Automatic population of creation_author and modified_by_label
- Rejection of status='Closed' without resolution text (guard_close_requires_resolution)
- Successful transition to status='Closed' when resolution text is supplied
"""

import uuid
import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.construction_ph.test import fixtures


class TestBCFTopicRuntime(IntegrationTestCase):
	"""Runtime execution of BCF Topic controller validation and lifecycle hooks."""

	def setUp(self):
		super().setUp()
		self.project = fixtures.make_project("BCFTOPIC")
		self.model = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": f"BCF-Topic-Model-{frappe.generate_hash(length=6)}",
				"project": self.project,
			}
		).insert(ignore_permissions=True).name

	def test_bcf_topic_insert_generates_guid_and_author(self):
		"""When no topic_guid is supplied, before_insert mints a UUIDv4 and sets creation_author."""
		doc = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"title": "Runtime BCF Topic Test",
				"project": self.project,
				"bim_model": self.model,
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)

		self.assertTrue(doc.topic_guid, "topic_guid must be assigned on insert")
		# Verify it parses as a valid UUID
		parsed = uuid.UUID(doc.topic_guid)
		self.assertEqual(parsed.version, 4, "Generated GUID must be UUIDv4")
		self.assertTrue(doc.creation_author, "creation_author must be set on insert")

	def test_bcf_topic_insert_preserves_custom_guid(self):
		"""Caller-supplied GUID is preserved on insert rather than overwritten."""
		custom_guid = str(uuid.uuid4())
		doc = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"topic_guid": custom_guid,
				"title": "Topic with Custom GUID",
				"project": self.project,
				"bim_model": self.model,
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)

		self.assertEqual(doc.topic_guid, custom_guid, "Caller-supplied GUID must be preserved")

	def test_bcf_topic_close_requires_resolution(self):
		"""Closing a topic without a resolution must be rejected by guard_close_requires_resolution."""
		doc = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"title": "Topic to be closed",
				"project": self.project,
				"bim_model": self.model,
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)

		doc.status = "Closed"
		doc.resolution = ""
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_bcf_topic_close_succeeds_with_resolution(self):
		"""Closing a topic with a resolution explaining what was done succeeds."""
		doc = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"title": "Topic to close properly",
				"project": self.project,
				"bim_model": self.model,
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)

		doc.status = "Closed"
		doc.resolution = "Rerouted HVAC branch line to avoid structural member B-102."
		doc.save(ignore_permissions=True)

		stored_status = frappe.db.get_value("BCF Topic", doc.name, "status")
		self.assertEqual(stored_status, "Closed")

	def test_bcf_topic_updates_modified_by_label(self):
		"""Updating an existing topic sets modified_by_label."""
		doc = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"title": "Initial Title",
				"project": self.project,
				"bim_model": self.model,
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)

		doc.title = "Updated Title"
		doc.modified_by_label = ""
		doc.save(ignore_permissions=True)

		self.assertTrue(doc.modified_by_label, "modified_by_label must be populated on save")
