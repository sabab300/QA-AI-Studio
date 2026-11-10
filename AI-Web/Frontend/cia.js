/* QA AI Studio - Change Impact Analysis (merged module).
   One page: choose a source document, press Analyze Impact, read five results, review them.
   Installed by index.html with the shared services (apiFetch, hasPermission, toast, openQaEngineering, token, apiBase).
   No browser dialogs; all dynamic text is escaped; AI text is always labelled and never changes a classification to Confirmed. */
window.installCia = function (services) {
  'use strict';
  var api = services.apiFetch, has = services.hasPermission, toast = services.toast;
  var root = document.getElementById('ciaRoot');
  if (!root) return;

  var LEVELS = ['Critical', 'High', 'Medium', 'Low'];
  var CLASS_CSS = { 'Confirmed Dependency': 'conf', 'Potential Dependency': 'pot', 'Needs Investigation': 'inv' };
  var STEP_ICON = { Pending: '', Running: '', Done: '', Failed: '' };
  var S = { options: null, runId: null, header: null, data: null, poll: null, busy: false, history: [], scopeOpen: false, reused: false, old: [], open: {},
    f: { chg: { q: '', kind: '' }, aff: { q: '', cls: '', domain: '', module: '', cross: false }, risk: { lv: '' }, test: { q: '', basis: '', prio: '', cat: '' }, cov: { q: '', status: '' } } };

  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function el(id) { return document.getElementById(id); }
  function when(v) { return v ? String(v).replace('T', ' ').slice(0, 16) : ''; }
  function uniq(list) { return Array.from(new Set(list.filter(function (v) { return v !== '' && v != null; }))).sort(function (a, b) { return String(a).localeCompare(String(b), undefined, { numeric: true }); }); }
  function pill(cls, text, title) { return '<span class="cia-pill cia-' + esc(cls) + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(text) + '</span>'; }
  function path(h) { return ['domain', 'module', 'knowledge_name', 'document_type'].map(function (k) { return h[k]; }).filter(Boolean).map(esc).join(' &rsaquo; ') + (h.version ? ' &middot; v' + esc(h.version) : '') + (h.file_name ? ' &middot; ' + esc(h.file_name) : ''); }
  function canEdit() { return has('qa_engineering', 'edit'); }
  function canStart() { return has('qa_engineering', 'create'); }

  // ------------------------------------------------------------------ layout
  function build() {
    root.innerHTML =
      '<div class="cia-top cia-noprint">' +
      '<section class="cia-card" aria-labelledby="ciaSrcH"><h2 id="ciaSrcH">1. What changed? <small>Source hierarchy</small></h2>' +
      '<div class="cia-grid" id="ciaSource"></div><div class="cia-baseline muted-small" id="ciaBaseline"></div></section>' +
      '<section class="cia-card" aria-labelledby="ciaScopeH"><h2 id="ciaScopeH">2. Where should impact be searched? <small>Impact hierarchy</small></h2>' +
      '<div class="cia-scope-choice"><label><input type="radio" name="ciaScopeMode" value="all" checked> Entire repository <span class="muted-small">(recommended, finds cross-module and cross-domain impact)</span></label>' +
      '<label><input type="radio" name="ciaScopeMode" value="some"> Limit to a part of the repository</label></div>' +
      '<div class="cia-grid" id="ciaScope" hidden></div></section>' +
      '<div class="cia-actions"><button type="button" class="cia-primary" id="ciaGo">Analyze Impact</button>' +
      '<label class="cia-ai" id="ciaAiLabel"><input type="checkbox" id="ciaAi"> Add AI opinion <span class="muted-small" id="ciaAiNote">(added after the results appear; optional)</span></label>' +
      '<span class="cia-spacer"></span><label class="muted-small" for="ciaHistory">Earlier analyses</label><select id="ciaHistory"><option value="">Open an earlier analysis…</option></select></div>' +
      '<details class="cia-old cia-noprint" id="ciaOld"><summary>Earlier reports from the previous Change Impact module <span class="muted-small" id="ciaOldCount"></span></summary><div id="ciaOldBody" class="muted-small cia-oldbody">Loading…</div></details>' +
      '<div class="cia-error" id="ciaError" role="alert"></div></div>' +
      '<div id="ciaProgress" class="cia-card cia-noprint" hidden></div>' +
      '<div id="ciaReport"></div>';
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
    root.addEventListener('input', onInput);
    root.addEventListener('keydown', function (e) { var tr = e.target.closest && e.target.closest('tr[data-x]'); if (tr && e.target === tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleRow(tr); } });
    el('ciaOld').addEventListener('toggle', function () { if (this.open) loadOld(); });
  }

  // ------------------------------------------------------------------ selectors
  var SRC_LEVELS = [['domain', 'Domain'], ['module', 'Module'], ['knowledge_name', 'Knowledge'], ['document_type', 'Document Type'], ['version', 'Version'], ['source_type', 'Source Type'], ['id', 'Source Document']];
  var SCOPE_LEVELS = [['domain', 'Domain'], ['module', 'Module'], ['knowledge_name', 'Knowledge'], ['document_type', 'Document Type'], ['version', 'Version'], ['id', 'Document']];

  function select(prefix, key, label, all) {
    return '<div class="field"><label for="' + prefix + key + '">' + esc(label) + '</label><select id="' + prefix + key + '" data-cia-sel="' + prefix + '" data-level="' + key + '"><option value="">' + (all ? 'All' : 'Select ' + esc(label) + '…') + '</option></select></div>';
  }
  function fillSelects(prefix, levels, all, auto) {
    var items = S.options ? S.options.sources : [];
    var chosen = {};
    levels.forEach(function (lv, i) {
      var key = lv[0], sel = el(prefix + key);
      if (!sel) return;
      var current = sel.value;
      var pool = items.filter(function (it) { return levels.slice(0, i).every(function (p) { var v = chosen[p[0]]; return !v || String(it[p[0]]) === v; }); });
      var values = key === 'id' ? pool.map(function (it) { return { value: String(it.id), label: it.file_name + ' (#' + it.id + ')' }; }) :
        uniq(pool.map(function (it) { return it[key]; })).map(function (v) { return { value: String(v), label: String(v) }; });
      var seen = {}; values = values.filter(function (o) { if (seen[o.value]) return false; seen[o.value] = 1; return true; });
      sel.innerHTML = '<option value="">' + (all ? 'All' : 'Select ' + esc(lv[1]) + '…') + '</option>' + values.map(function (o) { return '<option value="' + esc(o.value) + '">' + esc(o.label) + '</option>'; }).join('');
      var keep = values.some(function (o) { return o.value === current; }) ? current : (auto && values.length === 1 ? values[0].value : '');
      sel.value = keep;
      chosen[key] = keep;
    });
  }
  function sourceId() { var v = el('ciaSrcid') ? el('ciaSrcid').value : ''; return v ? Number(v) : 0; }
  function scopeValue() {
    var out = {};
    if (document.querySelector('input[name=ciaScopeMode]:checked').value !== 'some') return out;
    SCOPE_LEVELS.forEach(function (lv) { var v = el('ciaScope' + lv[0]).value; if (v) out[lv[0] === 'id' ? 'source_id' : lv[0]] = lv[0] === 'id' ? Number(v) : v; });
    return out;
  }
  function renderSelectors() {
    el('ciaSource').innerHTML = SRC_LEVELS.map(function (l) { return select('ciaSrc', l[0], l[1], false); }).join('');
    el('ciaScope').innerHTML = SCOPE_LEVELS.map(function (l) { return select('ciaScope', l[0], l[1], true); }).join('');
    fillSelects('ciaSrc', SRC_LEVELS, false, true);
    fillSelects('ciaScope', SCOPE_LEVELS, true, false);
    updateGo();
  }
  function updateGo() {
    var ready = !!sourceId() && canStart() && !S.busy;
    el('ciaGo').disabled = !ready;
    el('ciaGo').title = !canStart() ? 'Your role cannot start an analysis.' : !sourceId() ? 'Select the source document first.' : '';
    var src = S.options && sourceId() ? S.options.sources.filter(function (s) { return s.id === sourceId(); })[0] : null;
    el('ciaBaseline').textContent = src ? 'The earlier version of this document, if one exists, is found and used automatically. Otherwise the document is read as a proposed change.' : '';
  }

  // ------------------------------------------------------------------ events
  function onChange(e) {
    var t = e.target;
    if (t.matches('[data-cia-sel="ciaSrc"]')) { fillSelects('ciaSrc', SRC_LEVELS, false, true); updateGo(); }
    else if (t.matches('[data-cia-sel="ciaScope"]')) { fillSelects('ciaScope', SCOPE_LEVELS, true, false); }
    else if (t.name === 'ciaScopeMode') { el('ciaScope').hidden = t.value !== 'some'; }
    else if (t.id === 'ciaHistory') { if (t.value) openRun(t.value); }
    else if (t.hasAttribute('data-f')) { setFilter(t, t.type === 'checkbox' ? t.checked : t.value); }
  }
  function setFilter(t, value) {
    var p = t.getAttribute('data-f').split('.');
    S.f[p[0]][p[1]] = value;
    renderTable(p[0]);
  }
  var filterTimer = null;
  function onInput(e) {
    var t = e.target;
    if (!t.hasAttribute || !t.hasAttribute('data-f') || t.type === 'checkbox' || t.tagName === 'SELECT') return;
    clearTimeout(filterTimer);
    filterTimer = setTimeout(function () { setFilter(t, t.value); }, 150);
  }
  function subj(ref, text, n) {
    var t = String(text || '').trim();
    if (ref) { var i = t.toUpperCase().indexOf(String(ref).toUpperCase()); if (i >= 0 && i < 4) t = t.slice(i + String(ref).length).replace(/^[\s:|\-\u2013.]+/, ''); }
    return clip(t || String(text || ''), n);
  }
  function toggleRow(tr) {
    var key = tr.getAttribute('data-x'), detail = tr.nextElementSibling, open = detail.hidden;
    detail.hidden = !open; tr.classList.toggle('open', open); tr.setAttribute('aria-expanded', String(open));
    if (open) S.open[key] = true; else delete S.open[key];
  }
  function onClick(e) {
    var tr = e.target.closest('tr[data-x]');
    if (tr && !e.target.closest('button,input,select,textarea,a,summary')) { toggleRow(tr); return; }
    var b = e.target.closest('button');
    if (!b) return;
    if (b.hasAttribute('data-jump')) { var target = document.getElementById(b.getAttribute('data-jump')); if (target && target.scrollIntoView) target.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    if (b.id === 'ciaGo') start(false);
    else if (b.id === 'ciaRerun') start(true);
    else if (b.id === 'ciaCancel') cancel();
    else if (b.id === 'ciaExcel') download();
    else if (b.id === 'ciaPrint') { document.querySelectorAll('#ciaReport details').forEach(function (d) { d.open = true; }); document.querySelectorAll('#ciaReport .cia-d').forEach(function (d) { d.hidden = false; }); window.print(); }
    else if (b.hasAttribute('data-show-ni')) { S.showNI = true; renderTable('aff'); }
    else if (b.hasAttribute('data-review')) toggleReview(b.getAttribute('data-review'));
    else if (b.hasAttribute('data-review-save')) saveReview(b.getAttribute('data-review-save'));
    else if (b.hasAttribute('data-open-qe')) openQe(JSON.parse(b.getAttribute('data-open-qe')));
    else if (b.hasAttribute('data-old-xlsx')) oldExcel(b.getAttribute('data-old-xlsx'));
  }
  function setError(msg) { var box = el('ciaError'); box.textContent = msg || ''; box.classList.toggle('show', !!msg); }

  // ------------------------------------------------------------------ run
  function start(force) {
    setError('');
    if (!sourceId()) { setError('Select the source document first.'); return; }
    S.busy = true; updateGo();
    api('/api/cia/analyses', { method: 'POST', body: JSON.stringify({ source_id: sourceId(), scope: scopeValue(), use_ai: el('ciaAi').checked && !el('ciaAi').disabled, force: !!force }) })
      .then(function (h) { S.reused = !!h.reused; follow(h); })
      .catch(function (err) { S.busy = false; updateGo(); setError(err.message); });
  }
  function follow(h) {
    S.runId = h.id; S.header = h; S.data = null; el('ciaReport').innerHTML = '';
    renderProgress(h);
    clearTimeout(S.poll);
    if (h.status === 'Completed') return load(h.id);
    S.poll = setTimeout(tick, 1200);
  }
  function tick() {
    if (!S.runId) return;
    api('/api/cia/analyses/' + encodeURIComponent(S.runId)).then(function (h) {
      S.header = h; renderProgress(h);
      if (h.status === 'Queued' || h.status === 'Running') { S.poll = setTimeout(tick, 1500); return; }
      S.busy = false; updateGo();
      if (h.status === 'Completed') load(h.id);
      else setError(h.status === 'Cancelled' ? 'The analysis was cancelled.' : (h.error_message || 'The analysis failed.'));
    }).catch(function (err) { S.busy = false; updateGo(); setError(err.message); });
  }
  function cancel() { api('/api/cia/analyses/' + encodeURIComponent(S.runId) + '/cancel', { method: 'POST' }).then(function () { toast('Cancelling…'); }).catch(function (err) { setError(err.message); }); }
  function renderProgress(h) {
    var box = el('ciaProgress'), p = h.progress || {}, active = h.status === 'Queued' || h.status === 'Running';
    box.hidden = !active && h.status === 'Completed';
    if (box.hidden) return;
    var steps = p.steps && p.steps.length ? p.steps : (S.options ? S.options.steps : []).map(function (n) { return { name: n, status: 'Pending', detail: '' }; });
    box.innerHTML = '<div class="cia-prog-head"><b>' + esc(active ? 'Analyzing impact…' : h.status) + '</b><span class="muted-small">' + (p.percent || 0) + '%</span>' +
      (active ? '<button type="button" class="cia-btn" id="ciaCancel">Cancel</button>' : '') + '</div>' +
      '<div class="cia-bar" role="progressbar" aria-valuenow="' + (p.percent || 0) + '" aria-valuemin="0" aria-valuemax="100"><i style="width:' + (p.percent || 0) + '%"></i></div>' +
      '<ol class="cia-steps" aria-live="polite">' + steps.map(function (s) { return '<li class="' + esc(s.status) + '"><span class="cia-dot"></span><span>' + esc(s.name) + '</span><span class="muted-small">' + esc(s.detail || '') + '</span></li>'; }).join('') + '</ol>';
  }
  function load(id, keep) {
    return api('/api/cia/analyses/' + encodeURIComponent(id) + '/result').then(function (d) {
      S.busy = false; updateGo(); S.data = d; S.runId = id; if (!keep) { S.open = {}; S.showNI = false; } S.header = d.header || S.header; el('ciaProgress').hidden = true; renderReport(); watchRefine();
      if (!keep) { loadHistory(); var r = el('ciaReport'); if (r && r.scrollIntoView) r.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }).catch(function (err) { S.busy = false; updateGo(); setError(err.message); });
  }
  function openRun(id) { setError(''); S.reused = false; el('ciaHistory').value = ''; api('/api/cia/analyses/' + encodeURIComponent(id)).then(function (h) { follow(h); }).catch(function (err) { setError(err.message); }); }
  function loadHistory() {
    return api('/api/cia/analyses?limit=30').then(function (d) {
      S.history = d.items || [];
      el('ciaHistory').innerHTML = '<option value="">Open an earlier analysis…</option>' + S.history.map(function (h) { return '<option value="' + esc(h.id) + '">' + esc(when(h.created_at) + ' · ' + h.title + ' · ' + h.status) + '</option>'; }).join('');
    }).catch(function () { });
  }

  // ------------------------------------------------------------------ report (compact tables)
  var DEP_ORDER = ['Confirmed Dependency', 'Potential Dependency', 'Needs Investigation'];
  var TIER_CSS = { 1: 'ver', 2: 'ver', 3: 'strong', 4: 'sug' };
  var QA_SHORT = { 'Confirmed': 'Confirmed', 'Not Applicable': 'N/A', 'Needs Investigation': 'Investigate' };
  function D() { return S.data && S.data.result; }
  function reviewOf(key) { return (S.data && S.data.reviews && S.data.reviews[key]) || null; }
  function clip(t, n) { t = String(t == null ? '' : t).replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t; }
  function changeMap() { var m = {}; ((D() || {}).what_changed || { changes: [] }).changes.forEach(function (c) { m[c.key] = c; }); return m; }
  // A reference starting with INT- is generated by QA AI Studio (the document has no identifier for that requirement); say so.
  function refB(o) {
    var ref = String(o.reference || ''), internal = o.reference_kind === 'internal' || /^INT-[0-9A-F]{6}$/.test(ref);
    return internal ? '<b title="Internal reference generated by QA AI Studio. The document has no identifier for this requirement.">' + esc(ref) + '</b> <span class="muted-small">(internal)</span>' : '<b>' + esc(ref) + '</b>';
  }
  function refName(id) { var c = changeMap()[id]; return c ? (c.reference || clip(c.title, 28) || id) : id; }
  function refs(ids) { return uniq((ids || []).map(refName)).join(', '); }
  function hay(parts) { return parts.join(' ').toLowerCase(); }
  function covLabel(s) { return { 'Covered': 'Covered', 'Coverage Gap': 'Coverage Gap', 'Gap': 'Coverage Gap', 'Coverage Not Verified': 'Not verified', 'Partly covered (unverified)': 'Not verified' }[s] || s; }
  function covCss(s) { return s === 'Covered' ? 'ver' : (s === 'Coverage Gap' || s === 'Gap') ? 'gap' : 'nv'; }
  function tierOf(t) { return t.tier || (String(t.basis || '').indexOf('Verified') === 0 ? 2 : 4); }
  function qaChip(key) {
    var r = reviewOf(key);
    if (!r) return '<span class="muted-small">—</span>';
    return '<span class="cia-qa cia-qa-' + esc(r.decision.replace(/\s/g, '')) + '" title="' + esc((r.comment || '') + ' ' + (r.by || '')) + '">' + esc(QA_SHORT[r.decision] || r.decision) + '</span>';
  }
  function reviewHtml(key) {
    var r = reviewOf(key), cur = r ? '<span class="cia-qa cia-qa-' + esc(r.decision.replace(/\s/g, '')) + '">QA: ' + esc(r.decision) + '</span>' + (r.comment ? ' <span class="muted-small">' + esc(r.comment) + '</span>' : '') + ' <span class="muted-small">' + esc(r.by) + ' ' + esc(when(r.at)) + '</span>' : '';
    return '<div class="cia-review cia-noprint" data-key="' + esc(key) + '">' + (cur || '<span class="muted-small">Not reviewed by QA</span>') +
      (canEdit() ? ' <button type="button" class="cia-btn" data-review="' + esc(key) + '">' + (r ? 'Change' : 'Review') + '</button>' : '') + '<div class="cia-rform" id="rf-' + esc(key) + '" hidden></div></div>' +
      (r ? '<div class="cia-print-only muted-small">QA: ' + esc(r.decision) + (r.comment ? ' - ' + esc(r.comment) : '') + '</div>' : '');
  }
  function toggleReview(key) {
    var box = document.getElementById('rf-' + key);
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<select id="rd-' + esc(key) + '" aria-label="QA decision"><option>Confirmed</option><option>Not Applicable</option><option>Needs Investigation</option></select>' +
      '<input type="text" id="rc-' + esc(key) + '" maxlength="2000" placeholder="Comment (required unless Confirmed)" aria-label="Comment"> <button type="button" class="cia-btn cia-btn-p" data-review-save="' + esc(key) + '">Save</button>';
  }
  function saveReview(key) {
    var decision = el('rd-' + key).value, comment = el('rc-' + key).value;
    api('/api/cia/analyses/' + encodeURIComponent(S.runId) + '/review', { method: 'POST', body: JSON.stringify({ item_key: key, decision: decision, comment: comment }) })
      .then(function () { return api('/api/cia/analyses/' + encodeURIComponent(S.runId) + '/result'); })
      .then(function (d) { S.data.reviews = d.reviews; S.open[key] = true; renderAllTables(); toast('QA decision saved.'); })
      .catch(function (err) { toast(err.message, true); });
  }
  function qeButton(label, payload) {
    if (!services.openQaEngineering || !has('qa_engineering', 'create')) return '';
    return '<button type="button" class="cia-btn cia-noprint" data-open-qe=\'' + esc(JSON.stringify(payload)) + '\'>' + esc(label) + '</button>';
  }
  function openQe(payload) { services.openQaEngineering(payload); }

  // ---- table helpers
  function tbl(cols, body) {
    return '<div class="cia-tw"><table class="cia-t"><thead><tr>' + cols.map(function (c) { return '<th' + (c.w ? ' style="width:' + c.w + '"' : '') + '>' + esc(c.h) + '</th>'; }).join('') + '</tr></thead><tbody>' + body + '</tbody></table></div>';
  }
  function xrow(key, cells, detail, ncols) {
    var open = !!S.open[key];
    return '<tr class="cia-r' + (open ? ' open' : '') + '" data-x="' + esc(key) + '" tabindex="0" aria-expanded="' + open + '">' + cells.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>' +
      '<tr class="cia-d"' + (open ? '' : ' hidden') + ' data-d="' + esc(key) + '"><td colspan="' + ncols + '">' + detail + '</td></tr>';
  }
  function groupRow(label, note, ncols) { return '<tr class="cia-g"><td colspan="' + ncols + '"><b>' + label + '</b> <span class="muted-small">' + esc(note || '') + '</span></td></tr>'; }
  function hpath(h) { return ['domain', 'module', 'knowledge_name'].map(function (k) { return h[k]; }).filter(Boolean).map(esc).join(' &rsaquo; '); }
  function grouped(items, hierOf, makeRow, ncols, noteOf) {
    var groups = {}, order = [];
    items.forEach(function (it) { var h = hierOf(it), k = [h.domain, h.module, h.knowledge_name].join('\u0001'); if (!groups[k]) { groups[k] = { h: h, items: [] }; order.push(k); } groups[k].items.push(it); });
    order.sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true }); });
    return order.map(function (k) { var g = groups[k]; return groupRow(hpath(g.h), noteOf ? noteOf(g.items) : g.items.length + ' item(s)', ncols) + g.items.map(makeRow).join(''); }).join('');
  }
  function empty(text) { return '<div class="cia-empty">' + esc(text) + '</div>'; }
  function opt(list, current, all) { return '<option value="">' + esc(all) + '</option>' + list.map(function (v) { return '<option' + (String(v) === String(current) ? ' selected' : '') + '>' + esc(v) + '</option>'; }).join(''); }
  function fbar(sec, inner) { return '<div class="cia-fb cia-noprint" data-sec="' + sec + '">' + inner + '</div>'; }
  function search(sec, ph) { return '<input type="search" class="cia-q" data-f="' + sec + '.q" value="' + esc(S.f[sec].q || '') + '" placeholder="' + esc(ph) + '" aria-label="' + esc(ph) + '">'; }
  function sel(sec, key, options, all) { return '<select data-f="' + sec + '.' + key + '" aria-label="' + esc(all) + '">' + opt(options, S.f[sec][key], all) + '</select>'; }

  function section(num, title, count, help, body) {
    return '<section class="cia-card cia-sec" id="cia-sec-' + num + '"><h2>' + esc(title) + ' <span class="cia-count">' + esc(count) + '</span>' + (help ? '<span class="cia-help">' + esc(help) + '</span>' : '') + '</h2>' + body + '</section>';
  }
  function stack(parts) {
    var total = parts.reduce(function (a, p) { return a + p.n; }, 0);
    if (!total) return '<div class="cia-stack empty"></div>';
    return '<div class="cia-stack" role="img" aria-label="' + esc(parts.map(function (p) { return p.n + ' ' + p.label; }).join(', ')) + '">' + parts.filter(function (p) { return p.n; }).map(function (p) { return '<i class="' + p.cls + '" style="flex:' + p.n + '" title="' + esc(p.n + ' ' + p.label) + '"></i>'; }).join('') + '</div>';
  }
  function kpi(n, label, cls) { return '<div class="cia-kpi ' + (cls || '') + '"><b>' + esc(n) + '</b><span>' + esc(label) + '</span></div>'; }

  // ---- report
  function refineLine(d) {
    var r = S.header && S.header.refine, ai = d.ai || {};
    if (!ai.requested && !r) return '';
    var active = r && (r.status === 'Queued' || r.status === 'Running');
    var text = r ? (active ? 'AI opinion is being added in the background (' + (r.done || 0) + ' of ' + (r.total || '?') + '). The results below are already complete; AI only adds a note and never changes a class.' : 'AI opinion: ' + (r.message || r.status)) : 'AI opinion: ' + (ai.status || '');
    return '<div class="cia-note cia-noprint" id="ciaRefine">' + esc(text) + '</div>';
  }
  function watchRefine() {
    clearTimeout(S.refinePoll);
    var r = S.header && S.header.refine;
    if (!S.runId || !r || (r.status !== 'Queued' && r.status !== 'Running')) return;
    var id = S.runId;
    S.refinePoll = setTimeout(function () {
      api('/api/cia/analyses/' + encodeURIComponent(id)).then(function (h) {
        if (id !== S.runId) return;
        S.header = h;
        var nr = h.refine || {};
        if (nr.status === 'Queued' || nr.status === 'Running') { var n = el('ciaRefine'); if (n) n.textContent = 'AI opinion is being added in the background (' + (nr.done || 0) + ' of ' + (nr.total || '?') + '). The results below are already complete; AI only adds a note and never changes a class.'; watchRefine(); }
        else load(id, true);
      }).catch(function () { });
    }, 3000);
  }
  function renderReport() {
    var d = D(), box = el('ciaReport');
    if (!d) { box.innerHTML = ''; return; }
    var sum = d.summary, aff = sum.affected, v2 = String(d.engine_version) === '2.0';
    var top = (d.risks || []).filter(function (r) { return r.level === 'Critical' || r.level === 'High'; }).slice(0, 3);
    var covered = sum.coverage_covered != null ? sum.coverage_covered : d.coverage.filter(function (c) { return c.status === 'Covered'; }).length;
    var html = '<div class="cia-card cia-summary" id="cia-sec-0"><div class="cia-sum-head"><div><h2>Impact summary <span class="cia-eng" title="Analysis engine version">engine ' + esc(d.engine_version || '1.0') + '</span></h2>' +
      '<div class="muted-small">' + path(d.source) + '</div>' +
      '<div class="muted-small">' + (d.baseline ? 'Compared with version ' + esc(d.baseline.version) : 'No earlier version: proposed changes only') + ' &middot; Impact scope: ' + esc(d.scope.text) + ' &middot; ' + esc(when(d.generated_at)) + '</div></div>' +
      '<div class="cia-noprint cia-sum-actions"><button type="button" class="cia-btn" id="ciaExcel">Download Excel</button><button type="button" class="cia-btn" id="ciaPrint">Print</button>' +
      qeButton('Open in QA Engineering', { source_id: d.source.source_id, test_types: [] }) + '</div></div>' +
      (!v2 ? '<div class="cia-note warn cia-noprint">This result was produced by the earlier analysis engine (' + esc(d.engine_version || '1.0') + '), which matched wording. It is kept for history; run the analysis again for engine 2.0 results. <button type="button" class="cia-btn" id="ciaRerun">Run again</button></div>' : '') +
      (S.reused ? '<div class="cia-note cia-noprint">Nothing in the repository or the test cases changed since this analysis ran, so the saved result is shown. <button type="button" class="cia-btn" id="ciaRerun">Run again</button></div>' : '') +
      '<p class="cia-headline">' + esc(sum.headline) + '</p>' + refineLine(d) +
      '<div class="cia-kpis">' + kpi(sum.changes, 'Changed requirements') + kpi(aff['Confirmed Dependency'], 'Confirmed dependencies', aff['Confirmed Dependency'] ? 'alert' : '') + kpi(aff['Potential Dependency'], 'Potential') +
      kpi(aff['Needs Investigation'], 'Needs investigation') + kpi(sum.risk.Critical + sum.risk.High, 'Critical / High risk', sum.risk.Critical + sum.risk.High ? 'alert' : '') +
      kpi(sum.tests_total, 'Existing tests') + kpi(covered, 'Requirements covered') + kpi(sum.coverage_gaps, 'Coverage gaps', sum.coverage_gaps ? 'alert' : '') + kpi(sum.coverage_unverified, 'Not verified') + '</div>' +
      '<div class="cia-bars"><div><span class="cia-lbl">Dependencies</span>' + stack([{ n: aff['Confirmed Dependency'], cls: 'cia-conf', label: 'confirmed' }, { n: aff['Potential Dependency'], cls: 'cia-pot', label: 'potential' }, { n: aff['Needs Investigation'], cls: 'cia-inv', label: 'needs investigation' }]) + '</div>' +
      '<div><span class="cia-lbl">Risk</span>' + stack(LEVELS.map(function (l) { return { n: sum.risk[l], cls: 'cia-lv-' + l, label: l }; })) + '</div>' +
      '<div><span class="cia-lbl">Coverage</span>' + stack([{ n: covered, cls: 'cia-ver', label: 'covered' }, { n: sum.coverage_unverified, cls: 'cia-nv', label: 'not verified' }, { n: sum.coverage_gaps, cls: 'cia-gap', label: 'gap' }]) + '</div></div>' +
      (top.length ? '<div class="cia-top3"><span class="cia-lbl">Needs attention first</span>' + top.map(function (r) { return '<div>' + pill('lv-' + r.level, r.level) + ' <b>' + esc(r.reference || '') + '</b> ' + esc(subj(r.reference, r.subject, 90)) + ' <span class="muted-small">&mdash; ' + esc(clip(r.action, 130)) + '</span></div>'; }).join('') + '</div>' : '') +
      '<nav class="cia-nav cia-noprint" aria-label="Report sections">' + [[1, 'What Changed'], [2, 'What Is Affected'], [3, 'Risk Assessment'], [4, 'Regression Tests'], [5, 'Missing Coverage']].map(function (s) { return '<button type="button" data-jump="cia-sec-' + s[0] + '">' + esc(s[1]) + '</button>'; }).join('') + '</nav></div>';
    html += section(1, 'What Changed', d.what_changed.changes.length, 'One row per changed requirement. Click a row for before / after and evidence.', '<div class="cia-note' + (d.what_changed.mode === 'PROPOSED_CHANGES' ? ' warn' : '') + '">' + esc(d.what_changed.note) + '</div><div id="ciaH-chg"></div><div id="ciaT-chg"></div>');
    html += section(2, 'What Is Affected', d.affected.items.length, 'Existing requirements that may depend on the changes, with the evidence for each.', '<div id="ciaH-aff"></div><div id="ciaT-aff"></div>');
    html += section(3, 'Risk Assessment', d.risks.length, 'Rule-based; one explanation per row.', '<div id="ciaH-risk"></div><div id="ciaT-risk"></div>');
    html += section(4, 'Existing Regression Test Cases', sum.tests_total, 'Only test cases that already exist in QA Engineering; nothing is created or copied.', '<div class="muted-small">' + esc(d.test_search.tests_considered) + ' test case(s) searched' + (d.test_search.linked_to_document != null ? ', ' + esc(d.test_search.linked_to_document) + ' linked to the changed document' : '') + '.</div><div id="ciaH-test"></div><div id="ciaT-test"></div>');
    html += section(5, 'Missing Test Coverage', d.coverage.filter(function (c) { return c.status !== 'Covered'; }).length, 'Per changed requirement: covered, gap (no relevant test exists) or not verified.', '<div id="ciaH-cov"></div><div id="ciaT-cov"></div>');
    html += '<details class="cia-card cia-sec cia-lim"><summary>Limitations and how to read this report (' + d.limitations.length + ')</summary><ul class="cia-ul">' + d.limitations.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></details>';
    box.innerHTML = html;
    renderFilters();
    renderAllTables();
  }
  function renderFilters() {
    var d = D(), items = d.affected.items;
    el('ciaH-chg').innerHTML = fbar('chg', search('chg', 'Search changes') + sel('chg', 'kind', ['New requirement', 'Modified requirement', 'Removed requirement', 'Proposed addition', 'Proposed modification', 'Proposed removal', 'Proposed change', 'Changed requirement'].filter(function (l) { return d.what_changed.changes.some(function (c) { return c.label === l; }); }), 'All types'));
    el('ciaH-aff').innerHTML = fbar('aff', search('aff', 'Search requirement, document, identifier') + sel('aff', 'cls', DEP_ORDER, 'All classifications') + sel('aff', 'domain', uniq(items.map(function (i) { return i.hierarchy.domain; })), 'All domains') +
      sel('aff', 'module', uniq(items.map(function (i) { return i.hierarchy.module; })), 'All modules') + '<label class="cia-chk"><input type="checkbox" data-f="aff.cross"' + (S.f.aff.cross ? ' checked' : '') + '> Other domain only</label>');
    el('ciaH-risk').innerHTML = fbar('risk', sel('risk', 'lv', LEVELS, 'All levels'));
    var tests = allTests();
    el('ciaH-test').innerHTML = fbar('test', search('test', 'Search test id, title, evidence') + sel('test', 'basis', uniq(tests.map(function (t) { return t.basis; })), 'All evidence') + sel('test', 'prio', uniq(tests.map(function (t) { return t.priority; })), 'All priorities') + sel('test', 'cat', uniq(tests.map(function (t) { return t._cat; })), 'All categories'));
    el('ciaH-cov').innerHTML = fbar('cov', search('cov', 'Search requirement') + sel('cov', 'status', ['Coverage Gap', 'Coverage Not Verified', 'Covered'], 'Not covered (gap and not verified)'));
  }
  function allTests() { var d = D(), out = []; Object.keys(d.regression || {}).forEach(function (cat) { (d.regression[cat] || []).forEach(function (t) { t._cat = cat; out.push(t); }); }); return out; }
  function renderAllTables() { ['chg', 'aff', 'risk', 'test', 'cov'].forEach(renderTable); }
  function renderTable(sec) {
    var host = el('ciaT-' + sec); if (!host || !D()) return;
    host.innerHTML = { chg: tableChanges, aff: tableAffected, risk: tableRisks, test: tableTests, cov: tableCoverage }[sec]();
  }

  // ---- 1 What Changed
  function tableChanges() {
    var d = D(), f = S.f.chg, risks = {}, cov = {}, counts = {};
    d.risks.forEach(function (r) { risks[r.change_id] = r; });
    d.coverage.forEach(function (c) { cov[c.change_id] = c; });
    d.affected.items.forEach(function (i) { i.change_ids.forEach(function (id) { var c = counts[id] = counts[id] || { c: 0, p: 0 }; if (i.classification === DEP_ORDER[0]) c.c++; else if (i.classification === DEP_ORDER[1]) c.p++; }); });
    var rows = d.what_changed.changes.filter(function (c) {
      return (!f.kind || c.label === f.kind) && (!f.q || hay([c.reference, c.title, c.modified, c.original, c.category, c.keys.join(' ')]).indexOf(f.q.toLowerCase()) >= 0);
    });
    if (!rows.length) return empty(d.what_changed.changes.length ? 'No change matches the filter.' : 'No requirement changes were found in this document.');
    var w = d.what_changed;
    return tbl([{ h: 'Reference', w: '120px' }, { h: 'Type', w: '130px' }, { h: 'Changed requirement' }, { h: 'Category', w: '120px' }, { h: 'Risk', w: '74px' }, { h: 'Coverage', w: '104px' }, { h: 'Dependencies', w: '112px' }], rows.map(function (c) {
      var r = risks[c.key], cv = cov[c.key], n = counts[c.key] || { c: 0, p: 0 };
      var before = w.baseline_used && (c.original || c.modified) ?
        '<div class="cia-ba"><div><span class="cia-lbl">Before</span><div class="cia-quote">' + esc(c.original || 'Not present') + '</div></div><div><span class="cia-lbl">After</span><div class="cia-quote">' + esc(c.modified || 'Removed') + '</div></div></div>' :
        '<span class="cia-lbl">Proposed text</span><div class="cia-quote">' + esc(c.modified || c.original || '') + '</div>';
      var detail = (c.summary ? '<div class="cia-why">' + esc(c.summary) + '</div>' : '') + before +
        (c.details && c.details.length ? '<ul class="cia-ul">' + c.details.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') +
        (c.keys && c.keys.length ? '<div class="cia-chips"><span class="cia-lbl">Precise identifiers</span>' + c.keys.map(function (k) { return '<span class="cia-chip">' + esc(k) + '</span>'; }).join('') + '</div>' : '') +
        '<div class="muted-small">' + esc(c.document) + (c.location ? ' &middot; ' + esc(c.location) : '') + (c.merged_count > 1 ? ' &middot; ' + c.merged_count + ' statements of the same requirement combined' : '') + (c.occurrence_note ? ' &middot; ' + esc(c.occurrence_note) : '') + '</div>' +
        (r ? '<div class="cia-action"><span class="cia-lbl">Risk</span> ' + esc(r.reason) + ' <b>Action:</b> ' + esc(r.action) + '</div>' : '');
      return xrow(c.key, [c.reference ? refB(c) : '<span class="muted-small">no id</span>', pill('kind-' + c.kind, c.label), esc(clip(c.modified || c.original || c.title, 170)), esc(c.category || ''),
        r ? pill('lv-' + r.level, r.level) : '', cv ? pill(covCss(cv.status), covLabel(cv.status)) : '', (n.c ? '<b>' + n.c + '</b> conf' : '') + (n.c && n.p ? ' &middot; ' : '') + (n.p ? n.p + ' pot' : '') || '<span class="muted-small">none</span>'], detail, 7);
    }).join('')) + (w.other_differences.length ? '<details class="cia-more cia-noprint"><summary>' + w.other_differences.length + ' item(s) set aside: not product requirements (headers, QA instructions, fragments, captions)</summary><div class="cia-tw"><table class="cia-t"><thead><tr><th>Text</th><th style="width:300px">Why it was set aside</th></tr></thead><tbody>' +
      w.other_differences.map(function (o) { return '<tr><td>' + esc(clip(o.text, 200)) + '</td><td>' + esc(o.reason) + '</td></tr>'; }).join('') + '</tbody></table></div></details>' : '');
  }

  // ---- 2 What Is Affected
  function tableAffected() {
    var d = D(), f = S.f.aff, items = d.affected.items;
    var rows = items.filter(function (i) {
      return (!f.cls || i.classification === f.cls) && (!f.domain || i.hierarchy.domain === f.domain) && (!f.module || i.hierarchy.module === f.module) && (!f.cross || i.cross.domain) &&
        (!f.q || hay([i.reference, i.functionality, i.hierarchy.file_name, i.hierarchy.knowledge_name, i.shared_terms.join(' '), i.why]).indexOf(f.q.toLowerCase()) >= 0);
    });
    // "Needs investigation" is the lowest-confidence class: it stays folded until asked for (or until that class is chosen in the filter), so the table opens compact
    var folded = (!f.cls && !f.q && !S.showNI) ? rows.filter(function (i) { return i.classification === DEP_ORDER[2]; }).length : 0;
    if (folded) rows = rows.filter(function (i) { return i.classification !== DEP_ORDER[2]; });
    var more = folded ? '<div class="cia-foldrow cia-noprint"><button type="button" class="cia-btn" data-show-ni="1">Show ' + folded + ' &ldquo;Needs investigation&rdquo; item(s)</button> <span class="muted-small">lowest confidence: wording or similarity only, not counted as dependencies</span></div>' : '';
    if (!rows.length) return (items.length ? empty(folded ? 'No confirmed or potential dependency was found.' : 'Nothing matches the filter.') : empty('No other requirement, rule or interface was found to depend on these changes.')) + more;
    rows.sort(function (a, b) { return DEP_ORDER.indexOf(a.classification) - DEP_ORDER.indexOf(b.classification) || String(a.reference).localeCompare(String(b.reference)); });
    var out = tbl([{ h: 'Class', w: '118px' }, { h: 'Existing requirement' }, { h: 'Document', w: '190px' }, { h: 'Shared identifiers', w: '230px' }, { h: 'Changed by', w: '120px' }, { h: 'QA', w: '76px' }],
      grouped(rows, function (i) { return i.hierarchy; }, function (i) {
        var cross = i.cross.domain ? pill('cross', 'Other domain') : (i.cross.module ? pill('cross', 'Other module') : '');
        var ev = (i.evidence_detail || []).map(function (e) {
          return '<li><b>' + esc(refName(e.change_id)) + '</b>: ' + esc(e.level) + ' &mdash; ' + (e.keys.length ? e.keys.map(function (k) { return esc(k.kind) + ' <span class="cia-chip">' + esc(k.surface || k.key) + '</span> (' + esc(k.strength) + ')'; }).join(', ') : (e.similar ? 'different wording, similarity ' + esc(e.similar) + ' (search aid, not proof)' : 'no shared identifier')) +
            (e.context && e.context.length ? '; shared meaning: ' + esc(e.context.join(', ')) : '') + (e.defines ? '; the existing requirement defines the identifier' : '') + (e.document_named ? '; the document names this change request' : '') + (e.verified ? '; confirmed earlier by QA' : '') + '</li>';
        }).join('');
        var detail = '<div class="cia-why">' + esc(i.why) + '</div><span class="cia-lbl">Evidence (existing requirement)</span><blockquote class="cia-quote">' + esc(i.evidence.quote) + '</blockquote>' + (ev ? '<ul class="cia-ul">' + ev + '</ul>' : '') +
          '<div class="muted-small">' + esc(i.evidence.document) + ' &middot; ' + esc(i.evidence.location) + ' &middot; ' + esc(i.evidence.via) + (i.impact_kind ? ' &middot; ' + esc(i.impact_kind) : '') + '</div>' +
          (i.duplicates && i.duplicates.length ? '<div class="muted-small">The same file is also stored as: ' + i.duplicates.map(function (x) { return esc(x.file_name + ' (' + x.domain + ' / ' + x.module + ' / ' + x.knowledge_name + ' v' + x.version + ')'); }).join('; ') + '. It was searched once; nothing was merged or deleted.</div>' : '') +
          (i.ai_note ? '<div class="cia-ai-note"><b>AI opinion (' + esc(i.ai_view || 'unclear') + ')</b>: ' + esc(i.ai_note) + '</div>' : '') +
          (i.previous_review ? '<div class="muted-small">Earlier QA decision on this relationship: ' + esc(i.previous_review.decision) + ' (' + esc(i.previous_review.by) + ')</div>' : '') +
          (i.earlier_decision ? '<div class="muted-small">Decision recorded on this statement by the earlier engine (' + esc(i.earlier_decision.engine || '1.0') + '): ' + esc(i.earlier_decision.decision) + ' (' + esc(i.earlier_decision.by) + '). Shown for information only; it is not used as evidence.</div>' : '') +
          reviewHtml(i.key);
        return xrow(i.key, [pill(CLASS_CSS[i.classification], i.classification.replace(' Dependency', '')) + ' ' + cross + (i.ai_view ? ' ' + pill('ai-' + i.ai_view, 'AI: ' + i.ai_view) : ''), (i.reference ? '<b>' + esc(i.reference) + '</b> ' : '') + esc(clip(i.functionality, 150)),
          esc(i.hierarchy.file_name) + ' <span class="muted-small">v' + esc(i.hierarchy.version) + '</span>', i.shared_terms.length ? i.shared_terms.slice(0, 4).map(function (k) { return '<span class="cia-chip">' + esc(k) + '</span>'; }).join('') + (i.shared_terms.length > 4 ? ' +' + (i.shared_terms.length - 4) : '') : (i.origin && i.origin.indexOf('SIMILAR') === 0 ? '<span class="muted-small">similar meaning</span>' : '<span class="muted-small">wording only</span>'),
          esc(refs(i.change_ids)), qaChip(i.key)], detail, 6);
      }, 6, function (g) { var c = { c: 0, p: 0, n: 0 }; g.forEach(function (i) { c[i.classification === DEP_ORDER[0] ? 'c' : i.classification === DEP_ORDER[1] ? 'p' : 'n']++; }); return c.c + ' confirmed · ' + c.p + ' potential · ' + c.n + ' to investigate'; }));
    return out + more;
  }

  // ---- 3 Risk
  function tableRisks() {
    var d = D(), f = S.f.risk, rows = d.risks.filter(function (r) { return !f.lv || r.level === f.lv; });
    if (!rows.length) return empty(d.risks.length ? 'No risk row at this level.' : 'No changed requirement to rate.');
    var cov = {}; d.coverage.forEach(function (c) { cov[c.change_id] = c; });
    return tbl([{ h: 'Risk', w: '74px' }, { h: 'Reference', w: '120px' }, { h: 'Requirement', w: '26%' }, { h: 'Why' }, { h: 'Coverage', w: '104px' }, { h: 'Recommended QA action', w: '28%' }], rows.map(function (r) {
      var cv = cov[r.change_id];
      var detail = (r.factors && r.factors.length ? '<span class="cia-lbl">All factors considered</span><ul class="cia-ul">' + r.factors.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') + '<div class="muted-small">Score ' + esc(r.score) + ' (Low &lt; 3, Medium 3, High 5, Critical 7). Risk is a prioritisation aid, not a probability.</div>';
      return xrow(r.key, [pill('lv-' + r.level, r.level), r.reference ? refB(r) : '', esc(subj(r.reference, r.subject, 110)), esc(r.reason), cv ? pill(covCss(cv.status), covLabel(cv.status)) : '', esc(r.action)], detail, 6);
    }).join(''));
  }

  // ---- 4 Regression tests
  function tableTests() {
    var d = D(), f = S.f.test, all = allTests();
    var rows = all.filter(function (t) {
      return (!f.basis || t.basis === f.basis) && (!f.prio || t.priority === f.prio) && (!f.cat || t._cat === f.cat) &&
        (!f.q || hay([t.test_case_id, t.title, (t.evidence || []).join(' '), t.reason, t.hierarchy.knowledge_name]).indexOf(f.q.toLowerCase()) >= 0);
    });
    if (!rows.length) return empty(all.length ? 'No test case matches the filter.' : 'No existing test case was found to relate to these changes. Generic wording never selects a test; see Missing Test Coverage.');
    rows.sort(function (a, b) { return String(a.priority).localeCompare(String(b.priority)) || tierOf(a) - tierOf(b) || String(a.test_case_id).localeCompare(String(b.test_case_id), undefined, { numeric: true }); });
    return tbl([{ h: 'Test case', w: '112px' }, { h: 'Title' }, { h: 'Evidence', w: '190px' }, { h: 'Priority', w: '70px' }, { h: 'Execution', w: '110px' }, { h: 'For', w: '130px' }, { h: 'Last result', w: '84px' }, { h: 'QA', w: '76px' }],
      grouped(rows, function (t) { return t.hierarchy; }, function (t) {
        var detail = '<div class="cia-why">' + esc(t.reason) + '</div>' + (t.evidence && t.evidence.length ? '<div class="cia-chips"><span class="cia-lbl">Matched on</span>' + t.evidence.map(function (k) { return '<span class="cia-chip">' + esc(k) + '</span>'; }).join('') + '</div>' : '') +
          '<div class="muted-small">' + esc(t._cat) + ' &middot; database id ' + esc(t.database_id) + (t.tc_number && t.tc_number !== t.test_case_id ? ' &middot; ' + esc(t.tc_number) : '') + ' &middot; ' + hpath(t.hierarchy) + ' &middot; v' + esc(t.hierarchy.version) +
          (t.related_requirement ? ' &middot; linked requirement ' + esc(t.related_requirement) : '') + '</div>' + reviewHtml(t.key);
        return xrow(t.key, ['<b>' + esc(t.test_case_id) + '</b>', esc(clip(t.title, 120)), pill(TIER_CSS[tierOf(t)] || 'sug', t.basis), pill('p-' + t.priority, t.priority), esc(t.execution_type),
          esc(refs((t.change_ids || []).length ? t.change_ids : [])) || '<span class="muted-small">dependency</span>', esc(t.last_result || ''), qaChip(t.key)], detail, 8);
      }, 8, function (g) { return g.length + ' test(s)'; }));
  }

  // ---- 5 Coverage
  function tableCoverage() {
    var d = D(), f = S.f.cov, all = d.coverage.filter(function (c) { return c.kind === 'Changed requirement' || !c.kind; });
    var rows = all.filter(function (c) {
      return (f.status ? covLabel(c.status) === covLabel(f.status) : c.status !== 'Covered') && (!f.q || hay([c.reference, c.subject, c.evidence]).indexOf(f.q.toLowerCase()) >= 0);
    });
    if (!rows.length) return empty(f.status || f.q ? 'Nothing matches the filter.' : 'Every changed requirement has at least one verified or strongly matched existing test.');
    rows.sort(function (a, b) { return LEVELS.indexOf(a.risk) - LEVELS.indexOf(b.risk); });
    return tbl([{ h: 'Status', w: '104px' }, { h: 'Reference', w: '120px' }, { h: 'Requirement', w: '30%' }, { h: 'Risk', w: '74px' }, { h: 'What the search found' }, { h: 'Tests', w: '150px' }, { h: 'QA', w: '76px' }], rows.map(function (c) {
      var tests = (c.tests || []).map(function (t) { return t.test_case_id; });
      var detail = (c.gap ? '<div class="cia-why">' + esc(c.gap) + '</div>' : '') + (c.scenario ? '<div class="cia-action"><span class="cia-lbl">Recommended scenario (not an existing test)</span> ' + esc(c.scenario) + '</div>' : '') +
        '<div class="cia-acts">' + (c.status !== 'Covered' ? qeButton('Create test in QA Engineering', { source_id: (c.hierarchy && c.hierarchy.source_id) || d.source.source_id, test_types: [], change_id: c.change_id, requirement_refs: [], scenarios: c.scenario ? [c.scenario] : [], evidence_text: c.subject }) : '') + '</div>' + reviewHtml(c.key);
      return xrow(c.key, [pill(covCss(c.status), covLabel(c.status)), c.reference ? refB(c) : '', esc(subj(c.reference, c.subject, 120)), pill('lv-' + c.risk, c.risk), esc(c.evidence), tests.length ? esc(tests.slice(0, 4).join(', ')) + (tests.length > 4 ? ' +' + (tests.length - 4) : '') : '<span class="muted-small">none</span>', qaChip(c.key)], detail, 7);
    }).join(''));
  }

  // ------------------------------------------------------------------ export
  function authBlob(path, name) {
    var headers = {}; if (services.token && services.token()) headers.Authorization = 'Bearer ' + services.token();
    return fetch(services.apiBase + path, { headers: headers }).then(function (res) {
      if (!res.ok) return res.json().catch(function () { return {}; }).then(function (d) { var m = d && d.detail && (d.detail.message || d.detail); throw new Error(typeof m === 'string' ? m : 'The download failed (' + res.status + ').'); });
      return res.blob();
    }).then(function (b) { var u = URL.createObjectURL(b), a = document.createElement('a'); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 1000); });
  }
  function download() { authBlob('/api/cia/analyses/' + encodeURIComponent(S.runId) + '/report.xlsx', 'Change_Impact_Analysis_' + S.runId + '.xlsx').catch(function (e) { toast(e.message, true); }); }

  // ------------------------------------------------------------------ earlier module (read-only)
  function loadOld() {
    var body = el('ciaOldBody');
    api('/api/change-impact/analyses?limit=50').then(function (d) {
      var items = (d.items || []).filter(function (i) { return i.status === 'Completed'; });
      el('ciaOldCount').textContent = items.length ? '(' + items.length + ', read-only)' : '';
      body.innerHTML = items.length ? '<ul class="cia-ul">' + items.map(function (i) { return '<li>' + esc(when(i.created_at)) + ' &middot; ' + esc(i.title) + ' <button type="button" class="cia-btn" data-old-xlsx="' + esc(i.id) + '">Excel</button></li>'; }).join('') + '</ul>' : 'No earlier reports.';
    }).catch(function () { body.textContent = 'The earlier reports are not available.'; });
  }
  function oldExcel(id) { authBlob('/api/change-impact/analyses/' + encodeURIComponent(id) + '/report.xlsx', 'Change_Impact_' + id + '.xlsx').catch(function (e) { toast(e.message, true); }); }

  // ------------------------------------------------------------------ entry
  var built = false;
  window.ciaEnter = function () {
    if (!built) { build(); built = true; }
    setError('');
    api('/api/cia/options').then(function (o) {
      S.options = o;
      renderSelectors();
      var ai = el('ciaAi'); ai.disabled = !o.ai.available; el('ciaAiNote').textContent = o.ai.available ? '(added after the results appear; optional)' : '(not available: ' + (o.ai.reason || 'no AI provider') + ')';
      el('ciaGo').disabled = !canStart();
      updateGo();
      if (S.runId && S.header && (S.header.status === 'Queued' || S.header.status === 'Running') && !S.poll) tick();
    }).catch(function (err) { setError(err.message); });
    loadHistory();
  };
};
