// Copyright (c) 2026. Philippine AEC Engineering
// License: AGPL-3.0
// Minimal offline shell for the site log.
//
// Deliberately dependency-free and unbundled: a service worker that imports
// modules would need `bench build`, and `node` is absent from this bench
// (PH1-05). This file is served verbatim, so the shell works without it.
//
// WHAT IS AND IS NOT CLAIMED: that this file is present, coherent and served is
// verified by PH4-01's gate. That it actually caches and serves offline needs a
// browser and is NOT claimed -- see the gate partition.

const CACHE = "site-shell-v1";
const SHELL = ["/app/site-log", "/assets/erpnext_construction_ph/css/construction_desk.css"];

self.addEventListener("install", (event) => {
	event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => undefined));
});

self.addEventListener("activate", (event) => {
	event.waitUntil(
		caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
	);
});

// Network-first for reads: a site log must never show a stale ledger. Only
// fall back to cache when the network genuinely fails.
self.addEventListener("fetch", (event) => {
	if (event.request.method !== "GET") return;
	event.respondWith(
		fetch(event.request).catch(() => caches.match(event.request).then((hit) => hit || Response.error()))
	);
});
