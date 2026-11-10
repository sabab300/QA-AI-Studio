/* QA AI Studio — Task 4: Settings → LLM and AI Assistant platform UI.
 *
 * Self-contained. Talks to the app only through window.AiPlatformHost
 * ({apiFetch, toast, hasPermission}) which index.html exposes. Every value that
 * comes from the server is written with textContent / setAttribute, never
 * innerHTML, so provider names, model ids, error text and retrieved document
 * excerpts cannot inject markup. Secrets are never read back: the server only
 * reports has_secret, and key fields are write-only.
 */
(function(){
'use strict';

function host(){ return window.AiPlatformHost || null; }
function api(path, opts){ return host().apiFetch('/api/settings/llm' + path, opts); }
function send(method, obj){ return { method: method, body: JSON.stringify(obj || {}) }; }
function toast(msg, isError){ if(host()) host().toast(msg, !!isError); }
function can(action){ return !!host() && host().hasPermission('settings', action); }

/* ------------------------------------------------------------ DOM helpers */
function el(tag, attrs, kids){
  var n = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function(k){
    var v = attrs[k];
    if(v === null || v === undefined || v === false) return;
    if(k === 'class') n.className = v;
    else if(k === 'text') n.textContent = v;
    else if(k === 'value') n.value = v;
    else if(k === 'checked') n.checked = !!v;
    else if(k === 'disabled') n.disabled = !!v;
    else if(k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : String(v));
  });
  add(n, kids);
  return n;
}
function add(parent, kids){
  (kids === undefined || kids === null ? [] : [].concat(kids)).forEach(function(c){
    if(Array.isArray(c)) return add(parent, c);
    if(c === null || c === undefined || c === false) return;
    parent.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  });
}
function clear(n){ while(n.firstChild) n.removeChild(n.firstChild); return n; }
function pill(text, cls){ return el('span', { class: 'ap-pill' + (cls ? ' ' + cls : ''), text: text }); }
function banner(text, cls){ return el('div', { class: 'ap-banner' + (cls ? ' ' + cls : ''), text: text }); }
function btn(label, cls, onclick, extra){
  var attrs = { type: 'button', class: 'ap-btn' + (cls ? ' ' + cls : ''), text: label, onclick: onclick };
  Object.keys(extra || {}).forEach(function(k){ attrs[k] = extra[k]; });
  return el('button', attrs);
}
function busy(button, promise){
  var label = button.textContent;
  button.disabled = true;
  return promise.then(function(r){ button.disabled = false; button.textContent = label; return r; },
    function(e){ button.disabled = false; button.textContent = label; throw e; });
}
function fail(err){ toast((err && err.message) || 'Request failed.', true); }
function select(options, value, attrs){
  var s = el('select', attrs || {});
  options.forEach(function(o){
    var opt = el('option', { value: o[0], text: o[1] });
    s.appendChild(opt);
  });
  s.value = value === undefined || value === null ? '' : String(value);
  if(s.value !== String(value === undefined || value === null ? '' : value) && options.length) s.selectedIndex = 0;
  return s;
}
function field(label, control, note){
  return el('div', { class: 'ap-field' }, [
    el('label', { text: label }), control, note ? el('span', { class: 'ap-note', text: note }) : null ]);
}
function check(label, hint, checked, disabled){
  var input = el('input', { type: 'checkbox', checked: checked, disabled: disabled });
  var node = el('label', { class: 'ap-check' }, [ input,
    el('span', null, [ label, hint ? el('small', { text: hint }) : null ]) ]);
  return { node: node, input: input };
}
function sw(checked, disabled, onchange, label){
  var input = el('input', { type: 'checkbox', checked: checked, disabled: disabled,
                            'aria-label': label || 'toggle', onchange: onchange });
  return { node: el('span', { class: 'ap-switch' }, [ input, el('i') ]), input: input };
}
function section(title, help, kids){
  return el('div', { class: 'ap-section' }, [
    el('h4', { class: 'ap-h', text: title }), help ? el('p', { class: 'ap-help', text: help }) : null, kids ]);
}
function table(heads, rows, emptyText){
  if(!rows.length) return el('div', { class: 'ap-empty', text: emptyText || 'Nothing to show.' });
  var thead = el('tr', null, heads.map(function(h){
    var num = typeof h === 'object';
    return el('th', { class: num && h.num ? 'num' : '', text: num ? h.t : h }); }));
  var tbody = el('tbody');
  rows.forEach(function(r){
    var tr = el('tr');
    r.forEach(function(c, i){
      var cls = '';
      if(c && c.__num) cls = 'num';
      if(c && c.__mono) cls = 'mono';
      var content = (c && (c.__num || c.__mono)) ? c.v : c;
      tr.appendChild(el('td', { class: cls }, [ content ]));
    });
    tbody.appendChild(tr);
  });
  return el('div', { class: 'ap-table-wrap' }, [ el('table', { class: 'ap-table' }, [ el('thead', null, [ thead ]), tbody ]) ]);
}
function num(v){ return { __num: true, v: v === null || v === undefined ? '—' : String(v) }; }
function mono(v){ return { __mono: true, v: v }; }
function two(n){ return n === null || n === undefined ? '—' : Number(n).toLocaleString(); }
function statusPill(status){
  if(status === 'ok') return pill('Connected', 'ok');
  if(status === 'error') return pill('Error', 'bad');
  return pill('Not tested');
}
function confirmButton(label, onconfirm, extra){
  var timer = null, armed = false;
  var b = btn(label, 'danger', function(){
    if(!armed){
      armed = true; b.textContent = 'Click again to confirm';
      timer = setTimeout(function(){ armed = false; b.textContent = label; }, 4000);
      return;
    }
    clearTimeout(timer); armed = false; b.textContent = label; onconfirm(b);
  }, extra);
  return b;
}
function lines(text){
  return String(text || '').split(/\r?\n/).map(function(s){ return s.trim(); }).filter(Boolean);
}
function ofNumber(input, integer){
  var raw = String(input.value).trim();
  if(raw === '') return null;
  var n = Number(raw);
  if(isNaN(n)) throw new Error('"' + raw + '" is not a number.');
  return integer ? Math.round(n) : n;
}

/* Generic settings form: spec rows -> controls; collect() -> nested patch. */
function makeForm(spec, values, disabled){
  var grid = el('div', { class: 'ap-grid' });
  var controls = [];
  function getv(path){
    return path.split('.').reduce(function(o, k){ return o ? o[k] : undefined; }, values);
  }
  spec.forEach(function(s){
    var v = getv(s.k), c, node;
    if(s.type === 'bool'){
      c = check(s.label, s.note, !!v, disabled); node = c.node; node.style.gridColumn = '1 / -1';
      controls.push({ s: s, read: function(){ return c.input.checked; } });
    } else if(s.type === 'select'){
      c = select(s.options, v, { disabled: disabled });
      node = field(s.label, c, s.note);
      controls.push({ s: s, read: function(){ return c.value; } });
    } else if(s.type === 'lines'){
      c = el('textarea', { value: (v || []).join('\n'), disabled: disabled, rows: 5 });
      node = field(s.label, c, s.note);
      controls.push({ s: s, read: function(){ return lines(c.value); } });
    } else {
      c = el('input', { type: 'number', value: v === null || v === undefined ? '' : v, disabled: disabled,
        min: s.min, max: s.max, step: s.step || (s.type === 'float' ? '0.01' : '1') });
      node = field(s.label, c, s.note);
      controls.push({ s: s, read: function(){
        var n = ofNumber(c, s.type !== 'float');
        if(n === null) throw new Error(s.label + ' is required.');
        return n; } });
    }
    grid.appendChild(node);
  });
  return { node: grid, collect: function(){
    var patch = {};
    controls.forEach(function(c){
      var parts = c.s.k.split('.'), o = patch;
      for(var i = 0; i < parts.length - 1; i++){ o = o[parts[i]] = o[parts[i]] || {}; }
      o[parts[parts.length - 1]] = c.read();
    });
    return patch;
  } };
}
function saveRow(label, onsave, note){
  var b = btn(label, 'primary', function(){ onsave(b); }, { disabled: !can('edit') });
  return el('div', { class: 'ap-row end', style: 'margin-top:12px' }, [
    note ? el('span', { class: 'ap-note', text: note }) : null, b ]);
}

/* ======================================================= Settings → LLM */
var S = { ov: null, section: 'providers', root: null, body: null, editing: undefined,
          mcpEditing: undefined, notes: {}, openTools: null, audit: null, calls: null, usageDays: 7 };
var SECTIONS = [
  ['providers', 'Providers'], ['models', 'Models'], ['routing', 'AI Routing'],
  ['rag', 'Knowledge / RAG'], ['mcp', 'Plugins / MCP'], ['tokens', 'Token Optimization'],
  ['security', 'Security']
];

function mountSettings(container){
  S.root = container;
  S.mounted = true;
  paintFrame();
  reload();
}
function paintFrame(){
  clear(S.root);
  var nav = el('div', { class: 'ap-subnav', role: 'tablist' });
  SECTIONS.forEach(function(s){
    nav.appendChild(el('button', { type: 'button', role: 'tab', class: S.section === s[0] ? 'active' : '',
      'aria-selected': S.section === s[0] ? 'true' : 'false', text: s[1],
      onclick: function(){ S.section = s[0]; S.editing = undefined; S.mcpEditing = undefined; paintFrame(); paintBody(); } }));
  });
  S.body = el('div', { class: 'ap-bar-scroll' });
  add(S.root, el('div', { class: 'ap-root' }, [ nav, S.body ]));
  paintBody();
}
function reload(){
  if(!S.root) return Promise.resolve();
  return api('/overview').then(function(ov){ S.ov = ov; paintBody(); })
    .catch(function(err){ clear(S.body); S.body.appendChild(banner(err.message, 'bad')); });
}
function paintBody(){
  if(!S.body) return;
  clear(S.body);
  if(!S.ov){ S.body.appendChild(el('div', { class: 'ap-empty', text: 'Loading…' })); return; }
  var fn = { providers: renderProviders, models: renderModels, routing: renderRouting, rag: renderRag,
             mcp: renderMcp, tokens: renderTokens, security: renderSecurity }[S.section];
  S.body.appendChild(fn());
}
function providerById(id){
  return (S.ov.providers || []).filter(function(p){ return p.id === id; })[0] || null;
}
function modelChoices(includeDefaultLabel){
  var out = [];
  (S.ov.providers || []).forEach(function(p){
    if(!p.enabled) return;
    var seen = {};
    (p.models || []).forEach(function(m){
      if(!m.enabled) return;
      seen[m.model_id] = 1;
      out.push([p.id + '|' + m.model_id, p.name + ' · ' + (m.display_name || m.model_id) +
        (p.effective_kind === 'cloud' ? '  (cloud)' : '')]);
    });
    if(p.default_model && !seen[p.default_model]){
      out.push([p.id + '|' + p.default_model, p.name + ' · ' + p.default_model +
        (p.effective_kind === 'cloud' ? '  (cloud)' : '')]);
    }
  });
  return out;
}
function splitChoice(v){
  var i = String(v).indexOf('|');
  return i < 0 ? null : { provider_id: Number(String(v).slice(0, i)), model: String(v).slice(i + 1) };
}
function cloudSharingState(){
  var sec = (S.ov.settings || {}).security || {};
  return !!sec.cloud_data_sharing_enabled;
}

/* ------------------------------------------------------------ Providers */
function renderProviders(){
  var wrap = el('div');
  var providers = S.ov.providers || [];

  wrap.appendChild(section('Providers',
    'Local providers (Ollama, LM Studio, other OpenAI-compatible servers) and cloud providers ' +
    '(OpenAI, Anthropic, Google Gemini, OpenRouter) all go through one controlled gateway. ' +
    'Switching the answer model never changes the knowledge base or its embeddings.', [
    cloudSharingState()
      ? banner('Cloud data sharing is ON (Security tab). Private knowledge is sent only to cloud providers that also have "Allow private knowledge" ticked.', 'warn')
      : banner('Cloud data sharing is OFF. Cloud providers can be configured and tested, but no QA knowledge or user question is sent to them until it is enabled in the Security tab and allowed per provider.', 'ok'),
    el('div', { class: 'ap-row', style: 'margin-top:10px' }, [
      btn('Add provider', 'primary', function(){ S.editing = null; paintBody(); }, { disabled: !can('create') }) ])
  ]));

  if(S.editing !== undefined) wrap.appendChild(providerForm(S.editing === null ? null : providerById(S.editing)));

  var rows = providers.map(function(p){
    var enabledModels = (p.models || []).filter(function(m){ return m.enabled; }).length;
    var testOut = el('span', { class: 'ap-note', text: S.notes['p' + p.id] || '' });
    var testBtn = btn('Test', '', function(){
      busy(testBtn, api('/providers/' + p.id + '/test', send('POST'))).then(function(r){
        S.notes['p' + p.id] = r.ok ? ('OK · ' + (r.models_found || 0) + ' models · ' + r.latency_ms + ' ms')
                                   : ('Failed: ' + r.error);
        reload();
      }).catch(fail);
    }, { disabled: !can('execute') });
    var discBtn = btn('Discover models', '', function(){
      busy(discBtn, api('/providers/' + p.id + '/discover', send('POST'))).then(function(r){
        toast((r.models || []).length + ' model(s) found for ' + p.name + '.'); reload();
      }).catch(fail);
    }, { disabled: !can('execute') });
    return [
      el('strong', { text: p.name }),
      p.adapter_label,
      [ pill(p.effective_kind === 'cloud' ? 'Cloud' : 'Local', p.effective_kind === 'cloud' ? 'warn' : 'ok'),
        p.kind === 'local' && p.effective_kind === 'cloud' ? pill('public host', 'warn') : null ],
      mono(p.base_url),
      [ p.enabled ? pill('Enabled', 'ok') : pill('Disabled'), ' ', statusPill(p.status),
        p.last_error ? el('div', { class: 'ap-note', text: p.last_error }) : null ],
      [ p.has_secret ? pill('Key saved', 'ok') : (p.adapter_label && S.ov.catalog.filter(function(c){ return c.adapter === p.adapter; })[0].needs_api_key ? pill('Key missing', 'bad') : pill('No key needed')) ],
      [ p.permissions.allow_private_data ? pill('Private OK', 'warn') : pill('Private blocked'), ' ',
        p.permissions.allow_public_data ? pill('Public OK', 'info') : pill('Public blocked') ],
      el('span', { text: (p.default_model || '—') + '  (' + enabledModels + ' enabled)' }),
      el('div', { class: 'ap-row' }, [ testBtn, discBtn,
        btn('Edit', '', function(){ S.editing = p.id; paintBody(); }, { disabled: !can('edit') }),
        p.is_builtin ? null : confirmButton('Delete', function(b){
          busy(b, api('/providers/' + p.id, send('DELETE'))).then(function(){ toast('Provider deleted.'); reload(); }).catch(fail);
        }, { disabled: !can('delete') }),
        testOut ])
    ];
  });
  wrap.appendChild(el('div', { class: 'ap-section' }, [
    table(['Name', 'Type', 'Where', 'Base URL', 'Status', 'API key', 'Data allowed', 'Default model', ''], rows,
      'No providers configured.') ]));
  return wrap;
}

function providerForm(p){
  var cat = S.ov.catalog || [];
  var editing = !!p;
  var dis = editing ? !can('edit') : !can('create');
  var adapter = select(cat.map(function(c){ return [c.adapter, c.label]; }), editing ? p.adapter : cat[0].adapter, { disabled: editing });
  var name = el('input', { type: 'text', value: editing ? p.name : '', maxlength: 60, disabled: dis });
  var baseUrl = el('input', { type: 'text', value: editing ? p.base_url : '', disabled: dis, autocomplete: 'off' });
  var kind = select([['local', 'Local / self-hosted'], ['cloud', 'Cloud']], editing ? p.kind : 'local', { disabled: dis });
  var key = el('input', { type: 'password', value: '', autocomplete: 'new-password', disabled: dis,
    placeholder: editing && p.has_secret ? 'Saved — leave blank to keep' : '' });
  var keyNote = el('span', { class: 'ap-note' });
  var clearKey = check('Remove the saved key', null, false, dis || !editing || !p.has_secret);
  var defModel = el('input', { type: 'text', value: editing ? p.default_model : '', maxlength: 120, disabled: dis });
  var timeout = el('input', { type: 'number', value: editing ? p.timeout_s : 300, min: 5, max: 1800, disabled: dis });
  var pr = (editing ? p.params : {}) || {}, lim = (editing ? p.limits : {}) || {};
  var temp = el('input', { type: 'number', step: '0.05', min: 0, max: 2, value: pr.temperature === undefined ? '' : pr.temperature, disabled: dis });
  var topP = el('input', { type: 'number', step: '0.05', min: 0, max: 1, value: pr.top_p === undefined ? '' : pr.top_p, disabled: dis });
  var maxTok = el('input', { type: 'number', min: 16, value: pr.max_tokens === undefined ? '' : pr.max_tokens, disabled: dis });
  var numCtx = el('input', { type: 'number', min: 512, value: pr.num_ctx === undefined ? '' : pr.num_ctx, disabled: dis });
  var ctxLim = el('input', { type: 'number', min: 256, value: lim.context_limit === undefined ? '' : lim.context_limit, disabled: dis });
  var outLim = el('input', { type: 'number', min: 256, value: lim.max_output === undefined ? '' : lim.max_output, disabled: dis });
  var enabled = check('Enabled', 'Disabled providers are never used.', editing ? p.enabled : true, dis);
  var pPriv = check('Allow private knowledge', 'QA knowledge-base passages may be sent to this provider (after redaction). Effective only if cloud data sharing is enabled in Security.', editing ? p.permissions.allow_private_data : false, dis);
  var pPub = check('Allow public / non-sensitive prompts', 'GLOBAL-mode questions and public web text only. Effective only if cloud data sharing is enabled in Security.', editing ? p.permissions.allow_public_data : false, dis);

  var numCtxField = field('Context window (Ollama)', numCtx, 'Blank = derived from the token budgets.');
  function applyAdapter(){
    var c = cat.filter(function(x){ return x.adapter === adapter.value; })[0];
    if(!c) return;
    baseUrl.placeholder = c.default_base_url;
    if(!editing && (!baseUrl.value || cat.some(function(x){ return x.default_base_url === baseUrl.value; }))) baseUrl.value = c.default_base_url;
    if(c.kind === 'cloud'){ kind.value = 'cloud'; kind.disabled = true; } else { kind.disabled = dis; }
    keyNote.textContent = c.needs_api_key ? 'Required. Stored encrypted; never shown again.' : 'Optional for local servers. Stored encrypted; never shown again.';
    numCtxField.style.display = c.adapter === 'ollama' ? '' : 'none';
  }
  adapter.addEventListener('change', applyAdapter);
  applyAdapter();

  var form = el('div', { class: 'ap-section' }, [
    el('h4', { class: 'ap-h', text: editing ? 'Edit provider — ' + p.name : 'Add provider' }),
    el('p', { class: 'ap-help', text: 'Only official provider endpoints are offered for cloud services. Local-network servers are always allowed; a "local" server on a public address is treated as cloud.' }),
    el('div', { class: 'ap-grid' }, [
      field('Provider type', adapter), field('Display name', name),
      field('Base URL', baseUrl), field('Location', kind),
      field('API key', key, null), keyNote ? el('div', { class: 'ap-field' }, [ el('span'), keyNote ]) : null,
      editing ? el('div', { class: 'ap-field' }, [ clearKey.node ]) : null,
      field('Default model', defModel, 'Used when no model is chosen. Run "Discover models" to list what the server offers.'),
      field('Request timeout (s)', timeout) ]),
    el('h4', { class: 'ap-h', style: 'margin-top:14px', text: 'Generation defaults (optional overrides)' }),
    el('div', { class: 'ap-grid' }, [
      field('Temperature', temp), field('Top-p', topP), field('Max output tokens', maxTok),
      numCtxField,
      field('Context limit (tokens)', ctxLim), field('Max output limit', outLim) ]),
    el('h4', { class: 'ap-h', style: 'margin-top:14px', text: 'Data permissions' }),
    el('div', { class: 'ap-grid' }, [ pPriv.node, pPub.node ]),
    el('div', { class: 'ap-row', style: 'margin-top:10px' }, [ enabled.node ]),
    el('div', { class: 'ap-row end', style: 'margin-top:12px' }, [
      btn('Cancel', '', function(){ S.editing = undefined; paintBody(); }),
      (function(){
        var b = btn(editing ? 'Save provider' : 'Create provider', 'primary', function(){
          var payload;
          try {
            var params = {}, limits = {};
            [['temperature', temp, false], ['top_p', topP, false], ['max_tokens', maxTok, true], ['num_ctx', numCtx, true]].forEach(function(x){
              var v = ofNumber(x[1], x[2]); if(v !== null) params[x[0]] = v; });
            [['context_limit', ctxLim], ['max_output', outLim]].forEach(function(x){
              var v = ofNumber(x[1], true); if(v !== null) limits[x[0]] = v; });
            payload = { name: name.value.trim(), base_url: baseUrl.value.trim(), kind: kind.value,
              enabled: enabled.input.checked, timeout_s: ofNumber(timeout, true) || 300,
              default_model: defModel.value.trim(), params: params, limits: limits,
              permissions: { allow_private_data: pPriv.input.checked, allow_public_data: pPub.input.checked } };
            if(!editing) payload.adapter = adapter.value;
            if(key.value.trim()) payload.api_key = key.value.trim();
            if(editing && clearKey.input.checked) payload.clear_key = true;
          } catch(e){ return fail(e); }
          busy(b, api(editing ? '/providers/' + p.id : '/providers', send(editing ? 'PUT' : 'POST', payload))).then(function(){
            key.value = ''; S.editing = undefined; toast(editing ? 'Provider saved.' : 'Provider created.'); reload();
          }).catch(fail);
        }, { disabled: dis });
        return b;
      })() ])
  ]);
  return form;
}

/* --------------------------------------------------------------- Models */
function modelPath(pid, mid){
  return '/providers/' + pid + '/models/' + String(mid).split('/').map(encodeURIComponent).join('/');
}
function renderModels(){
  var wrap = el('div');
  wrap.appendChild(section('Models',
    'Enable the models users may pick, set display names and limits, and test one model with a harmless fixed prompt (no QA data is sent).', []));

  (S.ov.providers || []).forEach(function(p){
    var addInput = el('input', { type: 'text', class: 'ap-in', placeholder: 'Add a model id manually', style: 'max-width:280px', maxlength: 120, disabled: !can('create') });
    var head = el('div', { class: 'ap-row', style: 'margin-bottom:8px' }, [
      el('strong', { text: p.name }), pill(p.effective_kind === 'cloud' ? 'Cloud' : 'Local', p.effective_kind === 'cloud' ? 'warn' : 'ok'),
      p.enabled ? null : pill('Provider disabled'),
      el('span', { style: 'flex:1' }), addInput,
      btn('Add', '', function(){
        if(!addInput.value.trim()) return;
        api('/providers/' + p.id + '/models', send('POST', { model_id: addInput.value.trim() })).then(function(){ reload(); }).catch(fail);
      }, { disabled: !can('create') }) ]);
    var rows = (p.models || []).map(function(m){
      var disp = el('input', { type: 'text', class: 'ap-in', value: m.display_name || '', maxlength: 120, style: 'min-width:150px', disabled: !can('edit') });
      var ctx = el('input', { type: 'number', class: 'ap-in', value: m.context_limit || '', min: 0, style: 'width:100px', disabled: !can('edit') });
      var outl = el('input', { type: 'number', class: 'ap-in', value: m.max_output || '', min: 0, style: 'width:100px', disabled: !can('edit') });
      var en = sw(m.enabled, !can('edit'), null, 'Enable ' + m.model_id);
      var result = el('span', { class: 'ap-note' });
      var save = btn('Save', '', function(){
        var payload;
        try { payload = { enabled: en.input.checked, display_name: disp.value.trim(),
          context_limit: ofNumber(ctx, true) || 0, max_output: ofNumber(outl, true) || 0 }; } catch(e){ return fail(e); }
        busy(save, api(modelPath(p.id, m.model_id), send('PUT', payload))).then(function(){ toast('Model saved.'); reload(); }).catch(fail);
      }, { disabled: !can('edit') });
      var test = btn('Test', '', function(){
        busy(test, api('/models/test', send('POST', { provider_id: p.id, model: m.model_id }))).then(function(r){
          result.textContent = r.ok ? ('OK · ' + r.latency_ms + ' ms · reply “' + (r.reply || '') + '”') : ('Failed: ' + r.error);
        }).catch(fail);
      }, { disabled: !can('execute') });
      return [ mono(m.model_id), disp, ctx, outl, en.node, m.discovered ? pill('Discovered', 'info') : pill('Manual'),
        el('div', { class: 'ap-row' }, [ save, test, result ]) ];
    });
    wrap.appendChild(el('div', { class: 'ap-section' }, [ head,
      table(['Model id', 'Display name', 'Context', 'Max output', 'Enabled', 'Source', ''], rows,
        'No models yet. Use “Discover models” on the Providers tab or add one manually.') ]));
  });

  wrap.appendChild(compareSection());
  return wrap;
}

function compareSection(){
  var choices = modelChoices();
  var slots = [0, 1, 2].map(function(i){
    var opts = [['', i === 0 ? 'Choose a model' : '— none —']].concat(choices);
    return select(opts, '');
  });
  var prompt = el('textarea', { rows: 3, maxlength: 1500, class: 'ap-in', style: 'font-family:inherit',
    placeholder: 'A short, non-sensitive test prompt (do not paste confidential QA content).' });
  var out = el('div', { class: 'ap-split', style: 'margin-top:10px' });
  var run = btn('Compare', 'primary', function(){
    var entries = slots.map(function(s){ return splitChoice(s.value); }).filter(Boolean);
    if(!entries.length) return toast('Choose at least one model.', true);
    clear(out);
    busy(run, api('/models/compare', send('POST', { models: entries, prompt: prompt.value }))).then(function(r){
      (r.results || []).forEach(function(x){
        out.appendChild(el('div', { class: 'ap-result' }, [
          el('div', { class: 'ap-row' }, [ el('strong', { text: (x.provider || '?') + ' · ' + (x.model || '') }),
            x.ok ? pill(x.latency_ms + ' ms', 'ok') : pill('Failed', 'bad'),
            x.cloud ? pill('Cloud', 'warn') : null,
            x.ok && x.completion_tokens ? pill(x.prompt_tokens + ' in / ' + x.completion_tokens + ' out') : null ]),
          el('pre', { text: x.ok ? x.text : x.error }) ]));
      });
    }).catch(fail);
  }, { disabled: !can('execute') });
  return section('Compare models',
    'Runs the same prompt on up to three models side by side. Cloud models need their provider permission and the cloud master switch, exactly like normal use. This compares speed and wording only — it is not an accuracy evaluation.', [
    el('div', { class: 'ap-split' }, slots.map(function(s){ return s; })), el('div', { style: 'height:8px' }), prompt,
    el('div', { class: 'ap-row end', style: 'margin-top:8px' }, [ run ]), out ]);
}

/* -------------------------------------------------------------- Routing */
var TASK_LABELS = {
  answer: 'Ask Knowledge (LOCAL answers)', global_answer: 'GLOBAL answers (internet + model)',
  hybrid_answer: 'HYBRID answers', impact_analysis: 'Impact Analysis', test_case: 'Test-case generation',
  api: 'API test generation', sql: 'SQL generation', bug: 'Bug-report generation',
  automation: 'Automation scripts', tool_planner: 'Tool planner (plugins / MCP)'
};
function renderRouting(){
  var r = S.ov.settings.routing, dis = !can('edit');
  var choices = modelChoices();
  var wrap = el('div');

  var defaultSel = select([['', '— choose —']].concat(choices),
    r.default_provider_id ? r.default_provider_id + '|' + r.default_model : '', { disabled: dis });
  var mode = select([['auto', 'Auto'], ['local', 'Local (documents)'], ['global', 'Global (model + web)'], ['hybrid', 'Hybrid (documents + general)']], r.default_mode, { disabled: dis });
  var policy = select([['when_needed', 'Only when the question needs current information (recommended)'],
    ['always', 'Every Global question'], ['never', 'Never \u2014 the model answers from its own knowledge']],
    r.global_search_policy || 'when_needed', { disabled: dis });
  var modelMode = select([['auto', 'Auto'], ['manual', 'Manual']], r.model_mode, { disabled: dis });
  var manualOk = check('Let users choose a model in the AI Assistant', 'Users still cannot use a cloud model unless its provider permission and the cloud master switch allow it.', r.allow_manual_model_selection, dis);
  var internet = check('Allow internet search', 'Search is separate from the model\'s own knowledge: with search off or unreachable, Global still answers general questions from the model. This setting never sends QA knowledge to the internet or to a cloud model.', r.internet_search_enabled, dis);
  var hybridNet = check('Hybrid may use internet search when the knowledge base has no relevant evidence', null, r.hybrid_use_internet, dis);

  wrap.appendChild(section('Defaults', 'Auto mode never silently switches providers: the answer shows exactly which provider and model produced it.', [
    el('div', { class: 'ap-grid' }, [ field('Default mode', mode, 'Auto decides from the question and the selected scope. Local = PSW documents only; Global = the model\'s knowledge, plus internet only when needed; Hybrid = documents first, general knowledge or internet added and labelled.'), field('Internet search in Global', policy, 'Search uses only a sanitised copy of the question, never document text.'), field('Model selection', modelMode, 'Auto uses the routing below; Manual lets users pick a model per question.'),
      field('Default model', defaultSel, 'Used for every task without its own route.') ]),
    el('div', { class: 'ap-grid', style: 'margin-top:10px' }, [ manualOk.node, internet.node, hybridNet.node ]) ]));

  var taskSels = {};
  var rows = (S.ov.tasks || []).map(function(t){
    var cur = (r.task_routes || {})[t];
    var s = select([['', 'Use default model']].concat(choices), cur ? cur.provider_id + '|' + cur.model : '', { disabled: dis });
    taskSels[t] = s;
    return [ el('strong', { text: TASK_LABELS[t] || t }), s ];
  });
  wrap.appendChild(section('Model per task', 'Send different work to different models — for example a small fast model for SQL and a stronger one for Impact Analysis.', [ table(['Task', 'Model'], rows) ]));

  var fb = r.fallback || {};
  var fbEnabled = check('Enable fallback', 'If the chosen model fails, try the models below, in order. Each attempt is shown in the answer. Disabled by default.', fb.enabled, dis);
  var fbCloud = check('Allow fallback to cloud providers', 'Fallback to a cloud provider still needs that provider\'s data permission and the cloud master switch.', fb.allow_cloud_fallback, dis);
  var chain = (fb.chain || []).map(function(c){ return c.provider_id + '|' + (c.model || ''); });
  var chainBox = el('div', { class: 'ap-row', style: 'flex-direction:column; align-items:stretch; gap:6px' });
  var fbChoices = [];
  (S.ov.providers || []).forEach(function(p){
    if(!p.enabled) return;
    fbChoices.push([p.id + '|', p.name + ' · (provider default model)']);
  });
  fbChoices = fbChoices.concat(choices);
  function drawChain(){
    clear(chainBox);
    if(!chain.length) chainBox.appendChild(el('div', { class: 'ap-note', text: 'No fallback models configured.' }));
    chain.forEach(function(v, i){
      var s = select(fbChoices, v, { disabled: dis });
      s.addEventListener('change', function(){ chain[i] = s.value; });
      chainBox.appendChild(el('div', { class: 'ap-row' }, [ pill(String(i + 1), 'info'), s,
        btn('Up', '', function(){ if(i){ var t = chain[i - 1]; chain[i - 1] = chain[i]; chain[i] = t; drawChain(); } }, { disabled: dis || !i }),
        btn('Down', '', function(){ if(i < chain.length - 1){ var t = chain[i + 1]; chain[i + 1] = chain[i]; chain[i] = t; drawChain(); } }, { disabled: dis || i >= chain.length - 1 }),
        btn('Remove', '', function(){ chain.splice(i, 1); drawChain(); }, { disabled: dis }) ]));
    });
  }
  drawChain();
  wrap.appendChild(section('Fallback', 'Fallback happens only when you enable it here and only to providers that are permitted for the data involved. It is never silent.', [
    el('div', { class: 'ap-grid' }, [ fbEnabled.node, fbCloud.node ]), el('div', { style: 'height:8px' }), chainBox,
    el('div', { class: 'ap-row', style: 'margin-top:8px' }, [
      btn('Add fallback model', '', function(){ if(chain.length < 5){ chain.push(fbChoices.length ? fbChoices[0][0] : ''); drawChain(); } }, { disabled: dis }) ]),
    saveRow('Save routing', function(b){
      var d = splitChoice(defaultSel.value);
      var routes = {};
      Object.keys(taskSels).forEach(function(t){
        var c = splitChoice(taskSels[t].value);
        routes[t] = c ? { provider_id: c.provider_id, model: c.model } : null;
      });
      var chainOut = chain.map(splitChoice).filter(Boolean).map(function(c){ return { provider_id: c.provider_id, model: c.model }; });
      var patch = { default_mode: mode.value, global_search_policy: policy.value, model_mode: modelMode.value,
        allow_manual_model_selection: manualOk.input.checked, internet_search_enabled: internet.input.checked,
        hybrid_use_internet: hybridNet.input.checked, task_routes: routes,
        fallback: { enabled: fbEnabled.input.checked, allow_cloud_fallback: fbCloud.input.checked, chain: chainOut } };
      if(d){ patch.default_provider_id = d.provider_id; patch.default_model = d.model; }
      saveSection(b, 'routing', patch);
    }) ]));
  return wrap;
}
function saveSection(button, name, patch){
  busy(button, api('/settings/' + name, send('PUT', patch))).then(function(r){
    S.ov.settings[name] = r.settings; toast('Settings saved. Cached answers were cleared.'); reload();
  }).catch(fail);
}

/* ------------------------------------------------------------------ RAG */
function renderRag(){
  var r = S.ov.settings.rag, dis = !can('edit'), e = S.ov.embedding || {};
  var wrap = el('div');
  wrap.appendChild(section('Embeddings and knowledge store',
    'Read-only. The answer LLM and the embedding model are independent: changing the answer model never re-indexes, re-uploads or modifies the knowledge store.', [
    el('div', { class: 'ap-kpis' }, [
      kpi('Embedding model', e.model || 'unknown'), kpi('Dimension', e.dimension || '—'),
      kpi('Vectors stored', two(e.vectors)), kpi('Distance space', (e.distance_space || 'l2').toUpperCase()),
      kpi('Reranker', e.reranker || 'off') ]),
    el('p', { class: 'ap-help', style: 'margin-top:10px', text: e.note || '' }) ]));

  var form = makeForm([
    { k: 'enhanced_pipeline', type: 'bool', label: 'Use the improved retrieval pipeline', note: 'Candidate pool, correct similarity scores, reranking, per-document limits and a sufficiency check. Turn off only to compare with the previous behaviour.' },
    { k: 'top_k', type: 'int', label: 'Passages given to the model', min: 1, max: 20 },
    { k: 'candidate_pool', type: 'int', label: 'Candidate pool', min: 1, max: 100, note: 'Passages fetched before reranking.' },
    { k: 'max_chunks_per_document', type: 'int', label: 'Max passages per document', min: 1, max: 20 },
    { k: 'similarity_threshold', type: 'float', label: 'Candidate similarity floor', min: 0, max: 1, step: 0.01, note: 'Passages below this are discarded.' },
    { k: 'min_best_similarity', type: 'float', label: 'Minimum best similarity to answer', min: 0, max: 1, step: 0.01, note: 'Below this (and without other strong evidence) no answer is generated. Calibrate with the tool below.' },
    { k: 'context_chars', type: 'int', label: 'Context size (characters)', min: 500, max: 40000 },
    { k: 'global_context_chars', type: 'int', label: 'Web context size (characters)', min: 500, max: 20000 },
    { k: 'rerank_enabled', type: 'bool', label: 'Rerank with the cross-encoder', note: 'Needs the reranker model to be installed; otherwise it is skipped.' },
    { k: 'citations_required', type: 'bool', label: 'Require citations in answers' },
    { k: 'grounding_check', type: 'bool', label: 'Check answers against their sources', note: 'Removes invalid citations and flags numbers or IDs that no source contains.' },
    { k: 'diagnostics_enabled', type: 'bool', label: 'Include retrieval diagnostics in answers' }
  ], r, dis);
  wrap.appendChild(section('Retrieval settings', 'These defaults are starting points, not tuned values. Use the diagnostics below on your real documents before relying on the thresholds.', [
    form.node, saveRow('Save RAG settings', function(b){
      try { saveSection(b, 'rag', form.collect()); } catch(err){ fail(err); } }) ]));
  wrap.appendChild(ragDiagnostics());
  return wrap;
}
function kpi(label, value){ return el('div', { class: 'ap-kpi' }, [ el('span', { text: label }), el('strong', { text: String(value), style: 'font-size:14px; word-break:break-word' }) ]); }
function hierarchyNames(list){
  return (list || []).map(function(x){ return typeof x === 'string' ? x : (x && x.name); }).filter(Boolean);
}
function hierarchyGet(path, key){
  return host().apiFetch('/api/knowledge' + path).then(function(r){ return hierarchyNames(r && r[key]); }).catch(function(){ return []; });
}
/* Domain -> Module -> Knowledge picker that reuses the canonical hierarchy, so a
   misspelt scope cannot be typed. Empty = all. */
function scopePicker(){
  var d = select([['', 'All domains']], '', { class: 'ap-in', style: 'max-width:200px', 'aria-label': 'Domain' });
  var m = select([['', 'All modules']], '', { class: 'ap-in', style: 'max-width:200px', disabled: true, 'aria-label': 'Module' });
  var k = select([['', 'All knowledge']], '', { class: 'ap-in', style: 'max-width:220px', disabled: true, 'aria-label': 'Knowledge' });
  function fill(sel, first, names){
    clear(sel); sel.appendChild(el('option', { value: '', text: first }));
    names.forEach(function(n){ sel.appendChild(el('option', { value: n, text: n })); });
    sel.disabled = !names.length; sel.value = '';
  }
  hierarchyGet('/domains', 'domains').then(function(n){ fill(d, 'All domains', n); d.disabled = false; });
  d.addEventListener('change', function(){
    fill(m, 'All modules', []); fill(k, 'All knowledge', []);
    if(d.value) hierarchyGet('/domains/' + encodeURIComponent(d.value) + '/modules', 'modules').then(function(n){ fill(m, 'All modules', n); });
  });
  m.addEventListener('change', function(){
    fill(k, 'All knowledge', []);
    if(d.value && m.value) hierarchyGet('/domains/' + encodeURIComponent(d.value) + '/modules/' + encodeURIComponent(m.value) + '/knowledge-names', 'knowledge_names').then(function(n){ fill(k, 'All knowledge', n); });
  });
  return { nodes: [d, m, k], scope: function(){
    var o = {};
    if(d.value) o.domain = d.value;
    if(d.value && m.value) o.module = m.value;
    if(d.value && m.value && k.value) o.knowledge_name = k.value;
    return o; } };
}
function ragDiagnostics(){
  var q = el('input', { type: 'text', class: 'ap-in', placeholder: 'Type a real question your users ask', maxlength: 500 });
  var pick = scopePicker();
  var out = el('div', { style: 'margin-top:10px' });
  var run = btn('Run retrieval only', 'primary', function(){
    if(!q.value.trim()) return toast('Enter a question.', true);
    var payload = Object.assign({ question: q.value.trim() }, pick.scope());
    clear(out);
    busy(run, api('/rag/diagnostics', send('POST', payload))).then(function(r){
      var a = r.assessment || {}, mt = r.metrics || {}, su = r.settings_used || {};
      out.appendChild(banner((a.sufficient ? 'Would answer from the selected knowledge' : 'Would NOT answer from the selected knowledge') +
        ' — ' + ({ ok: 'enough evidence', low_similarity: 'evidence below the threshold', no_relevant_chunks: 'no passage found',
                       referenced_item_not_found: 'the requested item is not in the selected knowledge' }[a.reason] || (a.reason || '')), a.sufficient ? 'ok' : 'warn'));
      out.appendChild(el('div', { class: 'ap-grid', style: 'margin-top:8px' }, [
        kpi('Best similarity', mt.best_similarity === undefined || mt.best_similarity === null ? '—' : mt.best_similarity + '  (minimum ' + su.min_best_similarity + ')'),
        kpi('Best relevance (reranker)', mt.best_relevance === undefined || mt.best_relevance === null ? 'not used' : mt.best_relevance),
        kpi('Best keyword coverage', mt.best_keyword_coverage === undefined || mt.best_keyword_coverage === null ? '—' : mt.best_keyword_coverage),
        kpi('Auto routing would choose', ((r.auto_route || {}).mode || '—').toUpperCase() + ((r.auto_route || {}).probe ? ' (after an evidence check)' : '')) ]));
      out.appendChild(el('p', { class: 'ap-note', text: 'Similarity is how close the passage is to the question in vector space; relevance is a separate reranker score. Neither is the probability that an answer is correct.' }));
      var steps = el('ul', { class: 'ap-steps' });
      (r.decision_steps || []).forEach(function(t){ steps.appendChild(el('li', { text: t })); });
      (((r.auto_route || {}).reasons) || []).forEach(function(t){ steps.appendChild(el('li', { text: t })); });
      out.appendChild(steps);
      var rows = (r.candidates || []).map(function(c, i){
        return [ num(i + 1), c.file_name || '—', num(c.chunk_number), num(c.cosine), num(c.rerank_prob), num(c.lexical), num(c.fused),
          c.selected ? pill('Used', 'ok') : pill(c.drop_reason || 'Not used') ];
      });
      out.appendChild(el('div', { style: 'height:8px' }));
      out.appendChild(table(['#', 'File', 'Part', { t: 'Similarity', num: true }, { t: 'Relevance', num: true }, { t: 'Keyword', num: true }, { t: 'Fused', num: true }, 'Result'], rows, 'No candidates were retrieved.'));
    }).catch(fail);
  }, { disabled: !can('execute') });
  return section('Retrieval diagnostics', 'Runs retrieval only — no model is called and nothing is stored. Choose a Domain / Module / Knowledge from the hierarchy (leave blank for all). Shows file names, scores and the reasoning behind each threshold decision, never passage text.', [
    el('div', { class: 'ap-row' }, [ q ].concat(pick.nodes, [ run ])), out ]);
}

/* ----------------------------------------------------------- Plugins/MCP */
function renderMcp(){
  var wrap = el('div');
  var items = S.ov.mcp || [];
  var sec = S.ov.settings.security || {};
  wrap.appendChild(section('Plugins and MCP tools',
    'Connect MCP servers so the assistant can call read-only tools. The AI may only run tools classed as "read" in allowed modes; tools that change data can be run manually by an administrator after explicit confirmation. Every call is logged. ' +
    'Built-in read-only tools: qa.search_test_cases and qa.recent_executions (available to users who already have access to that data).', [
    sec.ai_tools_enabled === false ? banner('AI tool use is switched off in the Security tab. Integrations can still be tested and run manually.', 'warn') : null,
    el('div', { class: 'ap-row', style: 'margin-top:8px' }, [
      btn('Add integration', 'primary', function(){ S.mcpEditing = null; paintBody(); }, { disabled: !can('create') }) ]) ]));
  if(S.mcpEditing !== undefined){
    var cur = S.mcpEditing === null ? null : items.filter(function(i){ return i.id === S.mcpEditing; })[0] || null;
    wrap.appendChild(mcpForm(cur));
  }
  if(!items.length) wrap.appendChild(el('div', { class: 'ap-section' }, [ el('div', { class: 'ap-empty', text: 'No integrations yet.' }) ]));
  items.forEach(function(i){ wrap.appendChild(mcpCard(i)); });
  wrap.appendChild(callsSection());
  return wrap;
}
function mcpCard(i){
  var en = sw(i.enabled, !can('edit'), function(){
    api('/mcp/' + i.id, send('PUT', { enabled: en.input.checked })).then(function(){ toast(en.input.checked ? 'Integration enabled.' : 'Integration disabled.'); reload(); })
      .catch(function(err){ en.input.checked = !en.input.checked; fail(err); });
  }, 'Enable ' + i.name);
  var out = el('span', { class: 'ap-note', text: S.notes['m' + i.id] || '' });
  var testBtn = btn('Test connection', '', function(){
    busy(testBtn, api('/mcp/' + i.id + '/test', send('POST'))).then(function(r){
      S.notes['m' + i.id] = r.ok ? ('OK · ' + (r.tools || []).length + ' tool(s) found') : ('Failed: ' + r.error); reload();
    }).catch(fail);
  }, { disabled: !can('execute') });
  var target = i.transport === 'stdio' ? (i.command + ' ' + (i.args || []).join(' ')) : i.url;
  var tools = i.tools || [];
  var open = S.openTools === i.id;
  var card = el('div', { class: 'ap-section' }, [
    el('div', { class: 'ap-row' }, [ en.node, el('strong', { text: i.name }), pill(i.transport === 'stdio' ? 'Local process (stdio)' : 'HTTP', 'info'),
      statusPill(i.status), i.allow_write ? pill('Write tools allowed', 'warn') : pill('Read-only'),
      (i.allowed_modes || []).map(function(m){ return pill(m.toUpperCase()); }),
      el('span', { style: 'flex:1' }), testBtn,
      btn(open ? 'Hide tools (' + tools.length + ')' : 'Tools (' + tools.length + ')', '', function(){ S.openTools = open ? null : i.id; paintBody(); }),
      btn('Edit', '', function(){ S.mcpEditing = i.id; paintBody(); }, { disabled: !can('edit') }),
      confirmButton('Delete', function(b){ busy(b, api('/mcp/' + i.id, send('DELETE'))).then(function(){ toast('Integration deleted.'); reload(); }).catch(fail); }, { disabled: !can('delete') }),
      out ]),
    el('div', { class: 'ap-note', style: 'margin-top:6px; font-family:\'JetBrains Mono\',monospace', text: target }),
    i.last_error ? banner(i.last_error, 'bad') : null,
    open ? toolsPanel(i) : null ]);
  return card;
}
function toolsPanel(i){
  var tools = i.tools || [], dis = !can('edit');
  var sels = {};
  var rows = tools.map(function(t){
    var s = select([['read', 'Read (AI may call)'], ['write', 'Write (manual + confirm only)'], ['blocked', 'Blocked']], t.effective_permission || t.permission || 'write', { disabled: dis });
    sels[t.name] = s;
    return [ mono(t.name), el('span', { text: (t.description || '').slice(0, 160) }), s,
      btn('Run…', '', function(){ S.runTool = { id: i.id, tool: t }; paintBody(); }, { disabled: !can('execute') }) ];
  });
  var wrap = el('div', { style: 'margin-top:10px' }, [
    table(['Tool', 'Description', 'Permission', ''], rows, 'No tools known. Run “Test connection” to load the tool list.'),
    tools.length ? saveRow('Save tool permissions', function(b){
      var perms = {}; Object.keys(sels).forEach(function(k){ perms[k] = sels[k].value; });
      busy(b, api('/mcp/' + i.id, send('PUT', { tool_permissions: perms }))).then(function(){ toast('Tool permissions saved.'); reload(); }).catch(fail);
    }, 'Unknown tools are treated as "write" until reviewed.') : null ]);
  if(S.runTool && S.runTool.id === i.id) wrap.appendChild(runToolPanel(i, S.runTool.tool));
  return wrap;
}
function runToolPanel(i, tool){
  var args = el('textarea', { rows: 4, class: 'ap-in', style: 'font-family:\'JetBrains Mono\',monospace', value: '{}' });
  var conf = check('I confirm this tool may change data', null, false, false);
  var out = el('pre', { class: 'ap-result', style: 'white-space:pre-wrap; max-height:260px; overflow:auto' });
  var run = btn('Run tool', 'primary', function(){
    var parsed;
    try { parsed = JSON.parse(args.value || '{}'); } catch(e){ return toast('Arguments must be valid JSON.', true); }
    busy(run, api('/mcp/' + i.id + '/call', send('POST', { tool: tool.name, arguments: parsed, confirm: conf.input.checked }))).then(function(r){
      out.textContent = JSON.stringify(r, null, 2).slice(0, 6000);
    }).catch(function(err){ out.textContent = err.message; });
  }, { disabled: !can('execute') });
  return el('div', { class: 'ap-section', style: 'margin-top:10px' }, [
    el('h4', { class: 'ap-h', text: 'Run ' + tool.name }),
    tool.inputSchema ? el('p', { class: 'ap-help', style: 'font-family:\'JetBrains Mono\',monospace', text: JSON.stringify(tool.inputSchema).slice(0, 500) }) : null,
    args, el('div', { class: 'ap-row', style: 'margin-top:8px' }, [ (i.allow_write ? conf.node : null), run,
      btn('Close', '', function(){ S.runTool = null; paintBody(); }) ]), out ]);
}
function mcpForm(i){
  var editing = !!i, dis = editing ? !can('edit') : !can('create');
  var name = el('input', { type: 'text', value: editing ? i.name : '', maxlength: 60, disabled: dis });
  var transport = select([['stdio', 'Local process (stdio)'], ['streamable_http', 'Remote HTTP (streamable)']], editing ? i.transport : 'stdio', { disabled: dis });
  var command = el('input', { type: 'text', value: editing ? i.command : '', placeholder: 'e.g. npx', disabled: dis });
  var args = el('textarea', { rows: 3, value: editing ? (i.args || []).join('\n') : '', placeholder: 'One argument per line', disabled: dis });
  var url = el('input', { type: 'text', value: editing ? i.url : '', placeholder: 'https://host/mcp', disabled: dis });
  var secrets = el('textarea', { rows: 3, disabled: dis, placeholder: editing && i.has_secret ? 'Saved — leave blank to keep' : 'NAME=value, one per line' });
  var clearSec = check('Remove saved secrets', null, false, dis || !editing);
  var timeout = el('input', { type: 'number', value: editing ? i.timeout_s : 20, min: 2, max: 120, disabled: dis });
  var mLocal = check('LOCAL', null, editing ? (i.allowed_modes || []).indexOf('local') >= 0 : true, dis);
  var mHybrid = check('HYBRID', null, editing ? (i.allowed_modes || []).indexOf('hybrid') >= 0 : false, dis);
  var aw = check('Allow write tools (manual runs only)', 'The AI itself never runs write tools.', editing ? i.allow_write : false, dis);
  var allowed = ((S.ov.settings.security || {}).mcp_allowed_commands || []).join(', ');
  var stdioBox = el('div', { class: 'ap-grid', style: 'grid-column:1/-1' }, [
    field('Command', command, 'Allowed: ' + allowed + '. Change the list in the Security tab.'), field('Arguments', args) ]);
  var httpBox = el('div', { class: 'ap-grid', style: 'grid-column:1/-1' }, [ field('URL', url) ]);
  function applyT(){ stdioBox.style.display = transport.value === 'stdio' ? '' : 'none'; httpBox.style.display = transport.value === 'stdio' ? 'none' : ''; }
  transport.addEventListener('change', applyT); applyT();
  return el('div', { class: 'ap-section' }, [
    el('h4', { class: 'ap-h', text: editing ? 'Edit integration — ' + i.name : 'Add integration' }),
    el('p', { class: 'ap-help', text: 'New and changed integrations start disabled. Test the connection successfully, then enable. Secrets (environment variables for local processes, headers for HTTP) are stored encrypted and never shown again.' }),
    el('div', { class: 'ap-grid' }, [ field('Name', name), field('Transport', transport), stdioBox, httpBox,
      field('Secrets', secrets, 'stdio: environment variables · HTTP: request headers'), field('Timeout (s)', timeout) ]),
    el('div', { class: 'ap-row', style: 'margin-top:10px' }, [ el('span', { class: 'ap-label', text: 'Allowed modes:' }), mLocal.node, mHybrid.node,
      editing ? clearSec.node : null, aw.node ]),
    el('div', { class: 'ap-row end', style: 'margin-top:12px' }, [
      btn('Cancel', '', function(){ S.mcpEditing = undefined; paintBody(); }),
      (function(){
        var b = btn(editing ? 'Save integration' : 'Create integration', 'primary', function(){
          var payload;
          try {
            var sec = {};
            lines(secrets.value).forEach(function(l){ var k = l.indexOf('='); if(k < 1) throw new Error('Secrets must be NAME=value, one per line.'); sec[l.slice(0, k).trim()] = l.slice(k + 1); });
            payload = { name: name.value.trim(), transport: transport.value, timeout_s: ofNumber(timeout, true) || 20,
              allow_write: aw.input.checked,
              allowed_modes: [mLocal.input.checked ? 'local' : null, mHybrid.input.checked ? 'hybrid' : null].filter(Boolean) };
            if(transport.value === 'stdio'){ payload.command = command.value.trim(); payload.args = lines(args.value); }
            else payload.url = url.value.trim();
            if(Object.keys(sec).length) payload.secrets = sec;
            if(editing && clearSec.input.checked) payload.clear_secrets = true;
          } catch(e){ return fail(e); }
          busy(b, api(editing ? '/mcp/' + i.id : '/mcp', send(editing ? 'PUT' : 'POST', payload))).then(function(){
            S.mcpEditing = undefined; toast('Integration saved. Run “Test connection”, then enable it.'); reload();
          }).catch(fail);
        }, { disabled: dis });
        return b;
      })() ]) ]);
}
function callsSection(){
  var box = el('div');
  var load = btn('Show recent tool calls', '', function(){
    busy(load, api('/mcp/calls/recent?limit=50')).then(function(r){
      clear(box);
      box.appendChild(table(['Time', 'User', 'Integration', 'Tool', 'By', 'Result', { t: 'ms', num: true }, 'Arguments / error'],
        (r.calls || []).map(function(c){ return [ c.ts, c.username, c.integration, mono(c.tool), c.initiated_by,
          c.success ? pill('OK', 'ok') : pill('Failed', 'bad'), num(c.duration_ms), (c.error || c.args_summary || '').slice(0, 140) ]; }), 'No tool calls yet.'));
    }).catch(fail);
  });
  return section('Tool-call log', 'Every manual and AI-initiated call, with who started it. Argument values are summarised and secrets are never logged.', [ load, box ]);
}

/* ---------------------------------------------------------- Tokens/cache */
function renderTokens(){
  var t = S.ov.settings.tokens, dis = !can('edit');
  var wrap = el('div');
  var form = makeForm([
    { k: 'input_budget_tokens', type: 'int', label: 'Input budget (tokens)', min: 256, max: 200000, note: 'Total prompt size target. Evidence is trimmed by rank, not cut mid-passage.' },
    { k: 'output_budget_tokens', type: 'int', label: 'Output budget (tokens)', min: 16, max: 32000 },
    { k: 'conversation_turns', type: 'int', label: 'Recent turns kept verbatim', min: 0, max: 20 },
    { k: 'history_summary_chars', type: 'int', label: 'Older-history summary (chars)', min: 0, max: 4000 },
    { k: 'retries', type: 'int', label: 'Retries on transient errors', min: 0, max: 3 },
    { k: 'request_timeout_s', type: 'int', label: 'Request timeout (s)', min: 5, max: 1800 },
    { k: 'exact_cache.ttl_s', type: 'int', label: 'Answer cache lifetime (s)', min: 10, max: 86400 },
    { k: 'exact_cache.max_items', type: 'int', label: 'Answer cache size (items)', min: 10, max: 5000 },
    { k: 'context_optimization', type: 'bool', label: 'Optimise context', note: 'Rank-ordered evidence within the budget, no duplicate passages.' },
    { k: 'summarize_history', type: 'bool', label: 'Summarise older conversation turns' },
    { k: 'retrieval_filtering', type: 'bool', label: 'Filter weak passages before the model sees them' },
    { k: 'exact_cache.enabled', type: 'bool', label: 'Cache identical answers', note: 'Keyed by question, scope, user, role, knowledge version, model and settings. Failures, “not enough evidence”, web and tool answers are never cached.' },
    { k: 'usage_tracking', type: 'bool', label: 'Record usage (tokens, latency)' }
  ], t, dis);
  var cs = S.ov.cache || {};
  wrap.appendChild(section('Token optimisation and caching',
    'Saving settings clears cached answers so nothing is served from an old configuration. A semantic (similar-question) cache is not active in this release.', [
    form.node, saveRow('Save token settings', function(b){ try { saveSection(b, 'tokens', form.collect()); } catch(e){ fail(e); } }),
    el('div', { class: 'ap-row', style: 'margin-top:10px' }, [
      el('span', { class: 'ap-note', text: 'Answer cache: ' + two(cs.items === undefined ? cs.size : cs.items) + ' item(s) · ' + two(cs.hits) + ' hit(s) · ' + two(cs.misses) + ' miss(es)' }),
      btn('Clear answer cache', '', function(b){ api('/cache/clear', send('POST')).then(function(){ toast('Answer cache cleared.'); reload(); }).catch(fail); }, { disabled: !can('edit') }) ]) ]));
  wrap.appendChild(usageSection());
  return wrap;
}
function usageSection(){
  var box = el('div'), days = select([['1', 'Last 24 hours'], ['7', 'Last 7 days'], ['30', 'Last 30 days']], String(S.usageDays));
  function draw(u){
    clear(box);
    var tt = u.totals || {};
    box.appendChild(el('div', { class: 'ap-kpis' }, [ kpi('Requests', two(tt.requests)), kpi('Prompt tokens', two(tt.prompt_tokens)),
      kpi('Completion tokens', two(tt.completion_tokens)), kpi('Avg latency', (tt.avg_latency_ms || 0) + ' ms'),
      kpi('Cache hits', two(tt.cache_hits)), kpi('Cloud requests', two(tt.cloud_requests)), kpi('Fallbacks', two(tt.fallbacks)), kpi('Failures', two(tt.failures)) ]));
    box.appendChild(el('div', { style: 'height:10px' }));
    box.appendChild(table(['Provider', 'Model', { t: 'Requests', num: true }, { t: 'Prompt tokens', num: true }, { t: 'Completion tokens', num: true }, { t: 'Avg ms', num: true }],
      (u.per_model || []).map(function(r){ return [ r.provider || '—', mono(r.model || '—'), num(r.requests), num(r.prompt_tokens), num(r.completion_tokens), num(r.avg_latency_ms) ]; }), 'No usage recorded in this period.'));
  }
  days.addEventListener('change', function(){ S.usageDays = Number(days.value); api('/usage?days=' + S.usageDays).then(draw).catch(fail); });
  draw(S.ov.usage || {});
  if(S.usageDays !== 7) api('/usage?days=' + S.usageDays).then(draw).catch(function(){});
  return section('Usage', 'Token counts marked as estimates were not reported by the provider.', [ el('div', { class: 'ap-row', style: 'margin-bottom:8px' }, [ days ]), box ]);
}

/* ------------------------------------------------------------- Security */
function renderSecurity(){
  var s = S.ov.settings.security, dis = !can('edit');
  var wrap = el('div');
  var cloud = check('Allow QA data to be sent to cloud AI providers', 'Master switch, OFF by default. Even when ON, each cloud provider must also have its own permission ticked. Local-network models never need this.', s.cloud_data_sharing_enabled, dis);
  var ack = check('I am authorised to allow private QA knowledge to leave PSW infrastructure for approved cloud providers.', null, false, dis);
  var ackBox = el('div', { style: 'margin-top:8px; display:none' }, [ banner('Turning this on lets users’ questions and retrieved knowledge passages (redacted if enabled below) be sent to cloud providers that are individually permitted. Nothing is sent just because internet search is on.', 'warn'), ack.node ]);
  cloud.input.addEventListener('change', function(){ ackBox.style.display = (cloud.input.checked && !s.cloud_data_sharing_enabled) ? '' : 'none'; });
  var form = makeForm([
    { k: 'redact_cloud_prompts', type: 'bool', label: 'Redact sensitive patterns before anything goes to a cloud provider', note: 'E-mail addresses, tokens, keys, long numbers.' },
    { k: 'audit_enabled', type: 'bool', label: 'Write configuration changes to the audit log' },
    { k: 'ai_tools_enabled', type: 'bool', label: 'Let the AI call read-only plugin / MCP tools' },
    { k: 'tool_max_calls', type: 'int', label: 'Max tool calls per question', min: 1, max: 5 },
    { k: 'mcp_allowed_commands', type: 'lines', label: 'Allowed MCP commands (one per line)', note: 'Bare executable names only; no paths or shells.' }
  ], s, dis);
  wrap.appendChild(section('Data and tool security', 'Secrets are encrypted at rest and are never returned to the browser or written to logs. Authentication and role permissions are unchanged: only roles with Settings access can open this screen.', [
    cloud.node, ackBox, el('div', { style: 'height:12px' }), form.node,
    saveRow('Save security settings', function(b){
      var patch;
      try { patch = form.collect(); } catch(e){ return fail(e); }
      patch.cloud_data_sharing_enabled = cloud.input.checked;
      if(cloud.input.checked && !s.cloud_data_sharing_enabled && !ack.input.checked) return toast('Tick the confirmation to enable cloud data sharing.', true);
      saveSection(b, 'security', patch);
    }) ]));
  var box = el('div');
  var load = btn('Show configuration audit log', '', function(){
    busy(load, api('/audit?limit=100')).then(function(r){
      clear(box);
      box.appendChild(table(['Time', 'User', 'Action', 'Target', 'Detail'],
        (r.audit || []).map(function(a){ return [ a.ts, a.username, mono(a.action), a.target, (a.detail || '').slice(0, 160) ]; }), 'No entries.'));
    }).catch(fail);
  });
  wrap.appendChild(section('Audit log', 'Provider, model, routing, security and plugin changes — never key values.', [ load, box ]));
  return wrap;
}

/* ================================================ AI Assistant additions */
var A = { opts: null, loading: null, built: false, requestId: null, poll: null, progressEl: null,
          sel: { mode: 'auto', modelMode: 'auto', model: '', tools: false } };
function store(k, v){ try { if(v === undefined) return window.localStorage.getItem('ap.' + k); window.localStorage.setItem('ap.' + k, v); } catch(e){ return null; } return null; }
function barEl(){ return document.getElementById('aiPlatformBar'); }
function aiApi(path, opts){ return host().apiFetch('/api/ai-assistant' + path, opts); }

var MODE_HELP = {
  auto: 'Auto: the question decides. General questions are answered by the selected model, questions about PSW documents use the knowledge base, and questions that mix both use Hybrid. A selected Domain / Module always means Local.',
  local: 'Local (documents): answers only from the selected PSW knowledge and cites the passages used. It does not search the internet and does not answer from the model\'s own memory.',
  global: 'Global (model + web): answers from the selected model\'s general knowledge. It searches the internet only when the question needs current information (Settings \u2192 LLM \u2192 AI Routing). If search is unavailable, general questions are still answered and current-information questions say they cannot be verified. The PSW knowledge base is not used.',
  hybrid: 'Hybrid (documents + general): uses PSW knowledge first and may add general knowledge or internet results, clearly labelled. If the knowledge base has nothing relevant, a general question is answered from the model; a question about a specific PSW item is not guessed.'
};
var MODE_LABELS = [['auto', 'Auto'], ['local', 'Local (documents)'], ['global', 'Global (model + web)'], ['hybrid', 'Hybrid (documents + general)']];
function loadOptions(force){
  if(!host()) return Promise.resolve(null);
  if(A.opts && !force) return Promise.resolve(A.opts);
  if(A.loading) return A.loading;
  A.loading = aiApi('/options').then(function(o){ A.opts = o; A.loading = null; buildBar(); return o; })
    .catch(function(){ A.loading = null; A.opts = null; buildBar(); return null; });
  return A.loading;
}
function allChoices(){
  var out = [];
  ((A.opts || {}).providers || []).forEach(function(p){
    (p.models || []).forEach(function(m){ out.push({ value: p.id + '|' + m.id, provider: p, model: m }); });
  });
  return out;
}
function buildBar(){
  var bar = barEl();
  if(!bar) return;
  clear(bar);
  var o = A.opts;
  if(!o){ bar.style.display = 'none'; return; }
  bar.style.display = '';
  var saved = store('mode');
  A.sel.mode = (o.modes || []).indexOf(A.sel.mode) >= 0 && A.built ? A.sel.mode : ((o.modes || []).indexOf(saved) >= 0 ? saved : (o.default_mode || 'auto'));
  if(!A.built){ var pref = store('modelMode'); A.sel.modelMode = (pref === 'manual' || pref === 'auto') ? pref : (o.model_mode || 'auto'); }
  if(!o.allow_manual_model_selection) A.sel.modelMode = 'auto';
  A.built = true;

  var mode = select(MODE_LABELS, A.sel.mode,
    { id: 'aiModeSelect', 'aria-label': 'Answer mode', title: MODE_HELP[A.sel.mode] });
  mode.addEventListener('change', function(){ A.sel.mode = mode.value; mode.title = MODE_HELP[mode.value]; store('mode', mode.value); paintChip(); });
  bar.appendChild(el('label', null, [ 'Mode', mode ]));

  var manualSel = null;
  if(o.allow_manual_model_selection){
    var mm = select([['auto', 'Auto'], ['manual', 'Manual']], A.sel.modelMode, { id: 'aiModelModeSelect', 'aria-label': 'Model selection',
      title: 'Auto: the administrator\'s routing picks the model. Manual: you pick one.' });
    var choices = allChoices();
    manualSel = select(choices.map(function(c){
      return [c.value, c.provider.name + ' · ' + (c.model.name || c.model.id) + (c.provider.kind === 'cloud' ? '  (cloud)' : '')]; }),
      A.sel.model || (choices[0] ? choices[0].value : ''), { id: 'aiModelSelect', 'aria-label': 'Model' });
    if(!choices.length) mm.disabled = true;
    manualSel.style.display = A.sel.modelMode === 'manual' ? '' : 'none';
    A.sel.model = manualSel.value;
    mm.addEventListener('change', function(){ A.sel.modelMode = mm.value; store('modelMode', mm.value); manualSel.style.display = mm.value === 'manual' ? '' : 'none'; A.sel.model = manualSel.value; paintChip(); });
    manualSel.addEventListener('change', function(){ A.sel.model = manualSel.value; paintChip(); });
    bar.appendChild(el('label', null, [ 'Model', mm ]));
    bar.appendChild(manualSel);
  }
  bar.appendChild(el('span', { id: 'aiModelChip', class: 'ap-model-chip', 'aria-live': 'polite' }));
  if(o.tools_available){
    var tools = el('input', { type: 'checkbox', id: 'aiUseTools', checked: A.sel.tools, onchange: function(){ A.sel.tools = tools.checked; } });
    bar.appendChild(el('label', { title: 'Let the assistant call read-only plugin / MCP tools you have access to (LOCAL and HYBRID only).' }, [ tools, 'Use tools' ]));
  }
  bar.appendChild(btn('Stop', 'danger ap-stop', function(){ cancelRequest(); }, { id: 'aiStopBtn', 'aria-label': 'Stop generating' }));
  paintChip();
}
function paintChip(){
  var chip = document.getElementById('aiModelChip');
  if(!chip || !A.opts) return;
  clear(chip);
  var o = A.opts, label = '', cloud = false, status = '';
  var manual = A.sel.modelMode === 'manual' && o.allow_manual_model_selection;
  if(manual){
    var c = allChoices().filter(function(x){ return x.value === A.sel.model; })[0];
    if(c){ label = c.provider.name + ' · ' + (c.model.name || c.model.id); cloud = c.provider.kind === 'cloud'; status = c.provider.status; }
  } else if(o.default && o.default.provider){
    label = 'Auto → ' + o.default.provider + ' · ' + (o.default.model || 'default');
    var dp = (o.providers || []).filter(function(p){ return p.id === o.default.provider_id; })[0];
    cloud = !!(dp && dp.kind === 'cloud'); status = dp ? dp.status : '';
  } else label = 'Auto → no model configured';
  var dot = el('i', { class: 'ap-dot ' + (status === 'ok' ? 'ok' : status === 'error' ? 'bad' : '') });
  chip.appendChild(dot);
  chip.appendChild(document.createTextNode(label));
  chip.appendChild(pill(cloud ? 'Cloud' : 'Local', cloud ? 'warn' : 'ok'));
  chip.title = cloud ? 'A cloud model is used only if the administrator has allowed it for the data involved.' : 'Runs on a local-network model.';
}

/* ---- request lifecycle ---- */
function rid(){ var a = new Uint8Array(8); (window.crypto || window.msCrypto).getRandomValues(a); return [].map.call(a, function(b){ return ('0' + b.toString(16)).slice(-2); }).join(''); }
function stageText(p){
  var map = { searching_knowledge: 'Searching the knowledge base…', searching_web: 'Searching the internet…', using_tools: 'Running tools…',
    generating: 'Generating the answer…', checking_sources: 'Checking the answer against its sources…', unknown: 'Working…' };
  var t = map[p.stage] || 'Working…';
  if(p.detail) t += ' (' + p.detail + ')';
  if(p.elapsed_ms !== undefined) t += '  ' + Math.round(p.elapsed_ms / 1000) + 's';
  return t;
}
function begin(){
  var extras = {};
  if(!host() || !barEl() || !A.opts) return extras;
  A.requestId = rid();
  extras.mode = A.sel.mode;
  extras.request_id = A.requestId;
  extras.model_mode = 'auto';
  if(A.sel.modelMode === 'manual' && A.opts.allow_manual_model_selection){
    var c = splitChoice(A.sel.model);
    if(c){ extras.model_mode = 'manual'; extras.model_provider_id = c.provider_id; extras.model_name = c.model; }
  }
  extras.use_tools = !!A.sel.tools;
  var stop = document.getElementById('aiStopBtn'); if(stop) stop.classList.add('on');
  var wrap = document.getElementById('aiMessages');
  A.progressEl = el('div', { class: 'ap-progress', role: 'status' }, [ el('div', { class: 'ap-spin' }), el('span', { text: 'Working…' }) ]);
  if(wrap){ wrap.appendChild(A.progressEl); wrap.scrollTop = wrap.scrollHeight; }
  var myId = A.requestId;
  A.poll = setInterval(function(){
    if(A.requestId !== myId) return;
    aiApi('/progress?request_id=' + encodeURIComponent(myId)).then(function(p){
      if(A.requestId === myId && A.progressEl && p){
        A.progressEl.lastChild.textContent = stageText(p);
        // the history list re-renders when it finishes loading; keep the indicator on screen
        var w = document.getElementById('aiMessages');
        if(w && !A.progressEl.parentNode) w.appendChild(A.progressEl);
      }
    }).catch(function(){});
  }, 1000);
  return extras;
}
function end(){
  clearInterval(A.poll); A.poll = null; A.requestId = null;
  if(A.progressEl && A.progressEl.parentNode) A.progressEl.parentNode.removeChild(A.progressEl);
  A.progressEl = null;
  var stop = document.getElementById('aiStopBtn'); if(stop) stop.classList.remove('on');
}
function cancelRequest(){
  if(!A.requestId) return;
  var id = A.requestId;
  if(A.progressEl) A.progressEl.lastChild.textContent = 'Stopping…';
  aiApi('/cancel', send('POST', { request_id: id })).catch(function(){});
}

/* ---- answer decoration ---- */
var ERROR_HINTS = {
  not_configured: 'Open Settings → LLM → Providers and configure or enable a model for this task.',
  provider_failed: 'The model provider did not respond. Check its status in Settings → LLM → Providers, or choose another model.',
  permission_denied: 'This model is not permitted for the data in this question. An administrator can change this in Settings → LLM → Security.',
  cancelled: 'The request was stopped.'
};
function safeUrl(u){ return /^https?:\/\//i.test(u || '') ? u : null; }
function sourceNode(s){
  var web = s.kind === 'web', tool = s.kind === 'tool';
  var summary = el('summary', null, [ el('span', { class: 'ap-id' + (web ? ' web' : tool ? ' tool' : ''), text: '[' + s.id + ']' }) ]);
  if(web){
    summary.appendChild(el('span', { text: s.provider || 'Internet' }));
    var u = safeUrl((s.url || '').split(',')[0].trim());
    if(u) summary.appendChild(el('a', { href: u, target: '_blank', rel: 'noopener noreferrer', text: u.replace(/^https?:\/\//i, '').slice(0, 70) }));
  } else if(tool){
    summary.appendChild(el('span', { text: (s.integration || 'Tool') + ' / ' + (s.tool || '') }));
  } else {
    summary.appendChild(el('span', { text: s.file_name || 'document' }));
    var sub = [ s.document_type, s.version ? 'v' + s.version : '', [s.domain, s.module].filter(Boolean).join(' / '),
      s.section ? 'section: ' + s.section : '',
      s.chunk_number !== null && s.chunk_number !== undefined && s.total_chunks ? 'part ' + s.chunk_number + ' of ' + s.total_chunks : '' ].filter(Boolean).join(' · ');
    if(sub) summary.appendChild(el('span', { class: 'ap-sub', text: sub }));
  }
  var node = el('details', { class: 'ap-src' + (s.cited ? ' cited' : ''), 'data-src': s.id }, [ summary ]);
  if(s.cited && s.verified) summary.appendChild(pill('cited \u00b7 checked against the source', 'ok'));
  else if(s.cited) summary.appendChild(pill('cited \u00b7 not fully verified', 'warn'));
  else summary.appendChild(pill('retrieved, not cited'));
  if(s.same_text_in && s.same_text_in.length) summary.appendChild(el('span', { class: 'ap-sub', text: 'same text in ' + s.same_text_in.length + ' other version(s)' }));
  if(s.excerpt) node.appendChild(el('blockquote', { text: s.excerpt }));
  return node;
}
/* Turn [S1] / [W2] / [T1] markers in the answer into links that open the matching
   source below the answer. Only markers that exist in this answer's source list are
   linked; text nodes are split with DOM APIs (no innerHTML), so answer text cannot
   inject markup. */
function linkCitations(bubble, meta, result){
  var known = {};
  ((result && result.sources) || []).forEach(function(s){ known[s.id] = true; });
  if(!Object.keys(known).length) return;
  var walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT, {
    acceptNode: function(n){
      for(var p = n.parentNode; p && p !== bubble; p = p.parentNode){ if(p === meta || p.nodeName === 'A') return NodeFilter.FILTER_REJECT; }
      return /\[[SWT]\d+\]/.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    } });
  var nodes = [], n;
  while((n = walker.nextNode())) nodes.push(n);
  nodes.forEach(function(t){
    var frag = document.createDocumentFragment(), text = t.nodeValue, last = 0, m, re = /\[([SWT]\d+)\]/g;
    while((m = re.exec(text))){
      if(m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      if(known[m[1]]){
        (function(id){
          frag.appendChild(el('a', { href: '#', class: 'ap-cite', role: 'button', title: 'Show source ' + id, text: '[' + id + ']',
            onclick: function(ev){
              ev.preventDefault();
              var target = meta.querySelector('details[data-src="' + id + '"]');
              if(!target) return;
              for(var q = target.parentNode; q && q !== meta; q = q.parentNode){ if(q.nodeName === 'DETAILS') q.open = true; }
              target.open = true;
              target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
              target.classList.add('ap-flash');
              setTimeout(function(){ target.classList.remove('ap-flash'); }, 1600);
            } }));
        })(m[1]);
      } else frag.appendChild(document.createTextNode(m[0]));
      last = m.index + m[0].length;
    }
    if(last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    t.parentNode.replaceChild(frag, t);
  });
}
function decorateLast(result){
  try {
    var wrap = document.getElementById('aiMessages');
    if(!wrap || !wrap.lastElementChild) return;
    var bubble = wrap.lastElementChild;
    if(bubble.classList.contains('ap-progress')) bubble = bubble.previousElementSibling;
    if(!bubble) return;
    var p = (result && result.platform) || {};
    var meta = el('div', { class: 'ap-meta' });
    var chips = el('div', { class: 'ap-chips' });
    var llm = p.llm;
    var d = p.route_decision || {};

    if(result && result.route) chips.appendChild(pill((result.route || '').toUpperCase(), 'info'));
    if(result && result.success && !p.insufficient_evidence){
      var local = (result.sources || []).some(function(s){ return !s.kind || s.kind === 'knowledge'; });
      var web = (result.sources || []).some(function(s){ return s.kind === 'web'; });
      if(local) chips.appendChild(pill('Knowledge base', 'ok'));
      if(web || d.internet_used) chips.appendChild(pill('Internet', 'warn'));
    }
    if(llm){
      chips.appendChild(pill((llm.provider || '?') + ' · ' + (llm.model || '?')));
      chips.appendChild(pill(llm.cloud ? 'Cloud model' : 'Local model', llm.cloud ? 'warn' : 'ok'));
      if(llm.fallback_used) chips.appendChild(pill('Fallback used', 'warn'));
      if(llm.cached) chips.appendChild(pill('From cache', 'info'));
      else chips.appendChild(pill(Math.round((llm.latency_ms || 0)) + ' ms'));
      if(llm.prompt_tokens || llm.completion_tokens)
        chips.appendChild(pill((llm.usage_estimated ? '~' : '') + two(llm.prompt_tokens) + ' in / ' + (llm.usage_estimated ? '~' : '') + two(llm.completion_tokens) + ' out tokens'));
    }
    if(p.insufficient_evidence) chips.appendChild(pill('Not enough evidence — no model called', 'warn'));
    var as = p.assurance;
    if(result && result.success && as && as.label){
      var short = { strong: ['Checked against sources', 'ok'], moderate: ['Partly checked against sources', 'info'],
                    limited: ['Limited source support', 'warn'], none: ['No supporting information', 'warn'],
                    not_assessed: ['Model knowledge \u2014 not checked', 'warn'], not_applicable: ['Not in the selected sources', 'info'] }[as.level] || [as.label, ''];
      chips.appendChild(el('span', { class: 'ap-pill ' + short[1], title: as.label + ' \u2014 ' + (as.explanation || ''), text: short[0] }));
    }
    if(p.coverage){
      var cv = p.coverage;
      chips.appendChild(pill(cv.method === 'requirement_extraction'
        ? (cv.complete ? 'Scanned ' + cv.scope_passages + ' passages \u00b7 ' + cv.units_found + ' requirement statements' : 'Partial: ' + cv.units_used + ' of ' + cv.units_found + ' requirement statements')
        : (cv.complete ? 'Whole scope read (' + cv.scope_passages + ' passages)' : 'Partial coverage: ' + cv.passages_used + ' of ' + cv.scope_passages),
        cv.complete ? 'ok' : 'warn'));
    }
    if(chips.childNodes.length) meta.appendChild(chips);

    if(result && !result.success){
      var code = (p.error_code || '');
      if(ERROR_HINTS[code]) meta.appendChild(banner(ERROR_HINTS[code], 'warn'));
      (p.attempts || []).forEach(function(a){
        meta.appendChild(el('div', { class: 'ap-note', text: (a.provider || '?') + ' · ' + (a.model || '?') + ' — ' + (a.ok ? 'ok' : (a.error || 'failed')) }));
      });
    }
    (result && result.warnings || []).forEach(function(w){ meta.appendChild(banner(w, 'warn')); });

    if(result && result.sources && result.sources.length){
      var citedSrc = result.sources.filter(function(s){ return s.cited; });
      var otherSrc = result.sources.filter(function(s){ return !s.cited; });
      meta.appendChild(el('div', { class: 'ap-label', text: 'Cited sources (' + citedSrc.length + ')' }));
      citedSrc.forEach(function(s){ meta.appendChild(sourceNode(s)); });
      if(!citedSrc.length) meta.appendChild(el('div', { class: 'ap-note', text: 'The answer does not cite any of the retrieved passages.' }));
      if(otherSrc.length){
        var more = el('details', { class: 'ap-why' }, [ el('summary', { text: 'Retrieved but not cited (' + otherSrc.length + ')' }) ]);
        otherSrc.forEach(function(s){ more.appendChild(sourceNode(s)); });
        meta.appendChild(more);
      }
    }
    if(p.closest_documents && p.closest_documents.length){
      meta.appendChild(el('div', { class: 'ap-note', text: 'Closest documents (not used to answer): ' + p.closest_documents.map(function(c){ return c.file_name; }).join(', ') }));
    }
    (p.tools || []).forEach(function(t){
      meta.appendChild(el('div', { class: 'ap-note', text: 'Tool ' + t.integration + ' / ' + t.tool + ': ' + (t.ok ? 'ok' : 'failed — ' + (t.error || '')) }));
    });

    var why = el('details', { class: 'ap-why' }, [ el('summary', { text: 'How this answer was produced' }) ]);
    var ul = el('ul');
    (d.reasons || []).forEach(function(r){ ul.appendChild(el('li', { text: r })); });
    if(d.mode_used) ul.appendChild(el('li', { text: 'Mode: ' + d.mode_used.toUpperCase() + ' · model selection: ' + (d.model_mode || 'auto') + '. Internet used: ' + (d.internet_used ? 'yes' : 'no') + '. Cloud model used: ' + (d.cloud_inference_used || (llm && llm.cloud) ? 'yes' : 'no') + '.' }));
    var rd = ((p.retrieval || {}).diagnostics) || {};
    if(rd.pool !== undefined) ul.appendChild(el('li', { text: 'Retrieval: ' + rd.pool + ' candidates → ' + (rd.after_threshold !== undefined ? rd.after_threshold : '?') + ' above the floor' + (rd.rerank_used ? ', reranked' : ', not reranked') + '.' }));
    var ev = p.evidence;
    if(ev) ul.appendChild(el('li', { text: 'Evidence: ' + (ev.sufficient ? 'enough' : 'not enough') + (ev.reason === 'referenced_item_not_found' ? ' \u2014 the requested item (' + (ev.missing_references || []).join(', ') + ') is not in the selected knowledge' : '') + '. Closeness of the best passage to the question: ' + (ev.best_similarity === null || ev.best_similarity === undefined ? 'not measured' : ev.best_similarity) + ' (a vector distance, not the chance that the answer is right).' }));
    if(p.assurance) ul.appendChild(el('li', { text: 'Assurance: ' + p.assurance.label + '. ' + (p.assurance.explanation || '') }));
    var g = p.grounding;
    if(g){
      var cs = g.claim_summary || {};
      ul.appendChild(el('li', { text: 'Source check (' + (g.method || 'lexical') + '): ' + (cs.checked || 0) + ' statement(s) checked, ' + (cs.supported || 0) + ' supported' + (cs.unsupported ? ', ' + cs.unsupported + ' not supported' : '') + (cs.misattributed ? ', ' + cs.misattributed + ' cited to the wrong source' : '') + (cs.uncited ? ', ' + cs.uncited + ' without a citation' : '') + (g.invalid_citations && g.invalid_citations.length ? '; ' + g.invalid_citations.length + ' invalid citation(s) removed' : '') + '.' }));
      (g.claims || []).slice(0, 5).forEach(function(c){
        ul.appendChild(el('li', { class: 'ap-unsupported', text: 'Unsupported statement: \u201c' + c.text + '\u201d \u2014 ' + (c.detail || c.status) }));
      });
      if(g.general_knowledge_statements) ul.appendChild(el('li', { text: g.general_knowledge_statements + ' paragraph(s) marked \u201cGeneral knowledge\u201d are not from the documents.' }));
    }
    var sg = d.signals;
    if(sg && d.auto_routed){
      var cues = [];
      if((sg.enterprise_strong || []).length) cues.push('PSW/document cues: ' + sg.enterprise_strong.slice(0, 3).join(', '));
      if((sg.general || []).length) cues.push('general-knowledge phrasing: ' + sg.general.slice(0, 3).join(', '));
      if((sg.current_info || []).length) cues.push('current-information cues: ' + sg.current_info.slice(0, 3).join(', '));
      if(sg.explicit_web) cues.push('explicit web request');
      if(cues.length) ul.appendChild(el('li', { text: 'What the router noticed: ' + cues.join('; ') + '.' }));
    }
    var tm = p.timing_ms;
    if(tm && tm.total !== undefined){
      var parts = Object.keys(tm).filter(function(k){ return k !== 'total' && tm[k] >= 50; }).sort(function(a, b){ return tm[b] - tm[a]; })
        .slice(0, 6).map(function(k){ return k.replace(/_/g, ' ') + ' ' + Math.round(tm[k]) + ' ms'; });
      ul.appendChild(el('li', { text: 'Time: ' + (tm.total / 1000).toFixed(1) + ' s total' + (parts.length ? ' (' + parts.join(', ') + ')' : '') + '.' }));
    }
    if((p.attempts || (llm && llm.attempts) || []).length > 1) ul.appendChild(el('li', { text: 'Attempts: ' + (llm.attempts || []).map(function(a){ return (a.provider || '?') + '/' + (a.model || '?') + ' ' + (a.ok ? 'ok' : 'failed'); }).join(' → ') }));
    if(ul.childNodes.length){ why.appendChild(ul); meta.appendChild(why); }

    if(meta.childNodes.length) bubble.appendChild(meta);
    linkCitations(bubble, meta, result);
    wrap.scrollTop = wrap.scrollHeight;
  } catch(e){ /* decoration must never break the answer */ }
}

/* ---- exports ---- */
window.AiPlatform = {
  mountSettings: function(container){ if(!S.mounted) mountSettings(container); else reload(); },
  enterAssistant: function(){ loadOptions(true); },
  begin: begin, end: end, decorateLast: decorateLast, refreshOptions: function(){ return loadOptions(true); }
};
})();
