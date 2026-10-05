# Copyright (c) 2026. Philippine AEC Engineering
# License: AGPL-3.0
"""BCF 2.1 interop.

Written against the buildingSMART **BCF 2.1** `markup.bcf` schema, not against any
prior write-up. The namespace is `http://www.buildingsmart-tech.org/bcf/bcf/2.1`.

Deliberately Frappe-free. Everything in this package is pure Python so it can be
exercised without a site, a database or a browser -- which matters because the
thing being modelled, an IFC element reference, is the one piece of BCF that every
other tool agrees on and the piece this app was missing entirely.
"""
