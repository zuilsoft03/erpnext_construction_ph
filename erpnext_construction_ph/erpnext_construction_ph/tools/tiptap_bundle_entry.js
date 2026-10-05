// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
//
// Bundle entry for the TipTap browser global, built from THIS app's own
// node_modules (@tiptap/core 2.27.3 and StarterKit 2.27.3).
//
// PH1-10. `tiptap.js` reads `window.TipTap`, and nothing put it there: Desk has no
// bundler for app assets, so the editor could not mount. `ops/tmp-ph1-10/build.sh`
// bundles this file into `public/js/editor/tiptap.bundle.js`.
//
// It lives under `tools/`, NOT under `public/`, on purpose: everything in
// `public/` is served to the browser, and this file's bare specifiers
// (`@tiptap/core`) mean nothing to a browser. Shipping it would publish a broken
// asset. Only the bundle output belongs in `public/`.
//
// Only what `tiptap.js` actually uses is exported -- `Editor`, `StarterKit`,
// `Underline`, `Link`. Anything else is dead weight in a file loaded on every Desk
// page.

import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Underline from "@tiptap/extension-underline";
import Link from "@tiptap/extension-link";

window.TipTap = { Editor, StarterKit, Underline, Link };
