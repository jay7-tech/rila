// Minimal service worker: makes the app installable. Network-first, no offline cache yet.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () { /* default network handling */ });
