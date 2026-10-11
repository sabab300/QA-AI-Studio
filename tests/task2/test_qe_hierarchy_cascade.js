// Execute actual saved cascade functions against a small control fixture.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(__dirname+'/../../AI-Web/Frontend/index.html','utf8');
const controls={};
for(const prefix of ['qe','qeImport'])for(const suffix of ['Domain','Module','Knowledge','DocumentType','Version','SourceType'])controls[prefix+suffix]={value:'',values:[]};
const items=[['SRS','1.0'],['SRS','2.0'],['CRF','1.0'],['CRF','1.1']].map(([document_type,version])=>({domain_id:1,module_id:1,domain:'D',module:'M',knowledge_name:'K',document_type,version}));
let refreshed=0;
const scope={document:{getElementById:id=>controls[id]},qeScopeItems:items,
  qeUnique:values=>[...new Set(values)],
  qeFill:(control,values)=>{control.values=values;control.value='';},
  qeScope:()=>({domain_id:1,module_id:1,domain:'D',module:'M',knowledge_name:controls.qeKnowledge.value,document_type:controls.qeDocumentType.value,version:controls.qeVersion.value}),
  qeSourceTypes:()=>{refreshed++;controls.qeSourceType.value='';},
  qeImportSourceTypes:()=>{refreshed++;controls.qeImportSourceType.value='';}
};vm.createContext(scope);
for(const name of ['qeKnowledge','qeDocumentTypes','qeVersions','qeImportKnowledge','qeImportDocumentTypes','qeImportVersions']){
  const line=html.split('\n').find(line=>line.startsWith('  function '+name+'('));assert(line,name);vm.runInContext(line,scope);
}
scope.qeKnowledge({knowledge_name:'K',document_type:'SRS',version:'2.0'});
assert.deepStrictEqual(controls.qeVersion.values,['1.0','2.0']);assert.strictEqual(controls.qeVersion.value,'2.0');
controls.qeDocumentType.value='CRF';controls.qeSourceType.value='stale';scope.qeVersions(null);
assert.deepStrictEqual(controls.qeVersion.values,['1.0','1.1']);assert.strictEqual(controls.qeVersion.value,'');assert.strictEqual(controls.qeSourceType.value,'');
controls.qeImportDomain.value='1';controls.qeImportModule.value='1';scope.qeImportKnowledge('K');
assert.deepStrictEqual(controls.qeImportDocumentType.values,['SRS','CRF']);
scope.qeImportDocumentTypes('CRF');scope.qeImportVersions('1.1');
assert.deepStrictEqual(controls.qeImportVersion.values,['1.0','1.1']);assert.strictEqual(controls.qeImportVersion.value,'1.1');
assert(refreshed>0);console.log('QA Engineering main/import cascades PASS: independent versions, restore, child reset.');
