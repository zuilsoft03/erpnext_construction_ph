# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""The statutory WAIR report: structured payload first, PDF second.

**Shape, per D-031.** `build_report` returns the statutory payload and is the
part that carries legal weight, so it is the part the gate asserts. `render_pdf`
is a thin render over that payload, proven separately by asserting real `%PDF-`
bytes. A renderer is a tooling dependency with a different failure mode from the
arithmetic; coupling the two would let a renderer quirk cost the row its
substantive content, which is how PH2-01 lost its canvas.

**Two refusals, both load-bearing.**

1. **No LTI classification -> refuse.** Never zero lost days. An incident nobody
   has assessed and an incident assessed as causing no lost time are different
   facts, and a filing that cannot tell them apart is a filing that is wrong
   rather than one that is incomplete.
2. **Past the 24-hour window -> refuse.** The report exists to evidence timely
   notification. Issuing it late with a timestamp on it is worse than not issuing
   it, because it puts an artefact in front of the authority that says the
   deadline was met when it was not.

**LTI days are not computed here.** The classification-to-days table is not in
this repository and is not invented (D-029, and the same reasoning for CORE-12).
An empty table is therefore the mechanism by which refusal 1 happens.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now

from erpnext_construction_ph.construction_ph.doctype.wair_incident.wair_incident import (
	NOTIFICATION_WINDOW_HOURS,
)

# Classification -> lost days. EMPTY ON PURPOSE. See the module docstring and
# D-029: these are statutory figures and this project does not guess them. An
# empty table is what makes the "no classification -> refuse" rule reachable.
LTI_DAYS_BY_CLASSIFICATION = {}


def _refuse(reason, title):
	frappe.throw(_(reason), frappe.ValidationError, title=_(title))


def lti_days_for(classification):
	"""Lost days for a classification, or None when it cannot be determined.

	None is distinct from 0. Zero is a finding; None is the absence of one, and
	the caller must not conflate them.
	"""
	if not (classification or "").strip():
		return None
	if not LTI_DAYS_BY_CLASSIFICATION:
		return None
	return LTI_DAYS_BY_CLASSIFICATION.get(classification)


def build_report(incident):
	"""The statutory payload for one incident, or a refusal.

	Returns a dict with `refused: True` and a reason rather than raising, so the
	Desk page and the CLI can show the same message. The refusals are the product
	here as much as the payload is.
	"""
	doc = frappe.get_doc("WAIR Incident", incident) if isinstance(incident, str) else incident

	classification = (doc.lti_classification or "").strip()
	if not classification:
		return {
			"refused": True,
			"reason": _(
				"This incident has no LTI classification. An unclassified incident "
				"cannot be reported: it must not be recorded as zero lost days."
			),
			"incident": doc.name,
		}

	days = lti_days_for(classification)
	if days is None:
		return {
			"refused": True,
			"reason": _(
				"No lost-day figure is configured for classification '{0}'. The table "
				"is not populated because the statutory figures have not been sourced."
			).format(classification),
			"incident": doc.name,
		}

	deadline = get_datetime(doc.notification_deadline)
	now_dt = get_datetime(now())
	if now_dt > deadline:
		return {
			"refused": True,
			"reason": _(
				"The {0}-hour notification window closed at {1}. A report is not "
				"issued after the deadline, because an artefact bearing a timestamp "
				"suggests the deadline was met when it was not."
			).format(NOTIFICATION_WINDOW_HOURS, doc.notification_deadline),
			"incident": doc.name,
			"deadline": str(doc.notification_deadline),
		}

	return {
		"refused": False,
		"incident": doc.name,
		"incident_number": doc.incident_number,
		"payee": doc.project_name or doc.project,
		"project": doc.project,
		"occurred_at": str(doc.occurred_at),
		"reported_at": str(doc.reported_at),
		"location": doc.location,
		"incident_type": doc.incident_type,
		"persons_involved": int(doc.persons_involved or 0),
		"injured_persons": int(doc.injured_persons or 0),
		"fatalities": int(doc.fatalities or 0),
		"lti_classification": classification,
		"lti_days": days,
		"notification_deadline": str(doc.notification_deadline),
		"notification_status": doc.notification_status,
		"immediate_actions": doc.immediate_actions,
		"sti_reference": doc.sti_reference or None,
	}


def report_html(report):
	"""Minimal HTML for the payload. Kept separate so it is trivially inspectable."""
	rows = "".join(
		f"<tr><th>{label}</th><td>{report[key] if report.get(key) is not None else ''}</td></tr>"
		for label, key in (
			("Incident number", "incident_number"),
			("Payee", "payee"),
			("Occurred at", "occurred_at"),
			("Reported at", "reported_at"),
			("Location", "location"),
			("Type", "incident_type"),
			("Persons involved", "persons_involved"),
			("Persons injured", "injured_persons"),
			("Fatalities", "fatalities"),
			("LTI classification", "lti_classification"),
			("LTI days", "lti_days"),
			("Notification deadline", "notification_deadline"),
			("Notification status", "notification_status"),
			("Immediate actions", "immediate_actions"),
		)
	)
	return (
		"<html><head><meta charset='utf-8'><title>WAIR Report</title></head>"
		"<body><h1>WAIR Incident Report</h1>"
		f"<table>{rows}</table></body></html>"
	)


def render_pdf(report):
	"""Render a report payload to PDF bytes.

	Uses Frappe's **default** renderer. `weasyprint` is absent from this bench and
	forcing `pdf-generator: wkhtmltopdf` fails at runtime, so neither is passed --
	see D-031 for the measurement.
	"""
	from frappe.utils.pdf import get_pdf

	return get_pdf(report_html(report))
