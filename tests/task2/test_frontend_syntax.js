const fs=require('fs'),path=require('path'),vm=require('vm');
const html=fs.readFileSync(path.join(__dirname,'../../AI-Web/Frontend/index.html'),'utf8');
let scripts=0;
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)){
  if(match[1].trim()){new vm.Script(match[1]);scripts++;}
}
// Inspect markup only: JavaScript templates and data-*-id are not DOM IDs.
const markup=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const ids=Array.from(markup.matchAll(/\sid="([^"]+)"/g),x=>x[1]);
const duplicates=ids.filter((id,index)=>ids.indexOf(id)!==index);
if(duplicates.length)throw new Error('Duplicate DOM IDs: '+duplicates.join(', '));
console.log('Isolated frontend syntax PASS:',scripts,'scripts; duplicate IDs:',duplicates.length);
