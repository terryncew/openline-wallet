const CACHE = 'olw-cache-v1';
const ASSETS = ['.', './index.html', './manifest.webmanifest'];

self.addEventListener('install', (e)=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate', (e)=>{
  e.waitUntil(self.clients.claim());
});
self.addEventListener('fetch', (e)=>{
  const req = e.request;
  // Network-first for JSON receipts; cache-first for app shell
  if (req.destination==='document' || req.url.endsWith('index.html') || req.url.endsWith('manifest.webmanifest') || req.url.endsWith('sw.js')){
    e.respondWith(caches.match(req).then(r=> r || fetch(req)));
  } else {
    e.respondWith(fetch(req).catch(()=> caches.match(req)));
  }
});
