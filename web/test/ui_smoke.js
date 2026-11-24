// Smoke test for the web app: loads index.html and app.js in jsdom with a fake API and walks the new views.
// Run:  npm i jsdom && node web/test/ui_smoke.js      (exit code 0 = pass)
const fs = require('fs'), path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8').replace(/<script[^>]*src="https:[^>]*><\/script>/g, '').replace(/<link[^>]*https:[^>]*>/g, '');
const js = fs.readFileSync(path.join(root, 'app.js'), 'utf8');

const calls = [];
const REL = { score: 0.82, label: 'high', parts: [{ part: 'grounded', value: 1, weight: 0.3, points: 0.3 }, { part: 'location', value: 1, weight: 0.2, points: 0.2 }, { part: 'creator', value: 0.7, weight: 0.1, points: 0.07 }] };
const ITEM = { reliability: REL,  id: 1, kind: 'place', category: 'food', title: 'Cafe A', venue: 'A', city: 'Bengaluru', status: 'open_ended', time_left: 'no end date', lat: 12.97, lon: 77.64, geo: 'confirmed', reasons: [], grounded: true, evidence: 'try A', evidence_source: 'speech', evidence_at: 3 };
const FAKE = {
  '/api/me': { location: { lat: 12.97, lon: 77.64 } },
  '/api/foryou': { items: [ITEM], cold: false, trained_on: 6, learned: { likes: [{ feature: 'category food', weight: 0.9 }], avoids: [{ feature: 'category event', weight: -0.6 }] } },
  '/api/search': { items: [Object.assign({}, ITEM, { id: 7, title: 'Biryani Zone', reasons: ['matches biryani'] })] },
  '/api/near': { items: [ITEM] },
  '/api/items': { items: [ITEM] },
  '/api/places': { places: [{ name: 'Meghana Foods', city: 'Bengaluru', area: 'Indiranagar', lat: 12.97, lon: 77.64, geo: 'confirmed', mentions: 3, reels: 3, creators: ['a', 'b'], categories: ['food'], prices: ['Rs 299'], summaries: [], item_ids: [1], live_offers: [{ id: 2, title: '1+1 biryani' }], verified: 3, confidence: 0.97, visited: false, best_evidence: { quote: 'try Meghana Foods', source: 'speech', at: 3, url: 'https://y/1' }, distance_m: 400 }] },
  '/api/plan': { stops: [{ id: 1, title: 'Cafe A', category: 'food', lat: 12.97, lon: 77.64, arrive: '2026-11-23T10:10:00', depart: '2026-11-23T10:55:00', travel_min: 10, wait_min: 0, stay_min: 45, ends_at: null, venue: 'A', area: 'Indiranagar' }], candidates: 2, radius_m: 6000, unpinned: 0, total_minutes: 55, travel_minutes: 10, value: 0.5, considered: 2, skipped: 1, mode: 'auto', note: 'estimates' },
  '/api/me/interests': { interests: [{ category: 'food', interest: 0.8, learned: 0.6, evidence: 3, saved: 0.5 }, { category: 'event', interest: 0, learned: -0.5, evidence: 2, saved: 0 }, { category: 'store', interest: 0.4, learned: 0.2, evidence: 1, saved: 0.2 }] },
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
const $ = (sel) => w.document.querySelector(sel);
let failed = 0;
const check = (ok, msg) => { if (!ok) { failed++; console.error('FAIL', msg); } else console.log('ok  ', msg); };

(async () => {
  await wait(50);
  check(['For you', 'Near me', 'Plan', 'Places', 'Saved', 'Live conditions', 'You'].every((n) => tab(n)), 'all seven tabs are shown');
  check(tab('For you').getAttribute('aria-pressed') === 'true' && calls.some((c) => c[0].startsWith('/api/foryou')), 'opens on For you and asks the taste model');
  check(text().includes('Cafe A') && text().includes('trained on 6') && text().includes('Likes food') && text().includes('Less into event'), 'For you shows the item and what was learned');
  check(!!$('#left .hero'), 'the top pick is the yellow signboard');
  const meterBtn = $('#left .meter button');
  check(meterBtn && meterBtn.textContent.includes('High 82%') && $('#left .pip.f'), 'reliability meter shows level and pips');
  meterBtn.click();
  check(text().includes('Quote found in the video') && text().includes('Map pin'), 'tapping the meter explains the score');
  const labels = Array.from(w.document.querySelectorAll('#left .actions button')).map((b) => b.textContent);
  check(['Like', 'I went', 'Not for me'].every((l) => labels.includes(l)), 'feedback buttons are on each card');
  Array.from(w.document.querySelectorAll('#left .actions button')).find((b) => b.textContent === 'Like').click();
  await wait(20);
  const fb = calls.find((c) => c[0] === '/api/items/1/feedback');
  check(fb && fb[1] === 'POST' && JSON.parse(fb[2]).action === 'like', 'Like posts feedback');

  w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: '/', bubbles: true }));
  check(!$('#palette').hidden, 'pressing / opens the search palette');
  $('#palq').value = 'biriyani'; $('#palq').dispatchEvent(new w.Event('input', { bubbles: true })); await wait(400);
  check(calls.some((c) => c[0].startsWith('/api/search?q=biriyani')) && $('#pallist').textContent.includes('Biryani Zone') && $('#pallist').textContent.includes('matches biryani'), 'palette searches and lists results with reasons');
  $('#pallist .pal-item').click(); await wait(20);
  check($('#palette').hidden && text().includes('Results for "biriyani"') && text().includes('Biryani Zone'), 'choosing a result shows it in the results view');
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

  tab('Near me').click(); await wait(30);
  check(text().includes('Cafe A') && calls.some((c) => c[0].startsWith('/api/near?')), 'near view still lists the item');

  tab('Places').click(); await wait(30);
  check(text().includes('Meghana Foods') && text().includes('3 reels') && text().includes('97% sure') && text().includes('1+1 biryani'), 'places view shows merged venue, reels, confidence, live offer');
  check(text().includes('try Meghana Foods'), 'places view shows the proof quote');

  tab('Plan').click(); await wait(30);
  check(text().includes('Cafe A') && text().includes('55 minutes') && text().includes('10 min to get there') && !!$('#left ol.line') && !!$('#left .bar3'), 'plan view draws the metro line with a time budget');
  check(calls.some((c) => c[0].startsWith('/api/plan?') && c[0].includes('minutes=180')), 'plan asks for 180 minutes by default');
  Array.from(w.document.querySelectorAll('#left .filters button')).find((b) => b.textContent === '1 hour').click(); await wait(30);
  check(calls.some((c) => c[0].includes('minutes=60')), 'changing the time re-plans');

  tab('You').click(); await wait(30);
  check(text().includes('80%') && text().includes('honest') && text().includes('80% reliable'), 'You view shows interests and creator trust');
  check(!!$('#left svg.radar') && text().includes('food'), 'You view draws the taste radar and learned features');
  $('#themebtn').click(); check(['dark', 'light'].includes(w.document.documentElement.getAttribute('data-theme')), 'theme toggle sets a theme');

  FAKE['/api/plan'] = { stops: [], candidates: 0, radius_m: 6000, unpinned: 1, total_minutes: 0, travel_minutes: 0, skipped: 0 };
  tab('Plan').click(); await wait(30);
  check(text().includes('within 6 km') && text().includes('1 have no map pin') && text().includes('Add sample places around me'), 'empty plan explains why and offers sample data');
  Array.from(w.document.querySelectorAll('#left button')).find((b) => b.textContent === 'Add sample places around me').click(); await wait(20);
  const demo = calls.find((c) => c[0] === '/api/demo' && c[1] === 'POST');
  check(demo && JSON.parse(demo[2]).lat === 12.97, 'sample data is requested around the browser location');

  if (failed) { console.error(failed + ' check(s) failed'); process.exit(1); }
  console.log('all UI checks passed'); process.exit(0);
})();
