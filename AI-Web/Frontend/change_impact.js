/* QA AI Studio - Task 6: Change Impact Analysis & Intelligent Regression Selection.
   Installed by index.html with the shared services (apiFetch, hasPermission, toast, ...).
   No browser dialogs are used; all dynamic text is escaped; AI output is shown as pending-review text only. */
window.installChangeImpact=function(services){
  'use strict';
  var api=services.apiFetch,has=services.hasPermission,toast=services.toast;
  var root=document.getElementById('ciRoot');
  if(!root)return;
  var TABS=[['source','Change Source'],['analyze','Analyze'],['results','Impact Results'],['regression','Regression'],['report','Report'],['history','History']];
  var SUBS=[['changes','Changes'],['systems','Systems'],['requirements','Requirements'],['tests','Test Cases'],['automation','Automation'],['gaps','Coverage Gaps'],['questions','Open Questions'],['risk','Risk'],['graph','Impact Graph']];
  var ASSURANCE={CONFIRMED:'Confirmed',EVIDENCE_BACKED:'Evidence-backed',SUGGESTED:'Suggestion',HYPOTHESIS:'Hypothesis'};
  var S={config:null,sources:[],mode:'VERSION_COMPARE',tab:'source',sub:'changes',header:null,result:null,report:null,poll:null,busy:false,history:[],
         f:{q:'',change:'',assurance:'',severity:'',review:'',cls:'',cert:''},rf:{q:'',priority:'',risk:'',cls:'',exec:'',approved:''},sel:{},graph:null,started:false,loaded:false};

  function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function el(id){return document.getElementById(id);}
  function pill(cls,text){return '<span class="ci-pill ci-'+esc(cls)+'">'+esc(text)+'</span>';}
  function assurancePill(a){return pill(a,ASSURANCE[a]||a);}
  function certOf(f){var p=f.payload||{};return p.certainty||((f.assurance==='CONFIRMED'||f.assurance==='EVIDENCE_BACKED')?'Confirmed Impact':'Potential Impact');}
  function certPill(c){return pill(c==='Confirmed Impact'?'CERT_CONF':'CERT_POT',c);}
  function isFunctional(c){return c.functional!==false;}
  function basisTag(b){return b==='DERIVED'?'<span class="ci-tag">Derived</span>':'<span class="ci-tag src">Source</span>';}
  function impactList(list){return list&&list.length?'<ul class="ci-ul">'+list.map(function(i){return '<li>'+basisTag(i.basis)+' '+esc(i.text)+(i.certainty?' '+certPill(i.certainty):'')+'</li>';}).join('')+'</ul>':'<span class="muted-small">None identified from the documents.</span>';}
  function firstText(list){return list&&list.length?list[0].text:'';}
  function totalsOf(s){var t=s.dashboard.totals||{};return t;}
  function when(v){return v?String(v).replace('T',' ').slice(0,19):'';}
  function snap(){return S.result&&S.result.snapshot;}
  function findings(kind){var s=snap();return s?s.findings.filter(function(f){return f.kind===kind;}):[];}
  function reviewOf(id){var r=S.result&&S.result.review;return (r&&r.findings&&r.findings[id])||null;}
  function depDecision(id){var r=S.result&&S.result.review;return (r&&r.dependency_decisions&&r.dependency_decisions[id])||null;}
  function reviewState(id){var r=reviewOf(id);return r?r.state:'Pending';}
  function effectiveAssurance(f){var d=depDecision(f.finding_id);return d&&d.decision==='Confirmed'?'CONFIRMED':f.assurance;}
  function setError(id,message){var box=el(id);if(!box)return;box.textContent=message||'';box.classList.toggle('show',!!message);}
  function csvCell(v){var s=String(v==null?'':v).replace(/\r?\n/g,' ');if(/^[=+\-@]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
  function download(blob,name){var u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(u);},1000);}
  function authFetchBlob(path,name){
    var headers={};if(services.token&&services.token())headers.Authorization='Bearer '+services.token();
    return fetch(services.apiBase+path,{headers:headers}).then(function(res){
      if(!res.ok)return res.json().catch(function(){return {};}).then(function(d){var m=d&&d.detail&&(d.detail.message||d.detail);throw new Error(typeof m==='string'?m:'The export failed ('+res.status+').');});
      return res.blob();}).then(function(b){download(b,name);});
  }
  function unique(values){return Array.from(new Set(values.filter(function(v){return v!==''&&v!=null;}))).sort();}
  function options(values,placeholder,selected){return '<option value="">'+esc(placeholder)+'</option>'+values.map(function(v){var val=typeof v==='object'?v.value:v,label=typeof v==='object'?v.label:v;return '<option value="'+esc(val)+'"'+(String(val)===String(selected)?' selected':'')+'>'+esc(label)+'</option>';}).join('');}

  // ------------------------------------------------------------------ shell
  function build(){
    root.innerHTML='<div class="tabstrip ci-tabs" role="tablist">'+TABS.map(function(t){return '<button type="button" class="tab" role="tab" data-ci-tab="'+t[0]+'">'+t[1]+'</button>';}).join('')+'</div>'+
      TABS.map(function(t){return '<div class="ci-panel" id="ciPanel-'+t[0]+'" role="tabpanel"></div>';}).join('');
    root.addEventListener('click',onClick);root.addEventListener('change',onChange);root.addEventListener('input',onInput);
    showTab('source');
  }
  function showTab(name){
    S.tab=name;
    root.querySelectorAll('[data-ci-tab]').forEach(function(b){var on=b.getAttribute('data-ci-tab')===name;b.classList.toggle('active',on);b.setAttribute('aria-selected',on?'true':'false');});
    root.querySelectorAll('.ci-panel').forEach(function(p){p.classList.toggle('active',p.id==='ciPanel-'+name);});
    ({source:renderSource,analyze:renderAnalyze,results:renderResults,regression:renderRegression,report:renderReport,history:renderHistory})[name]();
  }

  // ------------------------------------------------------------------ Change Source tab
  function scope(){return {domain:val('ciDomain'),module:val('ciModule'),knowledge:val('ciKnowledge'),type:val('ciType')};}
  function val(id){var e=el(id);return e?e.value:'';}
  function scoped(){var s=scope();return S.sources.filter(function(i){return (!s.domain||i.domain===s.domain)&&(!s.module||i.module===s.module)&&(!s.knowledge||i.knowledge_name===s.knowledge)&&(!s.type||i.document_type===s.type);});}
  function renderSource(){
    var p=el('ciPanel-source'),compare=S.mode==='VERSION_COMPARE',can=S.config&&S.config.can&&S.config.can.start,keep=saveFields();
    var ai=S.config&&S.config.ai;
    p.innerHTML='<div class="ci-card"><h3>Select Change Source</h3><p class="ci-help">Compare a baseline version with a changed version, or analyse one change request (CRF). Only documents filed in Knowledge Hub with a resolved Domain, Module, Knowledge, Document Type and Version can be analysed.</p>'+
      '<div class="ci-row"><div class="ci-seg" role="group" aria-label="Change source mode"><button type="button" data-ci-mode="VERSION_COMPARE" class="'+(compare?'on':'')+'">Compare two versions</button><button type="button" data-ci-mode="SINGLE_DOCUMENT" class="'+(!compare?'on':'')+'">Single change request (CRF)</button></div></div></div>'+
      '<div class="ci-card"><div class="ci-grid">'+
        '<div class="field"><label for="ciDomain">Domain</label><select id="ciDomain"></select></div>'+
        '<div class="field"><label for="ciModule">Module</label><select id="ciModule"></select></div>'+
        '<div class="field"><label for="ciKnowledge">Knowledge</label><select id="ciKnowledge"></select></div>'+
        '<div class="field"><label for="ciType">Document Type</label><select id="ciType"></select></div>'+
        (compare?'<div class="field"><label for="ciBaseVersion">Baseline Version</label><select id="ciBaseVersion"></select></div><div class="field"><label for="ciBaseDoc">Baseline Source Document</label><select id="ciBaseDoc"></select></div>':'')+
        '<div class="field"><label for="ciChgVersion">'+(compare?'Changed Version':'Version')+'</label><select id="ciChgVersion"></select></div>'+
        '<div class="field"><label for="ciChgDoc">'+(compare?'Changed Source Document':'Source Document')+'</label><select id="ciChgDoc"></select></div>'+
      '</div></div>'+
      '<div class="ci-card"><div class="ci-grid">'+
        '<div class="field"><label for="ciTitle">Title (optional)</label><input type="text" id="ciTitle" maxlength="160" placeholder="e.g. Permit rules change"></div>'+
        '<div class="field"><label for="ciDepth">Dependency depth</label><select id="ciDepth"><option value="1">1 step</option><option value="2" selected>2 steps</option><option value="3">3 steps</option></select></div>'+
        '<div class="field"><label>&nbsp;</label><label class="ci-row" style="font-weight:400"><input type="checkbox" id="ciAi"'+(ai&&ai.available?'':' disabled')+'> Use AI'+(compare?' (explanations)':' (find extra proposed changes)')+'</label></div>'+
        '<div class="field"><label>&nbsp;</label><button type="button" class="btn btn-primary" id="ciRun" disabled>Analyze Impact</button></div>'+
      '</div>'+
      '<p class="ci-help" style="margin:8px 0 0">'+(ai&&ai.available?'AI uses the provider chosen in Settings &rarr; LLM. Private knowledge is sent only to a local provider unless an administrator enabled cloud sharing.':'AI is not available ('+esc(ai?ai.reason:'not loaded')+'). The analysis still runs with rule-based logic.')+'</p>'+
      '<div class="ci-error" id="ciSourceError"></div></div>'+
      (can===false?'<div class="ci-note warn">Your role can view analyses but not start one.</div>':'');
    restoreFields(keep);fillScope();
  }
  function saveFields(){var r={};['ciDomain','ciModule','ciKnowledge','ciType','ciBaseVersion','ciBaseDoc','ciChgVersion','ciChgDoc','ciTitle','ciDepth'].forEach(function(id){if(el(id))r[id]=el(id).value;});if(el('ciAi'))r.ciAi=el('ciAi').checked;return r;}
  function restoreFields(r){S._keep=r||{};}
  function fillScope(){
    var k=S._keep||{},compare=S.mode==='VERSION_COMPARE',all=S.sources;
    function sel(id,vals,ph,cur){el(id).innerHTML=options(vals,ph,cur);if(cur&&vals.indexOf&&vals.indexOf(cur)<0&&!(vals[0]&&typeof vals[0]==='object'))el(id).value='';}
    sel('ciDomain',unique(all.map(function(i){return i.domain;})),'Select Domain…',k.ciDomain);
    var d=val('ciDomain');
    sel('ciModule',unique(all.filter(function(i){return !d||i.domain===d;}).map(function(i){return i.module;})),'Select Module…',k.ciModule);
    var m=val('ciModule');
    sel('ciKnowledge',unique(all.filter(function(i){return (!d||i.domain===d)&&(!m||i.module===m);}).map(function(i){return i.knowledge_name;})),'Select Knowledge…',k.ciKnowledge);
    var kn=val('ciKnowledge');
    sel('ciType',unique(all.filter(function(i){return (!d||i.domain===d)&&(!m||i.module===m)&&(!kn||i.knowledge_name===kn);}).map(function(i){return i.document_type;})),'Select Document Type…',k.ciType);
    var rows=scoped(),versions=unique(rows.map(function(i){return i.version;}));
    if(compare){sel('ciBaseVersion',versions,'Select Baseline Version…',k.ciBaseVersion);docs('ciBaseDoc',val('ciBaseVersion'),k.ciBaseDoc);}
    sel('ciChgVersion',versions,compare?'Select Changed Version…':'Select Version…',k.ciChgVersion);docs('ciChgDoc',val('ciChgVersion'),k.ciChgDoc);
    ['ciDomain','ciModule','ciKnowledge','ciType'].forEach(function(id){el(id).disabled=false;});
    if(k.ciTitle!=null&&el('ciTitle'))el('ciTitle').value=k.ciTitle;if(k.ciDepth&&el('ciDepth'))el('ciDepth').value=k.ciDepth;if(k.ciAi&&el('ciAi')&&!el('ciAi').disabled)el('ciAi').checked=true;
    S._keep=null;updateRun();
  }
  function docs(id,version,cur){
    var items=scoped().filter(function(i){return version&&i.version===version;});
    el(id).disabled=!version;
    el(id).innerHTML=options(items.map(function(i){return {value:i.id,label:i.file_name+(i.source_type?' ('+i.source_type+')':'')};}),'Select Source Document…',cur);
  }
  function updateRun(keepError){
    var b=el('ciRun');if(!b)return;
    if(!keepError)S.startError='';
    var compare=S.mode==='VERSION_COMPARE',ok=!!val('ciChgDoc')&&(!compare||(!!val('ciBaseDoc')&&val('ciBaseDoc')!==val('ciChgDoc')&&val('ciBaseVersion')!==val('ciChgVersion')));
    b.disabled=!ok||S.busy||!(S.config&&S.config.can&&S.config.can.start);
    b.textContent=compare?'Compare Versions & Analyze':'Analyze Impact';
    var msg='';if(compare&&val('ciBaseVersion')&&val('ciBaseVersion')===val('ciChgVersion'))msg='The baseline and changed versions must be different.';
    setError('ciSourceError',msg||S.startError||'');   // a failure returned by the server stays visible until the selection changes
  }
  function startAnalysis(){
    if(S.busy)return;S.busy=true;updateRun();setError('ciSourceError','');
    var compare=S.mode==='VERSION_COMPARE';
    var body={mode:S.mode,changed_source_id:Number(val('ciChgDoc')),title:val('ciTitle'),options:{use_ai:!!(el('ciAi')&&el('ciAi').checked),max_depth:Number(val('ciDepth')||2)}};
    if(compare)body.baseline_source_id=Number(val('ciBaseDoc'));
    api('/api/change-impact/analyses',{method:'POST',body:JSON.stringify(body)}).then(function(h){
      S.header=h;S.result=null;S.report=null;S.sel={};toast('Analysis started.');showTab('analyze');beginPoll(h.id);
    }).catch(function(e){S.startError=e.message;setError('ciSourceError',e.message);}).finally(function(){S.busy=false;updateRun(true);});
  }

  // ------------------------------------------------------------------ Analyze tab
  function renderAnalyze(){
    var p=el('ciPanel-analyze'),h=S.header;
    if(!h){p.innerHTML='<div class="ci-empty">No analysis is open. Start one from Change Source, or open one from History.</div>';return;}
    var pr=h.progress||{steps:[],percent:0},running=h.status==='Queued'||h.status==='Running';
    p.innerHTML='<div class="ci-card"><div class="ci-row" style="justify-content:space-between"><div><h3 style="margin:0">'+esc(h.title)+'</h3><div class="muted-small">'+esc(h.id)+' &middot; started '+esc(when(h.created_at))+' by '+esc(h.created_by_name)+' &middot; status <b>'+esc(h.status)+'</b></div></div>'+
      '<div class="ci-row">'+(running?'<button type="button" class="ci-btn" data-ci-act="cancel">Cancel analysis</button>':'')+(h.status==='Completed'?'<button type="button" class="btn btn-primary" data-ci-act="open-results">View Impact Results</button>':'')+'</div></div>'+
      '<div class="ci-bar" style="margin:8px 0" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+(pr.percent||0)+'"><i style="width:'+(pr.percent||0)+'%"></i></div></div>'+
      '<div class="ci-steps">'+(pr.steps||[]).map(function(s){return '<div class="ci-step '+esc(s.status)+'"><span class="ci-dot"></span><b>'+esc(s.name)+'</b><span class="muted-small">'+esc(s.detail||s.status)+'</span></div>';}).join('')+'</div>'+
      (h.status==='Failed'||h.status==='Cancelled'?'<div class="ci-error show">'+esc(h.status==='Cancelled'?'This analysis was cancelled. Nothing was changed.':(h.error_message||'The analysis failed.'))+'</div>':'')+
      (h.ai_requested?'<div class="ci-note">AI: '+esc(h.ai_status||'Requested')+(h.ai_provider?' &middot; '+esc(h.ai_provider)+' '+esc(h.ai_model):'')+'</div>':'');
  }
  function beginPoll(id){
    stopPoll();
    function tick(){
      api('/api/change-impact/analyses/'+encodeURIComponent(id)).then(function(h){
        S.header=h;if(S.tab==='analyze')renderAnalyze();
        if(h.status==='Queued'||h.status==='Running'){S.poll=setTimeout(tick,1400);return;}
        S.poll=null;
        if(h.status==='Completed'){return loadResult(id).then(function(){toast('Impact analysis completed.');if(S.tab==='analyze')renderAnalyze();});}
        // The Analyze tab already shows the failure inline; a toast is only needed when another tab is open (never both).
        if(S.tab!=='analyze')toast(h.status==='Cancelled'?'Analysis cancelled.':(h.error_message||'Analysis failed.'),h.status!=='Cancelled');
      }).catch(function(e){S.poll=null;toast(e.message,true);});
    }
    tick();
  }
  function stopPoll(){if(S.poll){clearTimeout(S.poll);S.poll=null;}}
  function loadResult(id){
    return api('/api/change-impact/analyses/'+encodeURIComponent(id)+'/result').then(function(r){S.result=r;S.header=r.header;S.graph=null;S.report=null;});
  }
  function cancelAnalysis(){
    if(!S.header)return;
    api('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/cancel',{method:'POST',body:'{}'}).then(function(){toast('Cancelling…');}).catch(function(e){toast(e.message,true);});
  }

  // ------------------------------------------------------------------ Impact Results tab
  function matches(f,row){
    var q=S.f.q.trim().toLowerCase();
    if(S.f.change&&row.change_id!==S.f.change)return false;
    if(S.f.assurance&&(row.assurance?effectiveAssurance(row):'')!==S.f.assurance)return false;
    if(S.f.severity&&(row.severity||'')!==S.f.severity)return false;
    if(S.f.review&&reviewState(row.finding_id)!==S.f.review)return false;
    if(q&&JSON.stringify([row.title,row.payload,row.verification_status]).toLowerCase().indexOf(q)<0)return false;
    return true;
  }
  function renderResults(){
    var p=el('ciPanel-results');
    if(!snap()){p.innerHTML='<div class="ci-empty">'+(S.header&&(S.header.status==='Running'||S.header.status==='Queued')?'The analysis is still running. Open the Analyze tab to follow its progress.':'No completed analysis is open. Start one from Change Source, or open one from History.')+'</div>';return;}
    var s=snap(),t=s.dashboard.totals,hdr=S.header;
    var kp=function(label,value,sub,cls,note,cert){return '<button type="button" class="ci-kpi '+(cls||'')+'" data-ci-sub="'+sub+'"'+(cert?' data-ci-cert="'+cert+'"':'')+' aria-label="'+esc(label)+': '+value+(note?' ('+esc(note)+')':'')+'"><b>'+value+'</b><span>'+esc(label)+(note?'<small>'+esc(note)+'</small>':'')+'</span></button>';};
    var notes=(s.warnings||[]).map(function(w){return '<div class="ci-note warn">'+esc(w)+'</div>';}).join('')+(s.scope_notes||[]).map(function(w){return '<div class="ci-note">'+esc(w)+'</div>';}).join('');
    p.innerHTML='<div class="ci-row" style="justify-content:space-between"><div><b>'+esc(hdr.title)+'</b> <span class="muted-small">'+esc(hdr.id)+' &middot; '+esc(when(hdr.completed_at))+' &middot; '+(snap().analysis.mode==='VERSION_COMPARE'?'Version comparison':'Single change request')+'</span></div>'+
      '<div class="ci-row"><button type="button" class="ci-btn" data-ci-act="review-pending">Review Findings</button><button type="button" class="ci-btn" data-ci-sub="graph">View Dependency Graph</button>'+
      '<button type="button" class="ci-btn" data-ci-act="export-xlsx">Export Excel</button><button type="button" class="ci-btn" data-ci-act="export-pdf">Export PDF</button></div></div>'+
      '<div class="ci-kpis ci-kpis5">'+kp('Unique changes',t.changes,'changes','',t.functional_changes==null?'':t.functional_changes+' functional, '+t.administrative_changes+' administrative')+
        kp('Confirmed system impact',t.impacted_systems,'systems','','unique components','Confirmed Impact')+kp('Potential system impact',t.possible_systems_unverified||0,'systems','','needs verification','Potential Impact')+
        kp('Requirements',t.requirements,'requirements')+kp('High-risk impacts',t.high_risk_impacts,'risk',t.high_risk_impacts?'alert':'')+
        kp('Existing impacted tests',t.existing_impacted_tests==null?t.test_cases:t.existing_impacted_tests,'tests','',t.possible_tests?'+'+t.possible_tests+' possible':'')+kp('Automation assets',t.automation_assets,'automation')+
        kp('Coverage gaps',t.coverage_gaps,'gaps',t.coverage_gaps?'alert':'')+kp('Open questions',t.open_questions==null?0:t.open_questions,'questions',t.open_questions?'alert':'')+
        kp('Recommended regression',t.recommended_regression_scenarios==null?t.recommended_regression_tests:t.recommended_regression_scenarios,'regression','',t.recommended_new_scenarios?t.recommended_new_scenarios+' new scenarios':'')+'</div>'+
      '<div class="ci-note">'+esc(s.dashboard.note)+' Items marked Suggestion or Hypothesis need a person to verify them before you rely on them.</div>'+notes+
      '<div class="ci-sub" role="tablist">'+SUBS.map(function(x){return '<button type="button" data-ci-sub="'+x[0]+'" class="'+(S.sub===x[0]?'on':'')+'">'+x[1]+'</button>';}).join('')+'</div>'+
      '<div class="ci-filters" id="ciFilters"></div><div id="ciSubBody"></div>';
    renderFilters();renderSub();
  }
  function renderFilters(){
    var box=el('ciFilters');if(!box)return;
    if(S.sub==='graph'){box.innerHTML='';return;}
    var s=snap();
    box.innerHTML='<input type="search" id="ciQ" placeholder="Search this table…" value="'+esc(S.f.q)+'" aria-label="Search">'+
      '<select id="ciFChange" aria-label="Filter by change">'+options(s.changes.map(function(c){return {value:c.change_id,label:(c.reference||c.change_id)+' - '+(c.requirement||c.change_type.replace('_',' ').toLowerCase())+(isFunctional(c)?'':' (administrative)')};}),'All changes',S.f.change)+'</select>'+
      (S.sub==='changes'?'<select id="ciFCls" aria-label="Filter by change class">'+options([{value:'functional',label:'Functional changes'},{value:'administrative',label:'Administrative changes'}],'Functional and administrative',S.f.cls)+'</select>':'')+
      (S.sub==='systems'?'<select id="ciFCert" aria-label="Filter by certainty">'+options(['Confirmed Impact','Potential Impact'],'Confirmed and potential',S.f.cert)+'</select>':'')+
      (S.sub==='changes'||S.sub==='questions'?'':'<select id="ciFAssurance" aria-label="Filter by assurance">'+options([{value:'CONFIRMED',label:'Confirmed'},{value:'EVIDENCE_BACKED',label:'Evidence-backed'},{value:'SUGGESTED',label:'Suggestion'},{value:'HYPOTHESIS',label:'Hypothesis'}],'All assurance',S.f.assurance)+'</select>')+
      '<select id="ciFSeverity" aria-label="Filter by risk">'+options(['Critical','High','Medium','Low'],'All risk levels',S.f.severity)+'</select>'+
      (S.sub==='changes'||S.sub==='risk'||S.sub==='questions'?'':'<select id="ciFReview" aria-label="Filter by review state">'+options(['Pending','Accepted','Rejected','Investigate'],'All review states',S.f.review)+'</select>')+
      '<button type="button" class="ci-btn" data-ci-act="clear-filters">Clear</button>';
  }
  function renderSub(){
    var body=el('ciSubBody');if(!body)return;
    var fn={changes:tChanges,systems:tSystems,requirements:tRequirements,tests:tTests,automation:tAutomation,gaps:tGaps,questions:tQuestions,risk:vRisk,graph:vGraph}[S.sub]||tChanges;
    body.innerHTML=fn();
    if(S.sub==='graph')initGraph();
  }
  function table(cols,rows,empty){
    if(!rows.length)return '<div class="ci-empty">'+esc(empty||'Nothing matches the current filters.')+'</div>';
    return '<div class="ci-tw"><table><thead><tr>'+cols.map(function(c){return '<th'+(c[1]?' style="width:'+c[1]+'"':'')+'>'+esc(c[0])+'</th>';}).join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table></div><div class="muted-small" style="margin-top:3px">'+rows.length+' row(s)</div>';
  }
  function revCell(f){var r=reviewOf(f.finding_id),d=depDecision(f.finding_id);return '<span class="ci-state-'+esc(r?r.state:'Pending')+'">'+esc(r?r.state:'Pending')+'</span>'+(d?'<div class="muted-small">Dependency '+esc(d.decision.toLowerCase())+'</div>':'');}
  function acts(f,extra){return '<div class="ci-acts"><button type="button" class="ci-btn" data-ci-act="evidence" data-fid="'+esc(f.finding_id)+'">View Evidence</button>'+(extra||'')+'</div>';}
  function evRefs(list){return (list||[]).map(function(e){return esc(e.ref);}).join(', ')||'None';}
  function tChanges(){
    var s=snap(),rows=s.changes.filter(function(c){return (!S.f.change||c.change_id===S.f.change)&&(!S.f.severity||(s.risk[c.change_id]||{}).level===S.f.severity)&&(!S.f.cls||(S.f.cls==='functional'?isFunctional(c):!isFunctional(c)))&&(!S.f.q||JSON.stringify(c).toLowerCase().indexOf(S.f.q.toLowerCase())>=0);}).map(function(c){
      var r=s.risk[c.change_id]||{},occ=c.occurrences||[],oq=(c.open_question_ids||[]).length;
      return '<tr><td><button type="button" class="ci-link-btn" data-ci-act="change" data-cid="'+esc(c.change_id)+'">'+esc(c.change_id)+'</button><div class="muted-small">'+esc(c.reference||'')+'</div>'+(occ.length>1?'<div class="muted-small">'+occ.length+' occurrences consolidated</div>':'')+'</td>'+
        '<td>'+(isFunctional(c)?pill('CERT_CONF','Functional'):pill('P3','Administrative'))+'<div class="muted-small">'+esc(c.change_type.replace('_',' ').toLowerCase())+' &middot; '+esc(c.category)+'</div></td>'+
        '<td>'+esc(c.requirement||c.reference||'')+'</td><td>'+esc(c.description)+(oq?'<div class="muted-small">'+oq+' open question(s)</div>':'')+'</td>'+
        '<td>'+esc(c.old_behavior||c.original_text||c.original_note||'Not available')+'</td><td>'+esc(c.new_behavior||c.modified_text||'(removed)')+'</td>'+
        '<td>'+(firstText(c.business_impact)?esc(firstText(c.business_impact)):'<span class="muted-small">Open the change for impact details.</span>')+'</td>'+
        '<td>'+(r.level?pill(r.level,r.level):'')+'<div class="muted-small">'+esc(((r.reasons||[])[0]||{}).text||'')+'</div></td><td>'+esc(c.confidence||ASSURANCE[c.assurance]||'')+'</td><td>'+esc(c.source_document)+' v'+esc(c.source_version)+'<div class="muted-small">'+evRefs(c.evidence)+'</div></td></tr>';});
    return table([['Change ID','104px'],['Class','112px'],['Requirement','110px'],['Change',''],['Old behaviour',''],['New behaviour',''],['Business impact',''],['Risk and reason','150px'],['Confidence','82px'],['Source and evidence','118px']],rows,s.changes.length?'':'No changes were detected between the two documents.');
  }
  function tSystems(){
    var all=findings('SYSTEM').filter(function(f){return matches(S.f,f)&&(!S.f.cert||certOf(f)===S.f.cert);});
    var rows=all.map(function(f){var p=f.payload,dep=p.dependency,refs=p.requirement_refs||(p.source?[p.source]:[]),per=p.per_requirement||[],merged=p.merged_findings||1,
        extra=dep&&has('qa_engineering','edit')&&!depDecision(f.finding_id)?'<button type="button" class="ci-btn" data-ci-act="evidence" data-fid="'+esc(f.finding_id)+'">Confirm / reject</button>':'';
      var detail=per.length>1?'<details class="muted-small"><summary>'+per.length+' statement(s) across '+refs.length+' requirement(s)</summary><ul class="ci-ul">'+per.map(function(l){return '<li><b>'+esc(l.requirement)+'</b> '+(l.certainty?certPill(l.certainty):'')+'<div>'+esc(l.basis)+'</div></li>';}).join('')+'</ul></details>':'';
      return '<tr><td>'+certPill(certOf(f))+'</td><td><b>'+esc(p.component)+'</b><div class="muted-small">'+esc(p.component_kind||'')+(p.identity?' &middot; '+esc(p.identity):'')+'</div>'+(p.path?'<div class="muted-small">'+esc(p.path.join(' > '))+'</div>':'')+'</td>'+
        '<td>'+esc(refs.join(', '))+(merged>1?'<div class="muted-small">'+merged+' detailed finding(s) merged</div>':'')+'</td><td>'+esc((p.impact_types||[]).join(', '))+'</td>'+
        '<td>'+esc(p.basis||'')+detail+'<div class="muted-small">Evidence: '+evRefs(f.evidence)+'</div>'+(dep?'<div class="muted-small">"'+esc(dep.sentence)+'"</div>':'')+'</td><td>'+assurancePill(effectiveAssurance(f))+'<div class="muted-small">'+esc(f.verification_status)+'</div></td><td>'+revCell(f)+'</td><td>'+acts(f,extra)+'</td></tr>';});
    var sys=findings('SYSTEM'),conf=sys.filter(function(f){return certOf(f)==='Confirmed Impact';}).length,detail=sys.reduce(function(n,f){return n+((f.payload&&f.payload.merged_findings)||1);},0);
    var ne=((snap().insight||{}).no_evidence)||{},apis=ne.apis||[],reqs=ne.requirements||[];
    var none=(apis.length||reqs.length)?'<div class="ci-card" style="margin-top:8px"><h3>No evidence of impact ('+(apis.length+reqs.length)+')</h3><p class="ci-help">These items appear in the document but nothing in it shows that they changed. They are listed so they are not mistaken for impacted systems, and they are not counted above.</p>'+
      (apis.length?'<b class="muted-small">Unchanged interface details</b><ul class="ci-ul">'+apis.map(function(a){return '<li><b>'+esc(a.name)+'</b> <span class="muted-small">('+esc(a.context||'')+', '+esc(a.location||'')+')</span><div class="muted-small">'+esc(a.quote||'')+'</div></li>';}).join('')+'</ul>':'')+
      (reqs.length?'<b class="muted-small">Unchanged requirements</b><ul class="ci-ul">'+reqs.map(function(a){return '<li><b>'+esc(a.context||'')+'</b> <span class="muted-small">('+esc(a.location||'')+')</span><div class="muted-small">'+esc(a.quote||'')+'</div></li>';}).join('')+'</ul>':'')+'</div>':'';
    return '<div class="ci-note"><b>'+sys.length+' unique component(s)</b>: '+conf+' Confirmed Impact and '+(sys.length-conf)+' Potential Impact, merged from '+detail+' detailed finding(s) (the analysis records one per change and requirement; each row keeps all of them). '+
      'Confirmed means the changed text or the document states it. Potential means it is derived or only mentioned and needs verification. A system name in a document is not proof of a change.</div>'+
      table([['Certainty','104px'],['Impacted component','190px'],['Requirements','110px'],['Impact type','100px'],['Basis and evidence',''],['Status','110px'],['Review','76px'],['','112px']],rows)+none;
  }
  function tRequirements(){
    var rows=findings('REQUIREMENT').filter(function(f){return matches(S.f,f);}).map(function(f){var p=f.payload;
      return '<tr><td>'+esc((p.change_ids||[f.change_id]).join(', '))+'</td><td>'+esc(p.reference||'')+'</td><td>'+esc(p.description)+(p.previous_description?'<div class="muted-small">Before: '+esc(p.previous_description)+'</div>':'')+'</td><td>'+esc(p.impact)+'</td><td>'+esc(p.match_basis||'Changed requirement in the analysed document')+(p.change_count?'<div class="muted-small">'+p.change_count+' change(s) consolidated</div>':'')+((p.open_questions||[]).length?'<div class="muted-small">'+p.open_questions.length+' open question(s)</div>':'')+'</td><td>'+assurancePill(effectiveAssurance(f))+'</td><td>'+revCell(f)+'</td><td>'+acts(f)+'</td></tr>';});
    return table([['Change','92px'],['Ref','70px'],['Requirement',''],['Impact','70px'],['Source and basis','150px'],['Assurance','100px'],['Review','76px'],['','112px']],rows);
  }
  function tTests(){
    var rows=findings('TEST').filter(function(f){return matches(S.f,f);}).map(function(f){var p=f.payload;
      return '<tr><td><b>'+esc(p.tc_number||p.test_case_id)+'</b></td><td>'+esc(p.requirement_ref||'')+'</td><td>'+esc(p.title)+'</td><td>'+esc(p.current_version)+'</td><td><b>'+esc(p.classification)+'</b></td><td>'+esc(p.reason)+'</td><td>'+esc(p.recommended_action)+'</td><td>'+assurancePill(effectiveAssurance(f))+'<div class="muted-small">'+esc(f.verification_status)+'</div></td><td>'+revCell(f)+'</td><td>'+acts(f)+'</td></tr>';});
    return '<div class="ci-note">This list contains only existing QA Engineering test cases that have evidence of being affected. No test case is changed, retired or deleted by this analysis. Wording matches are possible matches, not confirmed traceability. Recommended new scenarios are separate and are listed in the Regression tab.</div>'+table([['Test ID','74px'],['Req.','64px'],['Title',''],['Version','70px'],['Class','98px'],['Reason',''],['Recommended action',''],['Traceability','120px'],['Review','70px'],['','100px']],rows,'No existing test case has evidence of being impacted. This does not mean none exists; see Coverage Gaps and the recommended scenarios in the Regression tab.');
  }
  function tAutomation(){
    var rows=findings('AUTOMATION').filter(function(f){return matches(S.f,f);}).map(function(f){var p=f.payload;
      return '<tr><td>'+esc(p.tc_number||'')+'</td><td>'+esc(p.asset_kind)+'</td><td>'+esc(p.asset_label)+'</td><td><b>'+esc(p.impact)+'</b></td><td>'+esc((p.matched_terms||[]).join(', '))+'</td><td>'+esc(p.reason)+'</td><td>'+assurancePill(f.assurance)+'</td><td>'+revCell(f)+'</td><td>'+acts(f)+'</td></tr>';});
    return '<div class="ci-note">Nothing was executed and no script was changed. Review each asset manually.</div>'+table([['Test','70px'],['Kind','70px'],['Asset',''],['Impact','130px'],['Matched terms',''],['Reason',''],['Assurance','100px'],['Review','70px'],['','100px']],rows,'No existing automation asset has evidence of being affected. Automation candidates for the recommended scenarios are listed in the Regression tab.');
  }
  function tGaps(){
    var s=snap(),rows=findings('GAP').filter(function(f){return matches(S.f,f);}).map(function(f){var p=f.payload,h=has('qa_engineering','create')?'<button type="button" class="ci-btn" data-ci-act="handoff" data-fid="'+esc(f.finding_id)+'">Open in QA Engineering</button>':'';
      return '<tr><td><b>'+esc(p.subject)+'</b><div class="muted-small">'+esc(p.description)+'</div></td><td>'+p.linked_tests+(p.suggested_tests?' <span class="muted-small">(+'+p.suggested_tests+' suggested)</span>':'')+'</td><td>'+esc((p.missing_scenarios||[]).join('; '))+'</td><td>'+esc((p.suggested_test_types||[]).join(', '))+'</td><td>'+(f.severity?pill(f.severity,f.severity):'')+'</td><td>'+esc(p.recommended_action)+'</td><td>'+revCell(f)+'</td><td>'+acts(f,h)+'</td></tr>';});
    return '<div class="ci-note">A gap means no confirmed test link was found. It does not prove that no test exists. Nothing is created or published automatically.</div>'+table([['Requirement / change','150px'],['Linked tests','86px'],['Missing scenarios',''],['Suggested types','130px'],['Risk','60px'],['Recommended action',''],['Review','70px'],['','140px']],rows);
  }
  function tQuestions(){
    var s=snap(),list=((s.insight||{}).questions||[]).filter(function(q){return (!S.f.change||(q.related_changes||[]).indexOf(S.f.change)>=0)&&(!S.f.severity||q.severity===S.f.severity)&&(!S.f.q||JSON.stringify(q).toLowerCase().indexOf(S.f.q.toLowerCase())>=0);});
    if(!list.length)return '<div class="ci-empty">'+(((s.insight||{}).questions||[]).length?'Nothing matches the current filters.':'No open questions were found. The documents did not show contradictions, ambiguities or missing rules.')+'</div>';
    return '<div class="ci-note">These points need a Business Analyst or owner to clarify. The tool lists them but never decides between conflicting statements.</div>'+list.map(function(q){
      return '<div class="ci-card" style="margin-top:8px"><div class="ci-row" style="justify-content:space-between"><div><b>'+esc(q.title||q.id)+'</b> '+pill(q.severity||'Medium',q.severity||'Medium')+' <span class="muted-small">'+esc(q.id)+' &middot; '+esc((q.type||'').replace(/_/g,' ').toLowerCase())+'</span></div></div>'+
        '<p>'+esc(q.question)+'</p><div class="muted-small">'+esc(q.basis||'')+'</div>'+
        ((q.statements||[]).length?'<div class="ci-section"><h3>Statements in the document</h3>'+q.statements.map(function(st){var e=st.evidence||{};return '<div class="ci-quote"><small>'+esc(e.ref||'')+' &middot; '+esc(e.context||'')+' &middot; '+esc(e.location||'')+(st.stance?' &middot; '+esc(st.stance):'')+'</small>'+esc(st.quote||e.quote||'')+'</div>';}).join('')+'</div>':'')+
        ((q.related_changes||[]).length?'<div class="muted-small" style="margin-top:4px">Related changes: '+q.related_changes.map(function(id){return '<button type="button" class="ci-link-btn" data-ci-act="change" data-cid="'+esc(id)+'">'+esc(id)+'</button>';}).join(', ')+'</div>':'')+'</div>';}).join('');
  }
  function vRisk(){
    var s=snap(),out=s.changes.filter(function(c){return (!S.f.change||c.change_id===S.f.change)&&(!S.f.severity||(s.risk[c.change_id]||{}).level===S.f.severity);}).map(function(c){
      var r=s.risk[c.change_id];if(!r)return '';
      return '<div class="ci-card"><div class="ci-row" style="justify-content:space-between"><div><b>'+esc(c.reference||c.change_id)+'</b> '+pill(r.level,r.level)+' <span class="muted-small">score '+r.score.toFixed(2)+' of 1.00</span><div class="muted-small">'+esc(c.description)+'</div></div><button type="button" class="ci-btn" data-ci-act="explain-risk" data-cid="'+esc(c.change_id)+'">Why this risk?</button></div>'+
        '<div class="ci-risk" style="margin-top:6px">'+r.factors.map(function(f){return '<div class="ci-factor '+(f.applied?'':'off')+'" title="'+esc(f.note)+'"><span>'+esc(f.label)+'</span><span class="ci-bar"><i style="width:'+Math.round(f.value*100)+'%"></i></span><span>'+f.value.toFixed(2)+'</span></div><div class="muted-small" style="margin:-3px 0 2px 158px">'+esc(f.note)+'</div>';}).join('')+'</div>'+
        ((r.reasons||[]).length?'<div class="ci-section"><h3>Why this level</h3><ul class="ci-ul">'+r.reasons.map(function(x){return '<li>'+esc(x.text)+(x.quote?'<div class="muted-small">"'+esc(x.quote)+'"</div>':'')+'</li>';}).join('')+'</ul></div>':'')+(((r.mitigation||[]).length)?'<div class="muted-small">Suggested mitigation: '+esc(r.mitigation.join(' '))+'</div>':'')+'<div class="muted-small" style="margin-top:4px">'+esc(r.disclaimer)+'</div></div>';}).join('');
    return out||'<div class="ci-empty">No changes to assess.</div>';
  }

  // ------------------------------------------------------------------ Impact graph (lightweight SVG, zoom / pan, cycle-safe)
  var COLS=['change','requirement','system','test','new_test','automation'],
    COLN={change:'Change',requirement:'Requirement',system:'System / Module',test:'Existing Test Case',new_test:'Recommended New Scenarios',reg_test:'Other Regression Recommendations',automation:'Automation'},
    COLH=['Change','Requirement','System / Module','Tests & Recommendations','Recommended Scenarios','Automation'],
    KINDS=['change','requirement','system','test','new_test','reg_test','automation'],
    GRAPH_W=196,GRAPH_IW=224,GRAPH_GX=48,GRAPH_GY=10,GRAPH_LH=14.5,GRAPH_SUBH=14,GRAPH_PADY=8,GRAPH_LINES=4,GRAPH_TOP=30,DUMMY_H=8,DUMMY_GAP=5,
    FIT_MAX=1.2,FIT_MARGIN=16,FIT_BOTTOM=8,NARROW_AT=1100,MIN_BOX_H=440,
    READ_MIN=.85,FIT_FLOOR=.75,GRAPH_BUILD='live-readable-view-20261016';   // READ_MIN: smallest scale the default view may use (10.2px text). FIT_FLOOR: smallest scale Fit to Screen may use (9px text).
  function colOf(n){var k=n.kind;return k==='rec_group'?3:(k==='new_test'||k==='reg_test')?4:Math.max(0,COLS.indexOf(k));}
  function fkind(n){return n.kind==='rec_group'?(n.category==='new_scenarios'?'new_test':'reg_test'):n.kind;}
  var SEVC={Critical:'#C22E2E',High:'#E5605A',Medium:'#E0A83E',Low:'#35B583'};
  function recLine(g){
    var r=g.recommendations||{},t=(snap().dashboard||{}).totals||{};
    if(!r.total)return '';
    var parts=[];if(r.new_scenarios)parts.push(r.new_scenarios+' new test scenario'+(r.new_scenarios===1?'':'s'));if(r.integration)parts.push(r.integration+' integration regression');if(r.existing_regression)parts.push(r.existing_regression+' existing-behaviour regression');if(r.other_regression)parts.push(r.other_regression+' other');
    return '<div class="ci-note">Recommended regression items: <b>'+r.total+'</b> ('+esc(parts.join(' + '))+'). Existing impacted test cases: <b>'+(t.existing_impacted_tests||0)+'</b>. Recommended items are suggestions, not test cases, and none is created automatically. Select a group and use <i>Expand</i> to see each item.'+(r.omitted?' '+r.omitted+' item(s) were left out of the graph by the configured limit; the Regression tab lists them all.':'')+'</div>';
  }
  function vGraph(){
    var g=snap().graph;
    return '<div id="ciGTools"><div class="ci-filters"><label class="ci-row"><input type="checkbox" id="ciGSoft" checked> Show suggestions &amp; hypotheses (dashed)</label>'+
      '<label class="ci-row"><input type="checkbox" id="ciGExp"> Expand recommended scenarios</label>'+
      '<select id="ciGSev" aria-label="Minimum risk">'+options(['Medium','High','Critical'],'All risk levels','')+'</select>'+
      '<input type="search" id="ciGQ" placeholder="Find a node…" aria-label="Find a node">'+
      KINDS.map(function(k){return '<label class="ci-row"><input type="checkbox" class="ci-gkind" value="'+k+'" checked> '+COLN[k]+'</label>';}).join('')+'</div>'+
      recLine(g)+
      (g.cycle_detected?'<div class="ci-note warn">A circular dependency was found between systems. Traversal stops at systems already visited, so it cannot loop.</div>':'')+
      (g.truncated?'<div class="ci-note warn">The graph was limited to '+g.max_nodes+' nodes. Use the tables for the full list.</div>':'')+'</div>'+
      '<div class="ci-graph-wrap" id="ciGWrap"><div class="ci-svgbox" id="ciSvgBox"><div class="ci-zoom"><button type="button" class="ci-btn" data-ci-act="zoom-in" aria-label="Zoom in" title="Zoom in">+</button><button type="button" class="ci-btn" data-ci-act="zoom-out" aria-label="Zoom out" title="Zoom out">&minus;</button>'+
      '<button type="button" class="ci-btn" data-ci-act="zoom-fit" title="Fit the whole graph to the window">Fit to Screen</button><button type="button" class="ci-btn" data-ci-act="zoom-reset" title="Return to the default view">Reset View</button>'+
      '<button type="button" class="ci-btn" id="ciGToggle" data-ci-act="side-toggle" aria-pressed="true">Hide details</button><output id="ciGZoom" aria-label="Graph zoom">100%</output><span id="ciGHint" class="muted-small" role="status"></span><span class="ci-gleg muted-small">Drag to pan &middot; Scroll to zoom &middot; Solid: confirmed &middot; Dashed: suggestion</span></div><svg id="ciSvg" role="img" aria-label="Impact graph"></svg></div>'+
      '<div class="ci-side" id="ciGSide">'+sideTop()+'<b>Select a node</b><p class="muted-small">Click a node to see why it is in the graph, its relationships and its evidence. Full names are shown here and on hover. Solid lines are evidence-backed or confirmed; dashed lines are suggestions that need verification. Recommended scenarios are suggestions, not existing test cases; click a group to expand it.</p></div></div>';
  }
  function sideTop(){return '<div class="ci-side-top"><span class="muted-small">Details</span><button type="button" class="ci-btn" data-ci-act="side-toggle">Hide</button></div>';}
  // Sizing. The graph box fills the window below the filter bar, so the whole graph is visible without scrolling the page.
  function graphScroller(){var b=el('ciSvgBox');return b?b.closest('.ci-panel'):null;}
  function sizeGraph(reveal){
    var wrap=el('ciGWrap'),box=el('ciSvgBox'),side=el('ciGSide'),sc=graphScroller(),tb=el('ciGTools');
    if(!wrap||!box||!side||!sc||!S.graph)return false;
    var sr=sc.getBoundingClientRect(),tbTop=(tb||wrap).getBoundingClientRect().top-sr.top+sc.scrollTop,wrapTop=wrap.getBoundingClientRect().top-sr.top+sc.scrollTop,
      h=Math.max(MIN_BOX_H,Math.min(1200,Math.round(sc.clientHeight-(wrapTop-tbTop)-16))),changed=S.graph.h!==h;
    S.graph.h=h;box.style.height=h+'px';side.style.height=h+'px';
    if(reveal)sc.scrollTop=Math.max(0,tbTop-6);
    return changed;
  }
  // A narrow workspace (small window, expanded sidebar) shows the details as a panel over the graph instead of beside it.
  function applyPanel(){
    var wrap=el('ciGWrap'),side=el('ciGSide'),g=S.graph;if(!wrap||!side||!g)return false;
    var narrow=wrap.clientWidth<NARROW_AT,was=!!g.narrow;g.narrow=narrow;
    wrap.classList.toggle('narrow',narrow);wrap.classList.toggle('nopanel',!narrow&&!!g.hidePanel);side.classList.toggle('open',narrow&&!!g.openNarrow);
    var shown=narrow?!!g.openNarrow:!g.hidePanel,tg=el('ciGToggle');
    if(tg){tg.textContent=shown?'Hide details':'Show details';tg.setAttribute('aria-pressed',shown?'true':'false');}
    return was!==narrow;
  }
  function graphBox(){var b=el('ciSvg');if(!b)return null;var r=b.getBoundingClientRect();return (r.width<80||r.height<80)?null:{w:r.width,h:r.height};}
  function setHint(t){var h=el('ciGHint');if(h)h.textContent=t||'';}
  // Default view: always readable. The scale follows the available width and height but never drops below READ_MIN, so a wide graph is panned, not shrunk.
  function defaultView(){
    var g=S.graph,bx=graphBox();if(!g||!bx)return;
    var cw=g.width||(COLS.length*(GRAPH_W+GRAPH_GX)),ch=g.height||200,
      s=Math.max(READ_MIN,Math.min(FIT_MAX,(bx.w-2*FIT_MARGIN)/cw,(bx.h-FIT_MARGIN-FIT_BOTTOM)/ch));
    g.scale=s;
    g.tx=cw*s+2*FIT_MARGIN<=bx.w?Math.max(FIT_MARGIN,(bx.w-cw*s)/2):FIT_MARGIN;
    g.ty=ch*s+FIT_MARGIN+FIT_BOTTOM<=bx.h?Math.max(FIT_MARGIN/2,(bx.h-ch*s)/2):FIT_MARGIN;
    g.manual=false;g.revealed=false;setHint('');
  }
  // Fit to Screen is an explicit request and shows as much of the graph as stays readable: it stops at FIT_FLOOR and the rest is reached by panning.
  function fitView(whole){
    var g=S.graph,bx=graphBox();if(!g||!bx)return;
    if(!whole){defaultView();return;}
    var aw=bx.w-2*FIT_MARGIN,ah=bx.h-FIT_MARGIN-FIT_BOTTOM,cw=g.width||(COLS.length*(GRAPH_W+GRAPH_GX)),ch=g.height||200,
      s=Math.min(FIT_MAX,aw/cw,ah/ch),floored=s<FIT_FLOOR;
    if(floored)s=FIT_FLOOR;
    g.scale=s;
    g.tx=cw*s+2*FIT_MARGIN<=bx.w?Math.max(FIT_MARGIN,(bx.w-cw*s)/2):FIT_MARGIN;
    g.ty=ch*s+FIT_MARGIN+FIT_BOTTOM<=bx.h?Math.max(FIT_MARGIN/2,(bx.h-FIT_BOTTOM-ch*s)/2+FIT_MARGIN/2):FIT_MARGIN;
    g.manual=true;g.revealed=false;
    setHint(floored?'Fit stopped at the smallest readable size \u00b7 drag to see the rest':'');
  }
  // After a group is opened, bring the group and its items into view without changing the zoom.
  function revealFocus(){
    var g=S.graph,bx=graphBox(),f=g&&g.focus;if(!g||!bx)return;
    if(!f){if(g.revealed&&!g.manual){defaultView();}return;}
    var s=g.scale,M=FIT_MARGIN,ox=g.tx,oy=g.ty,l=f.xl*s+g.tx,r=f.x1*s+g.tx,t=f.y0*s+g.ty,b=f.y1*s+g.ty;
    if(r-l>bx.w-2*M){l=f.x0*s+g.tx;}                                        // too wide with the column before the group: keep the group and its items
    if(r-l<=bx.w-2*M){if(l<M)g.tx+=M-l;else if(r>bx.w-M)g.tx-=r-(bx.w-M);}else g.tx=bx.w-M-f.x1*s;   // still too wide: the items matter most
    if(b-t<=bx.h-2*M){if(t<M)g.ty+=M-t;else if(b>bx.h-M)g.ty-=b-(bx.h-M);}else g.ty+=M-t;
    if(g.tx!==ox||g.ty!==oy)g.revealed=true;
  }
  var gRO=null,gRaf=0,gListening=false,gLast='';
  function onGraphResize(){
    gRaf=0;if(!el('ciSvgBox')||!S.graph)return;
    var key=(el('ciGWrap').clientWidth)+'x'+(graphScroller().clientHeight);if(key===gLast)return;gLast=key;
    sizeGraph(false);applyPanel();drawGraph();
    if(!S.graph.manual)defaultView();                                        // an untouched view follows the new size; a view the user set is kept
    applyView();if(S.graph.sel)ensureVisible(S.graph.sel);
  }
  function scheduleGraphResize(){if(!gRaf)gRaf=window.requestAnimationFrame(onGraphResize);}
  function initGraph(){
    if(!S.graph){S.graph={scale:1,tx:10,ty:6,sel:null,drag:null,exp:{},width:0,height:0,manual:false};}
    var kept=S.graph.manual?{s:S.graph.scale,x:S.graph.tx,y:S.graph.ty}:null;     // the zoom and position the user chose survive a re-render
    S.graph.sig=null;gLast='';
    sizeGraph(true);applyPanel();drawGraph();
    if(kept){S.graph.scale=kept.s;S.graph.tx=kept.x;S.graph.ty=kept.y;}else defaultView();
    var zl=el('ciGZoom');if(zl)zl.title='Graph zoom \u00b7 view build '+GRAPH_BUILD;
    applyView();
    gLast=(el('ciGWrap').clientWidth)+'x'+(graphScroller().clientHeight);
    var box=el('ciSvgBox');
    box.addEventListener('wheel',function(ev){ev.preventDefault();var r=el('ciSvg').getBoundingClientRect();S.graph.manual=true;zoom(ev.deltaY<0?1.12:0.89,ev.clientX-r.left,ev.clientY-r.top);},{passive:false});
    box.addEventListener('pointerdown',function(ev){if(ev.target.closest('.ci-node')||ev.target.closest('.ci-zoom')||!ev.target.closest('svg'))return;S.graph.drag={x:ev.clientX,y:ev.clientY,tx:S.graph.tx,ty:S.graph.ty};box.setPointerCapture(ev.pointerId);box.style.cursor='grabbing';});
    box.addEventListener('pointermove',function(ev){var d=S.graph.drag;if(!d)return;S.graph.manual=true;S.graph.tx=d.tx+ev.clientX-d.x;S.graph.ty=d.ty+ev.clientY-d.y;applyView();});
    function endDrag(){S.graph.drag=null;box.style.cursor='grab';}
    box.addEventListener('pointerup',endDrag);
    box.addEventListener('pointercancel',endDrag);
    box.addEventListener('lostpointercapture',endDrag);
    if(gRO){gRO.disconnect();gRO=null;}
    if(window.ResizeObserver){gRO=new ResizeObserver(scheduleGraphResize);gRO.observe(el('ciGWrap'));}
    if(!gListening){gListening=true;window.addEventListener('resize',scheduleGraphResize);}
  }
  // Which groups are open: those the user expanded plus any group holding a node that matches the search or is selected.
  function openGroups(g,q){
    var open=Object.assign({},S.graph.exp||{});
    g.nodes.forEach(function(n){if(n.group&&((q&&(n.label+' '+(n.detail||'')).toLowerCase().indexOf(q)>=0)||S.graph.sel===n.id))open[n.group]=true;});
    return open;
  }
  function visibleNodes(){
    var g=snap().graph,soft=el('ciGSoft')?el('ciGSoft').checked:true,min=val('ciGSev'),rank={Low:0,Medium:1,High:2,Critical:3},kinds={};
    document.querySelectorAll('.ci-gkind').forEach(function(c){kinds[c.value]=c.checked;});
    var q=(val('ciGQ')||'').toLowerCase(),open=openGroups(g,q);
    return g.nodes.filter(function(n){
      if(kinds[fkind(n)]===false)return false;
      if(n.group&&!open[n.group])return false;
      if(min&&(rank[n.severity]||0)<rank[min])return false;
      if(!soft&&(n.assurance==='SUGGESTED'||n.assurance==='HYPOTHESIS'))return false;
      return true;}).map(function(n){return Object.assign({},n,{match:!!q&&(n.label+' '+(n.detail||'')).toLowerCase().indexOf(q)>=0,open:!!open[n.id]});});
  }
  // Full names, wrapped on word boundaries using the real text width. Only a name longer than GRAPH_LINES lines is cut;
  // the complete name is always in the hover text and the details panel.
  var gMC=null;
  function measureInit(){try{if(!gMC)gMC=document.createElement('canvas').getContext('2d');var s=el('ciSvg');gMC.fam=(s&&window.getComputedStyle(s).fontFamily)||'sans-serif';}catch(e){gMC=null;}}
  function textWidth(s,bold){
    if(gMC){try{gMC.font=(bold?'600 ':'400 ')+'12px '+gMC.fam;return gMC.measureText(s).width;}catch(e){}}
    return String(s).length*(bold?6.9:6.4);
  }
  function wrapLabel(text,bold,tw){
    var words=String(text||'').split(/\s+/).filter(Boolean),lines=[],cur='';
    function fits(s){return textWidth(s,bold)<=tw;}
    words.forEach(function(w){
      while(w.length>1&&!fits(w)){var k=w.length;while(k>1&&!fits(w.slice(0,k)))k--;if(cur){lines.push(cur);cur='';}lines.push(w.slice(0,k));w=w.slice(k);}
      if(!cur)cur=w;else if(fits(cur+' '+w))cur+=' '+w;else{lines.push(cur);cur=w;}
    });
    if(cur)lines.push(cur);
    if(lines.length>GRAPH_LINES){lines=lines.slice(0,GRAPH_LINES);var last=lines[GRAPH_LINES-1];while(last.length>1&&!fits(last+'…'))last=last.slice(0,-1);lines[GRAPH_LINES-1]=last.replace(/\s+$/,'')+'…';}
    return lines;
  }
  function subLine(n){
    if(n.kind==='rec_group')return (n.open?'▾ Collapse':'▸ Expand')+' · '+(n.members||[]).length+' item'+((n.members||[]).length===1?'':'s');
    if(n.kind==='new_test'||n.kind==='reg_test')return (n.priority||'')+(n.execution&&n.execution.length?' · '+n.execution.join(', '):'');
    if(n.kind==='system'&&n.certainty)return n.certainty;
    return '';
  }
  // Layered layout. Nodes sit in columns; a line that skips a column gets a reserved lane between the nodes of that column, so it never runs through a node.
  // The order inside each column is improved by neighbour-average sweeps (the arrangement with the fewest crossings is kept), then the nodes are placed
  // level with the nodes they connect to, with no overlap, by a least-squares placement per column.
  function isItem(n){return n.kind==='new_test'||n.kind==='reg_test';}
  function gapBetween(a,b){return (a._d||b._d)?DUMMY_GAP:GRAPH_GY;}
  function placeColumn(list,desired){
    var n=list.length,off=[],o=0,i,blocks=[];
    for(i=0;i<n;i++){off[i]=o;o+=list[i]._h+(i+1<n?gapBetween(list[i],list[i+1]):0);}
    for(i=0;i<n;i++){
      var w=list[i]._d?0.3:1,t=desired[i]-list[i]._h/2-off[i];
      blocks.push({sum:t*w,w:w,from:i,to:i,v:t});
      while(blocks.length>1&&blocks[blocks.length-2].v>blocks[blocks.length-1].v){var b2=blocks.pop(),b1=blocks[blocks.length-1];b1.sum+=b2.sum;b1.w+=b2.w;b1.to=b2.to;b1.v=b1.sum/b1.w;}
    }
    blocks.forEach(function(b){for(var k=b.from;k<=b.to;k++)list[k]._y=Math.max(GRAPH_TOP,b.v)+off[k];});
  }
  function layout(nodes,links){
    measureInit();
    var W=GRAPH_W,pos={},cols={},byId={},sevr={Critical:0,High:1,Medium:2,Low:3},members={};
    nodes.forEach(function(n,i){byId[n.id]=n;n._i=i;n._c=colOf(n);n._d=false;n._w=n._c===4?GRAPH_IW:W;n._lines=wrapLabel(n.label,n.kind==='rec_group',n._w-22);n._sub=subLine(n);n._h=2*GRAPH_PADY+n._lines.length*GRAPH_LH+(n._sub?GRAPH_SUBH:0);(cols[n._c]=cols[n._c]||[]).push(n);});
    var keys=Object.keys(cols).map(Number).sort(function(a,b){return a-b;}),slot={},xs=[],ws=[],xa=0;keys.forEach(function(k,i){slot[k]=i;ws[i]=k===4?GRAPH_IW:W;xs[i]=xa;xa+=ws[i]+GRAPH_GX;});
    nodes.forEach(function(n){n._s=slot[n._c];if(isItem(n)&&byId[n.group])(members[n.group]=members[n.group]||[]).push(n);});
    keys.forEach(function(k){cols[k].sort(function(a,b){return (sevr[a.severity]==null?4:sevr[a.severity])-(sevr[b.severity]==null?4:sevr[b.severity])||String(a.label).localeCompare(String(b.label));});});
    var slots=keys.map(function(k){return cols[k];}),adj={},seen={},routes={};
    function nb(n){return adj[n.id]||(adj[n.id]={up:[],down:[]});}
    links.forEach(function(l){
      var a=byId[l.source],b=byId[l.target];if(!a||!b||a===b||a._s===b._s)return;
      var lo=a._s<b._s?a:b,hi=lo===a?b:a,key=lo.id+'|'+hi.id;if(seen[key])return;seen[key]=true;
      var prev=lo,chain=[];
      for(var s=lo._s+1;s<hi._s;s++){var d={id:'~'+key+'~'+s,_d:true,_s:s,_h:DUMMY_H,kind:'lane'};slots[s].push(d);chain.push(d);nb(prev).down.push(d);nb(d).up.push(prev);prev=d;}
      nb(prev).down.push(hi);nb(hi).up.push(prev);routes[key]=chain;
    });
    function rank(){slots.forEach(function(L){L.forEach(function(n,i){n._r=i;});});}
    rank();
    function crossings(){
      var c=0;
      for(var s=0;s<slots.length-1;s++){
        var es=[];slots[s].forEach(function(n){((adj[n.id]||{down:[]}).down).forEach(function(m){es.push([n._r,m._r]);});});
        for(var i=0;i<es.length;i++)for(var j=i+1;j<es.length;j++)if((es[i][0]-es[j][0])*(es[i][1]-es[j][1])<0)c++;
      }
      return c;
    }
    function sweep(s,dir){
      var L=slots[s];
      L.forEach(function(n){
        var list=(adj[n.id]||{up:[],down:[]})[dir<0?'up':'down'],sum=0;list.forEach(function(m){sum+=m._r;});
        n._b=list.length?sum/list.length:n._r;
        if(isItem(n)&&byId[n.group])n._b=byId[n.group]._r*1000+n._i/1000;       // a group's items stay together, in their given order
      });
      L.sort(function(a,b){return a._b-b._b||a._r-b._r;});L.forEach(function(n,i){n._r=i;});
    }
    var best=null,bestC=Infinity;
    for(var pass=0;pass<8;pass++){
      if(pass%2===0){for(var s1=1;s1<slots.length;s1++)sweep(s1,-1);}else{for(var s2=slots.length-2;s2>=0;s2--)sweep(s2,1);}
      var c=crossings();if(c<bestC){bestC=c;best=slots.map(function(L){return L.slice();});}
    }
    if(best){best.forEach(function(L,s){slots[s].length=0;L.forEach(function(n){slots[s].push(n);});});rank();}
    slots.forEach(function(L){var y=GRAPH_TOP;L.forEach(function(n,i){n._y=y;y+=n._h+(i+1<L.length?gapBetween(n,L[i+1]):0);});});
    function mid(n){return n._y+n._h/2;}
    function avg(a){var s=0;a.forEach(function(v){s+=v;});return s/a.length;}
    for(var it=0;it<10;it++){
      (it%2===0?slots.map(function(_,i){return i;}):slots.map(function(_,i){return slots.length-1-i;})).forEach(function(s){
        var L=slots[s];
        placeColumn(L,L.map(function(n){
          var t=[],a=adj[n.id];
          if(isItem(n)&&byId[n.group])return mid(byId[n.group]);                  // an item stays beside its group
          if(a){a.up.forEach(function(m){t.push(mid(m));});a.down.forEach(function(m){t.push(mid(m));});}
          var own=t.length?avg(t):null,mem=members[n.id]?avg(members[n.id].map(mid)):null;
          return own!=null&&mem!=null?(own+mem)/2:(own!=null?own:(mem!=null?mem:mid(n)));
        }));
      });
    }
    var minY=Infinity,maxY=0;
    nodes.forEach(function(n){minY=Math.min(minY,n._y);});
    var lift=isFinite(minY)?GRAPH_TOP-minY:0;
    nodes.forEach(function(n){n._y+=lift;maxY=Math.max(maxY,n._y+n._h);pos[n.id]={x:xs[n._s],y:n._y,w:ws[n._s],h:n._h,col:n._s,kcol:keys[n._s]};});
    pos.__route={};
    Object.keys(routes).forEach(function(k){pos.__route[k]=routes[k].map(function(d){return {x:xs[d._s],w:ws[d._s],y:d._y+lift+d._h/2};});});
    pos.__cols=keys;pos.__xs=xs;pos.__height=maxY+8;pos.__width=keys.length?xs[keys.length-1]+ws[keys.length-1]:0;
    return pos;
  }
  function visibleLinks(g,ids,open,sel,soft){
    var out=[],contain={};
    g.links.forEach(function(l){
      if(!ids[l.source]||!ids[l.target]||(!soft&&l.soft))return;
      if(l.group&&!l.aggregate&&!(sel&&(l.source===sel||l.target===sel)))return;   // one line per recommended item only when it, or its parent, is selected
      out.push(l);
    });
    g.nodes.forEach(function(n){if(n.group&&ids[n.id]&&ids[n.group]&&open[n.group])contain[n.id]=n.group;});
    Object.keys(contain).forEach(function(c){out.push({source:contain[c],target:c,label:'contains',contain:true,assurance:'SUGGESTED',soft:true,evidence:[],basis:'Group member'});});
    return out;
  }
  // Where each line leaves and enters a node: several lines on one side are spread along it, ordered by where they go, so they do not pile up.
  function attachPoints(links,pos){
    var out={},inn={},res={};
    links.forEach(function(l,i){
      var a=pos[l.source],b=pos[l.target];if(!a||!b||a.col>=b.col)return;
      var chain=routeOf(l,pos),first=chain.length?chain[0].y:b.y+b.h/2,last=chain.length?chain[chain.length-1].y:a.y+a.h/2;
      (out[l.source]=out[l.source]||[]).push({i:i,o:first});(inn[l.target]=inn[l.target]||[]).push({i:i,o:last});
    });
    function spread(map,key,field){
      Object.keys(map).forEach(function(id){
        var list=map[id].sort(function(p,q){return p.o-q.o||p.i-q.i;}),p=pos[id];
        list.forEach(function(e,k){(res[e.i]=res[e.i]||{})[field]=list.length>1?p.y+p.h*(0.2+0.6*(k+0.5)/list.length):p.y+p.h/2;});
      });
    }
    spread(out,'out','y1');spread(inn,'inn','y2');
    return res;
  }
  function routeOf(l,pos){
    var a=pos[l.source],b=pos[l.target];if(!a||!b||!pos.__route)return [];
    var lo=a.col<b.col?l.source:l.target,hi=lo===l.source?l.target:l.source,r=pos.__route[lo+'|'+hi]||[];
    return a.col<b.col?r:r.slice().reverse();
  }
  function curveTo(x1,y1,x2,y2){var mx=(x1+x2)/2;return 'C'+mx+','+y1+' '+mx+','+y2+' '+x2+','+y2;}
  function drawGraph(){
    var g=snap().graph,soft=el('ciGSoft')?el('ciGSoft').checked:true,q=(val('ciGQ')||'').toLowerCase(),open=openGroups(g,q);
    var nodes=visibleNodes(),ids={};nodes.forEach(function(n){ids[n.id]=n;});
    var sel=S.graph.sel,links=visibleLinks(g,ids,open,sel,soft),pos=layout(nodes,g.links.filter(function(l){return ids[l.source]&&ids[l.target]&&(soft||!l.soft)&&!(l.group&&!l.aggregate);}));
    S.graph.width=pos.__width||0;S.graph.height=pos.__height||0;
    var fr=null;                                                       // the open recommendation groups and their items
    nodes.forEach(function(n){
      var p=pos[n.id];if(!p||!((n.kind==='rec_group'&&open[n.id])||(isItem(n)&&open[n.group])))return;
      var pc=pos.__xs[Math.max(0,p.col-1)];fr=fr?{x0:Math.min(fr.x0,p.x),xl:Math.min(fr.xl,pc),x1:Math.max(fr.x1,p.x+p.w),y0:Math.min(fr.y0,p.y),y1:Math.max(fr.y1,p.y+p.h)}:{x0:p.x,xl:pc,x1:p.x+p.w,y0:p.y,y1:p.y+p.h};
    });
    S.graph.focus=fr;
    var shown=links.filter(function(l){return pos[l.source]&&pos[l.target];});
    // the lanes reserved by the layout belong to the lines it knew about; a line it did not know about (a selected item's own line) is drawn directly
    var fan=attachPoints(shown,pos);
    var hl=sel&&ids[sel]?neighborhood(sel,g.links.filter(function(l){return ids[l.source]&&ids[l.target];}),3):null,svg=el('ciSvg');
    var h='<defs><marker id="ciArrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" style="fill:var(--ink-faint)"></path></marker></defs><g id="ciViewport">';
    (pos.__cols||[]).forEach(function(k,i){h+='<text class="ci-collabel" x="'+pos.__xs[i]+'" y="18">'+esc(COLH[k])+'</text>';});
    shown.forEach(function(l,li){
      var a=pos[l.source],b=pos[l.target];
      var cls='ci-link'+(l.soft?' soft':'')+(l.contain?' contain':'')+(l.aggregate?' agg':'')+(hl?((l.contain?(hl.nodes[l.source]&&hl.nodes[l.target]):hl.links[l.source+'>'+l.target])?(l.contain?'':' hl'):' dim'):''),x1,x2,y1=a.y+a.h/2,y2=b.y+b.h/2,d;
      if(a.col<b.col){
        x1=a.x+a.w;x2=b.x;if(fan[li]&&fan[li].y1!=null)y1=fan[li].y1;if(fan[li]&&fan[li].y2!=null)y2=fan[li].y2;
        d='M'+x1+','+y1;var px=x1,py=y1;
        routeOf(l,pos).forEach(function(p){d+=' '+curveTo(px,py,p.x,p.y)+' L'+(p.x+p.w)+','+p.y;px=p.x+p.w;py=p.y;});
        d+=' '+curveTo(px,py,x2,y2);
      }else if(a.col===b.col){
        x1=a.x+a.w;x2=b.x+b.w;var bulge=Math.min(GRAPH_GX*0.8,16+Math.abs(y2-y1)*0.12);
        d='M'+x1+','+y1+' C'+(x1+bulge)+','+y1+' '+(x2+bulge)+','+y2+' '+x2+','+y2;
      }else{x1=a.x;x2=b.x+b.w;var mx2=(x1+x2)/2;d='M'+x1+','+y1+' C'+mx2+','+y1+' '+mx2+','+y2+' '+x2+','+y2;}
      h+='<path class="'+cls+'" d="'+d+'"'+(l.contain?'':' marker-end="url(#ciArrow)"')+'><title>'+esc(l.label+' ('+(ASSURANCE[l.assurance]||l.assurance)+')'+(l.basis?' - '+l.basis:''))+'</title></path>';
    });
    nodes.forEach(function(n){
      var p=pos[n.id],cls='ci-node ci-k-'+n.kind+(n.certainty==='Potential Impact'?' pot':'')+(sel===n.id||n.match?' hl':'')+(hl&&!hl.nodes[n.id]?' dim':''),txt='',base=GRAPH_PADY+11;
      n._lines.forEach(function(t,i){txt+='<text x="13" y="'+(base+i*GRAPH_LH)+'">'+esc(t)+'</text>';});
      if(n._sub)txt+='<text class="sub" x="13" y="'+(base+(n._lines.length-1)*GRAPH_LH+GRAPH_SUBH)+'">'+esc(n._sub)+'</text>';
      h+='<g class="'+cls+'" data-ci-node="'+esc(n.id)+'" tabindex="0" role="button" aria-label="'+esc((COLN[fkind(n)]||n.kind)+': '+n.label)+'" transform="translate('+p.x+','+p.y+')"><rect width="'+p.w+'" height="'+p.h+'" rx="6" '+(n.assurance==='HYPOTHESIS'||n.assurance==='SUGGESTED'?'stroke-dasharray="4 3"':'')+'></rect>'+
        '<rect class="sev" x="0" y="0" width="5" height="'+p.h+'" rx="2" fill="'+(SEVC[n.severity]||'#8A93A3')+'"></rect>'+txt+'<title>'+esc(nodeTip(n))+'</title></g>';
    });
    svg.innerHTML=h+'</g>';
    var sig=S.graph.width+'x'+S.graph.height+'x'+(pos.__cols||[]).length+(fr?'f':'');
    if(S.graph.sig!==sig){S.graph.sig=sig;} // Preserve the viewport when groups or filters change.
    applyView();
  }
  // The hover text carries the complete name and the key facts, so nothing important is lost to wrapping.
  function nodeTip(n){
    var t=[n.label,(COLN[fkind(n)]||n.kind)+(n.certainty?' - '+n.certainty:'')+(n.assurance?' - '+(ASSURANCE[n.assurance]||n.assurance):'')];
    if(n.kind==='new_test'||n.kind==='reg_test'){
      t.push('Priority '+(n.priority||'-')+(n.severity?' - Risk '+n.severity:'')+' - Execution: '+((n.execution||[]).join(', ')||'-'));
      if((n.requirements||[]).length)t.push('Requirement: '+n.requirements.map(function(r){return r.label;}).join('; '));
      if((n.change_ids||[]).length)t.push('Change: '+n.change_ids.join(', '));
      t.push('A suggestion, not an existing test case.');
    }else if(n.kind==='rec_group')t.push((n.members||[]).length+' recommended item(s); click to '+(n.open?'collapse':'expand'));
    else if(n.detail)t.push(String(n.detail).slice(0,220));
    return t.join('\n');
  }
  function applyView(){var v=el('ciViewport');if(v)v.setAttribute('transform','translate('+S.graph.tx+','+S.graph.ty+') scale('+S.graph.scale+')');var z=el('ciGZoom');if(z)z.textContent=Math.round(S.graph.scale*100)+'%';}
  function zoom(f,cx,cy){var g=S.graph,ns=Math.max(.3,Math.min(2.8,g.scale*f));if(cx==null){var b=el('ciSvg').getBoundingClientRect();cx=b.width/2;cy=b.height/2;g.manual=true;}g.tx=cx-(cx-g.tx)*ns/g.scale;g.ty=cy-(cy-g.ty)*ns/g.scale;g.scale=ns;setHint('');applyView();}
  // Bring a selected node into view (for example one chosen from the details panel that is outside the current view). With the details panel
  // over the graph (narrow workspace), the node is also kept clear of the panel. The zoom level is not changed.
  function ensureVisible(id){
    var svg=el('ciSvg'),side=el('ciGSide');if(!svg)return;
    var nd=null;document.querySelectorAll('.ci-node').forEach(function(x){if(x.getAttribute('data-ci-node')===id)nd=x;});if(!nd)return;
    var r=nd.getBoundingClientRect(),b=svg.getBoundingClientRect(),m=20,right=b.right,dx=0,dy=0;
    if(side&&side.classList.contains('open'))right=Math.min(right,side.getBoundingClientRect().left-12);
    if(r.left<b.left+m||r.right>right-m)dx=(b.left+right)/2-(r.left+r.right)/2;
    if(r.top<b.top+m||r.bottom>b.bottom-m)dy=(b.top+b.bottom)/2-(r.top+r.bottom)/2;
    if(dx||dy){S.graph.tx+=dx;S.graph.ty+=dy;applyView();}
  }
  function neighborhood(start,links,depth){
    var adj={};links.forEach(function(l){(adj[l.source]=adj[l.source]||[]).push(l.target);(adj[l.target]=adj[l.target]||[]).push(l.source);});
    var seen={},ls={},queue=[[start,0]];seen[start]=true;            // visited set: a cycle cannot re-enter a node
    while(queue.length){var cur=queue.shift();if(cur[1]>=depth)continue;(adj[cur[0]]||[]).forEach(function(n){ls[cur[0]+'>'+n]=true;ls[n+'>'+cur[0]]=true;if(!seen[n]){seen[n]=true;queue.push([n,cur[1]+1]);}});}
    return {nodes:seen,links:ls};
  }
  function gnode(id){return snap().graph.nodes.filter(function(x){return x.id===id;})[0];}
  function btnNode(id,text){return '<button type="button" class="ci-link-btn" data-ci-node-go="'+esc(id)+'">'+esc(text)+'</button>';}
  function field(label,html){return html?'<div class="ci-gf"><span>'+esc(label)+'</span><div>'+html+'</div></div>':'';}
  function evList(list){
    return (list||[]).length?list.map(function(e){return '<div class="ci-quote"><small>'+esc(e.ref+' '+(e.where||''))+'</small>'+esc(e.quote||'')+'</div>';}).join(''):'';
  }
  function scenarioPanel(n){
    var s=n.scenario||{},reqs=(n.requirements||[]).map(function(r){return btnNode(r.id,r.label);}).join('<br>'),
      refs=(s.references||[]).join(', ');
    return field('Category',esc(s.category||'')+' <span class="muted-small">(a suggestion, not an existing test case; not created automatically)</span>')+
      field('Related requirement',reqs||(refs?esc(refs):'<span class="muted-small">None - linked through the system or change shown below</span>'))+
      field('Change ID',(s.change_ids||[]).map(function(id){return '<button type="button" class="ci-link-btn" data-ci-act="change" data-cid="'+esc(id)+'">'+esc(id)+'</button>';}).join(', '))+
      field('Priority / risk',(s.priority?pill(s.priority,s.priority):'')+' '+(s.risk?pill(s.risk,s.risk):''))+
      field('Execution type',esc((s.execution||[]).join(' / ')))+
      field('Expected result basis',s.basis==='PENDING_BA'?pill('SUGGESTED','Awaiting BA')+' <span class="muted-small">The document does not state the expected result.</span>':pill('CONFIRMED','Source-supported')+' <span class="muted-small">The document states the expected behaviour.</span>')+
      field('Modules',esc((s.modules||[]).join(', ')))+field('Why recommended',esc(s.reason||''))+field('Expected',esc(s.expected||''))+
      ((s.open_questions||[]).length?field('Open questions',esc(s.open_questions.length+' related open question(s) - see the Open Questions view')):'')+
      field('Supporting evidence',evList(s.evidence)||'<span class="muted-small">None recorded</span>');
  }
  function groupPanel(n){
    var g=snap().graph,mem=(n.members||[]).map(function(id){return gnode(id);}).filter(Boolean),open=!!(S.graph.exp||{})[n.id],counts=Object.keys(n.counts||{}).sort().map(function(k){return pill(k,k+': '+n.counts[k]);}).join(' ');
    return '<p>'+esc(n.detail||'')+'</p>'+field('Items',esc(mem.length+' distinct item(s)'))+field('Priorities',counts)+
      '<button type="button" class="ci-btn" data-ci-act="grp-toggle" data-gid="'+esc(n.id)+'">'+(open?'Collapse group':'Expand group')+'</button>'+
      '<b class="muted-small" style="display:block;margin-top:8px">Items in this group</b><ul style="margin:4px 0 8px;padding-left:16px">'+mem.map(function(m){return '<li>'+btnNode(m.id,m.label)+' <span class="muted-small">'+esc((m.priority||'')+' · '+(m.execution||[]).join(', '))+'</span></li>';}).join('')+'</ul>';
  }
  function selectNode(id,fromClick){
    var g=snap().graph,n=gnode(id);if(!n)return;
    if(!S.graph.exp)S.graph.exp={};
    if(n.group)S.graph.exp[n.group]=true;                                  // selecting an item always shows its group
    if(n.kind==='rec_group'&&fromClick)S.graph.exp[n.id]=!S.graph.exp[n.id];
    S.graph.sel=id;drawGraph();
    if(n.kind==='rec_group'||n.group)revealFocus();
    var rel=g.links.filter(function(l){return !l.contain&&(l.source===id||l.target===id)&&!(n.kind==='rec_group'&&l.group===id&&!l.aggregate);}).map(function(l){var other=l.source===id?l.target:l.source,on=gnode(other);return '<li>'+btnNode(other,on?on.label:other)+' <span class="muted-small">'+(l.source===id?'&rarr; ':'&larr; ')+esc(l.label)+(l.soft?' (unverified)':' (confirmed or evidence-backed)')+(l.basis?'<br>'+esc(l.basis):'')+'</span>'+((l.evidence||[]).length?'<div class="ci-quote"><small>'+esc(l.evidence.map(function(e){return e.ref+' '+(e.where||'');}).join('; '))+'</small>'+esc(String(l.evidence[0].quote||'').slice(0,200))+'</div>':'')+'</li>';}).join('');
    var body=n.kind==='new_test'||n.kind==='reg_test'?scenarioPanel(n):n.kind==='rec_group'?groupPanel(n):'<p>'+esc(n.detail||'')+'</p>'+((n.evidence||[]).length?field('Supporting evidence',evList(n.evidence)):'');
    el('ciGSide').innerHTML=sideTop()+'<b>'+esc(n.label)+'</b><div class="muted-small">'+esc(n.kind==='rec_group'?'Recommendation group':(COLN[fkind(n)]||n.kind))+'</div><div style="margin:6px 0">'+(n.assurance?assurancePill(n.assurance):'')+' '+(n.severity?pill(n.severity,n.severity):'')+'</div>'+body+
      '<b class="muted-small">Relationships</b><ul style="margin:4px 0 8px;padding-left:16px">'+(rel||'<li class="muted-small">None</li>')+'</ul>'+
      (n.finding_id?'<button type="button" class="ci-btn" data-ci-act="evidence" data-fid="'+esc(n.finding_id)+'">View Evidence</button>':(n.change_id?'<button type="button" class="ci-btn" data-ci-act="change" data-cid="'+esc(n.change_id)+'">View Change</button>':''));
    if(S.graph.narrow&&n.kind!=='rec_group'){S.graph.openNarrow=true;applyPanel();}   // over the graph, so opening a group must not cover the items it just showed
    ensureVisible(id);
  }

  // ------------------------------------------------------------------ modal, evidence, review, explanations
  function modal(){
    var m=el('ciModal');
    if(!m){m=document.createElement('div');m.className='modal-overlay';m.id='ciModal';m.innerHTML='<div class="modal-card ci-modal-card" role="dialog" aria-modal="true" aria-labelledby="ciModalTitle"><button type="button" class="modal-x" data-ci-act="close-modal" aria-label="Close">&times;</button><h3 id="ciModalTitle"></h3><div class="modal-sub" id="ciModalSub"></div><div class="ci-modal-body" id="ciModalBody"></div><div class="modal-actions"><button type="button" class="btn" data-ci-act="close-modal">Close</button></div></div>';document.body.appendChild(m);
      m.addEventListener('click',function(ev){if(ev.target===m){closeModal();return;}onClick(ev);});m.addEventListener('change',onChange);
      document.addEventListener('keydown',function(ev){if(ev.key==='Escape'&&m.classList.contains('show'))closeModal();});}
    return m;
  }
  function openModal(title,sub,html){var m=modal();el('ciModalTitle').textContent=title;el('ciModalSub').textContent=sub||'';el('ciModalBody').innerHTML=html;m.classList.add('show');var c=m.querySelector('.modal-x');if(c)c.focus();}
  function closeModal(){var m=el('ciModal');if(m)m.classList.remove('show');S.modalFid=null;}
  function explanationFor(type,id){var list=(S.result&&S.result.explanations)||[],hit=null;list.forEach(function(e){if(e.subject_type===type&&e.subject_id===id)hit=e;});return hit;}
  function explainBlock(type,id){
    var e=explanationFor(type,id),can=has('ai_assistant','view');
    return '<div class="ci-section"><h3>Explanation</h3>'+(e?'<div class="ci-quote"><small>'+(e.mode==='AI'?'AI-written explanation ('+esc(e.provider||'provider')+' '+esc(e.model||'')+') - pending review':'Rule-based explanation (no AI used)')+(e.insufficient_evidence?' - the model reported insufficient evidence':'')+'</small>'+esc(e.text)+(e.source_refs&&e.source_refs.length?'<small>Cites: '+esc(e.source_refs.join(', '))+'</small>':'<small>No document citations.</small>')+(e.missing_evidence&&e.missing_evidence.length?'<small>Missing evidence: '+esc(e.missing_evidence.join(' '))+'</small>':'')+(e.note?'<small>'+esc(e.note)+'</small>':'')+'</div>':'<p class="muted-small">No explanation yet.</p>')+
      (can?'<button type="button" class="ci-btn" data-ci-act="explain" data-type="'+type+'" data-id="'+esc(id)+'" id="ciExplainBtn">'+(e?'Regenerate explanation':'Explain why')+'</button>':'<p class="muted-small">Your role cannot request AI explanations.</p>')+'</div>';
  }
  function evidenceHtml(list){
    if(!list||!list.length)return '<p class="muted-small">No document quotation supports this finding. Treat it as unverified.</p>';
    return list.map(function(e){return '<div class="ci-quote"><small>'+esc(e.ref)+' &middot; '+esc(e.role||'')+' &middot; '+esc(e.document||'')+(e.document_version?' v'+esc(e.document_version):'')+(e.clause_ref?' &middot; '+esc(e.clause_ref):'')+(e.verified?' &middot; quotation verified in stored text':'')+'</small>'+esc(e.quote||'')+'</div>';}).join('');
  }
  function reviewBlock(f){
    var r=reviewOf(f.finding_id),d=depDecision(f.finding_id),canEdit=has('qa_engineering','edit'),dep=f.payload&&f.payload.dependency;
    var hist=(r&&r.history||[]).map(function(x){return '<li>'+esc(x.decision)+' by '+esc(x.by)+' on '+esc(when(x.at))+(x.comment?' - '+esc(x.comment):'')+'</li>';}).join('');
    return '<div class="ci-section"><h3>Human review</h3><p>Current state: <b class="ci-state-'+esc(r?r.state:'Pending')+'">'+esc(r?r.state:'Pending')+'</b>. Findings are never approved automatically.</p>'+(hist?'<ul class="muted-small">'+hist+'</ul>':'')+
      (canEdit?'<div class="ci-field-inline"><select id="ciDecision" aria-label="Decision"><option>Accepted</option><option>Rejected</option><option>Investigate</option></select><textarea id="ciComment" maxlength="2000" placeholder="Comment (required for Rejected and Investigate)"></textarea><button type="button" class="ci-btn" data-ci-act="review-save" data-fid="'+esc(f.finding_id)+'">Save review</button></div>':'<p class="muted-small">Your role can read but not review findings.</p>')+
      (dep?'<h3>Dependency</h3><p>'+(d?'Decision: <b>'+esc(d.decision)+'</b> by '+esc(d.by)+' on '+esc(when(d.at)):'This relationship is stated in the document but not yet verified.')+'</p>'+(canEdit?'<div class="ci-field-inline"><select id="ciDepDecision" aria-label="Dependency decision"><option>Confirmed</option><option>Rejected</option></select><textarea id="ciDepComment" maxlength="2000" placeholder="Why? (required)"></textarea><button type="button" class="ci-btn" data-ci-act="dep-save" data-fid="'+esc(f.finding_id)+'">Save dependency decision</button></div>':''):'')+'</div>';
  }
  function openEvidence(fid){
    var f=snap().findings.filter(function(x){return x.finding_id===fid;})[0];if(!f)return;
    S.modalFid=fid;var p=f.payload||{},skip={handoff:1,dependency:1},kv=Object.keys(p).filter(function(k){return !skip[k]&&p[k]!=null&&p[k]!==''&&typeof p[k]!=='object';}).map(function(k){return '<dt>'+esc(k.replace(/_/g,' '))+'</dt><dd>'+esc(p[k])+'</dd>';}).join('');
    var dep=p.dependency?'<dt>dependency</dt><dd>'+esc(p.dependency.source_system)+' &rarr; '+esc(p.dependency.target_system)+' ('+esc(p.dependency.relationship.replace(/_/g,' '))+')<br>"'+esc(p.dependency.sentence)+'"</dd>':'';
    openModal(f.title,f.kind+' finding &middot; '.replace('&middot;','-')+f.finding_id,'<div>'+assurancePill(effectiveAssurance(f))+' '+(f.severity?pill(f.severity,f.severity):'')+' <span class="muted-small">'+esc(f.verification_status)+'</span></div><dl class="ci-kv">'+kv+dep+'</dl><div class="ci-section"><h3>Evidence</h3>'+evidenceHtml(f.evidence)+'</div>'+explainBlock('finding',fid)+reviewBlock(f));
  }
  function openChange(cid){
    var s=snap(),c=s.changes.filter(function(x){return x.change_id===cid;})[0];if(!c)return;S.modalFid=null;var r=s.risk[cid],oq=((s.insight||{}).questions||[]).filter(function(q){return (c.open_question_ids||[]).indexOf(q.id)>=0;});
    var occ=(c.occurrences||[]).map(function(o){return '<li>'+esc((o.context||'')+(o.label?' - '+o.label:''))+' <span class="muted-small">'+esc(o.location||'')+(o.section?' &middot; '+esc(o.section):'')+'</span></li>';}).join('');
    openModal(c.reference||c.change_id,c.change_id,'<div>'+(isFunctional(c)?pill('CERT_CONF','Functional change'):pill('P3','Administrative change'))+' '+assurancePill(c.assurance)+' '+(r?pill(r.level,r.level):'')+' <span class="muted-small">'+esc(c.confidence||'')+' &middot; '+esc(c.status)+'</span></div><p>'+esc(c.description)+'</p>'+
      '<dl class="ci-kv"><dt>Requirement</dt><dd>'+esc(c.requirement||c.reference||'')+'</dd><dt>Change type</dt><dd>'+esc(c.change_type.replace('_',' ').toLowerCase())+' &middot; '+esc(c.category)+(c.category_keywords&&c.category_keywords.length?' <span class="muted-small">(matched: '+esc(c.category_keywords.join(', '))+')</span>':'')+'</dd>'+
      '<dt>Old behaviour</dt><dd>'+esc(c.old_behavior||c.original_text||c.original_note||'Not available')+'</dd><dt>New behaviour</dt><dd>'+esc(c.new_behavior||c.modified_text||'(removed)')+'</dd><dt>Source</dt><dd>'+esc(c.source_document)+' v'+esc(c.source_version)+'</dd></dl>'+
      '<p class="muted-small">Source = stated in the document. Derived = reasoned by the tool from the wording; confirm with a Business Analyst.</p>'+
      '<div class="ci-section"><h3>Business impact</h3>'+impactList(c.business_impact)+'</div><div class="ci-section"><h3>System impact</h3>'+impactList(c.system_impact)+'</div><div class="ci-section"><h3>Technical impact</h3>'+impactList(c.technical_impact)+'</div><div class="ci-section"><h3>QA impact</h3>'+impactList(c.qa_impact)+'</div>'+
      (oq.length?'<div class="ci-section"><h3>Open questions</h3><ul class="ci-ul">'+oq.map(function(q){return '<li>'+esc(q.question)+'</li>';}).join('')+'</ul></div>':'')+
      (occ?'<div class="ci-section"><h3>Where it appears ('+(c.occurrences||[]).length+')</h3><ul class="ci-ul">'+occ+'</ul></div>':'')+
      '<div class="ci-section"><h3>Evidence</h3>'+evidenceHtml(c.evidence)+'</div>'+explainBlock('change',cid)+(r?'<div class="ci-section"><h3>Risk</h3><p>'+esc(r.summary)+'</p></div>':''));
  }
  function refreshAfter(fid){return loadResult(S.header.id).then(function(){renderResults();if(fid)openEvidence(fid);});}
  function saveReview(fid){
    var decision=val('ciDecision'),comment=val('ciComment');
    api('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/review',{method:'POST',body:JSON.stringify({finding_id:fid,decision:decision,comment:comment})}).then(function(){toast('Review saved.');return refreshAfter(fid);}).catch(function(e){toast(e.message,true);});
  }
  function saveDependency(fid){
    api('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/dependencies',{method:'POST',body:JSON.stringify({finding_id:fid,decision:val('ciDepDecision'),comment:val('ciDepComment')})}).then(function(){toast('Dependency decision saved.');return refreshAfter(fid);}).catch(function(e){toast(e.message,true);});
  }
  function explain(type,id,btn){
    if(btn){btn.disabled=true;btn.textContent='Working…';}
    api('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/explain',{method:'POST',body:JSON.stringify({subject_type:type,subject_id:id,refresh:!!explanationFor(type,id)})}).then(function(e){
      var list=S.result.explanations=S.result.explanations||[];list.push(e);
      var m=el('ciModal');if(!m||!m.classList.contains('show'))return;      // the reader closed the dialog while the request ran: do not reopen it
      if(type==='finding')openEvidence(id);else if(type==='change')openChange(id);else openRisk(id);
    }).catch(function(e){toast(e.message,true);if(btn){btn.disabled=false;btn.textContent='Explain why';}});
  }
  function openRisk(cid){
    var s=snap(),c=s.changes.filter(function(x){return x.change_id===cid;})[0],r=s.risk[cid];if(!c||!r)return;
    openModal('Why '+r.level+' risk?',(c.reference||c.change_id),'<p>'+esc(r.summary)+'</p>'+r.factors.map(function(f){return '<div class="ci-factor '+(f.applied?'':'off')+'"><span>'+esc(f.label)+'</span><span class="ci-bar"><i style="width:'+Math.round(f.value*100)+'%"></i></span><span>'+f.value.toFixed(2)+'</span></div><div class="muted-small" style="margin:-3px 0 4px">'+esc(f.note)+(f.applied?'':' (not applied)')+'</div>';}).join('')+explainBlock('risk',cid)+'<p class="muted-small">'+esc(r.disclaimer)+'</p>');
  }
  function handoff(fid){
    api('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/gaps/'+encodeURIComponent(fid)+'/handoff',{method:'POST',body:'{}'}).then(function(r){
      if(services.openQaEngineering){services.openQaEngineering(r.handoff);toast('Opened QA Engineering with the evidence. Nothing was created.');}
      else toast('QA Engineering hand-over is not available in this build.',true);
    }).catch(function(e){toast(e.message,true);});
  }

  // ------------------------------------------------------------------ Regression tab
  function approvedSet(){var r=S.result&&S.result.review,set={};((r&&r.regression_approval_history)||[]).forEach(function(a){(a.test_case_ids||[]).forEach(function(i){set[i]=a;});});return set;}
  function regItems(){
    var s=snap();if(!s)return [];var f=S.rf,q=f.q.trim().toLowerCase(),ap=approvedSet();
    return s.regression.items.filter(function(i){
      if(f.priority&&i.priority!==f.priority)return false;
      if(f.risk&&i.risk!==f.risk)return false;
      if(f.cls&&i.classification!==f.cls)return false;
      if(f.exec&&i.execution_type!==f.exec)return false;
      if(f.approved==='yes'&&!ap[i.test_case_id])return false;
      if(f.approved==='no'&&ap[i.test_case_id])return false;
      if(q&&JSON.stringify([i.tc_number,i.title,i.selection_reason,i.requirement_refs,i.related_changes,i.module]).toLowerCase().indexOf(q)<0)return false;
      return true;});
  }
  function selectedIds(){return Object.keys(S.sel).filter(function(k){return S.sel[k];}).map(Number);}
  function renderRegression(){
    var p=el('ciPanel-regression');
    if(!snap()){p.innerHTML='<div class="ci-empty">No completed analysis is open. Start one from Change Source, or open one from History.</div>';return;}
    var reg=snap().regression,c=reg.counts,items=reg.items,canApprove=S.config&&S.config.can&&S.config.can.approve,ap=S.result.review&&S.result.review.regression_approval;
    var cls=unique(items.map(function(i){return i.classification;})),rf=S.rf;
    p.innerHTML=regScenarios(reg)+'<div class="ci-card"><h3>Existing test cases to run</h3><p class="ci-help">Existing QA Engineering test cases that have evidence of being affected. Recommended new scenarios above are not test cases yet.</p></div><div class="ci-kpis">'+['P0','P1','P2','P3'].map(function(k){return '<button type="button" class="ci-kpi" data-ci-rpri="'+k+'" aria-label="Show '+k+' tests"><b>'+c[k]+'</b><span>'+k+' - '+esc(((reg.rules||{})[k]||'').split('.')[0].slice(0,46))+'</span></button>';}).join('')+'</div>'+
      '<div class="ci-note">'+esc(reg.coverage_statement)+(reg.truncated?' The list was capped at the configured maximum.':'')+'</div>'+
      (ap?'<div class="ci-note">Regression approved by <b>'+esc(ap.by)+'</b> on '+esc(when(ap.at))+' ('+ap.test_case_ids.length+' test(s))'+(ap.comment?': '+esc(ap.comment):'')+'. Later approvals are added to the audit trail, never overwritten.</div>':'<div class="ci-note warn">The regression selection has not been approved. Nothing is selected by default.</div>')+
      '<div class="ci-filters"><input type="search" id="ciRQ" placeholder="Search tests, requirements, reasons…" value="'+esc(rf.q)+'" aria-label="Search regression tests">'+
        '<select id="ciRPriority" aria-label="Priority">'+options(['P0','P1','P2','P3'],'All priorities',rf.priority)+'</select>'+
        '<select id="ciRRisk" aria-label="Risk">'+options(['Critical','High','Medium','Low'],'All risk levels',rf.risk)+'</select>'+
        '<select id="ciRCls" aria-label="Classification">'+options(cls,'All classes',rf.cls)+'</select>'+
        '<select id="ciRExec" aria-label="Execution type">'+options(['Automated','Manual'],'Manual and automated',rf.exec)+'</select>'+
        '<select id="ciRApproved" aria-label="Approval">'+options([{value:'yes',label:'Approved'},{value:'no',label:'Not approved'}],'Any approval state',rf.approved)+'</select>'+
        '<button type="button" class="ci-btn" data-ci-act="reg-clear-filters">Clear</button></div>'+
      '<div class="ci-row" style="margin:6px 0"><button type="button" class="ci-btn" data-ci-act="reg-select-visible">Select visible</button><button type="button" class="ci-btn" data-ci-act="reg-select-recommended">Select recommended</button><button type="button" class="ci-btn" data-ci-act="reg-clear">Clear selection</button>'+
        '<button type="button" class="ci-btn" data-ci-act="reg-csv">Export selection (CSV)</button><span class="muted-small" id="ciRegCount"></span></div>'+
      '<div id="ciRegBody"></div>'+
      (canApprove?'<div class="ci-card" style="margin-top:8px"><h3>Approve regression</h3><div class="ci-field-inline"><textarea id="ciRegComment" maxlength="2000" placeholder="Approval note (optional): scope, exclusions, who must run it"></textarea><button type="button" class="btn btn-primary" id="ciRegApprove" data-ci-act="reg-approve">Approve selected tests</button></div><div class="ci-error" id="ciRegError"></div></div>':'<div class="ci-note">Your role can view the recommendation but cannot approve it.</div>');
    renderRegTable();
  }
  function scenarioRows(list){
    return list.map(function(x){
      return '<tr><td>'+pill(x.priority,x.priority)+'</td><td><b>'+esc(x.title)+'</b><div class="muted-small">'+esc(x.category||'')+(x.modules&&x.modules.length?' &middot; '+esc(x.modules.join(', ')):'')+'</div></td><td>'+esc(x.reason)+(x.expected?'<div class="muted-small">Expected: '+esc(x.expected)+'</div>':'')+'</td>'+
        '<td>'+esc((x.related_references||[]).join(', '))+'<div class="muted-small">'+esc((x.related_changes||[]).join(', '))+'</div></td><td>'+(x.risk?pill(x.risk,x.risk):'')+'</td><td>'+esc((x.execution||[]).join(' / '))+'</td>'+
        '<td>'+(x.basis==='PENDING_BA'?pill('SUGGESTED','Awaiting BA'):pill('CONFIRMED','Source-supported'))+((x.open_questions||[]).length?'<div class="muted-small">'+x.open_questions.length+' open question(s)</div>':'')+'</td><td>'+esc((x.evidence||[]).map(function(e){return e.ref;}).join(', '))+'</td></tr>';});
  }
  function regScenarios(reg){
    var sc=reg.scenarios||[],smoke=reg.smoke||[],cand=reg.automation_candidates||[],cat=reg.categories||{},gaps=findings('GAP');
    var cols=[['Pri.','44px'],['Scenario',''],['Reason',''],['Related change','120px'],['Risk','62px'],['Suggested execution','100px'],['Basis','110px'],['Evidence','90px']];
    var head='<div class="ci-card"><h3>Recommended regression</h3><div class="ci-kpis">'+[['Recommended regression items',(cat.new_scenarios||0)+(cat.existing_regression||0)+(cat.integration||0)],['Recommended new scenarios',cat.new_scenarios],['Integration regression',cat.integration],['Existing-behaviour regression',cat.existing_regression],['Automation candidates',cat.automation_candidates],['Coverage gaps',cat.coverage_gaps]].map(function(k){return '<div class="ci-kpi"><b>'+(k[1]==null?0:k[1])+'</b><span>'+esc(k[0])+'</span></div>';}).join('')+'</div>'+
      '<p class="ci-help">Scenarios are recommendations derived from the changed wording. They are not test cases and nothing is created automatically. Awaiting BA means the expected result depends on an open question.</p>';
    if(!sc.length&&!smoke.length)return head+'<div class="ci-empty">No new regression scenarios were derived. '+(gaps.length?'Coverage gaps are listed in the Coverage Gaps view.':'This is expected when only administrative information changed.')+'</div></div>';
    return head+(sc.length?'<h3 style="margin-top:8px">New scenarios</h3>'+table(cols,scenarioRows(sc)):'')+(smoke.length?'<h3 style="margin-top:8px">Integration and existing-behaviour checks</h3>'+table(cols,scenarioRows(smoke)):'')+
      (cand.length?'<h3 style="margin-top:8px">Automation candidates</h3>'+table([['Pri.','44px'],['Scenario',''],['Suggested type','120px'],['Reason','']],cand.map(function(x){return '<tr><td>'+pill(x.priority,x.priority)+'</td><td>'+esc(x.title)+'</td><td>'+esc((x.kinds||[]).join(', '))+'</td><td>'+esc(x.reason)+'</td></tr>';})):'')+'</div>';
  }
  function items_empty(){return snap().regression.items.length?'No tests match the current filters.':'No existing test case has evidence of being affected by these changes, so none is selected. See the recommended scenarios above and the Coverage Gaps view.';}
  function renderRegTable(){
    var box=el('ciRegBody');if(!box)return;
    var rows=regItems(),ap=approvedSet();
    box.innerHTML=table([['','30px'],['Pri.','44px'],['Test','78px'],['Title',''],['Requirement','80px'],['Risk','62px'],['Class','100px'],['Execution','86px'],['Previous','70px'],['Why selected',''],['Approval','80px']],rows.map(function(i){
      var a=ap[i.test_case_id];
      return '<tr class="'+(S.sel[i.test_case_id]?'sel':'')+'"><td><input type="checkbox" class="ci-rsel" data-tc="'+i.test_case_id+'"'+(S.sel[i.test_case_id]?' checked':'')+' aria-label="Select '+esc(i.tc_number||i.test_case_id)+'"></td><td>'+pill(i.priority,i.priority)+'</td><td><b>'+esc(i.tc_number||i.test_case_id)+'</b></td><td>'+esc(i.title)+'<div class="muted-small">'+esc(i.module||'')+'</div></td><td>'+esc((i.requirement_refs||[]).join(', '))+'</td><td>'+pill(i.risk,i.risk)+'</td><td>'+esc(i.classification)+'<div class="muted-small">'+esc(i.link_status.replace(/_/g,' ').toLowerCase())+'</div></td><td>'+esc(i.execution_type)+'<div class="muted-small">'+esc(i.automation)+'</div></td><td>'+esc(i.previous_result)+'</td><td>'+esc(i.selection_reason)+(i.recommended?'':'<div class="muted-small">Not recommended by the rules.</div>')+'</td><td>'+(a?'<span class="ci-state-Accepted">Approved</span><div class="muted-small">'+esc(a.by)+'</div>':'<span class="muted-small">Not approved</span>')+'</td></tr>';}),
      items_empty());
    var n=selectedIds().length,count=el('ciRegCount');if(count)count.textContent=n+' selected of '+snap().regression.items.length;
    var b=el('ciRegApprove');if(b)b.disabled=!n;
  }
  function regSelect(mode){
    var items=mode==='visible'?regItems():snap().regression.items.filter(function(i){return i.recommended;});
    items.forEach(function(i){S.sel[i.test_case_id]=true;});renderRegTable();
  }
  function regCsv(){
    var ids=selectedIds(),items=snap().regression.items.filter(function(i){return ids.length?S.sel[i.test_case_id]:true;}),ap=approvedSet();
    var head=['Priority','Test ID','Title','Requirement','Risk','Classification','Execution','Automation','Previous result','Related changes','Selection reason','Approval'];
    var lines=[head.map(csvCell).join(',')].concat(items.map(function(i){return [i.priority,i.tc_number||i.test_case_id,i.title,(i.requirement_refs||[]).join('; '),i.risk,i.classification,i.execution_type,i.automation,i.previous_result,(i.related_changes||[]).join('; '),i.selection_reason,ap[i.test_case_id]?'Approved by '+ap[i.test_case_id].by:'Not approved'].map(csvCell).join(',');}));
    download(new Blob(['﻿'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}),'Regression_'+S.header.id+'.csv');
    toast((ids.length?'Exported the selected ':'Exported all ')+items.length+' test(s).');
  }
  function approveRegression(){
    var ids=selectedIds();if(!ids.length||S.busy)return;S.busy=true;var b=el('ciRegApprove');if(b)b.disabled=true;setError('ciRegError','');
    api('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/regression/approve',{method:'POST',body:JSON.stringify({test_case_ids:ids,comment:val('ciRegComment')})}).then(function(r){
      toast(r.approved+' test(s) approved for regression.');S.sel={};return loadResult(S.header.id).then(renderRegression);
    }).catch(function(e){setError('ciRegError',e.message);}).finally(function(){S.busy=false;var x=el('ciRegApprove');if(x)x.disabled=!selectedIds().length;});
  }

  // ------------------------------------------------------------------ Report tab
  function renderReport(){
    var p=el('ciPanel-report');
    if(!S.header||S.header.status!=='Completed'){p.innerHTML='<div class="ci-empty">No completed analysis is open. Start one from Change Source, or open one from History.</div>';return;}
    if(!S.report){
      p.innerHTML='<div class="ci-empty">Building the report from the stored analysis…</div>';
      var id=S.header.id;
      api('/api/change-impact/analyses/'+encodeURIComponent(id)+'/report').then(function(r){if(S.header&&S.header.id===id){S.report=r;if(S.tab==='report')renderReport();}}).catch(function(e){p.innerHTML='<div class="ci-error show">'+esc(e.message)+'</div>';});
      return;
    }
    var r=S.report;
    p.innerHTML='<div class="ci-row" style="justify-content:space-between"><div><b>'+esc(r.title)+'</b><div class="muted-small">'+esc(r.analysis_id)+' &middot; created '+esc(when(r.created_at))+' by '+esc(r.created_by)+' &middot; snapshot '+esc(String(r.snapshot_hash||'').slice(0,12))+'</div></div>'+
      '<div class="ci-row"><button type="button" class="ci-btn" data-ci-act="export-xlsx">Export Excel</button><button type="button" class="ci-btn" data-ci-act="export-pdf">Export PDF</button></div></div>'+
      '<div class="ci-note">The report is built from the frozen analysis snapshot plus the review log, so the same analysis always produces the same findings. The Excel and PDF files contain these same sections.</div>'+
      r.sections.map(function(sec,idx){
        var body=(sec.paragraphs||[]).map(function(t){return '<p>'+esc(t)+'</p>';}).join('')+(sec.columns&&sec.columns.length?(sec.rows.length?'<div class="ci-tw"><table><thead><tr>'+sec.columns.map(function(c){return '<th>'+esc(c)+'</th>';}).join('')+'</tr></thead><tbody>'+sec.rows.map(function(row){return '<tr>'+row.map(function(c){return '<td>'+esc(c)+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div>':'<p class="muted-small">None.</p>'):'');
        return '<details class="ci-card ci-rep" style="margin-top:8px"'+(idx<2?' open':'')+'><summary><b>'+esc(sec.heading||sec.title)+'</b> <span class="muted-small">'+(sec.rows&&sec.rows.length?sec.rows.length+' row(s)':'')+'</span></summary>'+body+'</details>';}).join('');
  }

  // ------------------------------------------------------------------ History tab
  function renderHistory(){
    var p=el('ciPanel-history');
    p.innerHTML='<div class="ci-filters"><input type="search" id="ciHQ" placeholder="Search title, ID or person…" aria-label="Search history" value="'+esc(S.hq||'')+'"><select id="ciHStatus" aria-label="Status">'+options(['Completed','Running','Queued','Failed','Cancelled'],'All statuses',S.hs||'')+'</select><label class="ci-row"><input type="checkbox" id="ciHMine"'+(S.hm?' checked':'')+'> Mine only</label><button type="button" class="ci-btn" data-ci-act="history-refresh">Refresh</button></div><div id="ciHistBody"><div class="ci-empty">Loading…</div></div>';
    loadHistory();
  }
  function loadHistory(){
    var q='?limit=200'+(S.hs?'&status='+encodeURIComponent(S.hs):'')+(S.hq?'&search='+encodeURIComponent(S.hq):'')+(S.hm?'&mine=true':'');
    api('/api/change-impact/analyses'+q).then(function(r){
      S.history=r.items||[];var box=el('ciHistBody');if(!box)return;
      box.innerHTML=table([['Analysis','150px'],['Mode','100px'],['Status','84px'],['Created',''],['Sources',''],['Results','170px'],['AI','100px'],['','150px']],S.history.map(function(h){
        var t=h.totals||{};
        return '<tr><td><b>'+esc(h.title)+'</b><div class="muted-small">'+esc(h.id)+'</div></td><td>'+(h.mode==='VERSION_COMPARE'?'Version comparison':'Single CRF')+'</td><td>'+esc(h.status)+'</td><td>'+esc(when(h.created_at))+'<div class="muted-small">'+esc(h.created_by_name)+'</div></td><td>'+esc((h.sources||[]).join(', '))+'</td>'+
          '<td>'+(h.status==='Completed'?esc((t.changes||0)+' changes'+(t.functional_changes!=null?' ('+t.functional_changes+' functional)':'')+', '+(t.high_risk_impacts||0)+' high risk, '+(t.existing_impacted_tests!=null?t.existing_impacted_tests:(t.test_cases||0))+' existing tests, '+(t.recommended_regression_scenarios!=null?t.recommended_regression_scenarios+' scenarios':(t.recommended_regression_tests||0)+' regression')):'')+'</td><td>'+esc(h.ai_status||'Not used')+(h.ai_provider?'<div class="muted-small">'+esc(h.ai_provider+' '+(h.ai_model||''))+'</div>':'')+'</td>'+
          '<td><div class="ci-acts"><button type="button" class="ci-btn" data-ci-act="history-open" data-id="'+esc(h.id)+'">Open</button><button type="button" class="ci-btn" data-ci-act="history-audit" data-id="'+esc(h.id)+'">Audit trail</button></div></td></tr>';}),'No analyses have been run yet.');
    }).catch(function(e){var box=el('ciHistBody');if(box)box.innerHTML='<div class="ci-error show">'+esc(e.message)+'</div>';});
  }
  function openHistory(id){
    stopPoll();
    api('/api/change-impact/analyses/'+encodeURIComponent(id)).then(function(h){
      S.header=h;S.result=null;S.report=null;S.sel={};S.graph=null;
      if(h.status==='Completed')return loadResult(id).then(function(){showTab('results');toast('Opened the stored result. It is exactly what was produced on '+when(h.completed_at)+'.');});
      showTab('analyze');if(h.status==='Queued'||h.status==='Running')beginPoll(id);
    }).catch(function(e){toast(e.message,true);});
  }
  function openAudit(id){
    api('/api/change-impact/analyses/'+encodeURIComponent(id)+'/events').then(function(r){
      var rows=(r.events||[]).map(function(e){return '<tr><td>'+esc(when(e.created_at))+'</td><td>'+esc(e.actor_name||'')+'</td><td>'+esc(e.action)+'</td><td>'+esc(JSON.stringify(e.detail||{}).slice(0,200))+'</td></tr>';});
      openModal('Audit trail',id,table([['When (UTC)','150px'],['Who','110px'],['Event','180px'],['Detail','']],rows,'No events recorded.'));
    }).catch(function(e){toast(e.message,true);});
  }

  // ------------------------------------------------------------------ events
  function firstPending(){
    var s=snap();if(!s)return null;
    var order=['REQUIREMENT','SYSTEM','TEST','AUTOMATION','GAP'],hit=null;
    order.forEach(function(k){if(!hit)hit=findings(k).filter(function(f){return reviewState(f.finding_id)==='Pending';})[0]||null;});
    return hit;
  }
  function onClick(ev){
    var t=ev.target,x;
    if((x=t.closest('[data-ci-tab]'))){showTab(x.getAttribute('data-ci-tab'));return;}
    if((x=t.closest('[data-ci-mode]'))){S._keep=saveFields();S.mode=x.getAttribute('data-ci-mode');renderSource();return;}
    if((x=t.closest('[data-ci-sub]'))){
      var sub=x.getAttribute('data-ci-sub');if(sub==='regression'){showTab('regression');return;}
      closeModal();if(S.tab!=='results')showTab('results');S.sub=sub;S.f.cert=x.getAttribute('data-ci-cert')||'';renderResults();return;}
    if((x=t.closest('[data-ci-rpri]'))){S.rf.priority=S.rf.priority===x.getAttribute('data-ci-rpri')?'':x.getAttribute('data-ci-rpri');renderRegression();return;}
    if((x=t.closest('[data-ci-node-go]'))){selectNode(x.getAttribute('data-ci-node-go'));return;}
    if((x=t.closest('[data-ci-node]'))){selectNode(x.getAttribute('data-ci-node'),true);return;}
    if((x=t.closest('[data-ci-act]'))){
      var a=x.getAttribute('data-ci-act'),d=function(n){return x.getAttribute('data-'+n);};
      switch(a){
        case 'close-modal':closeModal();break;
        case 'cancel':cancelAnalysis();break;
        case 'open-results':showTab('results');break;
        case 'evidence':openEvidence(d('fid'));break;
        case 'change':openChange(d('cid'));break;
        case 'review-save':saveReview(d('fid'));break;
        case 'dep-save':saveDependency(d('fid'));break;
        case 'explain':explain(d('type'),d('id'),x);break;
        case 'explain-risk':openRisk(d('cid'));break;
        case 'handoff':handoff(d('fid'));break;
        case 'export-xlsx':case 'export-pdf':{
          var fmt=a==='export-xlsx'?'xlsx':'pdf';x.disabled=true;
          authFetchBlob('/api/change-impact/analyses/'+encodeURIComponent(S.header.id)+'/report.'+fmt,'Change_Impact_'+S.header.id+'.'+fmt).then(function(){toast('Export ready.');}).catch(function(e){toast(e.message,true);}).finally(function(){x.disabled=false;});break;}
        case 'clear-filters':S.f={q:'',change:'',assurance:'',severity:'',review:'',cls:'',cert:''};renderResults();break;
        case 'review-pending':{
          var f=firstPending();if(!f){toast('Every finding has been reviewed.');break;}
          var sub2={REQUIREMENT:'requirements',SYSTEM:'systems',TEST:'tests',AUTOMATION:'automation',GAP:'gaps'}[f.kind];
          S.f.review='Pending';S.sub=sub2;renderResults();openEvidence(f.finding_id);break;}
        case 'zoom-in':zoom(1.2);break;
        case 'zoom-out':zoom(0.83);break;
        case 'zoom-fit':fitView(true);applyView();break;
        case 'zoom-reset':fitView(false);applyView();if(S.graph.sel)ensureVisible(S.graph.sel);break;
        case 'side-toggle':{var gs=S.graph;if(gs.narrow)gs.openNarrow=!gs.openNarrow;else gs.hidePanel=!gs.hidePanel;applyPanel();gLast='';drawGraph();if(!gs.manual)defaultView();applyView();if(gs.sel)ensureVisible(gs.sel);break;}
        case 'grp-toggle':{S.graph.exp[d('gid')]=!S.graph.exp[d('gid')];selectNode(d('gid'));break;}
        case 'reg-select-visible':regSelect('visible');break;
        case 'reg-select-recommended':regSelect('recommended');break;
        case 'reg-clear':S.sel={};renderRegTable();break;
        case 'reg-clear-filters':S.rf={q:'',priority:'',risk:'',cls:'',exec:'',approved:''};renderRegression();break;
        case 'reg-csv':regCsv();break;
        case 'reg-approve':approveRegression();break;
        case 'history-refresh':loadHistory();break;
        case 'history-open':openHistory(d('id'));break;
        case 'history-audit':openAudit(d('id'));break;
        default:break;}
      return;}
    if(t.id==='ciRun'){startAnalysis();}
  }
  function onChange(ev){
    var t=ev.target,id=t.id||'';
    if(t.classList&&t.classList.contains('ci-rsel')){S.sel[t.getAttribute('data-tc')]=t.checked;var tr=t.closest('tr');if(tr)tr.classList.toggle('sel',t.checked);var n=selectedIds().length,c=el('ciRegCount');if(c)c.textContent=n+' selected of '+snap().regression.items.length;var b=el('ciRegApprove');if(b)b.disabled=!n;return;}
    if(['ciDomain','ciModule','ciKnowledge','ciType','ciBaseVersion','ciChgVersion'].indexOf(id)>=0){S._keep=saveFields();fillScope();return;}
    if(id==='ciBaseDoc'||id==='ciChgDoc'){updateRun();return;}
    var fmap={ciFChange:'change',ciFAssurance:'assurance',ciFSeverity:'severity',ciFReview:'review',ciFCls:'cls',ciFCert:'cert'};
    if(fmap[id]){S.f[fmap[id]]=t.value;renderSub();return;}
    var rmap={ciRPriority:'priority',ciRRisk:'risk',ciRCls:'cls',ciRExec:'exec',ciRApproved:'approved'};
    if(rmap[id]){S.rf[rmap[id]]=t.value;renderRegTable();return;}
    if(id==='ciGExp'){snap().graph.nodes.forEach(function(n){if(n.kind==='rec_group')S.graph.exp[n.id]=t.checked;});drawGraph();revealFocus();applyView();return;}
    if(id==='ciGSoft'||id==='ciGSev'||(t.classList&&t.classList.contains('ci-gkind'))){drawGraph();return;}
    if(id==='ciHStatus'){S.hs=t.value;loadHistory();return;}
    if(id==='ciHMine'){S.hm=t.checked;loadHistory();return;}
  }
  var hTimer=null;
  function onInput(ev){
    var t=ev.target,id=t.id||'';
    if(id==='ciQ'){S.f.q=t.value;renderSub();return;}                 // renderSub leaves the search box alone, so focus is kept
    if(id==='ciRQ'){S.rf.q=t.value;renderRegTable();return;}
    if(id==='ciGQ'){drawGraph();return;}
    if(id==='ciHQ'){S.hq=t.value;clearTimeout(hTimer);hTimer=setTimeout(loadHistory,300);return;}
  }

  // ------------------------------------------------------------------ entry point
  function enter(){
    if(S.loaded){if(S.tab==='history')renderHistory();return Promise.resolve();}
    root.innerHTML='<div class="ci-empty">Loading Change Impact…</div>';
    return Promise.all([api('/api/change-impact/config'),api('/api/change-impact/sources')]).then(function(r){
      S.config=r[0];S.sources=(r[1]&&r[1].items)||[];S.loaded=true;S.sourceNotes=(r[1]&&r[1].notes)||[];build();
    }).catch(function(e){root.innerHTML='<div class="ci-error show">'+esc(e.message||'Change Impact could not be loaded.')+'</div><div class="ci-row" style="margin-top:8px"><button type="button" class="ci-btn" id="ciRetry">Try again</button></div>';var b=el('ciRetry');if(b)b.addEventListener('click',function(){S.loaded=false;enter();});});
  }
  window.changeImpactEnter=enter;
  return {enter:enter};
};
