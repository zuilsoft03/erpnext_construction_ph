# Copyright (c) 2026, Philippine AEC Engineering
# License: AGPL-3.0
"""Test-suite bootstrap. Runs once, before any test in this app does.

**Why this exists, measured rather than assumed.** A single suite run left 19 `Error Log`
rows behind, and **17 of them were the same line**:

    Unable to send new password notification

ERPNext sends a new-password email whenever a `User` is inserted, and this container has
no SMTP server -- so every test that creates a user sheds an Error Log as a side effect,
and none of them will ever notice because nobody wrote a test about email.

**Why a hook rather than a per-class teardown.** Two earlier shapes both failed:

* Asking each class to declare it sheds an Error Log puts the burden on every future
  test, and the first author who forgets leaks silently. `CommittingCase.ALWAYS_RECLAIMED`
  is that idea and it works -- but it only covers classes that opted in.
* An app-owned base class carrying the reclamation was tried and made things **worse**:
  its teardown committed before the harness rollback, defeating the rollback for every
  class that did not already commit, and the leak went from +31 to +123.

This is one line, runs before the first test, covers every class including ones that do
not exist yet, and **cannot have that failure mode because it never deletes anything and
never commits.**

What it does NOT do, stated plainly: it does not stop the rows being created. It stops
the test run from *sending* them, which is what turns an error into an Error Log. The
enabling therefore removes the cause rather than sweeping the symptom -- the opposite of
every other repair this session attempted.
"""

import frappe


def before_tests():
    """Disable outbound email for the whole suite."""
    frappe.flags.sending_email = False
    # Frappe's own gate, so a caller that checks it rather than the flag is covered too.
    frappe.flags.in_test = True
