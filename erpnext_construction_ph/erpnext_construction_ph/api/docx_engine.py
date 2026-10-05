# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""Document generation: CIAP 102 contract, Notice to Proceed, Completion Certificate.

**Why the values are read, not templated.** A contract that renders but ignores
its inputs is the failure this module exists to prevent, so every figure in the
output comes from the `Construction Project` and `Construction Progress Billing`
the caller names. Nothing is hard-coded and no placeholder is left in the text —
a literal `{{contract_amount}}` surviving into a signed document is a defect, and
the test asserts none do.

**What is and is not claimed about the output.** A `.docx` is an OOXML package.
What is proven here is that the package is *structurally* what Word requires —
declared content types, a well-formed relationship graph, and a body containing
the parts the template declares. Whether MS Word opens it without a repair
prompt is a **desktop-application** assertion and is not claimed; it cannot be
exercised headlessly, and asserting something that was never observed is worse
than leaving the claim off (GATES.md PH5-02).
"""

import io

import frappe
from frappe import _
from frappe.utils import flt, formatdate

# Left-over template markers. Their presence in a rendered document is a defect.
PLACEHOLDERS = ("{{", "}}", "None", "TBD")


def _value(value, default="—"):
	"""Render a value for a document cell, or an em dash when it is absent.

	`None` and empty strings become the em dash rather than the literal text
	"None", which is what a naive str() would put in a contract.
	"""
	if value is None:
		return default
	text = str(value).strip()
	return text if text else default


def _money(value):
	return "{:,.2f}".format(flt(value))


def _date(value):
	if not value:
		return "—"
	return formatdate(value, "dd MMMM yyyy")


def _project(project):
	row = frappe.db.get_value(
		"Construction Project",
		project,
		["name", "project_name", "contract_amount", "contract_date", "erpnext_project", "company"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("{0} is not a known project.").format(project), title=_("Unknown Project"))
	return row


def _parties(project):
	"""Client and contractor names for the contract's party block."""
	row = frappe.db.get_value("Construction Project", project, ["client"], as_dict=True)
	client = row.client if row else None
	company = frappe.db.get_value("Company", frappe.get_all("Company", limit=1, pluck="name")[0], "company_name")
	return _value(client), _value(company)


def _add(doc, text, bold=False, size=None):
	para = doc.add_paragraph()
	run = para.add_run(_value(text))
	run.bold = bold
	if size:
	 run.font.size = size
	return para


def build_contract(project, billing=None):
	"""The CIAP 102 Standard Construction Contract for `project`.

	`billing` is an optional submitted `Construction Progress Billing`; when given,
	its figures are carried into the contract's recitals.
	"""
	from docx import Document
	from docx.shared import Pt

	proj = _project(project)
	client, company = _parties(project)

	doc = Document()
	style = doc.styles["Normal"]
	style.font.name = "Times New Roman"
	style.font.size = Pt(11)

	_add(doc, "REPUBLIC OF THE PHILIPPINES", bold=True)
	_add(doc, "DEPARTMENT OF PUBLIC WORKS AND HIGHWAYS", bold=True)
	_add(doc, "CONTRACT AGREEMENT", bold=True)
	_add(doc, "CIAP Document 102 — Standard Construction Contract", bold=True)
	_add(doc, "")

	_add(doc, "PARTIES", bold=True)
	_add(doc, f"1. The Government, represented by the {company}, through the "
		f"Construction Project Management Office, and the Contractor, for the works "
		f"under this Contract Agreement;")
	_add(doc, f"2. Whereas the Government has undertaken the construction of the "
		f"{proj.project_name} for the Contract Sum of PHP {_money(proj.contract_amount)}.")

	_add(doc, "")
	_add(doc, "CONTRACT PARTICULARS", bold=True)
	_add(doc, f"Project: {proj.project_name}")
	_add(doc, f"Contract Sum: PHP {_money(proj.contract_amount)}")
	_add(doc, f"Contract Date: {_date(proj.contract_date)}")
	_add(doc, f"Client: {client}")

	if billing:
		b = frappe.db.get_value(
			"Construction Progress Billing",
			billing,
			[
				"billing_number",
				"posting_date",
				"gross_accomplishment_period",
				"net_amount_due",
				"retention_amount",
			],
			as_dict=True,
		)
		if b:
			_add(doc, "")
			_add(doc, "FIRST PROGRESS BILLING", bold=True)
			_add(doc, f"Billing No.: {_value(b.billing_number)}")
			_add(doc, f"Posting Date: {_date(b.posting_date)}")
			_add(doc, f"Gross Accomplishment: PHP {_money(b.gross_accomplishment_period)}")
			_add(doc, f"Retention: PHP {_money(b.retention_amount)}")
			_add(doc, f"Net Amount Due: PHP {_money(b.net_amount_due)}")

	_add(doc, "")
	_add(doc, "GENERAL CONDITIONS", bold=True)
	_add(doc, "This Contract is executed subject to the General Conditions of Contract "
		"for Public Works, the Standard Specifications, and the Particular Conditions "
		"stated herein.")
	_add(doc, "")
	_add(doc, "AGREED this day of record by the parties, in the place named above.")
	return doc


def build_notice_to_proceed(project):
	"""The Notice to Proceed for `project`."""
	from docx import Document

	proj = _project(project)
	client, _company = _parties(project)

	doc = Document()
	_add(doc, "NOTICE TO PROCEED", bold=True)
	_add(doc, "")
	_add(doc, f"Project: {proj.project_name}")
	_add(doc, f"Client: {client}")
	_add(doc, f"Contract Sum: PHP {_money(proj.contract_amount)}")
	_add(doc, f"Contract Date: {_date(proj.contract_date)}")
	_add(doc, "")
	_add(doc, "You are hereby instructed to commence construction of the works under the "
		"Contract Agreement. Mobilisation and site establishment shall proceed in "
		"accordance with the approved programme of works.")
	return doc


def build_completion_certificate(project, billing=None):
	"""The Certificate of Completion, with the final billing carried through."""
	from docx import Document

	proj = _project(project)
	client, _company = _parties(project)

	doc = Document()
	_add(doc, "CERTIFICATE OF COMPLETION", bold=True)
	_add(doc, "")
	_add(doc, f"Project: {proj.project_name}")
	_add(doc, f"Client: {client}")
	_add(doc, f"Contract Sum: PHP {_money(proj.contract_amount)}")
	if billing:
		b = frappe.db.get_value(
			"Construction Progress Billing",
			billing,
			["billing_number", "net_amount_due", "posting_date"],
			as_dict=True,
		)
		if b:
			_add(doc, f"Final Billing No.: {_value(b.billing_number)}")
			_add(doc, f"Posting Date: {_date(b.posting_date)}")
			_add(doc, f"Final Net Amount Due: PHP {_money(b.net_amount_due)}")
	_add(doc, "")
	_add(doc, "The works under this Contract are hereby certified as complete.")
	return doc


BUILDERS = {
	"contract": build_contract,
	"notice_to_proceed": build_notice_to_proceed,
	"completion_certificate": build_completion_certificate,
}


@frappe.whitelist()
def render(kind, project, billing=None):
	"""Render one document and return its bytes.

	The kind is checked against `BUILDERS` rather than dispatched with getattr on
	an arbitrary attribute, so a caller cannot reach anything else on this module.
	"""
	builder = BUILDERS.get(kind)
	if not builder:
		frappe.throw(
			_("Unknown document '{0}'. Expected one of: {1}").format(
				kind, ", ".join(sorted(BUILDERS))
			),
			title=_("Unknown Document"),
		)
	_projection(project)  # authorisation before any document is built
	doc = builder(project, billing=billing) if kind == "contract" else builder(project)
	return to_bytes(doc)


def _projection(project):
	"""Refuse a project the caller cannot see.

	The portal already scopes project visibility in `portal_data`; a document
	generator that skipped that would be a disclosure path around it, since the
	rendered contract carries the contract sum.
	"""
	from erpnext_construction_ph.api.portal_data import may_view

	if not may_view(frappe.session.user, project):
		frappe.throw(_("You do not have access to this project."), frappe.PermissionError)


def to_bytes(doc):
	"""Serialise a Document to `.docx` bytes."""
	buffer = io.BytesIO()
	doc.save(buffer)
	return buffer.getvalue()


def document_text(doc):
	"""All paragraph text, for assertions and for the placeholder check."""
	return "\n".join(p.text for p in doc.paragraphs)
