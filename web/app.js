(function () {
  'use strict';
  var CATS = { food: 'Food', store: 'Stores', event: 'Events', education: 'Learning', scheme: 'Schemes', service: 'Services', attraction: 'Places', other: 'Other' };
  var LABEL = { hazard: 'Hazard', weather: 'Weather on the ground', crowd: 'Crowd', traffic: 'Traffic', closure: 'Closed', opening: 'Open', deal: 'Deal', event: 'Event', price: 'Price' };
  var TABS = [['near', 'Near me'], ['plan', 'Plan'], ['places', 'Places'], ['all', 'All saved'], ['live', 'Live conditions'], ['you', 'You']];
  var state = { view: 'near', cat: 'all', radius: 3000, sel: null, items: [], loc: null, place: 'Gokarna', briefing: null, msg: '', minutes: 180, plan: null, places: [] };
  var token = null, map = null, layer = null, markers = {};

  var share = null;
  try {
    var sp = new URLSearchParams(location.search);
    var sUrl = sp.get('url'), sText = [sp.get('title'), sp.get('text')].filter(Boolean).join('\n');
    if (sUrl || sText) { share = { url: sUrl || '', text: sText }; history.replaceState(null, '', location.pathname); }
  } catch (e) { share = null; }

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
    if (opts.body && typeof opts.body !== 'string' && !(opts.body instanceof FormData)) { opts.body = JSON.stringify(opts.body); opts.headers['Content-Type'] = 'application/json'; }
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
    var wrap = document.querySelector('.wrap'); wrap.textContent = '';
    var box = el('div', { class: 'gate' }, [
      el('div', { class: 'brand' }, ['Ril', el('span', { text: 'l' }), 'a']),
      el('p', { text: text })]);
    wrap.appendChild(box);
    fetch('/api/config').then(function (r) { return r.json(); }).then(function (c) {
      if (c.signup === 'closed') {
        box.appendChild(el('p', { class: 'note', text: 'Sign-ups are closed. Ask the owner for your private link.' })); return;
      }
      var name = el('input', { type: 'text', placeholder: 'Your name', maxlength: '60', required: 'required', 'aria-label': 'Your name' });
      var code = c.signup === 'invite' ? el('input', { type: 'text', placeholder: 'Invite code', maxlength: '100', required: 'required', 'aria-label': 'Invite code' }) : null;
      var msg = el('p', { class: 'note', role: 'status' });
      var form = el('form', { class: 'signup' }, [name, code, el('button', { class: 'cta', type: 'submit', text: 'Create my account' }), msg]);
      form.onsubmit = function (e) {
        e.preventDefault(); msg.textContent = 'Creating...';
        api('/api/signup', { method: 'POST', body: { name: name.value.trim(), code: code ? code.value.trim() : '' } }).then(function (d) {
          try { localStorage.setItem('rilla_token', d.token); if (share) sessionStorage.setItem('rilla_share', JSON.stringify(share)); } catch (e2) {}
          location.reload();
        }).catch(function (err) { msg.textContent = err.message; });
      };
      box.appendChild(form);
    }).catch(function () {});
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
    if (state.view === 'you') { renderYou(); return; }
    if (state.view === 'plan') { loadPlan(); return; }
    if (state.view === 'places') { loadPlaces(); return; }
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
  function fail(e) { if (e.status === 401) showGate('Sign in to start saving what you watch.'); else note(e.message); }

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
      L.appendChild(el('div', { class: 'empty' }, [el('b', { text: 'Nothing here yet' }), el('span', { text: 'Add a video link, a screenshot or some text above. On Android, install the app and use Share to Rilla.' })]));
    }
    var ul = el('ul', { class: 'list' });
    items.forEach(function (i) {
      var soon = i.status === 'ending_soon';
      var meta = [i.price, i.when, i.distance_m != null ? km(i.distance_m) : null, i.creator ? 'from ' + i.creator : null].filter(Boolean);
      var acts = el('span', { class: 'actions' });
      var mapsQ = [i.venue_en || i.venue || i.title, i.area, i.city].filter(Boolean).join(', ');
      if (i.lat != null || mapsQ) {
        // an approximate pin is only the middle of the area, so search by name instead
        var href = (i.geo === 'confirmed' && i.lat != null) ? 'https://www.google.com/maps?q=' + i.lat + ',' + i.lon : 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(mapsQ);
        acts.appendChild(el('a', { href: href, target: '_blank', rel: 'noopener', text: 'Open in Maps' }));
      }
      if (i.source_url) acts.appendChild(el('a', { href: i.source_url, target: '_blank', rel: 'noopener', text: 'Original reel' }));
      var why = el('button', { class: 'ghost', type: 'button', text: 'Why this?' });
      why.onclick = function (ev) { ev.stopPropagation(); toggleProof(i, why.closest('.rec')); };
      acts.appendChild(why);
      [['Like', 'like', 'Noted'], ['I went', 'visit', 'Marked as visited'], ['Not for me', 'dismiss', 'Hidden']].forEach(function (f) {
        var fbtn = el('button', { class: 'ghost', type: 'button', text: f[0] });
        fbtn.onclick = function (ev) {
          ev.stopPropagation(); fbtn.disabled = true;
          api('/api/items/' + i.id + '/feedback', { method: 'POST', body: { action: f[1] } }).then(function () {
            fbtn.textContent = f[2]; if (f[1] !== 'like') setTimeout(load, 400);
          }).catch(function (err) { fbtn.disabled = false; fail(err); });
        };
        acts.appendChild(fbtn);
      });
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

  var SRC = { speech: 'spoken in the video', screen: 'shown on screen', caption: 'in the caption', text: 'in the text you pasted', multi: 'across a few lines' };
  function clock(sec) { sec = Math.round(sec); return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2); }
  function toggleProof(i, card) {
    var old = card.querySelector('.proof');
    if (old) { old.remove(); return; }
    var p = el('div', { class: 'proof', role: 'region', 'aria-label': 'Where this came from' });
    p.addEventListener('click', function (ev) { ev.stopPropagation(); });
    if (i.grounded === true && i.evidence) {
      p.appendChild(el('span', {}, ['Found in the video, ' + (SRC[i.evidence_source] || 'in the source') + (i.evidence_at != null ? ' at ' + clock(i.evidence_at) : '') + ':']));
      p.appendChild(el('q', { text: i.evidence }));
    } else {
      p.appendChild(el('span', { text: 'Rilla did not record an exact quote for this one, so it could not be checked against the video.' }));
    }
    if (i.venue_en && i.venue && i.venue_en !== i.venue) p.appendChild(el('span', { class: 'ctx', text: 'Name as written: ' + i.venue }));
    card.appendChild(p);
    if (i.evidence_at != null) {
      api('/api/items/' + i.id + '/source').then(function (d) {
        (d.around || []).forEach(function (sg) { p.appendChild(el('span', { class: 'ctx', text: clock(sg.start) + ' ' + sg.source + ': ' + sg.text })); });
      }).catch(function () {});
    }
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


  function mapsHref(name, area, city, lat, lon, geo) {
    var q = [name, area, city].filter(Boolean).join(', ');
    return (geo === 'confirmed' && lat != null) ? 'https://www.google.com/maps?q=' + lat + ',' + lon : 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(q);
  }
  function hm(iso) { var d = new Date(iso); return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }

  // Places: the same venue seen in several reels, merged
  function loadPlaces() {
    var p = state.loc ? '?lat=' + state.loc.lat + '&lon=' + state.loc.lon : '';
    api('/api/places' + p).then(function (d) { state.places = d.places; renderPlaces(); }).catch(fail);
  }
  function renderPlaces() {
    var L = document.getElementById('left'); L.textContent = '';
    L.appendChild(el('div', {}, [el('h1', { text: 'Your places' }), el('p', { class: 'sub', text: 'One card per venue. The more independent reels mention it, the more Rilla trusts it.' })]));
    if (!state.places.length) L.appendChild(el('div', { class: 'empty' }, [el('b', { text: 'No places yet' }), el('span', { text: 'Save a few reels and the venues they mention will gather here.' })]));
    var ul = el('ul', { class: 'list' });
    state.places.forEach(function (pl) {
      var meta = [pl.area, pl.city].filter(Boolean).join(', ') + (pl.geo === 'approx' ? ' (approximate pin)' : '');
      var facts = [pl.reels + (pl.reels === 1 ? ' reel' : ' reels'), pl.creators.length ? 'by ' + pl.creators.join(', ') : null, pl.prices.length ? pl.prices.join(' / ') : null,
        pl.distance_m != null ? km(pl.distance_m) : null].filter(Boolean);
      var acts = el('span', { class: 'actions' }, [el('a', { href: mapsHref(pl.name, pl.area, pl.city, pl.lat, pl.lon, pl.geo), target: '_blank', rel: 'noopener', text: 'Open in Maps' })]);
      if (pl.best_evidence && pl.best_evidence.url) acts.appendChild(el('a', { href: pl.best_evidence.url, target: '_blank', rel: 'noopener', text: 'Best reel' }));
      var card = el('div', { class: 'rec' }, [
        el('span', { class: 'row1' }, [el('span', { class: 'tag' }, [el('i', { style: 'background:var(--c-' + (pl.categories[0] || 'other') + ')' }), CATS[pl.categories[0]] || 'Other']),
          el('span', { class: 'left', text: Math.round(pl.confidence * 100) + '% sure' })]),
        el('span', { class: 'ttl', text: pl.name }),
        meta ? el('span', { class: 'meta', text: meta }) : null,
        el('span', { class: 'meta', text: facts.join(' · ') }),
        pl.live_offers.length ? el('span', { class: 'why', text: 'Live offer: ' + pl.live_offers.map(function (o) { return o.title; }).join(', ') }) : null,
        pl.best_evidence ? el('span', { class: 'proof' }, ['Proof (' + (SRC[pl.best_evidence.source] || 'in the source') + '): ', el('q', { text: pl.best_evidence.quote })]) : null,
        acts]);
      ul.appendChild(el('li', {}, [card]));
    });
    L.appendChild(ul);
    drawMap(state.places.map(function (pl, n) { return { id: 'p' + n, title: pl.name, category: pl.categories[0] || 'other', status: pl.live_offers.length ? 'active' : 'open_ended', time_left: pl.reels + ' reel(s)', lat: pl.geo === 'none' ? null : pl.lat, lon: pl.lon }; }));
  }

  // Sample data around you, so every screen can be tried before you have saved anything
  function demoButtons(into) {
    var row = el('div', { class: 'filters', role: 'group', 'aria-label': 'Sample data' });
    var add = el('button', { class: 'ghost', type: 'button', text: 'Add sample places around me' });
    add.onclick = function () {
      add.disabled = true;
      var body = state.loc ? { lat: state.loc.lat, lon: state.loc.lon } : {};
      api('/api/demo', { method: 'POST', body: body }).then(function () { load(); }).catch(function (e) { add.disabled = false; fail(e); });
    };
    var rm = el('button', { class: 'ghost', type: 'button', text: 'Remove sample data' });
    rm.onclick = function () { rm.disabled = true; api('/api/demo', { method: 'DELETE' }).then(function () { load(); }).catch(function (e) { rm.disabled = false; fail(e); }); };
    row.appendChild(add); row.appendChild(rm); into.appendChild(row);
  }

  // Plan: an ordered outing that fits the time you have
  function loadPlan() {
    if (!state.loc) { renderLocate(); return; }
    api('/api/plan?lat=' + state.loc.lat + '&lon=' + state.loc.lon + '&minutes=' + state.minutes).then(function (d) { state.plan = d; renderPlan(); }).catch(fail);
  }
  function renderPlan() {
    var L = document.getElementById('left'); L.textContent = '';
    var d = state.plan;
    L.appendChild(el('div', {}, [el('h1', { text: 'Plan my outing' }), el('p', { class: 'sub', text: 'Your saved places, ordered so you can fit the most in the time you have. Times are estimates.' })]));
    var f = el('div', { class: 'filters', role: 'group', 'aria-label': 'Time available' });
    [[60, '1 hour'], [120, '2 hours'], [180, '3 hours'], [300, '5 hours']].forEach(function (x) {
      var b = el('button', { class: 'chip', type: 'button', 'aria-pressed': String(state.minutes === x[0]), text: x[1] });
      b.onclick = function () { state.minutes = x[0]; loadPlan(); }; f.appendChild(b);
    });
    L.appendChild(f);
    if (!d.stops.length) {
      var why = d.candidates === 0
        ? 'None of your saved places are within ' + (d.radius_m / 1000) + ' km of where you are now' + (d.unpinned ? ' (' + d.unpinned + ' have no map pin yet)' : '') + '. Move, or add sample places around you.'
        : d.candidates + ' saved place(s) are near you but none fit in this time. Try more time.';
      L.appendChild(el('div', { class: 'empty' }, [el('b', { text: 'Nothing fits yet' }), el('span', { text: why + ' Visited and hidden places are left out.' })]));
      demoButtons(L);
      drawMap([]); return;
    }
    L.appendChild(el('p', { class: 'note', text: d.stops.length + ' stops, about ' + Math.round(d.total_minutes) + ' minutes in all (' + Math.round(d.travel_minutes) + ' travelling).' + (d.skipped ? ' ' + d.skipped + ' did not fit.' : '') }));
    var ol = el('ol', { class: 'list plan' });
    d.stops.forEach(function (s, n) {
      var acts = el('span', { class: 'actions' }, [el('a', { href: mapsHref(s.venue || s.title, s.area, null, s.lat, s.lon, 'confirmed'), target: '_blank', rel: 'noopener', text: 'Directions' })]);
      ol.appendChild(el('li', {}, [el('div', { class: 'rec' }, [
        el('span', { class: 'row1' }, [el('span', { class: 'tag' }, [el('i', { style: 'background:var(--c-' + (s.category || 'other') + ')' }), (n + 1) + '. ' + (CATS[s.category] || 'Other')]),
          el('span', { class: 'left', text: hm(s.arrive) + ' to ' + hm(s.depart) })]),
        el('span', { class: 'ttl', text: s.title }),
        el('span', { class: 'meta', text: [s.venue, s.area].filter(Boolean).join(', ') }),
        el('span', { class: 'meta', text: (s.travel_min ? Math.round(s.travel_min) + ' min to get there' : 'start here') + (s.wait_min > 1 ? ', wait ' + Math.round(s.wait_min) + ' min for it to open' : '') + ', stay about ' + s.stay_min + ' min' + (s.ends_at ? ', offer ends ' + hm(s.ends_at) : '') }),
        acts])]));
    });
    L.appendChild(ol);
    drawMap(d.stops.map(function (s) { return { id: s.id, title: (s.title), category: s.category || 'other', status: 'active', time_left: hm(s.arrive), lat: s.lat, lon: s.lon }; }));
  }

  // You: what Rilla has learned, in the open
  function renderYou() {
    var L = document.getElementById('left'); L.textContent = '';
    document.getElementById('mapcard').hidden = true;
    demoButtons(L);
    L.appendChild(el('div', {}, [el('h1', { text: 'What Rilla has learned' }), el('p', { class: 'sub', text: 'From what you save, like, visit and hide. Nothing is hidden from you, and it never leaves your account.' })]));
    Promise.all([api('/api/me/interests'), api('/api/creators')]).then(function (r) {
      var rows = r[0].interests.filter(function (x) { return x.evidence > 0 || x.saved > 0; });
      if (!rows.length) L.appendChild(el('div', { class: 'empty' }, [el('b', { text: 'Not enough yet' }), el('span', { text: 'Like, visit or hide a few places and your tastes show up here.' })]));
      else {
        L.appendChild(el('h2', { class: 'h2', text: 'Your interests' }));
        var ul = el('ul', { class: 'list' });
        rows.forEach(function (x) {
          ul.appendChild(el('li', {}, [el('div', { class: 'bars' }, [
            el('span', { class: 'bl', text: CATS[x.category] || x.category }),
            el('span', { class: 'track' }, [el('span', { class: 'fill', style: 'width:' + Math.round(x.interest * 100) + '%' })]),
            el('span', { class: 'bv', text: Math.round(x.interest * 100) + '%' })])]));
        });
        L.appendChild(ul);
      }
      var cs = r[1].creators;
      if (cs.length) {
        L.appendChild(el('h2', { class: 'h2', text: 'Creators you save from' }));
        var cl = el('ul', { class: 'list' });
        cs.forEach(function (c) {
          cl.appendChild(el('li', {}, [el('div', { class: 'rec' }, [
            el('span', { class: 'row1' }, [el('span', { class: 'ttl', text: c.creator }), el('span', { class: 'left', text: Math.round(c.trust * 100) + '% reliable' })]),
            el('span', { class: 'meta', text: c.reels + ' reels, ' + c.grounded + ' places checked in the video, ' + c.dropped + ' dropped as not in the video' + (c.likes + c.visits ? ', ' + (c.likes + c.visits) + ' liked or visited' : '') + (c.dismissals ? ', ' + c.dismissals + ' hidden' : '') })])]));
        });
        L.appendChild(cl);
      }
    }).catch(fail);
  }

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

  // add something: link, file or screenshot, or text
  function busy(on) { Array.prototype.forEach.call(document.querySelectorAll('#add .cta'), function (b) { b.disabled = on; }); }
  function startJob(request, what) {
    busy(true); note('Sending...');
    request.then(function (d) { note('Reading ' + what + '. This can take a minute or two.'); poll(d.job_id, 0); })
      .catch(function (err) { busy(false); note(err.message); });
  }
  function submitLink(url) { startJob(api('/api/submit', { method: 'POST', body: { url: url } }), 'the video'); }
  function submitText(text) { startJob(api('/api/submit/text', { method: 'POST', body: { text: text } }), 'the text'); }
  function submitFile(file) {
    var fd = new FormData(); fd.append('file', file, file.name || 'screenshot.png');
    startJob(api('/api/submit/file', { method: 'POST', body: fd }), 'the file');
  }
  document.getElementById('addform').addEventListener('submit', function (e) {
    e.preventDefault(); var i = document.getElementById('url'); var v = i.value.trim(); i.value = ''; submitLink(v);
  });
  document.getElementById('textform').addEventListener('submit', function (e) {
    e.preventDefault(); var i = document.getElementById('text'); var v = i.value.trim(); i.value = ''; submitText(v);
  });
  document.getElementById('fileform').addEventListener('submit', function (e) {
    e.preventDefault(); var i = document.getElementById('file'); if (i.files && i.files[0]) { submitFile(i.files[0]); i.value = ''; }
  });
  Array.prototype.forEach.call(document.querySelectorAll('#add [data-mode]'), function (b) {
    b.onclick = function () {
      Array.prototype.forEach.call(document.querySelectorAll('#add [data-mode]'), function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      Array.prototype.forEach.call(document.querySelectorAll('#add [data-pane]'), function (f) { f.hidden = f.getAttribute('data-pane') !== b.getAttribute('data-mode'); });
    };
  });
  // paste a screenshot straight from the clipboard
  document.addEventListener('paste', function (e) {
    var items = (e.clipboardData && e.clipboardData.items) || [];
    for (var k = 0; k < items.length; k++) {
      if (items[k].kind === 'file' && /^image\//.test(items[k].type)) { var f = items[k].getAsFile(); if (f) { e.preventDefault(); submitFile(f); return; } }
    }
  });
  // "Share to Rilla" from another app's share sheet (installed PWA on Android Chrome)
  function handleShare(sh) {
    var m = (sh.url || sh.text || '').match(/https?:\/\/[^\s]+/);
    if (m) submitLink(m[0]); else if ((sh.text || '').length >= 10) submitText(sh.text);
  }
  function poll(id, n) {
    if (n > 120) { busy(false); note('Still working. It will appear in All saved when done.'); return; }
    setTimeout(function () {
      api('/api/jobs/' + id).then(function (j) {
        if (j.status === 'done') {
          busy(false); var r = j.result || {};
          note((r.saved && r.saved.length) ? 'Saved ' + r.saved.length + ': ' + r.saved.map(function (s) { return s.title; }).join(', ') : (r.duplicates && r.duplicates.length ? 'Already on your map.' : 'Nothing specific to save in that.'));
          load();
        } else if (j.status === 'failed') { busy(false); note(j.error || 'That could not be read.'); }
        else poll(id, n + 1);
      }).catch(function () { poll(id, n + 1); });
    }, 3000);
  }

  if ('serviceWorker' in navigator) { navigator.serviceWorker.register('sw.js').catch(function () {}); }

  if (!token) { showGate('Rilla turns the videos you watch into places and offers on your own map.'); return; }
  api('/api/me').then(function (me) {
    if (me.location) state.loc = me.location;
    state.view = state.loc ? 'near' : 'all';
    load();
    var pending = share;
    try { var stored = sessionStorage.getItem('rilla_share'); if (!pending && stored) pending = JSON.parse(stored); sessionStorage.removeItem('rilla_share'); } catch (e) {}
    if (pending) handleShare(pending);
  }).catch(fail);
})();
