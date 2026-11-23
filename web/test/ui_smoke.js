// Smoke test for the web app: loads index.html and app.js in jsdom with a fake API and walks the new views.
// Run:  npm i jsdom && node web/test/ui_smoke.js      (exit code 0 = pass)
const fs = require('fs'), path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/<script[^>]*src="https:[^>]*><\/script>/g, '').replace(/<link[^>]*https:[^>]*>/g, '');
const js = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

const calls = [];
const ITEM = { id: 1, kind: 'place', category: 'food', title: 'Cafe A', venue: 'A', city: 'Bengaluru', status: 'open_ended', time_left: 'no end date', lat: 12.97, lon: 77.64, geo: 'confirmed', reasons: [], grounded: true, evidence: 'try A', evidence_source: 'speech', evidence_at: 3 };
const FAKE = {
  '/api/me': { location: { lat: 12.97, lon: 77.64 } },
  '/api/near': { items: [ITEM] },
  '/api/items': { items: [ITEM] },
  '/api/places': { places: [{ name: 'Meghana Foods', city: 'Bengaluru', area: 'Indiranagar', lat: 12.97, lon: 77.64, geo: 'confirmed', mentions: 3, reels: 3, creators: ['a', 'b'], categories: ['food'], prices: ['Rs 299'], summaries: [], item_ids: [1], live_offers: [{ id: 2, title: '1+1 biryani' }], verified: 3, confidence: 0.97, visited: false, best_evidence: { quote: 'try Meghana Foods', source: 'speech', at: 3, url: 'https://y/1' }, distance_m: 400 }] },
  '/api/plan': { stops: [{ id: 1, title: 'Cafe A', category: 'food', lat: 12.97, lon: 77.64, arrive: '2026-11-23T10:10:00', depart: '2026-11-23T10:55:00', travel_min: 10, wait_min: 0, stay_min: 45, ends_at: null, venue: 'A', area: 'Indiranagar' }], candidates: 2, radius_m: 6000, unpinned: 0, total_minutes: 55, travel_minutes: 10, value: 0.5, considered: 2, skipped: 1, mode: 'auto', note: 'estimates' },
  '/api/me/interests': { interests: [{ category: 'food', interest: 0.8, learned: 0.6, evidence: 3, saved: 0.5 }, { category: 'event', interest: 0, learned: -0.5, evidence: 2, saved: 0 }] },
  '/api/creators': { creators: [{ creator: 'honest', reels: 2, grounded: 3, dropped: 0, likes: 1, visits: 0, dismissals: 0, trust: 0.8 }] },
};
const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/?t=tok', pretendToBeVisual: true });
const w = dom.window;
w.fetch = (url, opts) => {
  calls.push([String(url), (opts && opts.method) || 'GET', opts && opts.body]);
  const key = String(url).split('?')[0];
  const body = key in FAKE ? FAKE[key] : {};
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
w.eval(js);

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const text = () => w.document.getElementById('left').textContent;
const tab = (name) => Array.from(w.document.querySelectorAll('#tabs button')).find((b) => b.textContent === name);
let failed = 0;
const check = (ok, msg) => { if (!ok) { failed++; console.error('FAIL', msg); } else console.log('ok  ', msg); };

(async () => {
  await wait(50);
  check(['Near me', 'Plan', 'Places', 'All saved', 'Live conditions', 'You'].every((n) => tab(n)), 'all six tabs are shown');
  check(text().includes('Cafe A'), 'near view lists the item');
  const labels = Array.from(w.document.querySelectorAll('#left .actions button')).map((b) => b.textContent);
  check(['Like', 'I went', 'Not for me'].every((l) => labels.includes(l)), 'feedback buttons are on each card');
  Array.from(w.document.querySelectorAll('#left .actions button')).find((b) => b.textContent === 'Like').click();
  await wait(20);
  const fb = calls.find((c) => c[0] === '/api/items/1/feedback');
  check(fb && fb[1] === 'POST' && JSON.parse(fb[2]).action === 'like', 'Like posts feedback');

  tab('Places').click(); await wait(30);
  check(text().includes('Meghana Foods') && text().includes('3 reels') && text().includes('97% sure') && text().includes('1+1 biryani'), 'places view shows merged venue, reels, confidence, live offer');
  check(text().includes('try Meghana Foods'), 'places view shows the proof quote');

  tab('Plan').click(); await wait(30);
  check(text().includes('1. Places') === false && text().includes('Cafe A') && text().includes('55 minutes'), 'plan view shows the stop and totals');
  check(calls.some((c) => c[0].startsWith('/api/plan?') && c[0].includes('minutes=180')), 'plan asks for 180 minutes by default');
  Array.from(w.document.querySelectorAll('#left .filters button')).find((b) => b.textContent === '1 hour').click(); await wait(30);
  check(calls.some((c) => c[0].includes('minutes=60')), 'changing the time re-plans');

  tab('You').click(); await wait(30);
  check(text().includes('80%') && text().includes('honest') && text().includes('80% reliable'), 'You view shows interests and creator trust');

  FAKE['/api/plan'] = { stops: [], candidates: 0, radius_m: 6000, unpinned: 1, total_minutes: 0, travel_minutes: 0, skipped: 0 };
  tab('Plan').click(); await wait(30);
  check(text().includes('within 6 km') && text().includes('1 have no map pin') && text().includes('Add sample places around me'), 'empty plan explains why and offers sample data');
  Array.from(w.document.querySelectorAll('#left button')).find((b) => b.textContent === 'Add sample places around me').click(); await wait(20);
  const demo = calls.find((c) => c[0] === '/api/demo' && c[1] === 'POST');
  check(demo && JSON.parse(demo[2]).lat === 12.97, 'sample data is requested around the browser location');

  if (failed) { console.error(failed + ' check(s) failed'); process.exit(1); }
  console.log('all UI checks passed'); process.exit(0);
})();
