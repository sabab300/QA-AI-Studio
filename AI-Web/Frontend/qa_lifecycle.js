/* Task 5-owned Reports extension. No AI/Settings/sidebar/engine modifications. */
window.installQaLifecycle=function(services){
  'use strict';
  var api=services.apiFetch,has=services.hasPermission,el=function(id){return document.getElementById(id);};
  var catalog=[],sources=[],requirements=[],assets=[],trace=null,busy=false,policy={review_transitions:{},defect_transitions:{}};
  var levels=[['t5Domain','domain'],['t5Module','module'],['t5Knowledge','knowledge_name'],['t5DocumentType','document_type'],['t5Version','version']];
  function esc(value){return String(value==null?'':value).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function fill(id,rows,label,value){var select=el(id),previous=select.value;select.innerHTML='<option value="">Select…</option>';rows.forEach(function(row){var option=document.createElement('option');option.value=String(value(row));option.textContent=label(row);option.title=label(row);select.appendChild(option);});if(rows.some(function(row){return String(value(row))===previous;}))select.value=previous;}
  function scope(){var result={};levels.forEach(function(pair){if(el(pair[0]).value)result[pair[1]]=el(pair[0]).value;});var row=catalog.find(function(item){return levels.every(function(pair){return item[pair[1]]===el(pair[0]).value;});});if(row&&row.document_version_id)result.document_version_id=row.document_version_id;return result;}
  function filters(){var result=scope();if(el('t5Type').value)result.execution_type=el('t5Type').value;if(el('t5Result').value)result.status=el('t5Result').value;if(el('t5From').value)result.date_from=el('t5From').value+'T00:00:00+00:00';if(el('t5To').value)result.date_to=el('t5To').value+'T23:59:59.999999+00:00';return result;}
  function error(message){el('t5Error').textContent=message||'';el('t5Error').classList.toggle('show',!!message);}
  function request(path,method,body){return api('/api/qa-lifecycle'+path,{method:method||'GET',body:body?JSON.stringify(body):undefined});}
  function controls(){
    var chosen=!!el('t5Case').value,req=!!el('t5Requirement').value,ready=trace&&trace.review.state==='Ready for Execution';
    var rules={t5Extract:has('qa_engineering','create')&&!!el('t5Source').value,t5Confirm:has('qa_engineering','edit')&&req,t5Link:has('qa_engineering','edit')&&req&&chosen,t5Review:has('qa_engineering','edit')&&chosen,t5Asset:has('automation','edit')&&chosen&&!!assets.length,t5Manual:has('automation','execute')&&chosen&&ready,t5CreateDefect:has('automation','edit')&&!!el('t5FailedRun').value,t5Transition:has('automation','edit')&&!!el('t5Defect').value,t5Retest:has('automation','execute')&&!!el('t5Defect').value&&!!el('t5RetestRun').value,t5Clickup:has('automation','edit')&&!!el('t5Defect').value,t5Automation:chosen};
    rules.t5Extract=rules.t5Extract&&!!scope().document_version_id;
    rules.t5Assign=has('automation','edit')&&!!el('t5Defect').value;
    rules.t5Review=rules.t5Review&&!!el('t5ReviewState').value&&!!el('t5ReviewComments').value.trim();
    rules.t5Manual=rules.t5Manual&&!!el('t5Environment').value.trim()&&!!el('t5Observation').value.trim();
    rules.t5CreateDefect=rules.t5CreateDefect&&!!el('t5DefectTitle').value.trim()&&!!el('t5DefectDescription').value.trim();
    rules.t5Transition=rules.t5Transition&&!!el('t5DefectState').value;
    rules.t5Asset=rules.t5Asset&&assets.some(function(row){return row.kind===el('t5AssetKind').value;})&&(el('t5AssetKind').value==='Manual'||!!el('t5AssetRef').value);
    Object.keys(rules).forEach(function(id){el(id).disabled=busy||!rules[id];});el('t5Refresh').disabled=busy;
  }
  function mutate(action){if(busy)return;busy=true;error('');controls();Promise.resolve().then(action).then(function(){return refresh();}).catch(function(e){error(e.message);}).finally(function(){busy=false;controls();});}
  function cascade(index){for(var next=index;next<levels.length;next++){var rows=catalog.filter(function(item){return levels.slice(0,next).every(function(pair){return !el(pair[0]).value||item[pair[1]]===el(pair[0]).value;});}),key=levels[next][1],values=Array.from(new Set(rows.map(function(item){return item[key];}).filter(Boolean))).sort();fill(levels[next][0],values,function(v){return v;},function(v){return v;});}trace=null;assets=[];['t5Case','t5Requirement','t5FailedRun','t5RetestRun','t5Defect','t5AssetRef'].forEach(function(id){fill(id,[],String,String);});return refresh();}
  function renderTrace(data){trace=data;el('t5Stage').style.display='block';el('t5Stage').textContent='Review: '+data.review.state+(data.review.stale_previous_decision?' — specification changed; previous decisions preserved':'')+'. Next: '+(data.review.state==='Ready for Execution'?'record execution and evidence':'complete review and approval');
    fill('t5ReviewState',policy.review_transitions[data.review.state]||[],String,String);
    var executions=data.executions.map(function(row){return Object.assign({},row,{result:JSON.parse(row.result_json)});});
    fill('t5FailedRun',executions.filter(function(row){return row.result.outcome==='Fail';}),function(row){return 'Run '+row.run_id+' · Fail';},function(row){return row.run_id;});
    fill('t5RetestRun',executions,function(row){return 'Run '+row.run_id+' · '+row.result.outcome;},function(row){return row.run_id;});
    fill('t5Defect',data.defects,function(row){return row.title+' · '+row.state;},function(row){return row.id;});
    defectChoices();
    el('t5Trace').innerHTML='<p><strong>'+esc(data.test_case.tc_number)+'</strong> · '+esc(data.owner.domain)+' → '+esc(data.owner.module)+' → '+esc(data.owner.knowledge_name)+' → '+esc(data.owner.document_type)+' → '+esc(data.owner.version)+'</p>'+
      '<p>'+data.requirements.length+' requirement links · '+data.assets.length+' asset versions · '+data.executions.length+' finalized executions · '+data.defects.length+' defects</p>'+
      '<ul>'+data.requirements.map(function(row){return '<li>'+esc(row.source_reference)+' — '+esc(row.link_status)+' — '+esc(row.description)+'</li>';}).join('')+'</ul>'+
      '<ul>'+executions.map(function(row){return '<li>Run '+row.run_id+' — '+esc(row.result.outcome)+' — '+esc(row.environment)+(row.result.asset_execution_verified?'':' — automated asset pin unverified')+'</li>';}).join('')+'</ul>'+
      '<ul>'+(data.dispatches||[]).map(function(row){var asset=JSON.parse(row.asset_identity_json);return '<li>Dispatch '+row.run_id+' — '+esc(asset.kind)+' — '+esc(asset.reference)+' — version '+esc(asset.version.slice(0,12))+' — '+esc(row.review_state)+' — '+row.asset_usages.length+' immutable asset usage(s)</li>';}).join('')+'</ul>'+
      '<ul>'+(data.recommendations||[]).map(function(row){return '<li>AI recommendation '+esc(row.status)+' — '+esc(row.rationale)+' — explicit review required; no automatic approval</li>';}).join('')+'</ul>'+
      '<ul>'+data.decisions.map(function(row){return '<li>'+esc(row.state)+' — '+esc(row.reviewer_name)+' — '+esc(row.created_at)+' — '+esc(row.comments)+'</li>';}).join('')+'</ul>'+
      '<ul>'+data.defects.map(function(row){return '<li>'+esc(row.title)+' — '+esc(row.state)+' — '+esc(row.assigned_name||'Unassigned')+' — '+row.retests.length+' retest attempts'+(row.clickup_id?' — existing ClickUp ID '+esc(row.clickup_id):'')+'</li>';}).join('')+'</ul>';
    controls();
  }
  function assetChoices(){var kind=el('t5AssetKind').value;fill('t5AssetRef',assets.filter(function(row){return row.kind===kind;}),function(row){return row.label;},function(row){return row.reference||'manual';});controls();}
  function defectChoices(){var item=trace&&trace.defects.find(function(row){return row.id===el('t5Defect').value;});fill('t5DefectState',item?policy.defect_transitions[item.state]||[]:[],String,String);if(item)el('t5Assignee').value=item.assigned_to?String(item.assigned_to):'';controls();}
  function loadTrace(){var id=el('t5Case').value;if(!id){trace=null;assets=[];['t5FailedRun','t5RetestRun','t5Defect','t5AssetRef'].forEach(function(key){fill(key,[],String,String);});el('t5Trace').textContent='Select a Test Case revision.';el('t5Stage').textContent='Select a Test Case to inspect its lifecycle stage.';controls();return Promise.resolve();}
    return request('/cases/'+id+'/trace').then(renderTrace).then(function(){if(!has('automation','view'))return;return request('/cases/'+id+'/asset-options').then(function(data){assets=data.assets||[];assetChoices();});});}
  function refresh(){error('');var selected=scope(),query=new URLSearchParams(filters());
    fill('t5Source',sources.filter(function(row){return levels.every(function(pair){return !selected[pair[1]]||row[pair[1]]===selected[pair[1]];});}),function(row){return row.file_name||row.knowledge_name;},function(row){return row.id;});
    var jobs=[request('/reports?'+query).then(function(data){el('t5Metrics').textContent='Confirmed requirements: '+data.requirement_coverage.confirmed+' · Covered: '+data.requirement_coverage.covered+' · Finalized runs: '+data.execution_summary.total+' · Pass: '+data.execution_summary.Pass+' · Fail: '+data.execution_summary.Fail;el('t5Facts').textContent=JSON.stringify(data,null,2);fill('t5Case',data.lifecycle_traceability,function(row){return row.tc_number+(row.revision_number?' · TC-V'+row.revision_number:' · legacy');},function(row){return row.test_case_id;});return loadTrace();})];
    if(selected.document_version_id&&has('qa_engineering','view'))jobs.push(request('/requirements?document_version_id='+selected.document_version_id).then(function(data){requirements=data.requirements;fill('t5Requirement',requirements,function(row){return row.source_reference+' · '+row.status+' · '+row.description.slice(0,80);},function(row){return row.id;});}));else{requirements=[];fill('t5Requirement',[],String,String);}
    return Promise.all(jobs).then(controls);
  }
  var startPromise=Promise.resolve();
  function start(){startPromise=Promise.all([api('/api/knowledge/hierarchy-catalog'),api('/api/knowledge/items'),request('/policy')]).then(function(data){catalog=data[0].items;policy=data[2];sources=data[1].items.filter(function(row){if(row.file_name||row.knowledge_name)return true;console.warn('Task5 source lacks display name, ID',row.id);return false;});if(has('automation','edit'))request('/actors').then(function(data){fill('t5Assignee',data.actors,function(row){return row.display_name;},function(row){return row.id;});}).catch(function(e){error(e.message);});return cascade(0);}).catch(function(e){error(e.message);});return startPromise;}
  document.querySelector('[data-tab="rep-lifecycle"]').addEventListener('click',function(){el('repSharedFilters').hidden=true;start();});
  levels.forEach(function(pair,index){el(pair[0]).addEventListener('change',function(){for(var n=index+1;n<levels.length;n++)el(levels[n][0]).value='';cascade(index+1).catch(function(e){error(e.message);});});});
  ['t5Type','t5Result','t5From','t5To'].forEach(function(id){el(id).addEventListener('change',function(){refresh().catch(function(e){error(e.message);});});});
  ['t5Source','t5Requirement','t5FailedRun','t5Defect','t5RetestRun'].forEach(function(id){el(id).addEventListener('change',controls);});
  el('t5Defect').addEventListener('change',defectChoices);
  ['t5ReviewState','t5ReviewComments','t5Environment','t5Observation','t5DefectTitle','t5DefectDescription','t5DefectState'].forEach(function(id){el(id).addEventListener('input',controls);el(id).addEventListener('change',controls);});
  el('t5Case').addEventListener('change',function(){loadTrace().catch(function(e){error(e.message);});});el('t5AssetKind').addEventListener('change',assetChoices);el('t5AssetRef').addEventListener('change',controls);el('t5Refresh').onclick=start;
  el('t5Extract').onclick=function(){mutate(function(){return request('/sources/'+el('t5Source').value+'/extract','POST');});};
  el('t5Confirm').onclick=function(){mutate(function(){return request('/requirements/'+el('t5Requirement').value+'/review','PATCH',{status:'Confirmed'});});};
  el('t5Link').onclick=function(){mutate(function(){return request('/requirements/'+el('t5Requirement').value+'/links','PUT',{test_case_id:Number(el('t5Case').value),status:'CONFIRMED',rationale:'User-reviewed source relationship'});});};
  el('t5Review').onclick=function(){mutate(function(){return request('/cases/'+el('t5Case').value+'/review','POST',{state:el('t5ReviewState').value,comments:el('t5ReviewComments').value});});};
  el('t5Asset').onclick=function(){mutate(function(){return request('/cases/'+el('t5Case').value+'/assets','POST',{kind:el('t5AssetKind').value,reference:el('t5AssetRef').value==='manual'?'':el('t5AssetRef').value});});};
  el('t5Manual').onclick=function(){mutate(function(){return request('/cases/'+el('t5Case').value+'/manual-results','POST',{outcome:el('t5ManualResult').value,environment:el('t5Environment').value,evidence:el('t5Evidence').value?[el('t5Evidence').value]:[],comments:el('t5Observation').value});});};
  el('t5CreateDefect').onclick=function(){mutate(function(){return request('/defects','POST',{run_id:Number(el('t5FailedRun').value),title:el('t5DefectTitle').value,description:el('t5DefectDescription').value,severity:el('t5Severity').value,priority:el('t5Priority').value});});};
  el('t5Transition').onclick=function(){mutate(function(){return request('/defects/'+el('t5Defect').value,'PATCH',{state:el('t5DefectState').value,resolution:el('t5Resolution').value});});};
  el('t5Assign').onclick=function(){mutate(function(){return request('/defects/'+el('t5Defect').value+'/assignment','PUT',{user_id:Number(el('t5Assignee').value)||null});});};
  el('t5Retest').onclick=function(){mutate(function(){return request('/defects/'+el('t5Defect').value+'/retests','POST',{run_id:Number(el('t5RetestRun').value)});});};
  el('t5Clickup').onclick=function(){mutate(function(){return request('/defects/'+el('t5Defect').value+'/clickup-link','PUT',{external_id:el('t5ClickupId').value});});};
  el('t5Automation').onclick=function(){document.querySelector('.nav-item[data-page="automation"]').click();services.toast('Select '+(trace&&trace.test_case.tc_number||'the chosen Test Case')+' in QA Automation. Existing execution controls are unchanged.');};
  // Read-only navigation helper used by the Automation execution notice: opens this page on the given Test Case
  // so a user with permission can review it. It selects nothing for approval and records no decision.
  window.qaLifecycleFocusCase=function(owner,caseId){
    var nav=document.querySelector('.nav-item[data-page="reports"]'),tab=document.querySelector('[data-tab="rep-lifecycle"]');
    if(nav)nav.click();if(tab)tab.click();
    return Promise.resolve(startPromise).then(function(){
      var chain=Promise.resolve();
      levels.forEach(function(pair,index){chain=chain.then(function(){if(!owner||!owner[pair[1]])return;el(pair[0]).value=String(owner[pair[1]]);return cascade(index+1);});});
      return chain;
    }).then(function(){
      var select=el('t5Case');
      if(Array.from(select.options).some(function(option){return option.value===String(caseId);})){select.value=String(caseId);return loadTrace();}
      error('Test Case was not found under its hierarchy filters; select it manually.');
    }).catch(function(e){error(e.message);});
  };
  controls();
};
