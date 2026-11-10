(function () {
  'use strict';
  window.installApiDiscovery = function (app) {
    var root = document.getElementById('auto-api-discovery');
    if (!root || root.dataset.installed) return;
    root.dataset.installed = 'true';
    root.innerHTML = '<div class="card"><div class="card-head"><h3>AI API Discovery</h3></div><div class="card-body">' +
      '<p>Record authorized business APIs. Safe samples are opt-in; sensitive values become references. API traffic stays on the approved target. Approve extra image/style/script origins explicitly when needed.</p>' +
      '<div class="filterbar"><label>Discovery name <input id="adName" maxlength="150" placeholder="Workflow discovery"></label>' +
      '<label>Recording mode <select id="adMode"><option value="Manual">Website Recording</option><option value="AI Agent">AI Agent (confirmed actions)</option></select></label>' +
      '<label id="adUrlLabel">Website URL <input id="adUrl" type="url" placeholder="https://authorized.example/"></label>' +
      '</div>' +
      '<label id="adGoalLabel" hidden>Describe the workflow <textarea id="adGoal" maxlength="4000" placeholder="Example: Open products, select a category, search for an item, and view its details."></textarea></label>' +
      '<div class="modal-actions"><button type="button" class="btn btn-primary" id="adStart">Import HAR</button>' +
      '<button type="button" class="btn" id="adPause" disabled>Pause capture</button><button type="button" class="btn" id="adResume" disabled>Resume capture</button>' +
      '<button type="button" class="btn" id="adStop" disabled>Stop &amp; preview</button><button type="button" class="btn" id="adRefresh">Refresh history</button></div>' +
      '<div id="adStatus" role="status" aria-live="polite"></div><div id="adCounts"></div><div id="adError" role="alert" style="color:var(--danger,#b42318);white-space:pre-wrap;"></div>' +
      '<h4>Discovery history</h4><select id="adHistory" aria-label="Discovery history"><option value="">Select a discovery</option></select>' +
      '<div class="modal-actions"><button type="button" class="btn" id="adAnalyze" disabled>Analyze with configured local AI</button>' +
      '<select id="adFormat" aria-label="Export format"><option value="json">Evidence JSON</option><option value="markdown">Documentation Markdown</option><option value="openapi">Partial OpenAPI</option><option value="python">Python client</option><option value="javascript">JavaScript client</option><option value="postman">Postman (review before API Automation import)</option><option value="xlsx">Excel report</option></select>' +
      '<button type="button" class="btn" id="adExport" disabled>Download</button>' +
      '<button type="button" class="btn" id="adDelete" disabled>Delete discovery history</button></div>' +
      '<div id="adDeleteConfirm" hidden><p>Delete this discovery and its captured evidence? Existing knowledge, test cases and automation assets will not be deleted.</p><button type="button" class="btn" id="adDeleteYes">Confirm delete</button><button type="button" class="btn" id="adDeleteNo">Cancel</button></div>' +
      '<p>Coverage concerns observed endpoints only. Safe request/response samples require explicit consent; oversized or unavailable samples are marked incomplete.</p>' +
      '<label>Filter endpoints <input id="adSearch" type="search"></label>' +
      '<div class="table-scroll"><table><thead><tr><th>Select</th><th>Method</th><th>Endpoint</th><th>Source</th><th>Authentication evidence</th><th>Statuses</th><th>Requests</th><th>Details</th></tr></thead><tbody id="adRows"></tbody></table></div>' +
      '<div id="adDetails" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;"></div>' +
      '<h4>AI suggestions (human review required)</h4><pre id="adAnalysis" style="white-space:pre-wrap;overflow-wrap:anywhere;"></pre>' +
      '<h4>Browser actions</h4><div id="adActions"></div>' +
      '<h4>Reviewed downstream outputs</h4><p>Select API rows, generate drafts, review/edit, then explicitly save. Nothing is executed or published automatically.</p>' +
      '<button type="button" class="btn" id="adSelectAll">Select visible APIs</button><button type="button" class="btn" id="adClear">Clear selection</button>' +
      '<button type="button" class="btn" id="adSelectEverything">Select All</button>' +
      '<button type="button" class="btn" id="adGenerate">Generate test case drafts</button>' +
      '<button type="button" class="btn" id="adCancelGeneration" disabled>Cancel generation</button>' +
      '<label>Canonical source destination <select id="adSource"><option value="">Load and select an existing Knowledge source</option></select></label><button type="button" class="btn" id="adSources">Load destinations</button>' +
      '<label>Review/edit canonical test case drafts (JSON) <textarea id="adDrafts" rows="10" style="width:100%;font-family:monospace;" aria-label="Reviewed test case drafts"></textarea></label>' +
      '<label><input type="checkbox" id="adCasesReviewed"> I reviewed these test cases and their source destination.</label>' +
      '<button type="button" class="btn" id="adSaveCases">Save canonical test cases</button>' +
      '<label>Saved batch <select id="adSavedBatch"><option value="">Select a saved batch</option></select></label>' +
      '<label><input type="checkbox" id="adTransferReviewed"> Transfer selected saved batch as editable API Automation drafts.</label>' +
      '<button type="button" class="btn" id="adTransfer">Send to API Automation</button><button type="button" class="btn" id="adOpenAutomation">Open API Automation</button>' +
      '<details><summary>Save reviewed API documentation to Knowledge Hub</summary>' +
      '<p>Select an existing source above to prefill its Domain/Module. The resulting document follows Domain → Module → Knowledge → Document Type → Version → Files.</p>' +
      '<label>Domain <input id="adKhDomain" readonly></label><label>Module <input id="adKhModule" readonly></label>' +
      '<label>Knowledge name <input id="adKhKnowledge" maxlength="300"></label>' +
      '<label>Document type <select id="adKhType"><option>Technical Document</option><option>Integration Document</option><option>Other</option></select></label>' +
      '<label>Version <input id="adKhVersion" value="1.0" maxlength="100"></label>' +
      '<label><input type="checkbox" id="adKnowledgeReviewed"> I reviewed the documentation and destination.</label>' +
      '<button type="button" class="btn" id="adSaveKnowledge">Confirm documentation save/index</button></details>' +
      '<pre id="adOutputs" style="white-space:pre-wrap;overflow-wrap:anywhere;"></pre>' +
      '</div></div>';
    function el(id) { return document.getElementById(id); }
    var current = null, timer = null, busy = false, selected = new Set(), sources = [], generationRequest = null;
    var prefix = '/api/api-discovery';
    function error(err) { el('adError').textContent = err ? (err.message || String(err)) : ''; }
    function status(text) { el('adStatus').textContent = text; }
    function can(action) { return app.hasPermission('automation', action); }
    function inspectApi(endpoint){
      var target=el('adDetails');target.replaceChildren();
      var heading=document.createElement('h4');heading.textContent=endpoint.method+' '+endpoint.endpoint;target.appendChild(heading);
      var contents={Request:{URL:endpoint.observed_url,Query:endpoint.query_sample||endpoint.query_parameters,Headers:endpoint.request_headers||endpoint.header_names,Body:endpoint.request_sample,Authentication:endpoint.authentication,Configuration:endpoint.requires_configuration?'REQUIRES CONFIGURATION':'Review approved request data'},Response:{Status:endpoint.status,Headers:endpoint.response_headers,Body:endpoint.response_sample,Schema:endpoint.response_schema,Duration_ms:endpoint.duration_ms},Evidence:{Timestamp:endpoint.captured_at,Page:endpoint.source_page,Source:endpoint.traffic_classification||endpoint.source,Occurrences:endpoint.occurrences,Network_failure:endpoint.network_error,Truncated:endpoint.response_truncated,Notice:endpoint.sample_notice,Limitations:endpoint.limitations,Blocked_resources:endpoint.blocked_resource_origins}};
      var area=document.createElement('div');
      var assertions=document.createElement('button');assertions.type='button';assertions.className='btn btn-sm';assertions.textContent='Assertions';assertions.onclick=function(){if(collectionBridge.openAssertions)collectionBridge.openAssertions(endpoint);};target.appendChild(assertions);
      Object.keys(contents).forEach(function(name){var button=document.createElement('button');button.type='button';button.className='btn btn-sm';button.textContent=name;button.onclick=function(){area.replaceChildren();Object.keys(contents[name]).forEach(function(key){var value=contents[name][key];if(value===undefined)return;var section=document.createElement('section'),title=document.createElement('strong'),pre=document.createElement('pre');title.textContent=key;pre.textContent=typeof value==='object'?JSON.stringify(value,null,2):String(value);section.append(title,pre);area.appendChild(section);});};target.appendChild(button);if(name==='Request')button.click();});target.appendChild(area);
      var advanced=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('pre');summary.textContent='Advanced evidence JSON';raw.textContent=JSON.stringify(endpoint,null,2);advanced.append(summary,raw);target.appendChild(advanced);
    }
    function active() { return current && ['starting', 'recording', 'paused','stopping'].includes(current.status); }
    function buttons() {
      el('adCounts').textContent='Captured: '+(current?current.captured_requests:0)+' | Unique: '+(current?current.endpoints.length:0)+' | Selected: '+selected.size+' | Saved: '+(collectionBridge&&collectionBridge.savedCount?collectionBridge.savedCount():0);
      el('adStart').disabled = busy || active() || !can('create');
      ['adMode','adName','adUrl','adGoal'].forEach(function (id) { el(id).disabled = busy || active(); });
      el('adStart').hidden = !!active();
      el('adPause').hidden = !current || current.status !== 'recording';
      el('adResume').hidden = !current || current.status !== 'paused';
      el('adStop').hidden = !current || !['recording','paused'].includes(current.status);
      el('adStart').textContent=current&&current.status==='failed'?'Retry Recording':current&&['completed','cancelled','interrupted'].includes(current.status)?'New Recording':'Start Recording';
      if(!active()&&el('adMode').value==='AI Agent')el('adStart').textContent='Analyze Workflow';
      el('adPause').disabled = busy || !current || current.status !== 'recording' || !can('create');
      el('adResume').disabled = busy || !current || current.status !== 'paused' || !can('create');
      el('adStop').disabled = busy || !active() || !can('create');
      el('adAnalyze').disabled = busy || !current || active() || !current.endpoints.length || !can('create') || !app.hasPermission('ai_assistant','view');
      el('adExport').disabled = busy || !current;
      el('adDelete').disabled = busy || !current || active() || !can('edit');
      el('adGenerate').disabled = busy || !current || active() || !selected.size || !app.hasPermission('qa_engineering','create') || !app.hasPermission('ai_assistant','view');
      el('adSaveCases').disabled = busy || !current || active() || !app.hasPermission('qa_engineering','create') || !app.hasPermission('knowledge','view');
      el('adTransfer').disabled = busy || !current || active() || !can('create');
      el('adSaveKnowledge').disabled = busy || !current || active() || !app.hasPermission('knowledge','create');
    }
    function mode() {
      el('adGoalLabel').hidden = el('adMode').value !== 'AI Agent';
      el('adStart').textContent = 'Start Recording';
      buttons();
    }
    async function raw(path, options) {
      var response = await fetch(app.apiBase + path, Object.assign({}, options, {headers: Object.assign({}, options.headers || {}, {'Authorization': 'Bearer ' + app.token()})}));
      if (!response.ok) {
        var body = response.headers.get('content-type') || '';
        var data = body.includes('json') ? await response.json().catch(function () { return {}; }) : null;
        throw new Error(data && typeof data.detail === 'string' ? data.detail : 'Request failed (HTTP ' + response.status + '). No success is claimed.');
      }
      return response;
    }
    async function history() {
      var result = await app.apiFetch(prefix + '/sessions');
      var selected = current && current.id;
      el('adHistory').replaceChildren(new Option('Select a discovery', ''));
      result.sessions.forEach(function (item) { el('adHistory').add(new Option(item.name + ' — ' + item.mode + ' — ' + item.status, item.id)); });
      el('adHistory').value = selected || '';
    }
    function render() {
      var rows = el('adRows'); rows.replaceChildren();
      if (!current) { buttons(); return; }
      if(collectionBridge.updated)collectionBridge.updated(current);
      status(current.status + ' · Captured requests: ' + current.captured_requests + ' · Unique endpoints: ' + current.endpoints.length);
      if (current.error) error(new Error(current.error));
      var search = el('adSearch').value.toLowerCase();
      if(!collectionBridge.renderGrid)current.endpoints.filter(function (endpoint) { return (endpoint.method + ' ' + endpoint.endpoint).toLowerCase().includes(search); }).forEach(function (endpoint) {
        var row = document.createElement('tr'), selectCell = document.createElement('td'), checkbox = document.createElement('input');
        checkbox.type='checkbox'; checkbox.checked=selected.has(endpoint.key); checkbox.setAttribute('aria-label','Select '+endpoint.method+' '+endpoint.endpoint);
        checkbox.onchange=function(){if(this.checked)selected.add(endpoint.key);else selected.delete(endpoint.key);buttons();};selectCell.appendChild(checkbox);row.appendChild(selectCell);
        [endpoint.method,endpoint.endpoint,endpoint.source,endpoint.authentication.join(', ') || 'Not observed',endpoint.observed_statuses.join(', '),endpoint.occurrences].forEach(function (value) { var cell = document.createElement('td'); cell.textContent = value; cell.style.overflowWrap = 'anywhere'; row.appendChild(cell); });
        var cell = document.createElement('td'), button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-sm'; button.textContent = 'View'; button.onclick = function () { inspectApi(endpoint); }; cell.appendChild(button); row.appendChild(cell); rows.appendChild(row);
      });
      el('adAnalysis').textContent = current.analysis && current.analysis.findings ? JSON.stringify(current.analysis,null,2) : 'No AI analysis has been performed.';
      el('adActions').replaceChildren();
      (current.actions || []).forEach(function(action){
        var item=document.createElement('div');item.textContent=(action.source==='AI Agent'?'Proposed action':'Observed action')+' · '+action.status+' · '+((action.reviewed_control||action.control||{}).name||action.kind||'')+' '+(action.reason||'');
        var choice=document.createElement('select');choice.setAttribute('aria-label','Observed action control');(action.alternatives||[]).forEach(function(control){choice.add(new Option(control.name||control.tag,control.id));});choice.value=(action.control||{}).id||'';if(action.status==='awaiting_confirmation')item.appendChild(choice);
        if(action.status==='awaiting_confirmation') ['Approve action','Reject action'].forEach(function(label,index){var button=document.createElement('button');button.type='button';button.className='btn btn-sm';button.textContent=label;button.disabled=busy||!can('create');button.onclick=function(){work(async function(){await app.apiFetch(prefix+'/sessions/'+current.id+'/agent/decision',{method:'POST',body:JSON.stringify({action_id:action.id,approved:index===0,control_id:choice.value||null})});await load(current.id);});};item.appendChild(button);});
        el('adActions').appendChild(item);
      });
      var batch=el('adSavedBatch').value;el('adSavedBatch').replaceChildren(new Option('Select a saved batch',''));
      (current.outputs||[]).filter(function(o){return o.kind==='test_cases'&&o.state==='completed';}).forEach(function(o){el('adSavedBatch').add(new Option('TC version '+o.result.version_number+' · '+o.result.test_case_ids.length+' cases',o.id));});el('adSavedBatch').value=batch;
      el('adOutputs').textContent=JSON.stringify(current.outputs||[],null,2);
      buttons();
      if(collectionBridge.renderGrid)collectionBridge.renderGrid();
    }
    async function load(id) {
      clearTimeout(timer);
      if(current&&current.id!==id){selected.clear();el('adDrafts').value='';el('adCasesReviewed').checked=false;el('adTransferReviewed').checked=false;el('adKnowledgeReviewed').checked=false;}
      current = await app.apiFetch(prefix + '/sessions/' + encodeURIComponent(id));
      render();
      if (active()) timer = setTimeout(function () { load(id).catch(error); },1500);
    }
    async function work(fn) {
      if (busy) return;
      busy = true; error(null); buttons();
      try { await fn(); } catch (err) { error(err); }
      finally { busy = false; buttons(); }
    }
    el('adMode').onchange = mode;
    el('adSelectEverything').onclick=function(){if(!current)return;current.endpoints.forEach(function(item){selected.add(item.key);});render();};
    el('adSearch').oninput = render;
    el('adStart').onclick = function () { work(async function () {
      var name = el('adName').value.trim();
      if (!name) throw new Error('Enter a discovery name.');
      if(collectionBridge.captureSamples&&collectionBridge.captureSamples()||collectionBridge.resourceOrigins&&collectionBridge.resourceOrigins().length){
        var contract=await app.apiFetch('/openapi.json',{cache:'no-store'});
        var operation=contract.paths&&contract.paths[prefix+'/sessions'];
        var requestSchema=operation&&operation.post&&operation.post.requestBody&&operation.post.requestBody.content['application/json'].schema;
        var captureContract=requestSchema&&requestSchema.$ref&&contract.components&&contract.components.schemas[requestSchema.$ref.split('/').pop()];
        if(!captureContract||!captureContract.properties||!captureContract.properties.resource_origins)throw new Error('The Web server is using an older capture engine. Safely restart the Web server before using safe response samples or approved resource origins.');
      }
        current = await app.apiFetch(prefix + '/sessions', {method:'POST', body:JSON.stringify({name:name,website:el('adUrl').value.trim(),authorized_target:true,mode:el('adMode').value,instructions:el('adGoal').value,environment:collectionBridge.environment?collectionBridge.environment():'',source_id:collectionBridge.sourceId?collectionBridge.sourceId():null,capture_samples:collectionBridge.captureSamples?collectionBridge.captureSamples():false,resource_origins:collectionBridge.resourceOrigins?collectionBridge.resourceOrigins():[]})});
      selected.clear();el('adDrafts').value='';el('adCasesReviewed').checked=false;el('adTransferReviewed').checked=false;el('adKnowledgeReviewed').checked=false;
      await history(); await load(current.id);
    }); };
    ['Pause','Resume','Stop'].forEach(function (action) { el('ad'+action).onclick = function () { work(async function () { await app.apiFetch(prefix+'/sessions/'+current.id+'/'+action.toLowerCase(),{method:'POST'}); await load(current.id); }); }; });
    el('adRefresh').onclick = function () { work(history); };
    el('adHistory').onchange = function () { if (this.value) work(function () { return load(el('adHistory').value); }); };
    el('adAnalyze').onclick = function () { work(async function () { status('Analyzing with the configured local AI provider…'); await app.apiFetch(prefix+'/sessions/'+current.id+'/analysis/run',{method:'POST',body:'{}'}); await load(current.id); }); };
    async function downloadSession(identity,format) {
      var response = await raw(prefix+'/sessions/'+identity+'/export/'+format,{});
      var blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = 'api-discovery-'+identity+'.'+({markdown:'md',python:'py',javascript:'js',openapi:'json',postman:'postman.json'}[format] || format); link.click(); setTimeout(function () { URL.revokeObjectURL(url); },1000);
    }
    el('adExport').onclick = function () { work(function(){return downloadSession(current.id,el('adFormat').value);}); };
    el('adDelete').onclick = function () { el('adDeleteConfirm').hidden = false; };
    el('adDeleteNo').onclick = function () { el('adDeleteConfirm').hidden = true; };
    el('adDeleteYes').onclick = function () { work(async function () { await app.apiFetch(prefix+'/sessions/'+current.id,{method:'DELETE'}); current=null; el('adDeleteConfirm').hidden=true; el('adRows').replaceChildren(); el('adDetails').textContent=''; el('adAnalysis').textContent=''; status('Discovery history deleted.'); await history(); }); };
    el('adSelectAll').onclick=function(){if(current)current.endpoints.filter(function(e){return(e.method+' '+e.endpoint).toLowerCase().includes(el('adSearch').value.toLowerCase());}).forEach(function(e){selected.add(e.key);});render();};
    el('adClear').onclick=function(){selected.clear();render();};
    function post(path,payload){return app.apiFetch(prefix+'/sessions/'+current.id+path,{method:'POST',body:JSON.stringify(payload)});}
    el('adGenerate').onclick=function(){work(async function(){generationRequest=crypto.randomUUID();el('adCancelGeneration').disabled=false;status('Generating grounded review drafts with the local model (bounded to five minutes).');try{var result=await post('/test-cases/generate',{keys:Array.from(selected),request_id:generationRequest});el('adDrafts').value=JSON.stringify(result.rows,null,2);el('adCasesReviewed').checked=false;status(result.count+' drafts generated; review and edit before canonical save.');}finally{generationRequest=null;el('adCancelGeneration').disabled=true;}});};
    el('adCancelGeneration').onclick=async function(){if(!generationRequest||!current)return;try{await post('/test-cases/cancel',{keys:Array.from(selected),request_id:generationRequest});status('Cancellation requested; no drafts will be approved or saved.');}catch(err){error(err);}};
    el('adSources').onclick=function(){work(async function(){var data=await app.apiFetch('/api/knowledge/items');sources=data.items;el('adSource').replaceChildren(new Option('Select an existing source',''));sources.forEach(function(item){el('adSource').add(new Option([item.domain,item.module,item.knowledge_name,item.document_type,item.version,item.file_name].filter(Boolean).join(' → '),String(item.id)));});});};
    el('adSource').onchange=function(){var source=sources.find(function(s){return String(s.id)===el('adSource').value;});el('adKhDomain').value=source?source.domain:'';el('adKhModule').value=source?source.module:'';el('adKhKnowledge').value=source?source.knowledge_name:'';el('adKhVersion').value=source?source.version:'1.0';el('adCasesReviewed').checked=false;el('adKnowledgeReviewed').checked=false;};
    el('adDrafts').oninput=function(){el('adCasesReviewed').checked=false;};
    el('adSaveCases').onclick=function(){work(async function(){if(!el('adCasesReviewed').checked||!el('adSource').value)throw new Error('Select a canonical source and confirm review.');var rows;try{rows=JSON.parse(el('adDrafts').value);}catch(err){throw new Error('Reviewed drafts must be valid JSON.');}await post('/test-cases/save',{reviewed:true,source_knowledge_id:Number(el('adSource').value),rows:rows});await load(current.id);status('Canonical test cases saved. Saved batch and workbook references are shown below.');});};
    el('adTransfer').onclick=function(){work(async function(){if(!el('adTransferReviewed').checked||!el('adSavedBatch').value)throw new Error('Choose a saved batch and confirm draft transfer.');await post('/automation/transfer',{reviewed:true,output_id:el('adSavedBatch').value});await load(current.id);status('Transferred as drafts. Configure and validate in the existing API Automation workspace before execution.');});};
    el('adOpenAutomation').onclick=function(){var tab=document.querySelector('[data-tab="auto-api"]');if(tab)tab.click();};
    el('adSaveKnowledge').onclick=function(){work(async function(){if(!el('adKnowledgeReviewed').checked)throw new Error('Review the documentation and destination first.');var result=await post('/knowledge/save',{reviewed:true,keys:Array.from(selected),domain:el('adKhDomain').value,module:el('adKhModule').value,knowledge_name:el('adKhKnowledge').value,document_type:el('adKhType').value,version:el('adKhVersion').value});await load(current.id);status(result.indexed?'Documentation saved and indexed.':'Documentation saved; indexing did not complete. Review processing status.');});};
    ['adKhKnowledge','adKhType','adKhVersion'].forEach(function(id){el(id).addEventListener('input',function(){el('adKnowledgeReviewed').checked=false;});});
    el('adSavedBatch').addEventListener('change',function(){el('adTransferReviewed').checked=false;});
    var reportHost=document.querySelector('[data-page-content="reports"] .tabstrip');
    if(reportHost){
      var reportButton=document.createElement('button');reportButton.type='button';reportButton.className='btn';reportButton.id='adReportsButton';reportButton.textContent='API Discovery report';reportHost.appendChild(reportButton);
      var reportModal=document.createElement('div');reportModal.className='modal-overlay';reportModal.id='adReportModal';reportModal.setAttribute('role','dialog');reportModal.setAttribute('aria-modal','true');reportModal.setAttribute('aria-labelledby','adReportTitle');
      reportModal.innerHTML='<div class="modal-card" style="max-width:900px;max-height:85vh;overflow:auto;"><h3 id="adReportTitle">API Discovery report</h3><p>Discovered endpoint coverage only; no complete application coverage is implied.</p><select id="adReportHistory" aria-label="Report discovery session"></select><pre id="adReportContent" style="white-space:pre-wrap;overflow-wrap:anywhere;"></pre><div class="modal-actions"><button type="button" class="btn" id="adReportDownload">Export Excel</button><button type="button" class="btn" id="adReportClose">Close</button></div></div>';
      document.body.appendChild(reportModal);
      async function showReport(){if(!el('adReportHistory').value){el('adReportContent').textContent='No discovery selected.';return;}var report=await app.apiFetch(prefix+'/sessions/'+el('adReportHistory').value+'/report/detail');el('adReportContent').textContent=JSON.stringify({coverage:report.coverage,test_cases:report.canonical_test_cases,execution_history:report.execution_history},null,2);}
      reportButton.onclick=function(){work(async function(){var data=await app.apiFetch(prefix+'/sessions');el('adReportHistory').replaceChildren(new Option('Select a discovery',''));data.sessions.forEach(function(s){el('adReportHistory').add(new Option(s.name,s.id));});if(current)el('adReportHistory').value=current.id;reportModal.classList.add('show');await showReport();});};
      el('adReportHistory').onchange=function(){showReport().catch(function(err){el('adReportContent').textContent=err.message;});};
      el('adReportClose').onclick=function(){reportModal.classList.remove('show');reportButton.focus();};
      el('adReportDownload').onclick=function(){if(el('adReportHistory').value)downloadSession(el('adReportHistory').value,'xlsx').catch(function(err){el('adReportContent').textContent=err.message;});};
    }
    // Load only when the user opens the workspace after login; no anonymous
    // startup call and no background polling when the workspace is hidden.
    var observer = new MutationObserver(function () {
      if (root.classList.contains('active') && app.token()) work(history);
      else clearTimeout(timer);
    });
    observer.observe(root,{attributes:true,attributeFilter:['class']});
    var collectionBridge={evidence:function(){return {identity:current&&current.id,name:current&&current.name,endpoints:current?current.endpoints.filter(function(item){return selected.has(item.key);}):[]};}};
    collectionBridge.loadSession=load;
    collectionBridge.select=function(keys){selected=new Set(keys);buttons();};
    collectionBridge.session=function(){return current;};
    mode();
    if(window.installDiscoveryCollections)window.installDiscoveryCollections(app,collectionBridge);
  };
}());
