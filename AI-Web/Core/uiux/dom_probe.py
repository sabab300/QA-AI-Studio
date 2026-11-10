"""In-page probe (JavaScript) that reads rendered state without modifying the page.

The probe is injected with page.evaluate (not a <script> tag), so a restrictive Content-Security-Policy on the
application cannot block it. It only READS: geometry, computed styles, attributes, accessible names.
"""

PROBE_JS = r"""
() => {
  if (window.__uiuxProbe) return true;
  const SKIP = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','TEMPLATE','BR','WBR','DEFS','PATH','G','CIRCLE','RECT','LINE','POLYLINE','POLYGON','ELLIPSE','USE','SYMBOL','CLIPPATH','MASK','LINEARGRADIENT','RADIALGRADIENT','STOP','TSPAN','TEXTPATH','FILTER','OPTION','OPTGROUP','SOURCE','TRACK','COLGROUP','COL']);
  const STYLE_PROPS = ['color','backgroundColor','backgroundImage','fontFamily','fontSize','fontWeight','fontStyle','lineHeight','letterSpacing','textAlign','textTransform','textDecorationLine','textOverflow','whiteSpace','opacity',
    'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderTopStyle','borderTopColor','borderRightColor','borderBottomColor','borderLeftColor',
    'borderTopLeftRadius','borderTopRightRadius','borderBottomRightRadius','borderBottomLeftRadius','boxShadow',
    'paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginRight','marginBottom','marginLeft',
    'display','position','overflowX','overflowY','zIndex','gap','rowGap','columnGap','flexDirection','justifyContent','alignItems','cursor',
    'outlineStyle','outlineWidth','outlineColor','outlineOffset','width','height','boxSizing','verticalAlign','visibility'];
  const els = [];
  window.__uiuxEls = els;
  const px = v => { const n = parseFloat(v); return isNaN(n) ? null : Math.round(n * 100) / 100; };
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const IMPLICIT = {A:'link',BUTTON:'button',H1:'heading',H2:'heading',H3:'heading',H4:'heading',H5:'heading',H6:'heading',IMG:'img',TABLE:'table',TH:'columnheader',TD:'cell',TR:'row',THEAD:'rowgroup',TBODY:'rowgroup',NAV:'navigation',UL:'list',OL:'list',LI:'listitem',FORM:'form',MAIN:'main',HEADER:'banner',FOOTER:'contentinfo',ASIDE:'complementary',DIALOG:'dialog',SELECT:'combobox',TEXTAREA:'textbox',PROGRESS:'progressbar',SVG:'img',LABEL:'',INPUT:'textbox'};
  const INPUT_ROLE = {button:'button',submit:'button',reset:'button',image:'button',checkbox:'checkbox',radio:'radio',range:'slider',number:'spinbutton',search:'searchbox',email:'textbox',tel:'textbox',url:'textbox',password:'textbox',text:'textbox',date:'textbox',file:'button'};
  function roleOf(el){
    const explicit = el.getAttribute('role'); if (explicit) return explicit.split(' ')[0];
    if (el.tagName === 'A' && !el.hasAttribute('href')) return '';
    if (el.tagName === 'INPUT') { const t = (el.getAttribute('type') || 'text').toLowerCase(); if (t === 'hidden') return ''; return INPUT_ROLE[t] || 'textbox'; }
    return IMPLICIT[el.tagName] || '';
  }
  function textOf(ids){ return ids.split(/\s+/).map(i => { const n = document.getElementById(i); return n ? norm(n.textContent) : ''; }).filter(Boolean).join(' '); }
  function nameOf(el, role){
    if (el.hasAttribute('aria-labelledby')) { const v = textOf(el.getAttribute('aria-labelledby')); if (v) return [v, 'aria-labelledby']; }
    const al = el.getAttribute('aria-label'); if (al && norm(al)) return [norm(al), 'aria-label'];
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
      const t = (el.getAttribute('type') || '').toLowerCase();
      if (tag === 'INPUT' && ['button','submit','reset'].includes(t)) { const v = el.value || (t === 'submit' ? 'Submit' : t === 'reset' ? 'Reset' : ''); if (v) return [norm(v), 'value']; }
      if (el.labels && el.labels.length) { const v = norm(Array.from(el.labels).map(l => l.textContent).join(' ')); if (v) return [v, 'label']; }
      if (el.title && norm(el.title)) return [norm(el.title), 'title'];
      if (el.placeholder && norm(el.placeholder)) return [norm(el.placeholder), 'placeholder'];
      return ['', ''];
    }
    if (tag === 'IMG') { const a = el.getAttribute('alt'); if (a !== null) return [norm(a), 'alt']; return ['', '']; }
    if (tag === 'SVG') { const t = el.querySelector('title'); if (t && norm(t.textContent)) return [norm(t.textContent), 'svg-title']; return ['', '']; }
    if (['button','link','heading','tab','menuitem','columnheader','rowheader','cell','option','checkbox','radio'].includes(role) || tag === 'LABEL' || tag === 'LEGEND' || tag === 'SUMMARY') {
      const v = norm(el.textContent); if (v) return [v.slice(0, 120), 'content'];
    }
    if (el.title && norm(el.title)) return [norm(el.title), 'title'];
    return ['', ''];
  }
  function layersBelow(el){
    // what is painted behind this element: the stack at its centre point (handles absolutely positioned siblings, not only ancestors)
    const r = el.getBoundingClientRect();
    let cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const sx = scrollX, sy = scrollY; let moved = false;
    if (cy < 0 || cy >= innerHeight || cx < 0 || cx >= innerWidth) { window.scrollTo(Math.max(0, r.left + sx + r.width / 2 - innerWidth / 2), Math.max(0, r.top + sy + r.height / 2 - innerHeight / 2)); moved = true;
      const r2 = el.getBoundingClientRect(); cx = r2.left + r2.width / 2; cy = r2.top + r2.height / 2; }
    let stack = []; try { stack = document.elementsFromPoint(cx, cy); } catch (e) {}
    if (moved) window.scrollTo(sx, sy);
    let at = stack.findIndex(n => n === el || el.contains(n));
    if (at < 0) return null;
    return stack.slice(at).filter(n => n === el || !el.contains(n));
  }
  function bgOf(el, precise){
    let nodes = null;
    if (precise) nodes = layersBelow(el);
    if (!nodes) { nodes = []; let n = el; while (n && n.nodeType === 1) { nodes.push(n); n = n.parentElement; } }
    let layers = [], image = false;
    for (const n of nodes) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') image = true;
      const m = cs.backgroundColor.match(/rgba?\(([^)]+)\)/);
      if (m) {
        const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
        const a = p.length > 3 ? p[3] : 1;
        if (a > 0) { layers.push([p[0], p[1], p[2], a]); if (a >= 0.999) break; }
      }
    }
    let base = [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) { const l = layers[i], a = l[3]; base = [l[0]*a + base[0]*(1-a), l[1]*a + base[1]*(1-a), l[2]*a + base[2]*(1-a), 1]; }
    return { rgba: 'rgba(' + base.map((v, i) => i < 3 ? Math.round(v) : v).join(',') + ')', image };
  }
  function selectorOf(el){
    if (el.getAttribute && el.getAttribute('data-testid')) return '[data-testid="' + el.getAttribute('data-testid') + '"]';
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && n !== document.documentElement && parts.length < 7) {
      let part = n.tagName.toLowerCase();
      if (n.id && document.querySelectorAll('#' + CSS.escape(n.id)).length === 1) { parts.unshift('#' + CSS.escape(n.id)); break; }
      const same = n.parentElement ? Array.from(n.parentElement.children).filter(c => c.tagName === n.tagName) : [n];
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(n) + 1) + ')';
      parts.unshift(part); n = n.parentElement;
    }
    return parts.join(' > ');
  }
  function focusable(el){
    if (el.disabled) return false;
    const t = el.tagName;
    if (el.tabIndex >= 0 && (['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY'].includes(t) ? (t !== 'A' || el.hasAttribute('href')) && !(t === 'INPUT' && (el.type || '') === 'hidden') : el.hasAttribute('tabindex') || el.isContentEditable)) return true;
    return false;
  }
  function lineCount(el, lineH){
    try {
      const range = document.createRange(); range.selectNodeContents(el);
      const tops = []; for (const rc of range.getClientRects()) { if (rc.width < 1 || rc.height < 1) continue; if (!tops.some(t => Math.abs(t - rc.top) < Math.max(3, lineH * 0.4))) tops.push(rc.top); }
      return Math.max(1, tops.length);
    } catch (e) { return 1; }
  }
  function describe(el, index, parentIndex, depth){
    const cs = getComputedStyle(el), r = el.getBoundingClientRect(), role = roleOf(el);
    const style = {};
    for (const k of STYLE_PROPS) style[k] = cs[k];
    let ownText0 = ''; for (const c of el.childNodes) if (c.nodeType === 3) ownText0 += c.data;
    const bg = bgOf(el, ownText0.trim().length > 0);
    const [accName, nameSource] = nameOf(el, role);
    let own = ''; for (const c of el.childNodes) if (c.nodeType === 3) own += c.data;
    own = norm(own).slice(0, 240);
    const aria = {}; for (const a of el.attributes) if (a.name.startsWith('aria-')) aria[a.name] = a.value.slice(0, 120);
    const lh = parseFloat(cs.lineHeight), fs = parseFloat(cs.fontSize);
    const lineH = isNaN(lh) ? fs * 1.2 : lh;
    // only 'hidden'/'clip' cut content off; 'auto'/'scroll' containers are scrollable, which is intended behaviour
    const clipsX = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
    let scroller = null;
    for (let par = el.parentElement, hops = 0; par && hops < 8 && par !== document.body && par !== document.documentElement; par = par.parentElement, hops++) {
      const pcs = getComputedStyle(par);
      if ((pcs.overflowX === 'auto' || pcs.overflowX === 'scroll') && par.scrollWidth > par.clientWidth + 1) {
        const pr = par.getBoundingClientRect();
        scroller = { x: Math.round((pr.left + scrollX) * 100) / 100, y: Math.round((pr.top + scrollY) * 100) / 100, w: Math.round(pr.width * 100) / 100, h: Math.round(pr.height * 100) / 100 };
        break;
      }
    }
    const d = {
      i: index, p: parentIndex, depth, tag: el.tagName.toLowerCase(), role, name: accName, nameSource,
      text: own, fullText: own ? own : (el.children.length === 0 ? norm(el.textContent).slice(0, 240) : ''),
      x: Math.round((r.left + scrollX) * 100) / 100, y: Math.round((r.top + scrollY) * 100) / 100, w: Math.round(r.width * 100) / 100, h: Math.round(r.height * 100) / 100,
      style, bg: bg.rgba, bgImage: bg.image,
      selector: selectorOf(el), classes: (el.getAttribute('class') || '').slice(0, 120),
      attrs: {}, aria,
      focusable: focusable(el), tabindex: el.hasAttribute('tabindex') ? el.getAttribute('tabindex') : null,
      lines: own ? lineCount(el, lineH) : 0,
      clipped: clipsX && el.scrollWidth > el.clientWidth + 1,
      truncated: cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1,
      scroller: scroller,
      childCount: el.children.length
    };
    for (const a of ['type','name','id','placeholder','href','alt','for','title','maxlength','min','max','pattern','autocomplete','src']) {
      const v = el.getAttribute(a); if (v !== null) d.attrs[a] = a === 'href' || a === 'src' ? v.split(/[?#]/)[0].slice(0, 160) : v.slice(0, 160);
    }
    for (const b of ['required','disabled','readonly','checked','multiple','hidden','selected']) if (el.hasAttribute(b) || el[b] === true) d.attrs[b] = true;
    const refs = [];
    for (const a of ['aria-labelledby','aria-describedby','aria-controls','aria-owns']) { const v = el.getAttribute(a); if (v) for (const id of v.split(/\s+/)) if (id && !document.getElementById(id)) refs.push(a + '=' + id); }
    if (refs.length) d.badRefs = refs;
    if (el.tagName === 'SELECT' && el.options && el.selectedIndex >= 0) { d.selectedText = norm(el.options[el.selectedIndex].text).slice(0, 120); d.optionCount = el.options.length; }
    if (el.tagName === 'IMG') { d.img = { naturalW: el.naturalWidth, naturalH: el.naturalHeight, complete: el.complete, broken: el.complete && el.naturalWidth === 0 }; }
    return d;
  }
  function inspect(){
    els.length = 0; const out = []; const map = new Map();
    const walk = (el, parentIndex, depth) => {
      if (out.length >= 4000) return;
      if (SKIP.has(el.tagName.toUpperCase())) return;
      const cs = getComputedStyle(el);
      if (cs.display === 'none') return;
      const r = el.getBoundingClientRect();
      const visible = cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0 && (r.width > 0 || r.height > 0);
      let idx = parentIndex;
      if (visible) {
        idx = out.length; el.__uiuxIndex = idx; els.push(el);
        out.push(describe(el, idx, parentIndex, depth));
      }
      if (el.tagName.toUpperCase() === 'SVG') return;
      for (const c of el.children) walk(c, idx, depth + (visible ? 1 : 0));
    };
    walk(document.body, -1, 0);
    // fonts actually rendered: does the first family of each distinct stack exist on this machine?
    const fams = {}; for (const e of out) { const f = e.style.fontFamily; if (f && !(f in fams)) fams[f] = true; }
    const canvas = document.createElement('canvas').getContext('2d'); const sample = 'mmmmmmmmmmlliWW@#0123456789';
    const width = f => { canvas.font = '72px ' + f; return canvas.measureText(sample).width; };
    const fonts = [];
    for (const stack of Object.keys(fams)) {
      const first = stack.split(',')[0].trim().replace(/^["']|["']$/g, '');
      const generic = ['serif','sans-serif','monospace','cursive','fantasy','system-ui','ui-sans-serif','ui-serif','ui-monospace','-apple-system','BlinkMacSystemFont'];
      let available = true;
      if (!generic.includes(first)) {
        let loaded = false; try { loaded = document.fonts.check('72px "' + first + '"') && Array.from(document.fonts).some(f => f.family.replace(/["']/g, '') === first && f.status === 'loaded'); } catch (e) {}
        const a = width('monospace'), b = width('serif'), c = width('"' + first + '", monospace'), d = width('"' + first + '", serif');
        available = loaded || a !== c || b !== d;
      }
      fonts.push({ stack: stack.slice(0, 160), first, available });
    }
    const overflow = out.filter(e => e.x + e.w > innerWidth + 1 && e.w > 0).slice(0, 40).map(e => e.i);
    return { url: location.pathname + location.search, title: document.title, viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
      page: { scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight, hOverflow: document.documentElement.scrollWidth > innerWidth + 1 },
      fonts, overflowIdx: overflow, elements: out, truncated: out.length >= 4000, lang: document.documentElement.lang || '' };
  }
  function describeIndex(i){
    const el = els[i]; if (!el || !el.isConnected) return null;
    return describe(el, i, -1, 0);
  }
  function focusInfo(){
    const el = document.activeElement; if (!el || el === document.body) return null;
    const i = el.__uiuxIndex; const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return { i: i === undefined ? -1 : i, tag: el.tagName.toLowerCase(), selector: selectorOf(el), x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height,
      outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth, outlineColor: cs.outlineColor, boxShadow: cs.boxShadow, borderColor: cs.borderTopColor, backgroundColor: cs.backgroundColor,
      textDecoration: cs.textDecorationLine, inViewport: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth };
  }
  function rectsFor(selector){
    try { return Array.from(document.querySelectorAll(selector)).slice(0, 50).map(e => { const r = e.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height }; }).filter(r => r.w > 0 && r.h > 0); }
    catch (e) { return []; }
  }
  function links(){
    return Array.from(document.querySelectorAll('a[href]')).slice(0, 300).map(a => ({ href: a.href.split('#')[0], text: norm(a.textContent).slice(0, 80) }));
  }
  window.__uiuxProbe = { inspect, describeIndex, focusInfo, rectsFor, links };
  return true;
}
"""

STABILIZE_CSS = """
*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;animation-iteration-count:1!important;
transition-duration:0s!important;transition-delay:0s!important;scroll-behavior:auto!important;caret-color:transparent!important}
"""

SETTLE_JS = """
async (quietMs) => {
  const sig = () => document.documentElement.scrollWidth + 'x' + document.documentElement.scrollHeight + ':' + document.querySelectorAll('*').length;
  try { await document.fonts.ready; } catch (e) {}
  const imgs = Array.from(document.images).filter(i => !i.complete);
  await Promise.race([Promise.all(imgs.map(i => i.decode ? i.decode().catch(() => {}) : Promise.resolve())), new Promise(r => setTimeout(r, 4000))]);
  let last = sig(), stable = 0, waited = 0;
  while (stable < 2 && waited < 5000) {
    await new Promise(r => setTimeout(r, quietMs)); waited += quietMs;
    const now = sig(); if (now === last) stable++; else { stable = 0; last = now; }
  }
  return { stable: stable >= 2, waitedMs: waited };
}
"""
