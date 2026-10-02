// Minimal service worker: makes the app installable. It does not cache customer data.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => new Response(
      '<!doctype html><meta name=viewport content="width=device-width"><body style="font-family:sans-serif;padding:30px"><h2>No connection</h2><p>The Order Form App needs internet. Try again when you have signal.</p>',
      {headers: {'Content-Type': 'text/html'}})));
  }
});
