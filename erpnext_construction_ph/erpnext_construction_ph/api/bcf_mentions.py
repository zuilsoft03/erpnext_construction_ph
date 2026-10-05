"""Resolve @mentions in BCF Topic comments to real people.

A mention is free text typed on a phone by a site coordinator. It has to resolve to a
REAL user, or it must visibly resolve to nobody — and those two outcomes must not be
the same thing. A mention that silently goes nowhere is worse than one that does
nothing, because the person who wrote it believes someone was told.

**MEASURED, and it decides the carrier: this site has no `To Do` and no `Assignment`
doctype.** `tabTo Do` does not exist, so the obvious "give them a To Do" is not
available here. `Notification Log` does exist, and it is exactly the desk bell a
Notification's `send_system_notification` produces — so a mention lands the way a
notification does, which is the same thing the reader is already used to seeing.

There is no `on_comment` hook on `Document` in this version (only `add_comment`), so
this is reached through a `doc_events` entry on Comment, the supported integration
point.

The return shape is the point: `notified` and `unknown` are separate keys, so a caller
can show the writer which of their mentions landed. Collapsing them into a count is
the failure this exists to prevent.
"""

import re

import frappe
from frappe import _

# A mention is `@username` or `@email` -- and NOTHING ELSE, deliberately. An earlier
# pattern also allowed spaces, to catch `@Juan Dela Cruz`, and it swallowed the rest
# of the sentence: `@nobody-at-all-9999 please check the slab` was reported as one
# unknown "person" called "nobody-at-all-9999 please check the slab". An unresolvable
# mention is already reported, and reporting it wrongly is worse than not offering
# the space-separated form at all.
MENTION = re.compile(r"@([A-Za-z0-9._%+-]+(?:@[A-Za-z0-9.-]+)?)")


def _candidates(text):
    """Every plausible username in a mention, most specific first.

    A mention may be `@juan` or `@juan.delacruz@example.com`, and the person may have
    written their name with spaces. Each form is tried in turn so an email mention is
    not matched as a name first.
    """
    raw = text.strip().rstrip(".,;:!?)")
    yield raw
    if "@" in raw:
        yield raw.split("@", 1)[0]


def find_user(mention):
    """The real user a mention names, or None.

    Matched on email first, then on the login, because a person who writes their email
    in a comment means that address and not their login.
    """
    for candidate in _candidates(mention):
        if not candidate:
            continue
        if frappe.db.get_value("User", {"email": candidate, "enabled": 1}):
            return candidate
        if frappe.db.get_value("User", {"name": candidate, "enabled": 1}):
            return candidate
    return None


def _notify(topic, user, mention):
    """Put the mention in the mentioned person's desk bell, on this site.

    A `Notification Log` and not a `To Do`, because `tabTo Do` does not exist here --
    measured, not assumed -- and a bell is the same thing the reader already gets from
    an assignment notification.
    """
    frappe.get_doc(
        {
            "doctype": "Notification Log",
            "user": user,
            "subject": _("You were mentioned on the BCF topic {0}").format(topic.title or topic.name),
            "type": "Mention",
            "document_type": "BCF Topic",
            "document_name": topic.name,
            "from_user": getattr(topic, "modified_by", None) or frappe.session.user,
        }
    ).insert(ignore_permissions=True)


def resolve_mentions(topic, comment_text):
    """Tell every mentioned person. Returns who was told and who was not."""
    notified, unknown = [], []
    for mention in MENTION.findall(comment_text or ""):
        user = find_user(mention)
        if user:
            _notify(topic, user, mention)
            notified.append(user)
        else:
            # Named but not a user. REPORTED, never dropped: a mention that quietly
            # reaches nobody is the failure this whole function exists to prevent.
            unknown.append(mention.strip())
    return {"notified": notified, "unknown": unknown}


def on_bcf_topic_comment(doc, method=None):
    """doc_events entry for Comment. Ignores every comment that is not a real one."""
    if doc.reference_doctype != "BCF Topic" or not doc.reference_name:
        return
    if doc.comment_type != "Comment":
        return
    if not frappe.db.exists("BCF Topic", doc.reference_name):
        return
    resolve_mentions(frappe.get_doc("BCF Topic", doc.reference_name), doc.content)
