(function () {
  'use strict';
  var CATS = { food: 'Food', store: 'Stores', event: 'Events', education: 'Learning', scheme: 'Schemes', service: 'Services', attraction: 'Places', other: 'Other' };
  var LABEL = { hazard: 'Hazard', weather: 'Weather on the ground', crowd: 'Crowd', traffic: 'Traffic', closure: 'Closed', opening: 'Open', deal: 'Deal', event: 'Event', price: 'Price' };
  var TABS = [['near', 'Near me'], ['all', 'All saved'], ['live', 'Live conditions']];
  var state = { view: 'near', cat: 'all', radius: 3000, sel: null, items: [], loc: null, place: 'Gokarna', briefing: null, msg: '' };
  var token = null, map = null, layer = null, markers = {};

  try {
    var q = new URLSearchParams(location.search).get('t');
    if (q) { localStorage.setItem('rilla_token', q); history.replaceState(null, '', location.pathname); }
    token = localStorage.getItem('rilla_token');
  } catch (e) { token = null; }

  function el(tag, props, kids) {
    var e = document.createElement(tag);
    for (var k in props || {}) { if (k === 'text') e.textContent = props[k]; else if (k === 'class') e.className = props[k]; else e.setAttribute(k, props[k]); }
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return e;
  }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'X-Rilla-Token': token || '' }, opts.headers || {});
    if (opts.body && typeof opts.body !== 'string') { opts.body = JSON.stringify(opts.body); opts.headers['Content-Type'] = 'application/json'; }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var err = new Error(d.detail || 'Something went wrong'); err.status = r.status; throw err; }
        return d;
      });
    });
  }
  function km(m) { return m == null ? 'no pin' : m < 1000 ? m + ' m away' : (m / 1000).toFixed(1) + ' km away'; }
  function note(t) { document.getElementById('addmsg').textContent = t || ''; }

  function showGate(text) {
    document.querySelector('.wrap').textContent = '';
    document.querySelector('.wrap').appendChild(el('div', { class: 'gate' }, [
      el('div', { class: 'brand' }, ['Ril', el('span', { text: 'l' }), 'a']),
      el('p', { text: text }),
      el('p', { class: 'note', text: 'In Telegram, message the Rilla bot and send /web.' })]));
  }

  function renderTabs() {
    var nav = document.getElementById('tabs'); nav.textContent = '';
    TABS.forEach(function (t) {
      var b = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(state.view === t[0]), text: t[1] });
      b.onclick = function () { state.view = t[0]; state.sel = null; load(); };
      nav.appendChild(b);
    });
  }

  function load() {
    renderTabs();
    if (state.view === 'live') { renderLive(); return; }
    var req;
    if (state.view === 'near') {
      if (!state.loc) { renderLocate(); return; }
      req = api('/api/near?lat=' + state.loc.lat + '&lon=' + state.loc.lon + '&radius=' + state.radius + '&limit=40');
    } else {
      var p = state.loc ? '&lat=' + state.loc.lat + '&lon=' + state.loc.lon : '';
      req = api('/api/items?x=1' + p);
    }
    req.then(function (d) { state.items = d.items; renderItems(); }).catch(fail);
  }
  function fail(e) { if (e.status === 401) showGate('This link has expired or is incomplete.'); else note(e.message); }

  function renderLocate() {
    var L = document.getElementById('left'); L.textContent = '';
    L.appendChild(el('div', {}, [el('h1', { text: 'What is near you?' }), el('p', { class: 'sub', text: 'Share your location and Rilla shows the offers and places you saved that are close, with the ones ending soon first.' })]));
    var b = el('button', { class: 'cta', type: 'button', text: 'Use my location' });
    b.onclick = locate; L.appendChild(b);
    L.appendChild(el('p', { class: 'note', id: 'locnote' }));
    drawMap([]);
  }
  function locate() {
    var n = document.getElementById('locnote');
    if (!navigator.geolocation) { if (n) n.textContent = 'This browser cannot share a location. Try All saved.'; return; }
    if (n) n.textContent = 'Finding you...';
    navigator.geolocation.getCurrentPosition(function (p) {
      state.loc = { lat: p.coords.latitude, lon: p.coords.longitude };
      api('/api/location', { method: 'POST', body: state.loc }).catch(function () {});
      load();
    }, function () { if (n) n.textContent = 'Location is blocked. Allow it in the browser, or open All saved.'; }, { enableHighAccuracy: true, timeout: 10000 });
  }

  function renderItems() {
    var L = document.getElementById('left'); L.textContent = '';
    var near = state.view === 'near';
    L.appendChild(el('div', {}, [
      el('h1', { text: near ? 'Near you' : 'Everything you saved' }),
      el('p', { class: 'sub', text: near ? 'Ranked by distance, time left and what you tend to save.' : 'Closest first. Expired offers are hidden.' })]));
    var f = el('div', { class: 'filters', role: 'group', 'aria-label': 'Category' });
    ['all'].concat(Object.keys(CATS)).forEach(function (k) {
      if (k !== 'all' && !state.items.some(function (i) { return i.category === k; })) return;
      var b = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(state.cat === k), text: k === 'all' ? 'All' : CATS[k] });
      b.onclick = function () { state.cat = k; renderItems(); }; f.appendChild(b);
    });
    L.appendChild(f);
    if (near) {
      var r = el('div', { class: 'filters', role: 'group', 'aria-label': 'Distance' });
      [[1000, '1 km'], [3000, '3 km'], [8000, '8 km']].forEach(function (x) {
        var b = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(state.radius === x[0]), text: x[1] });
        b.onclick = function () { state.radius = x[0]; load(); }; r.appendChild(b);
      });
      var re = el('button', { class: 'ghost', type: 'button', text: 'Update my location' }); re.onclick = locate; r.appendChild(re);
      L.appendChild(r);
    }
    var items = state.items.filter(function (i) { return state.cat === 'all' || i.category === state.cat; });
    if (!items.length) {
      L.appendChild(el('div', { class: 'empty' }, [el('b', { text: 'Nothing here yet' }), el('span', { text: 'Paste a reel link above, or forward one to the Rilla bot on Telegram.' })]));
    }
    var ul = el('ul', { class: 'list' });
    items.forEach(function (i) {
      var soon = i.status === 'ending_soon';
      var meta = [i.price, i.when, i.distance_m != null ? km(i.distance_m) : null, i.creator ? 'from ' + i.creator : null].filter(Boolean);
      var acts = el('span', { class: 'actions' });
      if (i.lat != null) acts.appendChild(el('a', { href: 'https://www.google.com/maps?q=' + i.lat + ',' + i.lon, target: '_blank', rel: 'noopener', text: 'Open in Maps' }));
      if (i.source_url) acts.appendChild(el('a', { href: i.source_url, target: '_blank', rel: 'noopener', text: 'Original reel' }));
      var del = el('button', { class: 'ghost', type: 'button', text: 'Remove' });
      del.onclick = function (ev) { ev.stopPropagation(); api('/api/items/' + i.id, { method: 'DELETE' }).then(load).catch(fail); };
      acts.appendChild(del);
      var card = el('div', { class: 'rec', role: 'button', tabindex: '0', 'aria-current': String(state.sel === i.id) }, [
        el('span', { class: 'row1' }, [
          el('span', { class: 'tag' }, [el('i', { style: 'background:var(--c-' + i.category + ')' }), CATS[i.category] || 'Other']),
          el('span', { class: 'left' + (soon ? ' soon' : ''), text: i.time_left })]),
        el('span', { class: 'ttl', text: i.title }),
        i.venue || i.area || i.city ? el('span', { class: 'meta', text: [i.venue, i.area, i.city].filter(Boolean).join(', ') + (i.geo === 'approx' ? ' (approximate pin)' : '') }) : null,
        meta.length ? el('span', { class: 'meta', text: meta.join(' · ') }) : null,
        i.reasons && i.reasons.length ? el('span', { class: 'why', text: i.reasons.join(' · ') }) : null,
        acts]);
      var pick = function () { state.sel = state.sel === i.id ? null : i.id; renderItems(); focusMarker(); };
      card.addEventListener('click', pick);
      card.addEventListener('keydown', function (e) { if (e.target === card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); pick(); } });
      ul.appendChild(el('li', {}, [card]));
    });
    L.appendChild(ul);
    drawMap(items);
  }

  function drawMap(items) {
    var mc = document.getElementById('mapcard'); mc.hidden = false;
    if (!window.L) { document.getElementById('map').textContent = 'The map could not load. The list still works.'; return; }
    if (!map) {
      map = L.map('map', { zoomControl: true }).setView([12.9716, 77.5946], 12);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
      layer = L.layerGroup().addTo(map);
    }
    layer.clearLayers(); markers = {};
    var pts = [];
    items.forEach(function (i) {
      if (i.lat == null) return;
      var soon = i.status === 'ending_soon';
      var col = getComputedStyle(document.documentElement).getPropertyValue('--c-' + i.category).trim() || '#0B7A83';
      var m = L.circleMarker([i.lat, i.lon], { radius: state.sel === i.id ? 11 : 8, color: soon ? getComputedStyle(document.documentElement).getPropertyValue('--alert').trim() : '#fff', weight: soon ? 3 : 2, fillColor: col, fillOpacity: 0.95 });
      m.bindPopup(el('div', {}, [el('b', { text: i.title }), el('br'), document.createTextNode(i.time_left)]));
      m.on('click', function () { state.sel = i.id; renderItems(); });
      m.addTo(layer); markers[i.id] = m; pts.push([i.lat, i.lon]);
    });
    if (state.loc) { L.circleMarker([state.loc.lat, state.loc.lon], { radius: 7, color: '#fff', weight: 2, fillColor: '#0B7A83', fillOpacity: 1 }).bindTooltip('You').addTo(layer); pts.push([state.loc.lat, state.loc.lon]); }
    if (pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 16 });
    setTimeout(function () { map.invalidateSize(); }, 50);
    var lg = document.getElementById('legend'); lg.textContent = '';
    Object.keys(CATS).forEach(function (k) {
      if (!items.some(function (i) { return i.category === k; })) return;
      lg.appendChild(el('span', {}, [el('i', { style: 'background:var(--c-' + k + ')' }), CATS[k]]));
    });
    lg.appendChild(el('span', { text: 'Red ring = ends within 48 hours' }));
  }
  function focusMarker() { var m = markers[state.sel]; if (m && map) { map.panTo(m.getLatLng()); m.openPopup(); } }

  function renderLive() {
    var L = document.getElementById('left'); L.textContent = '';
    document.getElementById('mapcard').hidden = true;
    L.appendChild(el('div', {}, [el('h1', { text: 'What is it like right now?' }), el('p', { class: 'sub', text: 'Same-day reports from local reels, shown only when independent sources agree.' })]));
    var form = el('form', { class: 'q' }, [el('label', { class: 'sr', for: 'place', text: 'Place' }), el('input', { id: 'place', type: 'text', value: state.place, placeholder: 'A town, beach or area', maxlength: '80' }), el('button', { class: 'cta', type: 'submit', text: 'Check' })]);
    form.onsubmit = function (e) { e.preventDefault(); state.place = document.getElementById('place').value.trim(); renderLive(); };
    L.appendChild(form);
    if (state.place.length < 2) return;
    api('/api/briefing?place=' + encodeURIComponent(state.place)).then(function (d) {
      if (!d.signals.length) { L.appendChild(el('div', { class: 'empty' }, [el('b', { text: 'No recent reports for ' + state.place }), el('span', { text: 'If you see a fresh reel from there, send it and the next person gets an answer.' })])); return; }
      var ul = el('ul', { class: 'list' });
      d.signals.forEach(function (s) {
        var conf = s.status === 'confirmed';
        ul.appendChild(el('li', {}, [el('div', { class: 'sig' }, [
          el('span', { class: 'bar ' + (conf ? 'fresh' : 'aging') }),
          el('span', { class: 'body' }, [el('span', { class: 'kind', text: (LABEL[s.type] || s.type) + ' · ' + s.area }), el('span', { class: 'val', text: s.value }),
            el('span', {}, [el('span', { class: 'badge ' + (conf ? 'ok' : 'warn'), text: conf ? s.sources + ' reports agree' : (s.sources === 1 ? '1 report, unconfirmed' : s.sources + ' reports, weak') })])]),
          el('span', { class: 'age', text: s.age })])]));
      });
      L.appendChild(ul);
    }).catch(fail);
  }

  // add a reel
  document.getElementById('addform').addEventListener('submit', function (e) {
    e.preventDefault();
    var input = document.getElementById('url'), btn = document.getElementById('addbtn');
    btn.disabled = true; note('Sending...');
    api('/api/submit', { method: 'POST', body: { url: input.value.trim() } }).then(function (d) {
      input.value = ''; note('Reading the reel. This usually takes under a minute.'); poll(d.job_id, 0);
    }).catch(function (err) { btn.disabled = false; note(err.message); });
  });
  function poll(id, n) {
    var btn = document.getElementById('addbtn');
    if (n > 80) { btn.disabled = false; note('Still working. It will appear in All saved when done.'); return; }
    setTimeout(function () {
      api('/api/jobs/' + id).then(function (j) {
        if (j.status === 'done') {
          btn.disabled = false; var r = j.result || {};
          note((r.saved && r.saved.length) ? 'Saved ' + r.saved.length + ': ' + r.saved.map(function (s) { return s.title; }).join(', ') : (r.duplicates && r.duplicates.length ? 'Already on your map.' : 'Nothing specific to save in that reel.'));
          load();
        } else if (j.status === 'failed') { btn.disabled = false; note(j.error || 'That reel could not be read.'); }
        else poll(id, n + 1);
      }).catch(function () { poll(id, n + 1); });
    }, 3000);
  }

  if ('serviceWorker' in navigator) { navigator.serviceWorker.register('sw.js').catch(function () {}); }

  if (!token) { showGate('Open Rilla from the link the Telegram bot sends you.'); return; }
  api('/api/me').then(function (me) {
    if (me.location) state.loc = me.location;
    state.view = state.loc ? 'near' : 'all';
    load();
  }).catch(fail);
})();
