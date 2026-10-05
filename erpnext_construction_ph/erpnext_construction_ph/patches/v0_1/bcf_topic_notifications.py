"""Enable comments on BCF Topic and create its two notifications.

**Why a patch at all, MEASURED rather than assumed.** Both of these are in the
doctype's own JSON, and both survived `bench migrate` without taking effect:
`DocType._comments` read `None`, and neither Notification existed -- because
`migrate` synchronises DOCTYPES, and a Notification is not one. Creating them by hand
inserts cleanly (verified), so the documents are sound and the delivery mechanism was
wrong. A patch is the supported, versioned, idempotent path.

**`allow_comments` is not the flag in this Frappe version.** Measured: `tabDocType` has
no `allow_comments` column -- only `_comments` -- and `frappe/model/document.py`
mentions `_comments` and `add_comment` and never mentions `allow_comments`. The
roadmap names the wrong key; both are kept in the JSON so a reader following the
document is not silently given nothing, with the reason recorded on the flag.

**The channels were wrong too.** This version accepts Email, Slack, System
Notification and SMS; "Email Notification" is refused outright. The roadmap's gate is
"desk notification AND email", which is ONE notification with both send flags set
rather than two records that would each need proving.

Idempotent: re-running is a no-op, because each object is created only if absent.
"""

import json
import pathlib

import frappe

APP = pathlib.Path(frappe.get_app_path("erpnext_construction_ph"))
NOTIFICATIONS = APP / "construction_ph" / "notification"


def execute():
    # 1. comments on a topic
    if not frappe.db.get_value("DocType", "BCF Topic", "_comments"):
        dt = frappe.get_doc("DocType", "BCF Topic")
        dt._comments = 1
        dt.save()
        print("CORE-23: enabled comments on BCF Topic")

    # 2. the two notifications
    for path in sorted(NOTIFICATIONS.glob("bcf_topic_*.json")):
        wanted = json.loads(path.read_text(encoding="utf-8"))
        if frappe.db.exists("Notification", wanted["name"]):
            continue
        frappe.get_doc(wanted).insert(ignore_permissions=True)
        print("CORE-23: created notification %s" % wanted["name"])
