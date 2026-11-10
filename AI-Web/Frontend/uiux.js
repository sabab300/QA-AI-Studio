/* QA AI Studio - AI UI/UX Testing workspace (QA Automation tab) and UI/UX Quality report tab (V4).
 * Workflow in four tabs: Design & Upload -> Test & Run -> Reports -> History. Select/upload a design, enter the website URL, choose tests,
 * run, read PASS/FAIL with evidence, export Excel or create a ClickUp ticket, and find everything again in History.
 * Independent of Playwright, API and SQL automation. All values are rendered with textContent; secrets are write-only.
 * Host contract: window.installUiux({apiFetch, hasPermission, apiBase, token}) - same contract as installApiDiscovery. */
(function () {
  'use strict';

  var CATEGORY_LABELS = {
    layout: 'Layout and alignment', spacing: 'Spacing and dimensions', css: 'CSS and styling', colors: 'Colors', typography: 'Fonts and typography', forms: 'Fields and forms', buttons: 'Buttons',
    icons_images: 'Icons and images', tables: 'Tables', navigation: 'Navigation', responsive: 'Responsive design', accessibility: 'Accessibility', visual: 'Visual comparison', states: 'Interactive states'
  };
  var CATEGORY_HINTS = {
    layout: 'Element positions, overlap, clipped content, missing layers.', spacing: 'Element sizes, gaps, table row heights and column widths.', css: 'Borders, corner radius, shadow, opacity.',
    colors: 'Background, border, text and icon colours.', typography: 'Font family, size, weight, line height, alignment, wording.', forms: 'Fields, labels, placeholders, required markers.',
    buttons: 'Button presence, text and styling.', icons_images: 'Icons, images, broken images.', tables: 'Columns, rows and header text.', navigation: 'Menu items and their order.',
    responsive: 'Overflow and viewport match for the tested viewport only.', accessibility: 'Automated checks only; manual-review items are listed separately.',
    visual: 'Pixel comparison of the page with the design image.', states: 'Hover, focus and other states - only where a design frame is mapped for the state.'
  };
  var ENGINE_CATEGORY_LABELS = { layout: 'Layout and spacing', css: 'CSS and styling', typography: 'Typography', forms: 'Forms', buttons: 'Buttons', navigation: 'Navigation', tables: 'Tables',
    icons_images: 'Icons and images', responsive: 'Responsive', accessibility: 'Accessibility', states: 'Interactive states', visual: 'Visual comparison' };
  var CATEGORY_ORDER = ['layout', 'spacing', 'css', 'colors', 'typography', 'forms', 'buttons', 'icons_images', 'tables', 'navigation', 'responsive', 'accessibility', 'visual', 'states'];
  var DEFAULT_CATS = CATEGORY_ORDER.filter(function (k) { return k !== 'states'; });
  var CLASSES = ['Confirmed Difference', 'Potential Difference', 'Needs Investigation', 'Not Verifiable'];
  var SEVERITIES = ['Critical', 'High', 'Medium', 'Low'];
  // The visible tolerance groups; each sets one or more engine tolerances (all validated server-side against the same ranges).
  var TOL_GROUPS = [
    { key: 'pos', label: 'Position / alignment (px)', keys: ['position_px', 'align_px'], def: 2, max: 100 },
    { key: 'size', label: 'Dimensions (px)', keys: ['size_px'], def: 2, max: 100 },
    { key: 'gap', label: 'Spacing (px)', keys: ['gap_px'], def: 2, max: 100 },
    { key: 'color', label: 'Colour difference', keys: ['color_distance'], def: 10, max: 100 },
    { key: 'visual', label: 'Visual similarity (colour threshold 1-255)', keys: [], def: 28, min: 1, max: 255, visual: true },
    { key: 'type', label: 'Typography (px)', keys: ['font_size_px', 'line_height_px', 'letter_spacing_px'], def: 0.5, max: 10 },
    { key: 'vp', label: 'Responsive viewport (px)', keys: ['viewport_px'], def: 16, max: 400 }
  ];
  var HIER = [
    ['domain', 'Domain'], ['module', 'Module'], ['knowledge_name', 'Knowledge Name'], ['document_type', 'Document Type'], ['version', 'Version'],
    ['source_type', 'Source Type'], ['source_file', 'Source Document']
  ];
  var TERMINAL = ['Completed', 'Partial', 'Failed', 'Blocked', 'Cancelled', 'Interrupted'];
  var ACTIVE = ['Queued', 'Running', 'Paused'];
  var ENVIRONMENTS = ['QA', 'UAT', 'Production', 'Development', 'Staging', 'Other'];
  var P = '/api/uiux';
  var STORE_KEY = 'uiux.lastDesign';

  function remember(value) { try { if (value) localStorage.setItem(STORE_KEY, JSON.stringify(value)); else localStorage.removeItem(STORE_KEY); } catch (e) { /* storage may be blocked: the selection is simply not remembered */ } }
  function recall() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch (e) { return null; } }

  // ------------------------------------------------------------------------------------------------ DOM helpers
  function h(tag, props) {
    var node = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (key) {
      var value = props[key];
      if (value === undefined || value === null || value === false) return;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'onclick') node.addEventListener('click', value);
      else if (key === 'onchange') node.addEventListener('change', value);
      else if (key === 'oninput') node.addEventListener('input', value);
      else if (/^on[a-z]+$/.test(key) && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else if (key === 'value') node.value = value;
      else if (key === 'checked') node.checked = !!value;
      else if (key === 'disabled') node.disabled = true;
      else if (key === 'hidden') node.hidden = true;
      else if (key === 'style') node.setAttribute('style', value);
      else node.setAttribute(key, value === true ? '' : value);
    });
    for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
    return node;
  }
  function append(node, child) {
    if (child === undefined || child === null || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(node, c); }); return; }
    node.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function put(node) { clear(node); for (var i = 1; i < arguments.length; i++) append(node, arguments[i]); return node; }
  function fmtDate(value) { if (!value) return ''; var d = new Date(value); return isNaN(d) ? String(value) : d.toLocaleString(); }
  function slug(text) { return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
  function chip(text, kind) { return h('span', { class: 'ux-chip ux-' + (kind || 'neutral'), text: text }); }
  function classKind(c) { return { 'Confirmed Difference': 'bad', 'Potential Difference': 'warn', 'Needs Investigation': 'info', 'Not Verifiable': 'mute' }[c] || 'neutral'; }
  function sevKind(s) { return { Critical: 'crit', High: 'bad', Medium: 'warn', Low: 'info' }[s] || 'neutral'; }
  function statusKind(s) { return { Completed: 'good', Approved: 'good', Active: 'good', Analyzed: 'good', Running: 'info', Queued: 'info', Partial: 'warn', 'In Review': 'warn', Suggested: 'warn', Draft: 'mute', Failed: 'bad', Blocked: 'bad', Rejected: 'bad', Cancelled: 'mute', Interrupted: 'warn', Paused: 'warn', Superseded: 'mute', 'Needs attention': 'warn' }[s] || 'neutral'; }
  function field(label, control, hint) {
    var id = 'ux' + Math.random().toString(36).slice(2, 9);
    control.id = control.id || id;
    return h('div', { class: 'ux-field' }, h('label', { for: control.id, text: label }), control, hint ? h('span', { class: 'ux-hint', text: hint }) : null);
  }
  function select(options, value, onchange, props) {
    var node = h('select', props || {});
    options.forEach(function (o) {
      var opt = typeof o === 'object' ? o : { value: o, label: o };
      node.add(new Option(opt.label, opt.value));
    });
    var wanted = value === undefined || value === null ? null : String(value);
    var known = Array.prototype.some.call(node.options, function (o) { return o.value === wanted; });
    if (known) node.value = wanted; else if (node.options.length) node.selectedIndex = 0;
    if (onchange) node.addEventListener('change', onchange);
    return node;
  }
  function table(headers, rows, caption) {
    var head = h('tr', null, headers.map(function (t) { return h('th', { scope: 'col', text: t }); }));
    var body = h('tbody', null, rows);
    return h('div', { class: 'ux-tw' }, h('table', null, caption ? h('caption', { class: 'ux-sr', text: caption }) : null, h('thead', null, head), body));
  }
  function td(content, cls) { return h('td', { class: cls || '' }, content); }
  function btn(label, onclick, opts) {
    opts = opts || {};
    return h('button', { type: 'button', class: 'btn' + (opts.primary ? ' btn-primary' : '') + (opts.small ? ' btn-sm' : ''), onclick: onclick, disabled: opts.disabled, title: opts.title, 'aria-label': opts.aria }, label);
  }
  function kv(pairs) {
    var dl = h('dl', { class: 'ux-kv' });
    pairs.forEach(function (p) {
      if (p[1] === undefined || p[1] === null || p[1] === '') return;
      dl.appendChild(h('dt', { text: p[0] }));
      dl.appendChild(h('dd', null, typeof p[1] === 'object' && p[1].nodeType ? p[1] : String(p[1])));
    });
    return dl;
  }
  function kpi(label, value, kind, onclick) { return h(onclick ? 'button' : 'div', { type: onclick ? 'button' : undefined, class: 'ux-kpi' + (kind ? ' ' + kind : ''), onclick: onclick }, h('b', { text: String(value) }), h('span', { text: label })); }
  function card(title, body, actions) {
    return h('section', { class: 'ux-card' }, h('div', { class: 'ux-card-head' }, h('h3', { text: title }), actions ? h('div', { class: 'ux-row' }, actions) : null), h('div', { class: 'ux-card-body' }, body));
  }

  // ------------------------------------------------------------------------------------------------ installer
  window.installUiux = function (app) {
    var root = document.getElementById('auto-uiux');
    var reportRoot = document.getElementById('rep-uiux');
    if (reportRoot && !reportRoot.dataset.installed) { reportRoot.dataset.installed = 'true'; installReport(app, reportRoot); }
    if (!root || root.dataset.installed) return;
    root.dataset.installed = 'true';
    installWorkspace(app, root);
  };

  function api(app, path, options) {
    options = options || {};
    var init = { method: options.method || 'GET' };
    if (options.json !== undefined) init.body = JSON.stringify(options.json);
    return app.apiFetch(P + path, init);
  }
  function qs(params) {
    var parts = [];
    Object.keys(params).forEach(function (k) { if (params[k] !== '' && params[k] !== undefined && params[k] !== null) parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k])); });
    return parts.length ? '?' + parts.join('&') : '';
  }
  function rawFetch(app, path, init) {
    init = init || {};
    init.headers = Object.assign({}, init.headers || {}, { Authorization: 'Bearer ' + app.token() });
    return fetch(app.apiBase + P + path, init).then(function (res) {
      if (res.ok) return res;
      return res.json().catch(function () { return {}; }).then(function (data) {
        var detail = data && data.detail;
        if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg || ''; }).join('; ');
        throw new Error(typeof detail === 'string' && detail ? detail : 'Request failed (HTTP ' + res.status + '). Nothing was changed.');
      });
    });
  }
  var blobCache = {};
  function blobUrl(app, path) {
    if (blobCache[path]) return Promise.resolve(blobCache[path]);
    return rawFetch(app, path).then(function (r) { return r.blob(); }).then(function (b) {
      var keys = Object.keys(blobCache);
      if (keys.length > 120) { URL.revokeObjectURL(blobCache[keys[0]]); delete blobCache[keys[0]]; }
      blobCache[path] = URL.createObjectURL(b);
      return blobCache[path];
    });
  }
  function image(app, path, alt, cls) {
    var img = h('img', { class: cls || 'ux-img', alt: alt || '', loading: 'lazy' });
    blobUrl(app, path).then(function (url) { img.src = url; }).catch(function () { img.replaceWith(h('span', { class: 'ux-hint', text: 'Image unavailable' })); });
    return img;
  }
  function download(app, path, filename, onError) {
    rawFetch(app, path).then(function (r) { return r.blob(); }).then(function (blob) {
      var a = document.createElement('a'); var url = URL.createObjectURL(blob);
      a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    }).catch(onError);
  }

  // ------------------------------------------------------------------------------------------------ V4 helpers
  var RESULTS = ['PASS', 'FAIL', 'Needs Review', 'Not Verifiable', 'Not Applicable', 'Not Executed'];
  function resultKind(r) { return { PASS: 'good', FAIL: 'bad', 'Needs Review': 'warn', 'Not Verifiable': 'mute', 'Not Applicable': 'mute', 'Not Executed': 'mute' }[r] || 'neutral'; }
  function resultMark(r) { return { PASS: '✓ ', FAIL: '✗ ', 'Needs Review': '? ', 'Not Verifiable': '− ', 'Not Applicable': '− ', 'Not Executed': '− ' }[r] || ''; }
  function resultChip(r) { return h('span', { class: 'ux-chip ux-' + resultKind(r) + ' ux-res', 'data-result': r, text: resultMark(r) + r }); }
  function safeUrl(u) { return /^https:\/\//i.test(String(u || '')) ? u : ''; }
  function extLink(url, text, cls) {
    var u = safeUrl(url);
    return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer', class: cls || '', text: text }) : h('span', { text: text });
  }
  function clamp(text, cls) { return h('div', { class: 'ux-clamp ' + (cls || ''), title: String(text || '') }, String(text || '')); }
  function dur(seconds) { if (seconds == null) return ''; var m = Math.floor(seconds / 60), s = seconds % 60; return m ? m + ' min ' + s + ' s' : s + ' s'; }

  // A dialog in the application's own modal style. Escape closes the top dialog, Tab stays inside it, focus returns to the opener.
  function openModal(title, sub, body, actions, opts) {
    opts = opts || {};
    var opener = document.activeElement;
    var card = h('div', { class: 'modal-card ux ux-mcard' + (opts.wide ? ' ux-mwide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('h3', { text: title }), sub ? h('div', { class: 'modal-sub', text: sub }) : null, h('div', { class: 'ux-mbody' }, body), h('div', { class: 'modal-actions' }, actions || []));
    var back = h('div', { class: 'modal-overlay show ux-moverlay', 'data-ux-modal': slug(title) }, card);
    var closed = false;
    function focusables() { return Array.prototype.slice.call(card.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')); }
    function topmost() { var all = document.querySelectorAll('.ux-moverlay'); return all[all.length - 1] === back; }
    function onKey(e) {
      if (!topmost()) return;
      if (e.key === 'Escape') { e.stopPropagation(); close(); return; }
      if (e.key === 'Tab') {
        var f = focusables(); if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    function close() {
      if (closed) return; closed = true;
      document.removeEventListener('keydown', onKey, true);
      if (back.parentNode) back.parentNode.removeChild(back);
      if (opts.onclose) opts.onclose();
      if (opener && opener.focus && document.body.contains(opener)) { try { opener.focus(); } catch (e) { /* the opener may be gone */ } }
    }
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(back);
    var f0 = focusables()[0]; if (f0) f0.focus();
    return { close: close, card: card, back: back, body: card.querySelector('.ux-mbody'), actions: card.querySelector('.modal-actions') };
  }

  // ------------------------------------------------------------------------------------------------ workspace
  function installWorkspace(app, root) {
    var can = function (a) { return app.hasPermission('automation', a); };
    var S = {
      health: null, sel: {}, versionId: null, asset: null, prepared: null, busy: false, current: null, tab: 'design', meta: null,
      q: { url: '', cats: null, customTol: false, tol: {}, authorized: false, needsAuth: false, auth: 'none', frames: {}, rows: null, urlNote: null },
      run: null, steps: [], liveSeq: 0, poll: null, polling: null, starting: false, elapsedTimer: null, lastSummary: null,
      rep: { runId: null, header: null, items: [], total: 0, offset: 0, limit: 25, filters: { result: '', category: '', severity: '', ticket: '', text: '' }, loading: false },
      history: null, historyText: ''
    };
    var ui = {};
    var last = recall();
    if (last && last.url) S.q.url = last.url;

    // -------- shell
    var banner = h('div', { class: 'ux-banner', role: 'alert', hidden: true });
    var statusLine = h('div', { class: 'ux-status', role: 'status', 'aria-live': 'polite' });
    var healthBar = h('div', { class: 'ux-row ux-health' });
    var tabsBar = h('div', { class: 'ux-tabs', role: 'tablist', 'aria-label': 'AI UI/UX Testing sections' });
    var panels = {};
    var TABS = [['design', 'Design & Upload'], ['run', 'Test & Run'], ['reports', 'Reports'], ['history', 'History']];
    var TAB_ALIAS = { test: 'run', app: 'run', results: 'reports' };
    TABS.forEach(function (t) {
      var b = h('button', { type: 'button', role: 'tab', class: 'ux-tab' + (t[0] === S.tab ? ' on' : ''), 'data-ux-tab': t[0], text: t[1], 'aria-selected': t[0] === S.tab ? 'true' : 'false', onclick: function () { showTab(t[0]); } });
      tabsBar.appendChild(b);
      panels[t[0]] = h('div', { class: 'ux-panel', role: 'tabpanel', hidden: t[0] !== S.tab, 'data-ux-panel': t[0] });
    });
    put(root, h('div', { class: 'ux' },
      h('div', { class: 'ux-title' }, h('h2', { text: 'AI UI/UX Testing' }), healthBar),
      h('p', { class: 'ux-lead', text: 'Select a design, enter the website URL, choose what to test and run. Every result shows what the design expects, what the website has and the evidence. AI text is advisory and is never evidence.' }),
      banner, tabsBar, statusLine, TABS.map(function (t) { return panels[t[0]]; })));

    function showTab(name) {
      name = TAB_ALIAS[name] || name;
      S.tab = name;
      tabsBar.querySelectorAll('.ux-tab').forEach(function (b) { var on = b.getAttribute('data-ux-tab') === name; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
      Object.keys(panels).forEach(function (k) { panels[k].hidden = k !== name; });
      renderTab(name);
    }
    function renderTab(name) {
      if (name === 'design') renderDesign();
      else if (name === 'run') renderRun();
      else if (name === 'reports') renderReports();
      else if (name === 'history') renderHistory();
    }
    function say(text) { statusLine.textContent = text || ''; }
    function fail(error) {
      banner.hidden = !error; banner.textContent = error ? (error.message || String(error)) : '';
      if (error) say('');
    }
    // One action at a time; a click that arrives while another action runs is ignored.
    function work(fn, doing) {
      if (S.busy) return Promise.resolve();
      S.busy = true; fail(null); if (doing) say(doing);
      S.current = Promise.resolve().then(fn).catch(fail).then(function () { S.busy = false; if (doing && statusLine.textContent === doing) say(''); });
      return S.current;
    }
    function empty(text) { return h('div', { class: 'ux-empty', text: text }); }
    function details(title, body, open) { var d = h('details', { class: 'ux-details ux-adv' }, h('summary', { text: title }), h('div', { class: 'ux-adv-body' }, body)); if (open) d.open = true; return d; }
    function hierPath() { var s = S.sel || {}; return [s.domain, s.module, s.knowledge_name, s.document_type, s.version].filter(Boolean).join(' › '); }
    function postJson(path, json) {
      return fetch(app.apiBase + P + path, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + app.token() }, body: JSON.stringify(json) })
        .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, status: res.status, data: data }; }); });
    }
    function detailOf(r) {
      var d = r.data && r.data.detail;
      if (r.status === 404 && d === 'Not Found') return 'The running service does not have the new AI UI/UX Testing endpoints yet. Restart QA AI Studio (run_web.py) and try again.';
      if (Array.isArray(d)) d = d.map(function (x) { return x.msg || ''; }).join('; ');
      return typeof d === 'string' && d ? d : 'Request failed (HTTP ' + r.status + '). Nothing was changed.';
    }

    // -------- health
    function loadHealth() {
      return api(app, '/health').then(function (hl) {
        S.health = hl;
        var b = hl.build || {};
        put(healthBar,
          chip(hl.browser.available ? 'Browser ready' : 'Browser unavailable', hl.browser.available ? 'good' : 'bad'),
          chip(b.loaded_hash && b.disk_hash && b.loaded_hash !== b.disk_hash ? 'Restart needed: code on disk is newer' : 'Build ' + String(b.loaded_hash || '').slice(0, 8), b.loaded_hash === b.disk_hash ? 'neutral' : 'warn'));
        if (b.loaded_hash && b.disk_hash && b.loaded_hash !== b.disk_hash) fail(new Error('The AI UI/UX Testing files on disk are newer than the running service. Restart QA AI Studio (run_web.py) so this page uses the updated service.'));
        return true;
      }).catch(function (e) {
        var missing = /not found|\b404\b/i.test(String((e && e.message) || ''));
        put(healthBar, chip(missing ? 'Server restart needed' : 'Service unavailable', 'bad'));
        fail(missing ? new Error('The running server has not loaded the AI UI/UX Testing service yet. Restart QA AI Studio (run_web.py) so it loads the updated code, then reload this page.') : e);
        return false;
      });
    }
    function loadMeta() {
      if (S.meta) return Promise.resolve(S.meta);
      return api(app, '/categories').then(function (m) { S.meta = m; if (!S.q.cats) S.q.cats = (m.categories || []).filter(function (c) { return c.default; }).map(function (c) { return c.key; }); return m; })
        .catch(function () { S.meta = { categories: [], tolerances: { defaults: {}, limits: {} } }; if (!S.q.cats) S.q.cats = DEFAULT_CATS.slice(); return S.meta; });
    }
    function categoryList() {
      var fromServer = (S.meta && S.meta.categories) || [];
      return fromServer.length ? fromServer : CATEGORY_ORDER.map(function (k) { return { key: k, label: CATEGORY_LABELS[k], hint: CATEGORY_HINTS[k], default: DEFAULT_CATS.indexOf(k) >= 0 }; });
    }
    function levelOptions(level, parents) {
      return api(app, '/hierarchy/options' + qs(Object.assign({ level: level }, parents || {}))).then(function (r) {
        return (r.options || []).map(function (o) { return typeof o === 'object' ? { value: o.value, label: o.value + (o.kind ? ' (' + o.kind + ')' : ''), asset_id: o.asset_id } : { value: o, label: o }; });
      });
    }
    function fillSelect(node, opts, label) {
      var prev = node.value;
      clear(node);
      node.add(new Option('Select ' + label.toLowerCase() + '...', ''));
      opts.forEach(function (o) { node.add(new Option(o.label, o.value)); });
      if (prev && opts.some(function (o) { return o.value === prev; })) node.value = prev;
    }

    // ================================================================================== design adoption
    function adoptDesign(a) {
      S.asset = a; S.q.rows = null;
      S.versionId = a.document_version_id || S.versionId;
      S.sel = { domain: a.domain, module: a.module, knowledge_name: a.knowledge_name, document_type: a.document_type, version: a.version, source_type: a.source_type, source_file: a.source_file };
      S.q.frames = {};
      var any = (a.frames || []).some(function (f) { return f.selected; });
      (a.frames || []).forEach(function (f) { S.q.frames[f.id] = any ? !!f.selected : true; });
      remember(Object.assign({}, S.sel, { asset_id: a.id, url: S.q.url }));
    }
    function openDesign(id) {
      return api(app, '/designs/' + encodeURIComponent(id)).then(function (a) {
        adoptDesign(a);
        if (!can('execute')) return null;
        return api(app, '/quick/prepare/' + encodeURIComponent(id), { method: 'POST' }).then(function (r) {
          if (r.asset) { var keep = S.q.frames; S.asset = r.asset; S.q.frames = keep; (r.asset.frames || []).forEach(function (f) { if (!(f.id in keep)) keep[f.id] = !!f.selected; }); }
          S.prepared = r;
        });
      });
    }
    function selectedFrameIds() { return Object.keys(S.q.frames).filter(function (k) { return S.q.frames[k]; }); }
    function selectedFrames() { var ids = selectedFrameIds(); return ((S.asset && S.asset.frames) || []).filter(function (f) { return ids.indexOf(f.id) >= 0; }); }

    // ================================================================================== TAB 1: DESIGN & UPLOAD
    function renderDesign() {
      var p = put(panels.design);
      var a = S.asset;
      var actions = h('div', { class: 'ux-row ux-design-actions' },
        btn('Select Design', openSelectDesign, { primary: !a, title: 'Pick a design that is already stored in the Knowledge Hub.' }),
        btn('Upload Design', openUploadDesign, { disabled: !can('create'), title: can('create') ? 'Add a design file to the Knowledge Hub.' : 'You do not have permission to upload.' }),
        btn('Continue to Test', function () { showTab('run'); }, { primary: !!a, disabled: !a, title: a ? '' : 'Select or upload a design first.' }));
      p.appendChild(actions);
      if (!a) { p.appendChild(empty('No design selected. Use Select Design to pick one from the Knowledge Hub, or Upload Design to add a .fig, Figma JSON, PNG, JPG or SVG file.')); return; }
      p.appendChild(designSummary(a));
    }
    function designSummary(a) {
      var an = a.analysis || {};
      var frames = a.frames || [];
      var shown = selectedFrames()[0] || frames[0];
      var dims = an.dimensions;
      var dimText = dims && dims.width ? (dims.width + ' × ' + dims.height + ' px — from ' + dims.source + (dims.reliable ? '' : ' (not the design size)')) : 'not available';
      var preview = shown && shown.has_image ? image(app, '/frames/' + encodeURIComponent(shown.id) + '/image', 'Preview of design frame ' + shown.name, 'ux-evimg ux-preview') : h('div', { class: 'ux-thumb ux-noimg', text: 'No preview image' });
      var statusKindMap = { Completed: 'good', Limited: 'warn', Failed: 'bad' };
      var body = [h('div', { class: 'ux-sumgrid' },
        h('div', { class: 'ux-previewbox' }, preview, h('div', { class: 'ux-hint', text: shown ? 'Preview of ' + shown.name : '' })),
        h('div', { class: 'ux-sumbody' },
          kv([['Design', a.source_file], ['Version', a.version], ['Source', a.source_type + ' (' + a.asset_kind + ')'], ['Knowledge Hub path', hierPath()],
              ['Analysis', h('span', null, chip(an.status || 'Unknown', statusKindMap[an.status] || 'neutral'), ' ', an.summary || '')],
              ['Available checks', an.available || 'None'], ['Frame', an.frame || (shown && shown.name) || ''], ['Dimensions', dimText]]),
          (an.limitations || []).length ? h('details', { class: 'ux-details' }, h('summary', { text: 'Limitations (' + an.limitations.length + ')' }), h('ul', { class: 'ux-list' }, an.limitations.map(function (l) { return h('li', { text: l }); }))) : null))];
      if (frames.length > 1) {
        var list = h('div', { class: 'ux-frames' }, frames.map(function (f) {
          var cb = h('input', { type: 'checkbox', checked: !!S.q.frames[f.id], 'aria-label': 'Test frame ' + f.name, onchange: function () { S.q.frames[f.id] = cb.checked; S.q.rows = null; } });
          return h('label', { class: 'ux-check ux-framepick' }, cb, h('span', { text: f.name + ' (' + Math.round(f.width) + '×' + Math.round(f.height) + ')' }));
        }));
        body.push(details('Frames to test (' + selectedFrameIds().length + ' of ' + frames.length + ')', list));
      }
      return card('Selected design', body);
    }

    // ---- Select Design: the Knowledge Hub hierarchy, one level at a time
    function openSelectDesign() {
      var sels = {}, docs = {}, state = { assetId: null };
      var grid = h('div', { class: 'kh-form-grid ux-hmodal' });
      var note = h('div', { class: 'ux-note bad', role: 'alert', hidden: true });
      var ok = btn('Select', function () { choose(); }, { primary: true, disabled: true });
      HIER.forEach(function (lvl, i) {
        var s = h('select', { 'aria-label': lvl[1], id: 'ux-sd-' + lvl[0], disabled: i > 0 });
        s.add(new Option('Select ' + lvl[1].toLowerCase() + '...', ''));
        sels[lvl[0]] = s;
        s.addEventListener('change', function () { changed(i); });
        grid.appendChild(h('div', { class: 'field' }, h('label', { for: s.id, text: lvl[1] }), s));
      });
      var dlg = openModal('Select Design', 'Choose the design from the Knowledge Hub hierarchy.', [grid, note], [btn('Cancel', function () { dlg.close(); }), ok], { wide: true });
      function parents(upTo) { var o = {}; for (var i = 0; i < upTo; i++) { var k = HIER[i][0]; if (sels[k].value) o[k] = sels[k].value; } return o; }
      function problem(e) { note.hidden = false; note.textContent = e && e.message ? e.message : String(e); }
      function loadLevel(i, want) {
        var key = HIER[i][0], node = sels[key];
        if (i > 0 && !sels[HIER[i - 1][0]].value) { fillSelect(node, [], HIER[i][1]); node.disabled = true; return Promise.resolve(); }
        return levelOptions(key, parents(i)).then(function (opts) {
          fillSelect(node, opts, HIER[i][1]); node.disabled = false;
          if (key === 'source_file') { docs = {}; opts.forEach(function (o) { docs[o.value] = o.asset_id; }); }
          var pick = want && opts.some(function (o) { return o.value === want; }) ? want : (i >= 5 && opts.length === 1 ? opts[0].value : '');
          if (pick) node.value = pick;
          if (!opts.length && i >= 5) problem(new Error(i === 6 ? 'No design is registered under this source type yet.' : 'No source is registered for this version yet. Use Upload Design.'));
        }).catch(function (e) { fillSelect(node, [], HIER[i][1]); node.disabled = true; problem(e); });
      }
      function settle() {
        state.assetId = sels.source_file.value ? docs[sels.source_file.value] || null : null;
        ok.disabled = !state.assetId;
      }
      function changed(i) {
        note.hidden = true; state.assetId = null; ok.disabled = true;
        for (var k = i + 1; k < HIER.length; k++) { fillSelect(sels[HIER[k][0]], [], HIER[k][1]); sels[HIER[k][0]].value = ''; sels[HIER[k][0]].disabled = true; }
        if (i >= HIER.length - 1) { settle(); return; }
        var chain = loadLevel(i + 1);
        if (i + 1 === 5) chain = chain.then(function () { return sels.source_type.value ? loadLevel(6) : null; });     // a single source type is chosen for the user
        chain.then(settle);
      }
      function choose() {
        if (!state.assetId) return;
        ok.disabled = true;
        work(function () {
          return openDesign(state.assetId).then(function () { dlg.close(); say('Design selected: ' + S.asset.source_file); renderDesign(); });
        }, 'Opening the design...').then(function () { ok.disabled = false; });
      }
      // start from the current selection when there is one
      var want = S.asset ? { domain: S.asset.domain, module: S.asset.module, knowledge_name: S.asset.knowledge_name, document_type: S.asset.document_type, version: S.asset.version, source_type: S.asset.source_type, source_file: S.asset.source_file } : null;
      var chain = loadLevel(0, want && want.domain);
      for (var i = 1; i < HIER.length; i++) {
        chain = chain.then((function (ii) { return function () {
          if (!want || !sels[HIER[ii - 1][0]].value) return null;
          return loadLevel(ii, want[HIER[ii][0]]);
        }; })(i));
      }
      chain.then(settle);
    }

    // ---- Upload Design: a dialog like External Test Case Import
    var UPLOAD_ACCEPT = ['.fig', '.json', '.png', '.jpg', '.jpeg', '.svg'];
    function openUploadDesign() {
      var domain = h('select', { id: 'ux-up-domain', 'aria-label': 'Domain' });
      var module_ = h('select', { id: 'ux-up-module', 'aria-label': 'Module' });
      var knowledge = h('input', { type: 'text', id: 'ux-up-knowledge', list: 'ux-dl-knowledge', maxlength: 120, 'aria-label': 'Knowledge Name', autocomplete: 'off' });
      var doctype = h('input', { type: 'text', id: 'ux-up-doctype', list: 'ux-dl-doctype', maxlength: 120, 'aria-label': 'Document Type', autocomplete: 'off' });
      var version = h('input', { type: 'text', id: 'ux-up-version', list: 'ux-dl-version', maxlength: 60, 'aria-label': 'Version', autocomplete: 'off' });
      var dlK = h('datalist', { id: 'ux-dl-knowledge' }), dlT = h('datalist', { id: 'ux-dl-doctype' }), dlV = h('datalist', { id: 'ux-dl-version' });
      var srcType = h('select', { id: 'ux-up-srctype', 'aria-label': 'Source Type' }); srcType.add(new Option('Figma', 'Figma')); srcType.disabled = true;
      var fileInput = h('input', { type: 'file', id: 'ux-up-file', accept: UPLOAD_ACCEPT.join(',') + ',image/png,image/jpeg,image/svg+xml', hidden: true, 'aria-label': 'Design file' });
      var fileName = h('span', { class: 'muted-small ux-hint', id: 'ux-up-filename', text: 'No file selected.' });
      var chooseBtn = btn('Choose file', function () { fileInput.click(); });
      var err = h('div', { class: 'error-inline ux-note bad', id: 'ux-up-error', role: 'alert', hidden: true });
      var save = btn('Save & Analyze', function () { submit(); }, { primary: true });
      var cancel = btn('Cancel', function () { dlg.close(); });
      var grid = h('div', { class: 'kh-form-grid ux-hmodal' },
        h('div', { class: 'field' }, h('label', { for: domain.id, text: 'Domain' }), domain),
        h('div', { class: 'field' }, h('label', { for: module_.id, text: 'Module' }), module_),
        h('div', { class: 'field' }, h('label', { for: knowledge.id, text: 'Knowledge Name' }), knowledge, dlK),
        h('div', { class: 'field' }, h('label', { for: doctype.id, text: 'Document Type' }), doctype, dlT),
        h('div', { class: 'field' }, h('label', { for: version.id, text: 'Version' }), version, dlV),
        h('div', { class: 'field' }, h('label', { for: srcType.id, text: 'Source Type' }), srcType));
      var dlg = openModal('Upload Design', 'Select the hierarchy and the design file. Existing Knowledge, Document Type and Version values are suggested; a new value creates that node.',
        [grid, h('div', { class: 'field' }, h('label', { text: 'Source Document' }), h('div', { class: 'ux-row' }, chooseBtn, fileName, fileInput),
          h('span', { class: 'ux-hint', text: 'Supported: .fig, Figma JSON, PNG, JPG and safe SVG. The original file name is kept.' })), err], [cancel, save], { wide: true });
      function problem(text) { err.hidden = !text; err.textContent = text || ''; }
      function fillDatalist(dl, opts) { clear(dl); opts.forEach(function (o) { dl.appendChild(h('option', { value: o.value })); }); }
      levelOptions('domain').then(function (o) {
        fillSelect(domain, o, 'domain');
        var pre = S.sel && S.sel.domain; if (pre && o.some(function (x) { return x.value === pre; })) { domain.value = pre; return onDomain(S.sel.module); }
      }).catch(function (e) { problem(e.message); });
      function onDomain(wantModule) {
        if (!domain.value) { fillSelect(module_, [], 'module'); return Promise.resolve(); }
        return levelOptions('module', { domain: domain.value }).then(function (o) { fillSelect(module_, o, 'module'); if (wantModule && o.some(function (x) { return x.value === wantModule; })) { module_.value = wantModule; return onModule(); } });
      }
      function onModule() {
        if (!domain.value || !module_.value) { fillDatalist(dlK, []); return Promise.resolve(); }
        return levelOptions('knowledge_name', { domain: domain.value, module: module_.value }).then(function (o) { fillDatalist(dlK, o); });
      }
      function onKnowledge() {
        if (!(domain.value && module_.value && knowledge.value)) { fillDatalist(dlT, []); return; }
        levelOptions('document_type', { domain: domain.value, module: module_.value, knowledge_name: knowledge.value.trim() }).then(function (o) { fillDatalist(dlT, o); }).catch(function () { fillDatalist(dlT, []); });
      }
      function onDoctype() {
        if (!(domain.value && module_.value && knowledge.value && doctype.value)) { fillDatalist(dlV, []); return; }
        levelOptions('version', { domain: domain.value, module: module_.value, knowledge_name: knowledge.value.trim(), document_type: doctype.value.trim() }).then(function (o) { fillDatalist(dlV, o); }).catch(function () { fillDatalist(dlV, []); });
      }
      domain.addEventListener('change', function () { problem(''); onDomain(); });
      module_.addEventListener('change', function () { problem(''); onModule(); });
      knowledge.addEventListener('change', onKnowledge); knowledge.addEventListener('blur', onKnowledge);
      doctype.addEventListener('change', onDoctype); doctype.addEventListener('blur', onDoctype);
      if (S.sel && S.sel.knowledge_name) { knowledge.value = S.sel.knowledge_name; doctype.value = S.sel.document_type || ''; version.value = S.sel.version || ''; }
      fileInput.addEventListener('change', function () {
        var f = fileInput.files[0]; fileName.textContent = f ? f.name + ' (' + Math.max(1, Math.round(f.size / 1024)) + ' KB)' : 'No file selected.'; problem('');
      });
      function validate() {
        if (!domain.value) return 'Select the Domain.';
        if (!module_.value) return 'Select the Module.';
        if (!knowledge.value.trim()) return 'Enter the Knowledge Name.';
        if (!doctype.value.trim()) return 'Enter the Document Type.';
        if (!version.value.trim()) return 'Enter the Version.';
        var f = fileInput.files[0];
        if (!f) return 'Choose the design file (.fig, Figma JSON, PNG, JPG or SVG).';
        var ext = (f.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
        if (UPLOAD_ACCEPT.indexOf(ext) < 0) return 'This file type is not supported. Use .fig, Figma JSON, PNG, JPG or SVG.';
        if (f.size === 0) return 'The selected file is empty.';
        if (f.size > 150 * 1024 * 1024) return 'The file is larger than the 150 MiB limit.';
        return '';
      }
      function submit() {
        var why = validate(); if (why) { problem(why); return; }
        var f = fileInput.files[0];
        problem(''); save.disabled = true; cancel.disabled = true; save.textContent = 'Saving and analysing...';
        rawFetch(app, '/designs/upload' + qs({ domain: domain.value, module: module_.value, knowledge_name: knowledge.value.trim(), document_type: doctype.value.trim(), version: version.value.trim(), filename: f.name, create_missing: 'true' }),
          { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: f })
          .then(function (r) { return r.json(); })
          .then(function (r) {
            var id = (r.asset || r).id;
            return openDesign(id).then(function () {
              dlg.close(); renderDesign();
              say(r.duplicate ? 'This exact file is already saved in this version; the existing design was selected instead of creating a duplicate.' : 'Design saved to the Knowledge Hub and analysed.');
            });
          })
          .catch(function (e) { problem(e.message || String(e)); save.disabled = false; cancel.disabled = false; save.textContent = 'Save & Analyze'; });   // the selections stay as the user entered them
      }
    }

    // ================================================================================== TAB 2: TEST & RUN
    function urlProblem(value) {
      value = (value || '').trim();
      if (!value) return 'Enter the target website URL (for example https://timemedico.com.pk).';
      var u; try { u = new URL(value); } catch (e) { return 'The URL is not valid. Include http:// or https://.'; }
      if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'Only http:// and https:// addresses can be tested.';
      if (u.username || u.password) return 'Remove the user name and password from the URL; use the sign-in option under Advanced.';
      return '';
    }
    function tolProblems() {
      var out = [];
      if (!S.q.customTol) return out;
      TOL_GROUPS.forEach(function (g) {
        var v = S.q.tol[g.key];
        if (v === undefined || v === '') return;
        var min = g.min !== undefined ? g.min : 0;
        if (typeof v !== 'number' || isNaN(v)) out.push(g.label + ' must be a number.');
        else if (v < min || v > g.max) out.push(g.label + ' must be between ' + min + ' and ' + g.max + '.');
      });
      return out;
    }
    function activeRun() { return S.run && ACTIVE.indexOf(S.run.status) >= 0; }
    function runBlockers() {
      var out = [];
      if (!can('execute')) out.push('You do not have permission to run tests.');
      if (!S.asset) out.push('Select or upload a design first.');
      else if (!selectedFrameIds().length) out.push('Select at least one design frame on the Design & Upload tab.');
      var u = urlProblem(S.q.url); if (u) out.push(u);
      if (!(S.q.cats || []).length) out.push('Select at least one test category.');
      tolProblems().forEach(function (t) { out.push(t); });
      if (S.q.needsAuth && !S.q.authorized) out.push('Confirm that you are authorized to test this website.');
      if (activeRun()) out.push('A test is already running.');
      return out;
    }
    function buildBody() {
      var tol = {}, visual = {};
      if (S.q.customTol) TOL_GROUPS.forEach(function (g) {
        var v = S.q.tol[g.key];
        if (v === undefined || v === '' || typeof v !== 'number') return;
        if (g.visual) visual.color_threshold = v; else g.keys.forEach(function (k) { tol[k] = v; });
      });
      return { document_version_id: S.versionId, asset_id: S.asset.id, url: S.q.url.trim(), environment: 'auto', authorized: !!S.q.authorized, frame_ids: selectedFrameIds(), rows: S.q.rows || [],
        viewport: 'auto', custom_viewport: null, categories: (S.q.cats || []).slice(), tolerances: tol, visual: visual, build_label: '', auth: S.q.auth, ai: false, colour_scheme: 'light' };
    }

    function renderRun() {
      var p = put(panels.run);
      if (!S.asset) {
        p.appendChild(empty('Select or upload a design on the Design & Upload tab first.'));
        p.appendChild(h('div', { class: 'ux-row' }, btn('Go to Design & Upload', function () { showTab('design'); }, { primary: true })));
        return;
      }
      loadMeta().then(function () { if (S.tab === 'run' && !ui.catsBox) renderRun(); });
      if (!S.meta) return;
      p.appendChild(designStrip());
      p.appendChild(setupCard());
      p.appendChild(controlsCard());
      refreshRun(); updateControls();
      S.liveSeq = 0;
      if (S.run) applyLive(null);
    }
    function designStrip() {
      var a = S.asset, an = a.analysis || {};
      return h('div', { class: 'ux-ctx ux-strip' }, h('span', { class: 'ux-hint', text: 'Design' }), h('strong', { text: a.source_file + ' · ' + a.version }),
        chip(an.status || 'Unknown', { Completed: 'good', Limited: 'warn', Failed: 'bad' }[an.status] || 'neutral'), h('span', { class: 'ux-hint', text: an.available || '' }),
        btn('Change', function () { showTab('design'); }, { small: true }));
    }
    function setupCard() {
      var url = h('input', { type: 'url', id: 'ux-url', maxlength: 2048, value: S.q.url, placeholder: 'https://timemedico.com.pk', 'aria-label': 'Target Website URL', autocomplete: 'off',
        oninput: function () { S.q.url = url.value; S.q.urlNote = null; S.q.needsAuth = false; S.q.authorized = false; drawAuth(); refreshRun(); },
        onchange: function () { checkTarget(false); } });
      ui.url = url;
      ui.urlNote = h('div', { class: 'ux-hint', 'aria-live': 'polite' });
      ui.authBox = h('div', { class: 'ux-authbox' });
      function drawAuth() {
        put(ui.authBox);
        var n = S.q.urlNote;
        put(ui.urlNote); ui.urlNote.className = 'ux-hint';
        if (n) { ui.urlNote.className = 'ux-note' + (n.kind === 'bad' ? ' bad' : (n.kind === 'warn' ? ' warn' : '')); ui.urlNote.textContent = n.text; }
        if (S.q.needsAuth) {
          var cb = h('input', { type: 'checkbox', id: 'ux-authorized', checked: !!S.q.authorized, onchange: function () { S.q.authorized = cb.checked; refreshRun(); } });
          ui.authBox.appendChild(h('label', { class: 'ux-check' }, cb, h('span', { text: 'I confirm I am authorized to test this website.' })));
        }
      }
      ui.drawAuth = drawAuth;
      var cats = categoryList();
      var catBox = h('div', { class: 'ux-cats', role: 'group', 'aria-label': 'Test categories' }, cats.map(function (c) {
        var cb = h('input', { type: 'checkbox', checked: (S.q.cats || []).indexOf(c.key) >= 0, 'data-cat': c.key, onchange: function () {
          var i = S.q.cats.indexOf(c.key); if (cb.checked && i < 0) S.q.cats.push(c.key); if (!cb.checked && i >= 0) S.q.cats.splice(i, 1); drawCount(); refreshRun(); } });
        return h('label', { class: 'ux-cat ux-cat-compact', title: c.hint }, cb, h('span', null, h('b', { text: c.label }), h('span', { class: 'ux-tip', 'aria-hidden': 'true', text: ' ⓘ' })));
      }));
      ui.catsBox = catBox;
      ui.catCount = h('span', { class: 'ux-hint' });
      function drawCount() { ui.catCount.textContent = (S.q.cats || []).length + ' of ' + cats.length + ' categories selected'; }
      drawCount();
      function setAll(on) { S.q.cats = on ? cats.map(function (c) { return c.key; }) : []; catBox.querySelectorAll('input[data-cat]').forEach(function (cb) { cb.checked = on; }); drawCount(); refreshRun(); }
      var tolBox = h('div', { class: 'ux-sub', hidden: !S.q.customTol });
      var tolCb = h('input', { type: 'checkbox', id: 'ux-customtol', checked: !!S.q.customTol, onchange: function () { S.q.customTol = tolCb.checked; tolBox.hidden = !S.q.customTol; refreshRun(); } });
      function drawTol() {
        put(tolBox, h('div', { class: 'ux-grid' }, TOL_GROUPS.map(function (g) {
          var i = h('input', { type: 'number', step: 'any', min: g.min !== undefined ? g.min : 0, max: g.max, placeholder: String(g.def), value: S.q.tol[g.key] !== undefined ? S.q.tol[g.key] : '', 'aria-label': g.label,
            oninput: function () { if (i.value === '') delete S.q.tol[g.key]; else S.q.tol[g.key] = Number(i.value); i.classList.toggle('ux-bad', tolProblems().some(function (t) { return t.indexOf(g.label) === 0; })); refreshRun(); } });
          return field(g.label, i, 'Default ' + g.def + ', allowed ' + (g.min !== undefined ? g.min : 0) + ' to ' + g.max);
        })), h('p', { class: 'ux-hint', text: 'A difference within a tolerance passes. Out-of-range values are refused; no check is switched off by a tolerance.' }));
      }
      drawTol();
      var hasCreds = false;
      var auth = select([{ value: 'none', label: 'No sign-in (public pages)' }, { value: 'stored', label: 'Use stored credentials for this website' }], S.q.auth, function () { S.q.auth = auth.value; });
      api(app, '/targets').then(function (r) { hasCreds = (r.targets || []).some(function (t) { return t.credentials && t.credentials.is_set; }); if (!hasCreds) auth.title = 'No stored credentials yet. Store them under Advanced options in an earlier setup.'; }).catch(function () { /* optional */ });
      drawAuth();
      var frames = selectedFrames();
      var info = frames.length === 1 && S.asset.analysis && S.asset.analysis.dimensions
        ? h('p', { class: 'ux-hint', text: 'Design frame “' + frames[0].name + '” will be compared with the page.' + (S.asset.analysis.dimensions.reliable ? ' The page is tested at the design size.' : ' The design gives no reliable size, so the page is tested at 1440 × 900 and the report says so.') }) : null;
      var mapping = mappingBlock();
      return card('Test setup', [
        field('Target Website URL', url, 'The page to compare with the design.'), ui.urlNote, ui.authBox, info, mapping,
        h('div', { class: 'ux-sub' }, h('div', { class: 'ux-row' }, h('h4', { text: 'What to test' }), btn('Select All', function () { setAll(true); }, { small: true }), btn('Clear All', function () { setAll(false); }, { small: true }), ui.catCount), catBox),
        h('label', { class: 'ux-check' }, tolCb, h('span', { text: 'Customize Tolerances' })), tolBox,
        details('Advanced (optional)', [field('Website sign-in', auth, 'Credentials are stored securely and never shown.')])]);
    }
    function mappingBlock() {
      if (!S.q.rows || S.q.rows.length < 2) return null;
      var rows = S.q.rows.map(function (r) {
        var route = h('input', { type: 'text', value: r.route, maxlength: 300, 'aria-label': 'Page for ' + r.frame, oninput: function () { r.route = route.value; r.status = 'edited'; } });
        var skip = h('input', { type: 'checkbox', checked: !!r.skip, 'aria-label': 'Skip ' + r.frame, onchange: function () { r.skip = skip.checked; } });
        return h('tr', null, td(h('strong', { text: r.frame })), td(route), td(chip(r.status === 'needs_choice' ? 'Please check' : 'Matched', r.status === 'needs_choice' ? 'warn' : 'good')), td(skip));
      });
      return h('div', { class: 'ux-sub' }, h('h4', { text: 'Pages for the design frames' }), h('p', { class: 'ux-hint', text: 'More than one page could match some frames. Check the page for each, then run again.' }), table(['Frame', 'Page path', 'Match', 'Skip'], rows, 'Frame to page mapping'));
    }
    function checkTarget(quiet) {
      var prob = urlProblem(S.q.url);
      if (prob) { if (!quiet) { S.q.urlNote = { kind: 'bad', text: prob }; ui.drawAuth && ui.drawAuth(); } return Promise.resolve(false); }
      return postJson('/quick/target-check', { url: S.q.url.trim(), environment: 'auto', authorized: !!S.q.authorized }).then(function (r) {
        if (r.ok) { S.q.needsAuth = false; S.q.urlNote = { kind: r.data.reachable === false ? 'warn' : 'good', text: 'Authorized target ' + r.data.origin + (r.data.reachable === false ? ' (not reachable right now)' : '') }; }
        else if (r.status === 409 && r.data.code === 'needs_confirmation') { S.q.needsAuth = true; S.q.urlNote = { kind: 'warn', text: r.data.detail }; }
        else S.q.urlNote = { kind: 'bad', text: detailOf(r) };
        ui.drawAuth && ui.drawAuth(); refreshRun(); return r.ok;
      }).catch(function (e) { S.q.urlNote = { kind: 'bad', text: e.message }; ui.drawAuth && ui.drawAuth(); return false; });
    }

    // ---- run controls and live view
    function controlsCard() {
      ui.runBtn = h('button', { type: 'button', class: 'btn btn-primary', id: 'ux-run', onclick: startRun }, '▶ Run Test');
      ui.pauseBtn = h('button', { type: 'button', class: 'btn', id: 'ux-pause', onclick: function () { pauseResume(false); } }, '⏸ Pause');
      ui.resumeBtn = h('button', { type: 'button', class: 'btn', id: 'ux-resume', onclick: function () { pauseResume(true); } }, '▶ Resume');
      ui.stopBtn = h('button', { type: 'button', class: 'btn', id: 'ux-stop', onclick: stopRun }, '■ Stop');
      ui.reasons = h('ul', { class: 'ux-reasons', 'aria-live': 'polite' });
      ui.liveStatus = h('div', { class: 'ux-row' });
      ui.liveBar = h('div', { class: 'ux-bar', role: 'progressbar', 'aria-label': 'Test progress', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': 0 }, h('i', { style: 'width:0%' }));
      ui.liveFacts = h('div', { class: 'ux-livefacts' });
      ui.liveImg = h('img', { class: 'ux-liveimg', alt: 'Latest browser screenshot of the running test', hidden: true });
      ui.liveEmpty = h('div', { class: 'ux-empty', text: 'A preview appears here while the test runs.' });
      ui.liveCap = h('div', { class: 'ux-hint' });
      ui.after = h('div', { class: 'ux-row ux-after' });
      return card('Run', [
        h('div', { class: 'ux-row ux-controls' }, ui.runBtn, ui.pauseBtn, ui.resumeBtn, ui.stopBtn), ui.reasons,
        h('div', { class: 'ux-live' }, h('div', { class: 'ux-livehead' }, h('h4', { text: 'Live preview' }), ui.liveStatus), ui.liveBar, ui.liveFacts,
          h('div', { class: 'ux-liveframe' }, ui.liveEmpty, ui.liveImg, ui.liveCap), ui.after)]);
    }
    function refreshRun() {
      if (!ui.runBtn || !document.body.contains(ui.runBtn)) return;
      var why = runBlockers(), busy = activeRun();
      ui.runBtn.disabled = why.length > 0 || S.starting;
      ui.runBtn.title = why.length ? why[0] : '';
      put(ui.reasons, why.filter(function (w) { return !busy || w.indexOf('already running') < 0; }).map(function (w) { return h('li', { text: w }); }));
      ui.reasons.hidden = !why.length || busy;
    }
    function updateControls() {
      if (!ui.pauseBtn || !document.body.contains(ui.pauseBtn)) return;
      var st = S.run && S.run.status;
      ui.pauseBtn.disabled = st !== 'Running' || !can('execute');
      ui.resumeBtn.disabled = st !== 'Paused' || !can('execute');
      ui.stopBtn.disabled = !activeRun() || !can('execute');
      ui.pauseBtn.title = ui.pauseBtn.disabled ? 'Available while a test is running.' : 'Pause at the next safe point; finished results are kept.';
      ui.resumeBtn.title = ui.resumeBtn.disabled ? 'Available while a test is paused.' : '';
      ui.stopBtn.title = ui.stopBtn.disabled ? 'Available while a test is running.' : 'Stop safely; finished results and evidence are kept.';
      refreshRun();
    }
    function startRun() {
      var why = runBlockers(); if (why.length) return fail(new Error(why[0]));
      S.starting = true; fail(null); refreshRun(); say('Starting the test...');
      postJson('/runs/quick', buildBody()).then(function (r) {
        S.starting = false; say('');
        if (r.ok) {
          S.run = r.data; S.steps = []; S.liveSeq = 0; S.lastSummary = null; S.q.rows = null; S.q.needsAuth = false;
          S.q.urlNote = { kind: 'good', text: 'Authorized target ' + (r.data.target || {}).origin };
          remember(Object.assign({}, S.sel, { asset_id: S.asset.id, url: S.q.url }));
          trackRun(r.data.run_id); renderRun(); return;
        }
        if (r.status === 409 && r.data.code === 'needs_mapping') { S.q.rows = r.data.plan.rows; renderRun(); return fail(new Error(r.data.detail)); }
        if (r.status === 409 && r.data.code === 'needs_confirmation') { S.q.needsAuth = true; S.q.urlNote = { kind: 'warn', text: r.data.detail }; renderRun(); return fail(new Error(r.data.detail)); }
        fail(new Error(detailOf(r))); refreshRun();
      }).catch(function (e) { S.starting = false; say(''); fail(e); refreshRun(); });
    }
    function pauseResume(resuming) {
      if (!S.run) return;
      work(function () { return api(app, '/runs/' + S.run.run_id + (resuming ? '/resume' : '/pause'), { method: 'POST' }).then(function (r) { S.run = r; updateControls(); applyLive(null); }); }, resuming ? 'Resuming...' : 'Pausing at the next safe point...');
    }
    function stopRun() {
      if (!S.run) return;
      work(function () { return api(app, '/runs/' + S.run.run_id + '/cancel', { method: 'POST' }).then(function (r) { S.run = r; updateControls(); applyLive(null); }); }, 'Stopping...');
    }
    function trackRun(runId) {
      clearTimeout(S.poll);
      S.polling = runId;
      clearInterval(S.elapsedTimer);
      S.elapsedTimer = setInterval(function () { if (S.run && activeRun() && ui.elapsed && document.body.contains(ui.elapsed)) ui.elapsed.textContent = elapsedText(); }, 1000);
      (function tick() {
        if (S.polling !== runId) return;
        var after = S.steps.length ? S.steps[S.steps.length - 1].seq : 0;
        api(app, '/runs/' + encodeURIComponent(runId) + '/live' + qs({ after: after })).then(function (d) {
          if (S.polling !== runId) return;
          if (!S.run || S.run.run_id !== runId) S.run = { run_id: runId };
          S.run.status = d.status; S.run.status_detail = d.status_detail; S.run.progress = d.progress; S.run.cancel_requested = d.cancel_requested;
          if (d.started_at) S.run.started_at = d.started_at;
          (d.steps || []).forEach(function (s) { S.steps.push(s); });
          applyLive(d);
          if (TERMINAL.indexOf(d.status) < 0) S.poll = setTimeout(tick, d.status === 'Paused' ? 1500 : 900);
          else finishRun(runId);
        }).catch(function (e) { fail(e); S.poll = setTimeout(tick, 4000); });
      })();
    }
    function finishRun(runId) {
      S.polling = null; clearInterval(S.elapsedTimer);
      api(app, '/runs/' + encodeURIComponent(runId)).then(function (r) { S.run = r; return api(app, '/runs/' + encodeURIComponent(runId) + '/summary').then(function (sm) { S.lastSummary = sm; }).catch(function () { S.lastSummary = null; }); })
        .then(function () { S.history = null; applyLive(null); updateControls(); });
    }
    function elapsedText() {
      var r = S.run; if (!r) return '';
      var start = r.started_at ? new Date(r.started_at).getTime() : null;
      if (!start || isNaN(start)) return '';
      var end = TERMINAL.indexOf(r.status) >= 0 && r.ended_at ? new Date(r.ended_at).getTime() : Date.now();
      return dur(Math.max(0, Math.round((end - start) / 1000)));
    }
    var liveBlob = '';
    function applyLive(d) {
      var r = S.run; if (!r) return;
      if (!ui.liveStatus || !document.body.contains(ui.liveStatus)) return;
      var pr = r.progress || {}, st = r.status;
      ui.elapsed = ui.elapsed && document.body.contains(ui.elapsed) ? ui.elapsed : h('span', { class: 'ux-mono', 'aria-label': 'Elapsed time' });
      ui.elapsed.textContent = elapsedText();
      put(ui.liveStatus, chip(st || '', statusKind(st)), ui.elapsed, h('strong', { class: 'ux-mono', text: r.run_id }));
      var pct = TERMINAL.indexOf(st) >= 0 ? (st === 'Completed' ? 100 : (pr.percent || 0)) : (pr.percent || 0);
      ui.liveBar.setAttribute('aria-valuenow', pct); ui.liveBar.firstChild.setAttribute('style', 'width:' + pct + '%');
      var live = pr.live || {};
      put(ui.liveFacts, [['Progress', Math.round(pct) + '%'], ['Category', pr.category || ''], ['Action', pr.message || ''], ['Status', st === 'Paused' ? 'Paused — completed results are kept' : st], ['Page', pr.page || live.url || '']]
        .filter(function (x) { return x[1]; }).map(function (x) { return h('span', { class: 'ux-fact' }, h('span', { class: 'ux-hint', text: x[0] }), h('b', { text: x[1] })); }));
      if (r.status_detail && TERMINAL.indexOf(st) >= 0) append(ui.liveFacts, h('div', { class: 'ux-note' + (st === 'Completed' ? '' : ' warn'), text: r.status_detail }));
      var seq = live.seq || 0;
      if (seq && seq !== S.liveSeq) {
        S.liveSeq = seq;
        rawFetch(app, '/runs/' + encodeURIComponent(r.run_id) + '/live-frame' + qs({ seq: seq })).then(function (res) { return res.blob(); }).then(function (b) {
          var url = URL.createObjectURL(b); if (liveBlob) URL.revokeObjectURL(liveBlob); liveBlob = url; ui.liveImg.src = url; ui.liveImg.hidden = false; ui.liveEmpty.hidden = true;
          ui.liveCap.textContent = (live.caption || '') + ' — refreshed screenshots, not a video stream';
        }).catch(function () { /* the next step produces another frame */ });
      }
      put(ui.after);
      if (TERMINAL.indexOf(st) >= 0) {
        var sm = S.lastSummary, c = sm && sm.counts && sm.counts.by_status;
        if (c) append(ui.after, h('span', { class: 'ux-hint', text: c.PASS + ' PASS · ' + c.FAIL + ' FAIL · ' + c['Needs Review'] + ' Needs Review · ' + c['Not Verifiable'] + ' Not Verifiable' + (st !== 'Completed' ? ' — run ' + st.toLowerCase() : '') }));
        append(ui.after, btn('View Report', function () { openReport(r.run_id); }, { primary: true }));
      }
      updateControls();
    }

    // ================================================================================== TAB 3: REPORTS
    function openReport(runId) {
      S.rep = { runId: runId, header: null, items: [], total: 0, offset: 0, limit: 25, filters: { result: '', category: '', severity: '', ticket: '', text: '' }, loading: false };
      showTab('reports');
    }
    function repQuery() {
      var f = S.rep.filters;
      return qs({ result: f.result, category: f.category, severity: f.severity, ticket_status: f.ticket, text: f.text, limit: S.rep.limit, offset: S.rep.offset });
    }
    function loadReport(keepPage) {
      var R = S.rep;
      if (!R.runId) return Promise.resolve();
      R.loading = true;
      var header = R.header && keepPage ? Promise.resolve(R.header) : api(app, '/runs/' + encodeURIComponent(R.runId) + '/summary');
      return header.then(function (hd) {
        R.header = hd;
        return api(app, '/runs/' + encodeURIComponent(R.runId) + '/results' + repQuery());
      }).then(function (r) { R.items = r.items; R.total = r.total; R.loading = false; }).catch(function (e) { R.loading = false; throw e; });
    }
    function latestRunId() {
      if (S.run && S.run.run_id && TERMINAL.indexOf(S.run.status) >= 0) return Promise.resolve(S.run.run_id);
      return api(app, '/history' + qs({ limit: 20 })).then(function (r) {
        var withResults = (r.runs || []).filter(function (x) { return x.has_results; })[0];
        return withResults ? withResults.run_id : ((r.runs || [])[0] || {}).run_id || null;
      });
    }
    function renderReports() {
      var p = put(panels.reports);
      if (!S.rep.runId) {
        latestRunId().then(function (id) {
          if (!id) { put(panels.reports, empty('No test has been run yet. Run a test on the Test & Run tab, and its report appears here.')); return; }
          S.rep.runId = id; renderReports();
        }).catch(fail);
        p.appendChild(empty('Loading the latest report...'));
        return;
      }
      p.appendChild(empty('Loading the report...'));
      loadReport(false).then(drawReport).catch(function (e) { put(panels.reports, empty('The report could not be loaded.')); fail(e); });
    }
    function drawReport() {
      var R = S.rep, hd = R.header, p = put(panels.reports);
      if (!hd) return;
      if (!hd.has_results) {
        p.appendChild(card('AI UI/UX Test Report', [kv([['Run', hd.run_id], ['Design', hd.design], ['Website', hd.website], ['Status', hd.status + (hd.status_detail ? ' — ' + hd.status_detail : '')]]),
          empty('This run has no saved result rows (it ran before result reporting existed, or it stopped before any screen finished). Its findings are in the downloadable reports below.'),
          h('div', { class: 'ux-row' }, exportButtons(hd.run_id, true))]));
        return;
      }
      var c = hd.counts.by_status;
      var kpis = h('div', { class: 'ux-kpis ux-reskpis' }, [
        kpi('PASS', c.PASS, 'good', function () { setFilter('result', 'PASS'); }), kpi('FAIL', c.FAIL, 'bad', function () { setFilter('result', 'FAIL'); }),
        kpi('Needs Review', c['Needs Review'], 'warn', function () { setFilter('result', 'Needs Review'); }), kpi('Not Verifiable', c['Not Verifiable'], '', function () { setFilter('result', 'Not Verifiable'); }),
        kpi('Not Applicable', c['Not Applicable'], '', function () { setFilter('result', 'Not Applicable'); }), kpi('Not Executed', c['Not Executed'], '', function () { setFilter('result', 'Not Executed'); }),
        kpi('Total checks', hd.counts.checks_executed, ''), kpi('Categories selected', hd.categories_selected.length, ''), kpi('Categories checked', hd.categories_checked.length, ''),
        kpi('Duration', dur(hd.duration_s) || 'n/a', ''), kpi('Run status', hd.status, hd.status === 'Completed' ? 'good' : 'warn')]);
      kpis.querySelectorAll('.ux-kpi').forEach(function (k) { k.setAttribute('data-kpi', k.querySelector('span').textContent); });
      var total = hd.counts.total_rows, sum = RESULTS.reduce(function (a, k) { return a + (c[k] || 0); }, 0);
      var head = card('AI UI/UX Test Report', [
        h('div', { class: 'ux-repmeta' }, kv([['Design', hd.design + (hd.version ? ' (' + hd.version + ')' : '')], ['Website', hd.website], ['Test date', fmtDate(hd.test_date)], ['Run ID', hd.run_id]])),
        kpis, h('p', { class: 'ux-hint', 'data-reconcile': total === sum ? 'ok' : 'bad', text: 'Result rows: ' + total + ' = ' + RESULTS.map(function (k) { return c[k] + ' ' + k; }).join(' + ') + (total === sum ? '' : ' — these do not add up; please report this.') + ' Total checks counts PASS + FAIL + Needs Review only.' }),
        (hd.notes || []).map(function (n) { return h('div', { class: 'ux-note warn', text: n }); }),
        (hd.units_not_tested || []).length ? h('div', { class: 'ux-note warn', text: 'Not tested: ' + hd.units_not_tested.map(function (u) { return u.screen + ' — ' + u.reason; }).join('; ') }) : null,
        h('div', { class: 'ux-row ux-exports' }, exportButtons(hd.run_id))], [btn('Refresh', function () { work(function () { return loadReport(false).then(drawReport); }); }, { small: true })]);
      p.appendChild(head);
      p.appendChild(filterBar());
      p.appendChild(resultsTable());
      p.appendChild(pager());
    }
    function exportButtons(runId, secondaryOnly) {
      function dl(fmt, ext, label, primary) {
        return btn(label, function () { say('Preparing ' + label + '...'); download(app, '/runs/' + encodeURIComponent(runId) + '/report?format=' + fmt, runId + '.' + ext, fail); setTimeout(function () { say(''); }, 2500); }, { primary: primary, title: primary ? 'Download the Excel report with embedded screenshots.' : '' });
      }
      var out = [];
      if (!secondaryOnly) out.push(dl('xlsx', 'xlsx', 'Export Excel', true));
      out.push(h('span', { class: 'ux-hint', text: 'Other formats:' }), dl('pdf', 'pdf', 'PDF'), dl('html', 'html', 'HTML'), dl('json', 'json', 'JSON'));
      return out;
    }
    function setFilter(key, value) { S.rep.filters[key] = S.rep.filters[key] === value ? '' : value; S.rep.offset = 0; work(function () { return loadReport(true).then(drawReport); }); }
    function filterBar() {
      var f = S.rep.filters;
      var text = h('input', { type: 'search', id: 'ux-rep-search', placeholder: 'Search results', value: f.text, 'aria-label': 'Search results', maxlength: 120,
        onkeydown: function (e) { if (e.key === 'Enter') apply(); } });
      var cat = select([{ value: '', label: 'All categories' }].concat(((S.rep.header && S.rep.header.counts && Object.keys(S.rep.header.counts.by_category)) || []).map(function (k) { return { value: k, label: S.rep.header.counts.by_category[k].label || k }; })), f.category, apply, { 'aria-label': 'Category', id: 'ux-rep-cat' });
      var res = select([{ value: '', label: 'All results' }].concat(RESULTS), f.result, apply, { 'aria-label': 'Result', id: 'ux-rep-result' });
      var sev = select([{ value: '', label: 'Any severity' }].concat(SEVERITIES), f.severity, apply, { 'aria-label': 'Severity', id: 'ux-rep-sev' });
      var tk = select([{ value: '', label: 'Any ticket status' }, 'Not Created', 'Created', 'Failed'], f.ticket, apply, { 'aria-label': 'Ticket status', id: 'ux-rep-ticket' });
      function apply() { S.rep.filters = { text: text.value.trim(), category: cat.value, result: res.value, severity: sev.value, ticket: tk.value }; S.rep.offset = 0; (S.current || Promise.resolve()).then(function () { return work(function () { return loadReport(true).then(drawReport); }); }); }
      return h('div', { class: 'ux-filters' }, text, cat, res, sev, tk, btn('Search', apply, { small: true }), btn('Reset', function () { S.rep.filters = { result: '', category: '', severity: '', ticket: '', text: '' }; S.rep.offset = 0; work(function () { return loadReport(true).then(drawReport); }); }, { small: true }));
    }
    function resultsTable() {
      var R = S.rep;
      if (!R.items.length) return empty(R.total === 0 && Object.keys(R.filters).some(function (k) { return R.filters[k]; }) ? 'No result matches these filters.' : 'No results to show.');
      var rows = R.items.map(function (r) {
        var created = r.ticket_status === 'Created' && r.ticket;
        var evBtn = btn('View Evidence', function () { openEvidence(r.id); }, { small: true, aria: 'View evidence for ' + r.test_id });
        var tkBtn = btn('Create Ticket', function () { openTicket(r.id); }, { small: true, disabled: !can('execute'), title: can('execute') ? '' : 'You do not have permission to create tickets.', aria: 'Create ClickUp ticket for ' + r.test_id });
        var tr = h('tr', { 'data-result-id': r.id, 'data-test-id': r.test_id, 'data-result': r.result },
          td(h('span', { class: 'ux-mono', text: r.test_id })), td(r.category_label), td(h('div', null, clamp(r.name), r.member_count > 1 ? h('span', { class: 'ux-hint', text: r.member_count + ' similar elements grouped' }) : null)),
          td(clamp(r.expected)), td(clamp(r.actual)), td(h('div', null, resultChip(r.result), r.severity ? h('div', { class: 'ux-hint', text: r.severity }) : null)), td(clamp(r.reason, 'ux-clamp3')),
          td(evBtn), td(h('div', { class: 'ux-col' }, tkBtn, created ? extLink(r.ticket.task_url, 'Open Ticket') : null)),
          td(h('span', { class: 'ux-chip ux-' + (r.ticket_status === 'Created' ? 'good' : (r.ticket_status === 'Failed' ? 'bad' : 'mute')), 'data-ticket-status': r.ticket_status, text: r.ticket_status === 'Created' ? 'Created ✓' : r.ticket_status })));
        return tr;
      });
      return table(['Test ID', 'Category', 'Test / Check', 'Expected Design', 'Actual Website', 'Result', 'Reason', 'Evidence', 'ClickUp Ticket', 'Ticket Status'], rows, 'Test results');
    }
    function pager() {
      var R = S.rep, from = R.total ? R.offset + 1 : 0, to = Math.min(R.total, R.offset + R.limit);
      var size = select([{ value: '25', label: '25 per page' }, { value: '50', label: '50 per page' }, { value: '100', label: '100 per page' }], String(R.limit), function () { R.limit = +size.value; R.offset = 0; work(function () { return loadReport(true).then(drawReport); }); }, { 'aria-label': 'Rows per page' });
      return h('div', { class: 'ux-row ux-pager' }, h('span', { class: 'ux-hint', text: 'Showing ' + from + '–' + to + ' of ' + R.total }),
        btn('Previous', function () { R.offset = Math.max(0, R.offset - R.limit); work(function () { return loadReport(true).then(drawReport); }); }, { small: true, disabled: R.offset <= 0 }),
        btn('Next', function () { R.offset += R.limit; work(function () { return loadReport(true).then(drawReport); }); }, { small: true, disabled: to >= R.total }), size);
    }
    function refreshRows() { return loadReport(true).then(function () { if (S.tab === 'reports') drawReport(); }); }

    // ---- evidence dialog
    function openEvidence(resultId) {
      api(app, '/results/' + encodeURIComponent(resultId)).then(function (r) { showEvidence(r); }).catch(fail);
    }
    function specTable(sp) {
      var rows = (sp.fields || []).filter(function (f) { return f.field !== 'Screen / page' && f.field !== 'Location (hierarchy)' && f.field !== 'Position in page'; }).map(function (f) {
        var mark = { differs: '✗ Differs', match: '✓ Match', 'Not Verifiable': '− Not Verifiable', info: '' }[f.status] || '';
        return h('tr', { class: 'ux-spec-' + (f.status === 'Not Verifiable' ? 'nv' : f.status) + (f.focus ? ' ux-focus' : '') , 'data-field': f.field }, td(f.field), td(f.expected), td(f.actual), td(h('span', null, mark, f.note ? h('div', { class: 'ux-hint', text: f.note }) : null)));
      });
      return rows.length ? table(['Field', 'Expected (design)', 'Actual (website)', 'Result'], rows, 'Expected and actual specification') : null;
    }
    function evidencePanel(title, evId, box, natural) {
      var body = h('div', { class: 'ux-zbox' });
      if (!evId) { body.appendChild(h('div', { class: 'ux-empty ux-nopanel', text: 'Not available for this result.' })); return h('figure', { class: 'ux-panelfig', 'data-panel': slug(title) }, h('figcaption', null, h('strong', { text: title })), body); }
      var wrap = h('div', { class: 'ux-zwrap' });
      var img = image(app, '/evidence/' + encodeURIComponent(evId), title, 'ux-zimg');
      wrap.appendChild(img);
      if (box && natural) {
        var mark = h('div', { class: 'ux-hl', 'aria-hidden': 'true', 'data-highlight': 'true' });
        wrap.appendChild(mark);
        var place = function () {
          var nw = img.naturalWidth, nh = img.naturalHeight; if (!nw || !nh) return;
          mark.style.left = (box.x / nw * 100) + '%'; mark.style.top = (box.y / nh * 100) + '%'; mark.style.width = (box.w / nw * 100) + '%'; mark.style.height = (box.h / nh * 100) + '%';
        };
        img.addEventListener('load', place);
      }
      body.appendChild(wrap);
      return h('figure', { class: 'ux-panelfig', 'data-panel': slug(title) }, h('figcaption', null, h('strong', { text: title })), body);
    }
    function showEvidence(r) {
      var ev = r.evidence || {}, sp = ev.spec || {};
      var page = ev.mode === 'page';
      var zoom = 1;
      var panelsBox = h('div', { class: 'ux-panels3' }, [
        evidencePanel('Design Screenshot', page ? ev.page_design : ev.design, page && ev.region ? ev.region.design : null, page),
        evidencePanel('Website Screenshot', page ? ev.page_actual : ev.actual, page && ev.region ? ev.region.page : null, page),
        evidencePanel('Highlighted Difference', page ? ev.page_diff : ev.diff, page && ev.region ? ev.region.page : null, page)]);
      function setZoom(z) { zoom = Math.max(1, Math.min(4, z)); panelsBox.style.setProperty('--ux-z', zoom); zoomLabel.textContent = Math.round(zoom * 100) + '%'; }
      var zoomLabel = h('span', { class: 'ux-hint', text: '100%' });
      var zoomBar = h('div', { class: 'ux-row' }, h('span', { class: 'ux-hint', text: 'Zoom' }), btn('−', function () { setZoom(zoom - 0.5); }, { small: true, aria: 'Zoom out' }), btn('+', function () { setZoom(zoom + 0.5); }, { small: true, aria: 'Zoom in' }), btn('Reset', function () { setZoom(1); }, { small: true }), zoomLabel,
        ev.mode === 'crop' ? h('span', { class: 'ux-hint', text: 'The outlined box marks the element compared.' }) : (page ? h('span', { class: 'ux-hint', text: 'Full-page screenshots; the highlighted box marks the element.' }) : null));
      var locRows = [['Website page', sp.page_url], ['Screen / design frame', sp.screen], ['Component', sp.element], ['Location on the page', sp.website_location], ['Location in the design', sp.design_location], ['Position in page', sp.position],
        ['Element locator', sp.locator ? h('span', null, h('code', { class: 'ux-mono', text: sp.locator }), ' ', h('span', { class: 'ux-hint', text: '(' + sp.locator_kind + ')' })) : ''], ['Category', r.category_label], ['Viewport', r.viewport]];
      var members = (r.members || []).length > 1 ? h('details', { class: 'ux-details' }, h('summary', { text: 'All ' + r.member_count + ' grouped elements' + (r.members.length < r.member_count ? ' (first ' + r.members.length + ' listed)' : '') }),
        table(['Component', 'Expected', 'Actual', 'Locator'], r.members.map(function (m) { return h('tr', null, td(m.component), td(m.expected), td(m.actual), td(h('span', { class: 'ux-mono', text: m.selector }))); }), 'Grouped elements')) : null;
      var diffs = (sp.differences || []);
      var body = [
        h('div', { class: 'ux-row ux-evhead' }, resultChip(r.result), r.severity ? chip(r.severity, sevKind(r.severity)) : null, h('span', { class: 'ux-mono', text: r.test_id }), h('strong', { text: r.name })),
        h('section', { class: 'ux-evsec', 'data-section': 'summary' }, h('h4', { text: 'Issue summary' }), h('p', { class: 'ux-p', text: r.reason || '' }),
          kv([['Expected', r.expected], ['Actual', r.actual]])),
        h('section', { class: 'ux-evsec', 'data-section': 'location' }, h('h4', { text: 'Exact location' }), kv(locRows.filter(function (x) { return x[1]; })),
          sp.also_affected_total ? h('p', { class: 'ux-hint', text: 'Also affected: ' + sp.also_affected_total + ' more similar element(s)' + ((sp.also_affected || []).length ? ': ' + sp.also_affected.join(', ') : '') }) : null),
        (sp.fields || []).length ? h('section', { class: 'ux-evsec', 'data-section': 'spec' }, h('h4', { text: 'Expected vs actual specification' }), specTable(sp),
          sp.inferred ? h('div', { class: 'ux-note', text: 'Some values were read from the design picture, not from design data, and are labelled inferred.' }) : null,
          (sp.also_differs || []).length ? h('p', { class: 'ux-hint', text: 'Also differs (not part of this result): ' + sp.also_differs.map(function (d) { return d.field; }).join(', ') }) : null)
          : (sp.note ? h('section', { class: 'ux-evsec', 'data-section': 'spec' }, h('h4', { text: 'Specification' }), h('p', { class: 'ux-hint', text: sp.note })) : null),
        h('section', { class: 'ux-evsec', 'data-section': 'screens' }, h('h4', { text: 'Screenshots' }), zoomBar, panelsBox,
          ev.limitation ? h('div', { class: 'ux-note warn', text: ev.limitation }) : null, ev.mode === 'none' ? h('div', { class: 'ux-note warn', text: 'No screenshot is stored for this result.' }) : null),
        diffs.length ? h('section', { class: 'ux-evsec', 'data-section': 'difference' }, h('h4', { text: 'Difference' }), h('ul', { class: 'ux-list' }, diffs.map(function (d) { return h('li', { text: d.field + ': expected ' + d.expected + ', actual ' + d.actual }); }))) : null,
        sp.suggested_fix ? h('section', { class: 'ux-evsec ux-sugg', 'data-section': 'fix' }, h('h4', { text: 'Suggested developer fix' }), h('p', { class: 'ux-p', text: sp.suggested_fix })) : null,
        members,
        h('p', { class: 'ux-hint', text: 'Confidence ' + Math.round((r.confidence || 0) * 100) + '% · source: ' + (r.source || 'n/a') + ' · executed ' + fmtDate(r.executed_at) + ' · design ' + (r.design_name || '') + ' ' + (r.design_version || '') })
      ];
      var tk = btn('Create ClickUp Ticket', function () { dlg.close(); openTicket(r.id); }, { primary: true, disabled: !can('execute') });
      var dlg = openModal('Evidence — ' + r.test_id, null, body, [r.ticket_status === 'Created' && r.ticket ? extLink(r.ticket.task_url, 'Open existing ticket', 'btn') : null, tk, btn('Close', function () { dlg.close(); })], { wide: true, xwide: true });
      dlg.card.classList.add('ux-evmodal');
    }

    // ---- ClickUp ticket dialog
    function openTicket(resultId) {
      api(app, '/results/' + encodeURIComponent(resultId) + '/ticket-form').then(function (form) {
        if (form.ticket_status === 'Created' && (form.existing || []).length) return duplicateDialog(form);
        ticketForm(form, false);
      }).catch(fail);
    }
    function duplicateDialog(form) {
      var ex = form.existing[form.existing.length - 1];
      var dlg = openModal('Ticket already created', 'A ClickUp ticket already exists for ' + form.result.test_id + '.',
        [kv([['Task', ex.task_id], ['Title', ex.task_title], ['Created', fmtDate(ex.created_at) + ' by ' + ex.created_by]])],
        [extLink(ex.task_url, 'Open Existing', 'btn btn-primary'), btn('Create Another', function () { dlg.close(); ticketForm(form, true); }), btn('Cancel', function () { dlg.close(); })]);
    }
    function ticketForm(form, another) {
      var res = form.result, cfgs = form.configurations || [];
      var token = form.attempt_token;
      var st = { cfg: null, ws: '', space: '', folder: '', list: '', meta: null };
      var err = h('div', { class: 'ux-note bad', role: 'alert', hidden: true });
      function problem(text) { err.hidden = !text; err.textContent = text || ''; }
      if (!cfgs.length) {
        openModal('Create ClickUp Ticket', null, [h('div', { class: 'ux-note warn', text: 'No ClickUp configuration is saved. Configure ClickUp (token, workspace and list) under Reports › Test Cases Publish first, then create the ticket here.' })], [btn('Close', function () { this.closest('.ux-moverlay').remove(); })]);
        return;
      }
      var cfgSel = h('select', { id: 'ux-tk-config', 'aria-label': 'ClickUp configuration' });
      cfgs.forEach(function (c) { cfgSel.add(new Option(c.name + (c.is_default ? ' (default)' : '') + (c.secret_is_set ? '' : ' — no token'), String(c.id))); });
      var def = cfgs.filter(function (c) { return c.is_default; })[0] || cfgs[0]; cfgSel.value = String(def.id);
      var ws = h('select', { id: 'ux-tk-workspace', 'aria-label': 'Workspace' }), space = h('select', { id: 'ux-tk-space', 'aria-label': 'Space' }), folder = h('select', { id: 'ux-tk-folder', 'aria-label': 'Folder' }), list = h('select', { id: 'ux-tk-list', 'aria-label': 'List' });
      var type = select(form.task_types.map(function (t) { return { value: t, label: t }; }), form.task_type, null, { id: 'ux-tk-type', 'aria-label': 'Task type' });
      var status = h('select', { id: 'ux-tk-status', 'aria-label': 'Status' }), priority = select([{ value: '1', label: 'Urgent' }, { value: '2', label: 'High' }, { value: '3', label: 'Normal' }, { value: '4', label: 'Low' }], String(form.priority || 3), null, { id: 'ux-tk-priority', 'aria-label': 'Priority' });
      var assignee = h('select', { id: 'ux-tk-assignee', 'aria-label': 'Assignee' });
      var title = h('input', { type: 'text', id: 'ux-tk-title', maxlength: 250, value: form.title, 'aria-label': 'Ticket title' });
      var desc = h('textarea', { id: 'ux-tk-desc', rows: 12, maxlength: 30000, 'aria-label': 'Ticket description' }); desc.value = form.description;
      var tags = h('input', { type: 'text', id: 'ux-tk-tags', value: (form.tags || []).join(', '), maxlength: 300, 'aria-label': 'Tags' });
      var sev = select([{ value: '', label: 'n/a' }].concat(SEVERITIES), form.severity || '', function () { var m = { Critical: '1', High: '2', Medium: '3', Low: '4' }[sev.value]; if (m) priority.value = m; }, { id: 'ux-tk-sev', 'aria-label': 'Severity' });
      var attach = {};
      var ev = form.evidence || {};
      var attachBox = h('div', { class: 'ux-row' }, [['design', 'Expected design screenshot'], ['actual', 'Actual website screenshot'], ['diff', 'Difference screenshot']].map(function (a) {
        var has = !!ev[a[0]]; attach[a[0]] = has;
        var cb = h('input', { type: 'checkbox', checked: has, disabled: !has, 'data-attach': a[0], onchange: function () { attach[a[0]] = cb.checked; } });
        return h('label', { class: 'ux-check', title: has ? '' : 'No stored image for this result.' }, cb, h('span', { text: a[1] + (has ? '' : ' (not available)') }));
      }));
      var create = btn(another ? 'Create Another Ticket' : 'Create Ticket', function () { submit(); }, { primary: true });
      function opts(node, items, label, keep) {
        var prev = keep ? node.value : '';
        clear(node); items.forEach(function (i) { node.add(new Option(i.name, i.id)); });
        if (!items.length) node.add(new Option('No ' + label.toLowerCase() + ' available', ''));
        if (prev && items.some(function (i) { return i.id === prev; })) node.value = prev;
      }
      function disc(kind, parent) { return api(app, '/clickup/discover' + qs({ config_id: cfgSel.value, kind: kind, parent_id: parent || '' })).then(function (r) { return r.items || []; }); }
      function loadWorkspaces() {
        var c = cfgs.filter(function (x) { return String(x.id) === cfgSel.value; })[0] || {};
        return disc('workspaces').then(function (items) { opts(ws, items, 'workspaces'); if (c.workspace_id && items.some(function (i) { return i.id === c.workspace_id; })) ws.value = c.workspace_id; return loadSpaces(c); });
      }
      function loadSpaces(c) {
        if (!ws.value) { opts(space, [], 'spaces'); return Promise.resolve(); }
        return disc('spaces', ws.value).then(function (items) { opts(space, items, 'spaces'); if (c && c.space_id && items.some(function (i) { return i.id === c.space_id; })) space.value = c.space_id; return loadFolders(c); });
      }
      function loadFolders(c) {
        if (!space.value) { opts(folder, [], 'folders'); opts(list, [], 'lists'); return Promise.resolve(); }
        return disc('folders', space.value).then(function (items) {
          opts(folder, [{ id: '', name: '— none (list directly in the space) —' }].concat(items), 'folders');
          folder.value = c && c.folder_id && items.some(function (i) { return i.id === c.folder_id; }) ? c.folder_id : '';
          return loadLists(c);
        });
      }
      function loadLists(c) {
        var p = folder.value ? disc('lists', folder.value) : disc('folderless_lists', space.value);
        return p.then(function (items) { opts(list, items, 'lists'); if (c && c.list_id && items.some(function (i) { return i.id === c.list_id; })) list.value = c.list_id; return loadMeta2(); });
      }
      function loadMeta2() {
        if (!list.value) { opts(status, [], 'statuses'); opts(assignee, [], 'members'); return Promise.resolve(); }
        return api(app, '/clickup/metadata' + qs({ config_id: cfgSel.value, list_id: list.value })).then(function (m) {
          st.meta = m;
          opts(status, [{ id: '', name: '— list default —' }].concat((m.statuses || []).map(function (s) { return { id: s.name, name: s.name }; })), 'statuses');
          opts(assignee, [{ id: '', name: '— unassigned —' }].concat(m.members || []), 'members');
        });
      }
      function guard(fn) { return function () { problem(''); Promise.resolve().then(fn).catch(function (e) { problem(e.message || String(e)); }); }; }
      cfgSel.addEventListener('change', guard(loadWorkspaces));
      ws.addEventListener('change', guard(function () { return loadSpaces(null); }));
      space.addEventListener('change', guard(function () { return loadFolders(null); }));
      folder.addEventListener('change', guard(function () { return loadLists(null); }));
      list.addEventListener('change', guard(loadMeta2));
      var grid = h('div', { class: 'kh-form-grid ux-tkgrid' }, [['Configuration', cfgSel], ['Workspace', ws], ['Space', space], ['Folder', folder], ['List', list], ['Task type', type], ['Status', status], ['Priority', priority], ['Assignee', assignee], ['Severity', sev]]
        .map(function (x) { return h('div', { class: 'field' }, h('label', { for: x[1].id, text: x[0] }), x[1]); }));
      var info = kv([['Test ID', res.test_id], ['Run ID', res.run_id], ['Result', resultChip(res.result)], ['Category', res.category_label], ['Severity', res.severity || 'n/a'], ['Expected', res.expected], ['Actual', res.actual]]);
      var body = [h('h4', { text: 'Result' }), info, h('h4', { text: 'Destination' }), grid,
        h('h4', { text: 'Ticket details' }), h('div', { class: 'field' }, h('label', { for: title.id, text: 'Title' }), title),
        h('div', { class: 'field' }, h('label', { for: desc.id, text: 'Description (includes expected, actual, location, locator, difference, reason, suggested fix and reproduction steps)' }), desc),
        h('div', { class: 'field' }, h('label', { for: tags.id, text: 'Tags (comma separated)' }), tags), h('div', { class: 'field' }, h('label', { text: 'Attachments' }), attachBox), err];
      var dlg = openModal('Create ClickUp Ticket', 'Nothing is sent to ClickUp until you choose Create Ticket.', body, [btn('Cancel', function () { dlg.close(); }), create], { wide: true });
      dlg.card.classList.add('ux-tkmodal');
      guard(loadWorkspaces)();
      function submit() {
        if (!list.value) return problem('Choose the ClickUp list for the ticket.');
        if (!title.value.trim()) return problem('Enter a ticket title.');
        problem(''); create.disabled = true; create.textContent = 'Creating ticket...';
        postJson('/results/' + encodeURIComponent(res.id) + '/ticket', {
          config_id: +cfgSel.value, list_id: list.value, title: title.value.trim(), description: desc.value, task_type: type.value, priority: +priority.value, status: status.value,
          assignees: assignee.value ? [assignee.value] : [], tags: tags.value.split(',').map(function (t) { return t.trim(); }).filter(Boolean).slice(0, 12),
          attach: Object.keys(attach).filter(function (k) { return attach[k]; }), attempt_token: token, create_another: !!another
        }).then(function (r) {
          if (r.ok) return created(r.data);
          if (r.status === 409 && r.data.code === 'exists') { dlg.close(); return openTicket(res.id); }
          problem(detailOf(r)); create.disabled = false; create.textContent = another ? 'Create Another Ticket' : 'Create Ticket';
        }).catch(function (e) { problem(e.message || String(e)); create.disabled = false; create.textContent = another ? 'Create Another Ticket' : 'Create Ticket'; });
      }
      function created(out) {
        var t = out.ticket || {};
        clear(dlg.body); clear(dlg.actions);
        dlg.body.appendChild(h('div', { class: 'ux-ticket-ok', 'data-ticket-created': 'true' }, h('h4', { text: 'Ticket Created ✓' }),
          kv([['Task ID', t.task_id], ['Link', t.task_url ? extLink(t.task_url, t.task_url) : ''], ['Created', fmtDate(t.created_at)], ['Created by', t.created_by], ['Status', t.task_status || 'n/a'], ['Title', t.task_title]]),
          (t.attachments || []).length ? h('ul', { class: 'ux-list' }, t.attachments.map(function (a) { return h('li', { text: a.name + ': ' + a.status + (a.error ? ' (' + a.error + ')' : '') }); })) : null,
          (out.warnings || []).map(function (w) { return h('div', { class: 'ux-note warn', text: w }); })));
        dlg.actions.appendChild(t.task_url ? extLink(t.task_url, 'Open Ticket', 'btn btn-primary') : h('span'));
        dlg.actions.appendChild(btn('Close', function () { dlg.close(); }));
        refreshRows().catch(function () { /* the next refresh shows it */ });
        S.history = null;
      }
    }

    // ================================================================================== TAB 4: HISTORY
    function renderHistory() {
      var p = put(panels.history);
      var text = h('input', { type: 'search', id: 'ux-hist-search', placeholder: 'Search design, website or run ID', value: S.historyText, maxlength: 120, 'aria-label': 'Search history',
        onkeydown: function (e) { if (e.key === 'Enter') go(); } });
      function go() { S.historyText = text.value.trim(); S.history = null; renderHistory(); }
      p.appendChild(h('div', { class: 'ux-filters' }, text, btn('Search', go, { small: true }), btn('Refresh', function () { S.history = null; renderHistory(); }, { small: true })));
      if (!S.history) {
        p.appendChild(empty('Loading history...'));
        api(app, '/history' + qs({ limit: 200, text: S.historyText })).then(function (r) { S.history = r.runs || []; if (S.tab === 'history') renderHistory(); }).catch(function (e) { fail(e); put(panels.history, empty('History could not be loaded.')); });
        return;
      }
      if (!S.history.length) { p.appendChild(empty(S.historyText ? 'No run matches this search.' : 'No test has been run yet.')); return; }
      var rows = S.history.map(function (r) {
        var c = r.counts || {};
        var tick = r.tickets ? btn(r.tickets + ' ticket' + (r.tickets === 1 ? '' : 's'), function () { openRunTickets(r); }, { small: true, aria: 'Open ClickUp tickets for ' + r.run_id }) : null;
        return h('tr', { 'data-run-id': r.run_id },
          td(fmtDate(r.started_at || r.created_at)), td(h('div', null, h('strong', { text: r.source_file || r.knowledge_name }), h('div', { class: 'ux-hint', text: r.version }))), td(r.website || r.app_url || ''),
          td(h('span', { class: 'ux-pass', text: String(c.PASS || 0) })), td(h('span', { class: 'ux-failn', text: String(c.FAIL || 0) })), td(chip(r.status, statusKind(r.status))),
          td(h('div', { class: 'ux-row' }, btn('View Report', function () { openReport(r.run_id); }, { small: true, disabled: !r.has_results, title: r.has_results ? '' : 'This run has no saved result rows.' }),
            btn('Replay', function () { openReplay(r.run_id); }, { small: true }), btn('Export Excel', function () { say('Preparing Excel...'); download(app, '/runs/' + encodeURIComponent(r.run_id) + '/report?format=xlsx', r.run_id + '.xlsx', fail); setTimeout(function () { say(''); }, 2500); }, { small: true }), tick)));
      });
      p.appendChild(table(['Date', 'Design', 'Website', 'PASS', 'FAIL', 'Status', 'Actions'], rows, 'Test run history'));
    }
    function openRunTickets(r) {
      api(app, '/runs/' + encodeURIComponent(r.run_id) + '/tickets').then(function (d) {
        var created = (d.tickets || []).filter(function (t) { return t.outcome === 'Created'; });
        var rows = created.map(function (t) { return h('tr', null, td(t.task_title), td(t.task_id), td(t.task_url ? extLink(t.task_url, 'Open Ticket') : ''), td(fmtDate(t.created_at)), td(t.created_by)); });
        var dlg = openModal('ClickUp tickets — ' + r.run_id, null, [rows.length ? table(['Title', 'Task', 'Link', 'Created', 'By'], rows, 'Tickets created from this run') : empty('No ticket was created from this run.')], [btn('Close', function () { dlg.close(); })], { wide: true });
      }).catch(fail);
    }
    // replay: recorded steps and screenshots (or a recording when one exists); the website is never contacted
    function replayFrames(d) {
      var shots = (d.screenshots || []), byId = {};
      shots.forEach(function (e) { byId[e.id] = e; });
      var frames = [], lastEv = null;
      (d.steps || []).forEach(function (s) {
        if (s.evidence_id && byId[s.evidence_id]) lastEv = byId[s.evidence_id];
        frames.push({ at: s.at, message: s.message, status: s.status, ev: lastEv, own: !!(s.evidence_id && byId[s.evidence_id]) });
      });
      if (!frames.length) shots.forEach(function (e) { frames.push({ at: '', message: e.label, status: 'info', ev: e, own: true }); });
      return frames;
    }
    function openReplay(runId) {
      api(app, '/runs/' + encodeURIComponent(runId) + '/replay').then(function (d) {
        var videos = d.videos || [], frames = replayFrames(d), i = 0, timer = null;
        var body = [h('div', { class: 'ux-row' }, chip(d.capability === 'video' ? 'Screen recording' : (d.capability === 'screenshots' ? 'Recorded screenshots and steps' : 'Recorded steps only'), d.capability === 'video' ? 'good' : 'info'),
          chip('Recorded evidence — the website is not opened again', 'neutral')), h('p', { class: 'ux-hint', text: d.note })];
        if (videos.length) {
          var vid = h('video', { class: 'ux-video', controls: true, preload: 'metadata', 'aria-label': 'Screen recording of the test run' });
          rawFetch(app, '/evidence/' + videos[0].id).then(function (r) { return r.blob(); }).then(function (b) { vid.src = URL.createObjectURL(new Blob([b], { type: 'video/webm' })); }).catch(function () { vid.replaceWith(h('div', { class: 'ux-note warn', text: 'The recording could not be loaded.' })); });
          body.push(vid);
        }
        if (frames.length) {
          var img = h('div', { class: 'ux-replayimg' }), info = h('div', { class: 'ux-replayinfo' });
          var scrub = h('input', { type: 'range', min: 0, max: frames.length - 1, value: 0, 'aria-label': 'Replay position', oninput: function () { i = +scrub.value; draw(); } });
          var play = btn('▶ Play', function () {
            if (timer) { clearInterval(timer); timer = null; play.textContent = '▶ Play'; return; }
            play.textContent = '⏸ Pause';
            timer = setInterval(function () { if (i >= frames.length - 1) { clearInterval(timer); timer = null; play.textContent = '▶ Play'; return; } i++; scrub.value = i; draw(); }, 1200);
          }, { small: true });
          var draw = function () {
            var f = frames[i];
            put(info, h('div', { class: 'ux-row' }, chip('Step ' + (i + 1) + ' of ' + frames.length, 'neutral'), f.at ? h('span', { class: 'ux-mono', text: f.at.replace('T', ' ').slice(0, 19) }) : null,
              chip(f.status || 'info', (f.status === 'fail' || f.status === 'blocked') ? 'bad' : 'neutral')), h('p', { text: f.message }));
            put(img);
            if (f.ev) img.appendChild(h('figure', { class: 'ux-evfig' }, image(app, '/evidence/' + f.ev.id, f.ev.label, 'ux-evimg wide'), h('figcaption', { text: f.ev.label + (f.own ? '' : ' (latest screenshot before this step)') })));
            else img.appendChild(h('div', { class: 'ux-empty', text: 'No screenshot was recorded at this step.' }));
          };
          draw();
          body.push(h('h4', { text: 'Step-by-step replay' }), h('div', { class: 'ux-row' }, btn('⏮ Previous', function () { if (i > 0) { i--; scrub.value = i; draw(); } }, { small: true }), play,
            btn('Next ⏭', function () { if (i < frames.length - 1) { i++; scrub.value = i; draw(); } }, { small: true }), scrub), info, img);
        } else if (!videos.length) body.push(empty('Nothing was recorded for this run, so there is nothing to replay.'));
        var dlg = openModal('Replay — ' + runId, null, body, [btn('Close', function () { dlg.close(); })], { wide: true, onclose: function () { if (timer) clearInterval(timer); } });
      }).catch(fail);
    }

    // ================================================================================== activation
    var lastToken = null;
    function resetState() {
      clearTimeout(S.poll); clearInterval(S.elapsedTimer); S.polling = null;
      S.asset = null; S.versionId = null; S.sel = {}; S.run = null; S.steps = []; S.liveSeq = 0; S.rep = { runId: null, header: null, items: [], total: 0, offset: 0, limit: 25, filters: { result: '', category: '', severity: '', ticket: '', text: '' }, loading: false };
      S.history = null; S.lastSummary = null; S.q.rows = null; S.q.needsAuth = false; S.q.authorized = false; S.q.urlNote = null;
    }
    function restoreDesign() {
      var r = recall();
      if (!r || !r.asset_id) return Promise.resolve();
      return openDesign(r.asset_id).catch(function () { remember(null); });       // a stale remembered design is simply ignored
    }
    function resumeActiveRun() {
      return api(app, '/history' + qs({ limit: 20 })).then(function (d) {
        var active = (d.runs || []).filter(function (r) { return ACTIVE.indexOf(r.status) >= 0; })[0];
        if (active && !S.polling) { S.run = { run_id: active.run_id, status: active.status, started_at: active.started_at }; S.steps = []; S.liveSeq = 0; trackRun(active.run_id); }
      }).catch(function () { /* optional */ });
    }
    function activate() {
      var token = app.token();
      if (!token) return;
      var fresh = token !== lastToken;
      if (fresh) { lastToken = token; resetState(); }
      fail(null);
      loadHealth().then(function (ok) {
        if (!ok) return null;                       // the service itself is unavailable: show only that message
        return loadMeta().then(function () { return fresh ? restoreDesign().then(resumeActiveRun) : null; });
      }).then(function () { renderTab(S.tab); });
    }
    var tabBtn = document.querySelector('[data-tab="auto-uiux"]');
    if (tabBtn) tabBtn.addEventListener('click', activate);
    root.addEventListener('uiux:refresh', function () { S.history = null; renderTab(S.tab); });
    renderTab(S.tab);
  }

  // ------------------------------------------------------------------------------------------------ trend chart
  function trendChart(points) {
    var W = 560, H = 150, pad = 28, n = points.length;
    var max = Math.max.apply(null, [1].concat(points.map(function (p) { return Math.max(p.confirmed, p.potential); })));
    function x(i) { return pad + (n === 1 ? 0 : i * (W - 2 * pad) / (n - 1)); }
    function y(v) { return H - pad - (v / max) * (H - 2 * pad); }
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('class', 'ux-trend'); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Confirmed and potential differences per run: ' + points.map(function (p) { return p.label + ' ' + p.confirmed + ' confirmed, ' + p.potential + ' potential'; }).join('; '));
    function el(tag, attrs, text) { var e = document.createElementNS(NS, tag); Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); }); if (text !== undefined) e.textContent = text; svg.appendChild(e); return e; }
    el('line', { x1: pad, y1: H - pad, x2: W - pad, y2: H - pad, class: 'ux-axis' });
    el('line', { x1: pad, y1: pad, x2: pad, y2: H - pad, class: 'ux-axis' });
    el('text', { x: 4, y: pad + 4, class: 'ux-tick' }, String(max)); el('text', { x: 10, y: H - pad + 4, class: 'ux-tick' }, '0');
    ['confirmed', 'potential'].forEach(function (key) {
      el('polyline', { class: 'ux-line ux-line-' + key, points: points.map(function (p, i) { return x(i) + ',' + y(p[key]); }).join(' ') });
      points.forEach(function (p, i) { el('circle', { cx: x(i), cy: y(p[key]), r: i === n - 1 ? 4 : 2.5, class: 'ux-dot ux-dot-' + key }); });
    });
    points.forEach(function (p, i) { if (n <= 8 || i === 0 || i === n - 1) el('text', { x: x(i), y: H - 8, 'text-anchor': 'middle', class: 'ux-tick' }, String(p.label).slice(0, 14)); });
    return h('div', { class: 'ux-chart' }, h('div', { class: 'ux-legend' }, h('span', { class: 'ux-key ux-key-confirmed' }), ' Confirmed ', h('span', { class: 'ux-key ux-key-potential' }), ' Potential'), svg);
  }

  // ------------------------------------------------------------------------------------------------ Reports & Analytics tab
  function installReport(app, root) {
    var body = h('div'); var banner = h('div', { class: 'ux-banner', role: 'alert', hidden: true });
    put(root, h('div', { class: 'ux' }, h('div', { class: 'ux-card' }, h('div', { class: 'ux-card-head' }, h('h3', { text: 'UI/UX Quality' }), btn('Refresh', load, { small: true })),
      h('div', { class: 'ux-card-body' }, h('p', { class: 'ux-hint', text: 'Results of AI UI/UX Testing runs across designs. Counts come from measured checks; Potential and Needs Investigation items are not defects until QA confirms them.' }), banner, body))));
    function load() {
      banner.hidden = true;
      api(app, '/analytics').then(function (a) {
        var last = (a.trend || [])[a.trend.length - 1];
        var cats = {};
        (a.by_category || []).forEach(function (r) { cats[r.category] = cats[r.category] || {}; cats[r.category][r.classification] = r.n; });
        var rows = Object.keys(cats).sort().map(function (k) { var c = cats[k]; return h('tr', null, td(ENGINE_CATEGORY_LABELS[k] || k), td(String(c['Confirmed Difference'] || 0)), td(String(c['Potential Difference'] || 0)), td(String(c['Needs Investigation'] || 0)), td(String(c['Not Verifiable'] || 0))); });
        var rv = (a.review_status || []).map(function (r) { return h('li', { text: r.review_status + ': ' + r.n }); });
        put(body,
          h('div', { class: 'ux-kpis' }, kpi('Runs', a.runs || 0, ''), kpi('Latest confirmed', last ? last.confirmed : '-', 'bad'), kpi('Latest potential', last ? last.potential : '-', 'warn'), kpi('Latest critical', last ? last.critical : '-', 'crit'), kpi('Latest high', last ? last.high : '-', 'bad')),
          (a.trend || []).length > 1 ? trendChart(a.trend.map(function (r) { return { label: r.build || r.run_id.slice(-6), confirmed: r.confirmed, potential: r.potential }; })) : h('div', { class: 'ux-empty', text: (a.trend || []).length ? 'A trend needs at least two runs.' : 'No UI/UX run exists yet.' }),
          rows.length ? table(['Category', 'Confirmed', 'Potential', 'Needs investigation', 'Not verifiable'], rows, 'Findings by category') : null,
          rv.length ? h('div', null, h('h4', { text: 'QA review decisions' }), h('ul', { class: 'ux-list' }, rv)) : null);
      }).catch(function (e) { banner.hidden = false; banner.textContent = e.message; });
    }
    var t = document.querySelector('[data-tab="rep-uiux"]');
    if (t) t.addEventListener('click', load);
  }
})();
