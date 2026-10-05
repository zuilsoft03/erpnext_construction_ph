# Vendored BCF schemas

`v2.1/` and `v3.0/` hold buildingSMART's BCF XML schemas, copied **byte-for-byte**
from <https://github.com/buildingSMART/BCF-XML> — `v2.1/` from tag `v2.1`,
`v3.0/` from tag `release_3_0`.

## Licencing: stated on one branch, asserted nowhere

This is murkier than it first looks, and it is recorded rather than smoothed over
because this repository ships to a client.

* A `LICENSE` file **on the `master` branch** reads: "(c) buildingSMART
  International Ltd. Creative Commons Attribution-NoDerivatives 4.0
  International" — <http://creativecommons.org/licenses/by-nd/4.0/>.
* **There is no `LICENSE` at the `v2.1` tag**, which is the tag `v2.1/` was taken
  from. Fetching it there returns 404.
* The GitHub API reports the repository as
  `license.spdx_id = "NOASSERTION"`, `name = "Other"` — i.e. GitHub does not
  recognise a licence here either.

So the terms above are **stated in a file on one branch and machine-asserted
nowhere**. Whether CC BY-ND 4.0 governs the schemas at the tags these copies came
from is a question for whoever owns this repo's commercial exposure, not something
to assume in either direction.

If the answer is that ND applies, the operative half is **NoDerivatives**: the
copies may be redistributed verbatim with attribution, and **may never be edited**.
The digests below make that enforceable rather than aspirational — any edit to a
vendored schema fails the suite instead of quietly becoming a prohibited
derivative.

## Pinned digests — byte-identical to upstream, and asserted on every run

SHA-256, recorded from the tags. `test_vendored_schemas_are_byte_identical_to_upstream`
fails if any of these changes, so a schema can never be "fixed" in place: a
suspicious schema is a finding to report upstream, and the fix belongs in the code.

| file | sha256 |
|---|---|
| `v2.1/markup.xsd` | `15e9af22065870d93ad45431147748adc5b32ef830d6dfe7b4eb10cf5e6c4441` |
| `v2.1/visinfo.xsd` | `c46100eeab8211ecd62b888fdb41d1c0c47b74fa210e35da18259ddf6156e7b9` |
| `v3.0/markup.xsd` | `6c80f1c566ace8b405ae0ab530438aec2014bf35dd14cfc3c4cf0c5aa0b9d169` |
| `v3.0/visinfo.xsd` | `0855bc002310b4a9decd67b1b21631bd2e0368f0878183da8448e34d807e7418` |
| `v3.0/shared-types.xsd` | `93d20dfcc6d7f451ae85a8277559b0347478c278218a42f0ef41dcc5550a2090` |

## Deliberately NOT vendored: the AGPL study notes

The prior notes under `ERP/construction_bim/study/` are AGPL-licensed and are not
copied here. Vendoring a Creative Commons NoDerivatives document into an AGPL
codebase is a different legal question from vendoring AGPL into AGPL, which is why
the schemas are copied and the prose is not. The schemas are also the normative
artefact; the notes are a reading of it, and where they disagreed with the schema
the schema won every time.

## A missing file is worse than an absent one

`shared-types.xsd` was once fetched at the `v2.1` tag, where it does not exist, and
the 14-byte `404: Not Found` body was committed as though it were a schema. 2.1's
`markup.xsd` is self-contained and never includes it, so nothing failed — the file
was simply a lie sitting in the tree looking authoritative.

`test_every_vendored_schema_is_real_and_its_includes_resolve` now asserts every
file here begins with an XML declaration, contains an `xs:schema` element, and that
each `xs:include` resolves beside it. It was verified to fail when a 14-byte 404
body is put back.
