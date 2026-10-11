const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(__dirname+'/../../AI-Web/Frontend/index.html','utf8');
const items=[['SRS','1.0'],['SRS','2.0'],['CRF','1.0'],['CRF','1.1']].map(([document_type,version])=>({domain:'D',module:'M',knowledge_name:'K',document_type,version}));
for(const prefix of ['acu','ssu','pwu']){
  const controls={};for(const field of ['Knowledge','DocumentType','Version'])controls[prefix+field]={value:'',disabled:false,values:[]};
  const scope={document:{getElementById:id=>controls[id]},uniqueVals:values=>[...new Set(values)],fillSelect:(control,values)=>{control.values=values;control.value='';}};
  scope[prefix+'Selected']=()=>({domain:'D',module:'M',knowledge_name:controls[prefix+'Knowledge'].value,document_type:controls[prefix+'DocumentType'].value,version:controls[prefix+'Version'].value});
  scope[prefix+'Items']=()=>items;scope[prefix+'RefreshSourceTypes']=()=>{};vm.createContext(scope);
  for(const kind of ['Knowledge','DocumentTypes','Versions']){
    const name=prefix+'Refresh'+kind;
    const match=html.match(new RegExp('^  function '+name+'\\(\\)\\{[\\s\\S]*?^  \\}','m'));assert(match,name);vm.runInContext(match[0],scope);
  }
  scope[prefix+'RefreshKnowledge']();controls[prefix+'Knowledge'].value='K';scope[prefix+'RefreshDocumentTypes']();
  assert.deepStrictEqual(controls[prefix+'DocumentType'].values,['SRS','CRF']);
  controls[prefix+'DocumentType'].value='CRF';scope[prefix+'RefreshVersions']();
  assert.deepStrictEqual(controls[prefix+'Version'].values,['1.0','1.1']);
  controls[prefix+'Version'].value='1.1';controls[prefix+'DocumentType'].value='SRS';scope[prefix+'RefreshVersions']();
  assert.strictEqual(controls[prefix+'Version'].value,'');assert.deepStrictEqual(controls[prefix+'Version'].values,['1.0','2.0']);
  for(const field of ['Knowledge','DocumentType','Version']){
    const handler=html.split('\n').find(line=>line.startsWith("  document.getElementById('"+prefix+field+"').addEventListener('change'"));
    const expected=field==='Knowledge'?'RefreshDocumentTypes':field==='DocumentType'?'RefreshVersions':'RefreshSourceTypes';assert(handler.includes(prefix+expected),handler);
  }
}
console.log('API/SQL/Playwright upload cascades PASS: type-owned versions, disabled state, reset and bindings.');
{
  const controls={};for(const field of ['Domain','Module','Knowledge','DocumentType','Version','SourcePicker'])controls['tcImport'+field]={value:'',disabled:false,values:[]};
  const scope={document:{getElementById:id=>controls[id]},tcImportAutomationType:'Playwright',autoScopesByType:{Playwright:{sources:items}},uniqueVals:values=>[...new Set(values)],fillSelect:(control,values)=>{control.values=values;control.value='';},setSourcePicker:(control,sources,disabled)=>{control.sources=sources;control.disabled=disabled;}};
  vm.createContext(scope);const match=html.match(/^  function refreshImportHierarchy\(level\)\{[\s\S]*?^  \}/m);assert(match);vm.runInContext(match[0],scope);
  controls.tcImportDomain.value='D';scope.refreshImportHierarchy('domain');controls.tcImportModule.value='M';scope.refreshImportHierarchy('module');controls.tcImportKnowledge.value='K';scope.refreshImportHierarchy('knowledge');
  assert.deepStrictEqual(controls.tcImportDocumentType.values,['SRS','CRF']);assert.strictEqual(controls.tcImportVersion.disabled,true);
  controls.tcImportDocumentType.value='CRF';scope.refreshImportHierarchy('document_type');assert.deepStrictEqual(controls.tcImportVersion.values,['1.0','1.1']);
  controls.tcImportVersion.value='1.1';scope.refreshImportHierarchy('version');assert.strictEqual(controls.tcImportSourcePicker.sources.length,1);
  controls.tcImportDocumentType.value='SRS';scope.refreshImportHierarchy('document_type');assert.strictEqual(controls.tcImportVersion.value,'');assert.deepStrictEqual(controls.tcImportVersion.values,['1.0','2.0']);assert.strictEqual(controls.tcImportSourcePicker.disabled,true);
  console.log('Automation workbook import cascade PASS: Document Type owns Version, stale sources cleared.');
}
