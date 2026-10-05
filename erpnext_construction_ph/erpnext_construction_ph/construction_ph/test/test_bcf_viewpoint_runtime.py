# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Runtime test for BCF Viewpoint validation and UUID assignment.

Child tables (istable: 1) in Frappe do not dispatch `before_insert` during
parent `Document.save()` / `update_children()`. To ensure the invariant
"snapshot_image is required on creation" and automatic UUIDv4 minting hold
when viewpoints are appended to a BCF Topic, `BCFTopic.validate()` explicitly
dispatches `_validate_viewpoints()`.

This test module verifies:
1. Direct invocation of BCFViewpoint controller methods.
2. Saving child viewpoints through BCFTopic enforces snapshot requirement
   and mints a UUIDv4 `viewpoint_guid` when omitted.
3. Caller-supplied `viewpoint_guid` is preserved intact.
"""

import uuid
import frappe
from frappe.tests import IntegrationTestCase

from erpnext_construction_ph.construction_ph.test import fixtures

CONTROLLER_MESSAGE = "A snapshot image is required when a viewpoint is created."


class TestBCFViewpointRuntime(IntegrationTestCase):
	"""BCF Viewpoint: what enforces the snapshot rule, and what does not."""

	def setUp(self):
		super().setUp()
		self.project = fixtures.make_project("BCFVW")
		self.model = frappe.get_doc(
			{
				"doctype": "BIM Model",
				"model_name": f"BCF-Viewpoint-Model-{frappe.generate_hash(length=6)}",
				"project": self.project,
			}
		).insert(ignore_permissions=True).name
		self.topic = frappe.get_doc(
			{
				"doctype": "BCF Topic",
				"title": "Viewpoint runtime topic",
				"project": self.project,
				"bim_model": self.model,
			}
		).insert(ignore_permissions=True)

	# --- the controller's own contract, invoked directly --------------------

	def test_the_controller_refuses_a_viewpoint_without_a_snapshot(self):
		"""Called directly, `before_insert` does enforce the rule it documents."""
		row = frappe.get_doc({"doctype": "BCF Viewpoint", "comment": "no snapshot"})
		with self.assertRaises(frappe.ValidationError) as caught:
			row.before_insert()
		self.assertIn("snapshot image is required", str(caught.exception))

	def test_the_controller_mints_the_viewpoint_guid(self):
		"""Called directly, `before_insert` assigns a UUIDv4."""
		row = frappe.get_doc(
			{
				"doctype": "BCF Viewpoint",
				"comment": "with snapshot",
				"snapshot_image": "/private/files/probe.png",
			}
		)
		row.before_insert()
		self.assertEqual(uuid.UUID(row.viewpoint_guid).version, 4)

	# --- what the framework actually does -----------------------------------

	def test_appending_a_viewpoint_mints_the_guid_on_save(self):
		"""Saving the parent topic mints a UUIDv4 on child viewpoints without a GUID."""
		doc = frappe.get_doc("BCF Topic", self.topic.name)
		doc.append(
			"viewpoints",
			{
				"comment": "appended by the test",
				"camera_vectors": '{"position": [0.0, 0.0, 0.0]}',
				"snapshot_image": "/private/files/probe.png",
			},
		)
		doc.save(ignore_permissions=True)

		stored = frappe.db.get_value(
			"BCF Viewpoint",
			{"parent": self.topic.name, "comment": "appended by the test"},
			["name", "viewpoint_guid", "snapshot_image"],
			as_dict=True,
		)
		self.assertTrue(stored, "the viewpoint row must land")
		self.assertEqual(stored.snapshot_image, "/private/files/probe.png")
		self.assertTrue(
			stored.viewpoint_guid,
			"BCFTopic.validate must assign a viewpoint_guid to child viewpoints",
		)
		self.assertEqual(uuid.UUID(stored.viewpoint_guid).version, 4)

	def test_appending_a_viewpoint_preserves_custom_guid(self):
		"""A caller-provided viewpoint_guid is preserved on save."""
		custom_guid = str(uuid.uuid4())
		doc = frappe.get_doc("BCF Topic", self.topic.name)
		doc.append(
			"viewpoints",
			{
				"comment": "custom guid test",
				"camera_vectors": '{"position": [0.0, 0.0, 0.0]}',
				"snapshot_image": "/private/files/probe2.png",
				"viewpoint_guid": custom_guid,
			},
		)
		doc.save(ignore_permissions=True)

		stored = frappe.db.get_value(
			"BCF Viewpoint",
			{"parent": self.topic.name, "comment": "custom guid test"},
			"viewpoint_guid",
		)
		self.assertEqual(stored, custom_guid)

	def test_a_snapshot_less_viewpoint_is_refused_on_save(self):
		"""BCFTopic.validate raises ValidationError if a viewpoint lacks a snapshot."""
		doc = frappe.get_doc("BCF Topic", self.topic.name)
		doc.append(
			"viewpoints",
			{"comment": "snapshot-less", "camera_vectors": "{}"},
		)
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.save(ignore_permissions=True)

		self.assertIn("snapshot image is required", str(caught.exception).lower())
		self.assertEqual(
			frappe.db.count("BCF Viewpoint", {"parent": self.topic.name, "comment": "snapshot-less"}),
			0,
			"a refused save must leave no half record behind",
		)