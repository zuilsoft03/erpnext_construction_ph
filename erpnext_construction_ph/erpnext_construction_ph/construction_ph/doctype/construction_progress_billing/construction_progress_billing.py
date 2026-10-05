# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""CIAP Document 102 progress billing certification.

Implements the class A money invariant in docs/ARCHITECTURE.md:82-87:

    net_amount_due = gross_accomplishment_period
                   - mobilization_recoupment
                   - (net_before_retention * 0.10 if cum <= 50% else 0.00)
                   + taxable_base * 0.12
                   - taxable_base * 0.02

where ``taxable_base = gross_accomplishment_period - mobilization_recoupment``
and ``net_before_retention = taxable_base + vat``.

``api/billing_engine.py`` consumes the resulting figures on submit; it performs
no arithmetic of its own.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now

# Statutory rates. VAT is the Philippine 12% output rate; retention is the
# CIAP Document 102 10% holdback; CWT is the 2% creditable withholding tax
# creditable against BIR Form 2307 (ATC WC120).
VAT_RATE = 0.12
RETENTION_RATE = 0.10
RETENTION_STOP_PERCENT = 50.0
CWT_RATE = 0.02


class ConstructionProgressBilling(Document):
	def validate(self):
		self.guard_submitted_on()
		self.calculate_certification()

	def guard_certified_figures(self):
		"""A submitted billing's certified figures are not editable. THIS is the
		hook that can see the edit; `validate` and `before_save` cannot.

		Measured, not assumed, and two beliefs about this were wrong first:

		- `validate` does NOT run on the edit path. A submitted document is saved
		  without the controller's `validate` being called at all, so a guard
		  written there is dead code that still reads correctly in the
		  insert-and-submit tests.
		- The edit is NOT hidden by `_restore_masked_fields_from_db()`. Measured
		  with the mask invoked directly, the field still reads the NEW value
		  afterwards: `contract_amount` is not `allow_on_submit`, so it is never
		  masked in the first place.

		Probed on the actual edit save, the only controller hook that fires is
		`validate_update_after_submit`, and it does see the edited value.
		`before_save` is NOT among them -- Frappe does not run it for a submitted
		document -- which is why a guard placed there looks right and does nothing.
		"""
		if self.docstatus != 1 or self.is_new():
			return
		for field, label in (
			("contract_amount", _("Contract Amount")),
			("period_accomplishment_percent", _("Period Accomplishment Percent")),
		):
			stored = frappe.db.get_value(self.doctype, self.name, field)
			if stored is None:
				continue
			if flt(self.get(field)) != flt(stored):
				frappe.throw(
					_("{0} cannot be changed once the billing has been submitted.").format(label),
					title=_("CIAP 102 Certified Figures"),
				)

	def guard_submitted_on(self):
		"""The CIAP 102 clock is evidence, so its start is not editable.

		``submitted_on`` is set once, on submission, and a draft must report
		none. A stamp that can be moved after the fact is not a clock start: a
		billing re-dated to reopen the 30-day window would defeat the rule the
		field exists to support, and the audit trail would still show a valid
		timestamp.
		"""
		if self.is_new():
			return
		# Compare against the value actually STORED, not `get_doc_before_save()`.
		# The before-save snapshot is a second, separately-maintained copy of the
		# row, and when the two ever disagree it is the snapshot that has been
		# proven wrong here -- it reported an unset field on a billing that was
		# demonstrably stamped, so the guard short-circuited and a submitted
		# billing's statutory clock was re-dated five days backwards. The
		# database is the record of what was stamped; it is what this checks.
		stored = frappe.db.get_value(self.doctype, self.name, "submitted_on")
		if stored and str(stored) != str(self.submitted_on or ""):
			frappe.throw(
				_("Submission date cannot be changed once the billing has been submitted."),
				title=_("CIAP 102 Clock"),
			)

	def validate_update_after_submit(self):
		"""**This is the hook that actually governs the clock start.**

		Measured: a guard in `validate` fires on insert and on submit, and on
		NOTHING else. Frappe does not call `validate` when a submitted document
		is saved with `allow_on_submit` changes -- it calls
		`validate_update_after_submit` -- so a guard written only in `validate`
		looked correct, ran in the tests that exercised it, and let a submitted
		billing's statutory clock be re-dated five days backwards. A control
		whose enforcement hook is wrong is not a control, however well it reads.
		"""
		self.guard_submitted_on()
		self.guard_certified_figures()

	def before_submit(self):
		"""Start the 30-day clock at submission, not at creation.

		A draft can sit for a fortnight before anyone certifies it. Charging
		the contractor 30 days from *creation* would bill time the contractor was
		never actually given, which is the kind of defect that ends in dispute
		rather than in a bug report.

		**Set here, not in `on_submit`.** `on_submit` runs after the submit has
		already been written, so the value has to go in with `db_set` -- and
		`db_set` does not update the document's before-save snapshot. The
		snapshot then reports the field as unset, the immutability guard reads it
		as "never stamped", and the guard protects nothing. Setting the value as
		part of the submit's own save keeps the document and the database in
		agreement from the start.
		"""
		if not self.submitted_on:
			self.submitted_on = now()

	def calculate_certification(self):
		"""Derive every certification figure from the five billed inputs."""
		contract_amount = flt(self.contract_amount)
		if contract_amount <= 0:
			frappe.throw(_("Contract Amount must be greater than zero."), title=_("Invalid Certification"))

		if flt(self.cumulative_accomplishment) < 0:
			frappe.throw(_("Cumulative Accomplishment cannot be negative."), title=_("Invalid Certification"))

		self.gross_accomplishment_period = self._money(
			contract_amount * flt(self.period_accomplishment_percent) / 100.0
		)
		# The stored percent must be the SAME number the milestone decision used.
		# Rounding it to 2dp here is what produced a claim that read "50.00%"
		# beside a zero retention — exactly the dispute this figure exists to
		# settle. The field carries an explicit precision (6) so the exact ratio
	# survives to MariaDB, and retention_milestone_reached records which rule fired.
		cumulative_ratio = flt(self.cumulative_accomplishment) / contract_amount * 100.0
		self.cumulative_percent = cumulative_ratio
		self.retention_milestone_reached = int(cumulative_ratio > RETENTION_STOP_PERCENT)

		# Recoupment is the only permitted deduction from the certified
		# accomplishment; the taxable base can never go negative.
		self.taxable_base = max(
			self._money(self.gross_accomplishment_period - flt(self.mobilization_recoupment)),
			0.0,
		)
		self.vat_amount = self._money(self.taxable_base * VAT_RATE)
		self.net_before_retention = self._money(self.taxable_base + self.vat_amount)

		# CIAP 102 milestone: 10% retention is withheld on each billing up to and
		# including the 50% milestone. Once cumulative accomplishment passes 50%
		# of the contract amount no further retention is deducted.
		if cumulative_ratio > RETENTION_STOP_PERCENT:
			self.retention_amount = 0.0
		else:
			self.retention_amount = self._money(self.net_before_retention * RETENTION_RATE)

		self.cwt_withheld = self._money(self.taxable_base * CWT_RATE)
		self.net_amount_due = self._money(
			self.taxable_base - self.retention_amount + self.vat_amount - self.cwt_withheld
		)

	@staticmethod
	def _money(value):
		"""Round to the peso. Every published figure is 2 decimal places."""
		return flt(value, 2)
