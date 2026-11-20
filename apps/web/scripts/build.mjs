// Build static assets and bind them to unchanged inputs. This is not acceptance.
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {lstat, readdir, readFile, writeFile, rename, unlink} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const output=path.join(root,'frontend-dist');
const digest=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
async function files(directory,prefix='',source=false){
  const result=[];
  for(const entry of await readdir(directory,{withFileTypes:true})){
    if(source&&(entry.name==='node_modules'||entry.name==='.git'||entry.name.endsWith('.tsbuildinfo')))continue;
    const name=prefix+entry.name;
    if(entry.isSymbolicLink())throw new Error('Build provenance refuses symlinks');
    if(entry.isDirectory())result.push(...await files(path.join(directory,entry.name),name+'/',source));
    else if(entry.isFile())result.push(name);
    else throw new Error('Build provenance requires regular files');
  }
  return result.sort();
}
const sourceFiles=async()=>[...(await files(path.join(root,'apps/web'),'',true)).map(name=>'apps/web/'+name),
  'package.json','package-lock.json','RELEASE'].sort();
const sources=await sourceFiles(),sourceSha256={};
for(const name of sources)sourceSha256[name]=await digest(path.join(root,name));
await unlink(path.join(output,'BUILD-PROVENANCE.json')).catch(error=>{if(error.code!=='ENOENT')throw error;});
const build=spawnSync(process.execPath,[path.join(root,'node_modules/vite/bin/vite.js'),'build','--config','apps/web/vite.config.ts'],{cwd:root,stdio:'inherit'});
if(build.error)throw build.error;
if(build.status!==0)throw new Error('Vite build failed');
if(JSON.stringify(sources)!==JSON.stringify(await sourceFiles()))throw new Error('Frontend source list changed during build');
for(const name of sources)if(sourceSha256[name]!==await digest(path.join(root,name)))throw new Error('Frontend source changed during build: '+name);
const artifacts=(await files(output)).filter(name=>name!=='BUILD-PROVENANCE.json');
if(!artifacts.includes('index.html')||!(await lstat(path.join(output,'index.html'))).isFile())throw new Error('Missing frontend entrypoint');
const artifactSha256={};
for(const name of artifacts)artifactSha256[name]=await digest(path.join(output,name));
const record={format:1,release:(await readFile(path.join(root,'RELEASE'),'utf8')).trim(),
  generatedAt:new Date().toISOString(),node:process.version,sourceSha256,artifactSha256,
  scope:'Static frontend build provenance only; no backend, browser or security acceptance claimed.'};
const target=path.join(output,'BUILD-PROVENANCE.json'),temporary=target+'.tmp-'+process.pid;
try{await writeFile(temporary,JSON.stringify(record,null,2)+'\n',{flag:'wx'});await rename(temporary,target);}
finally{await unlink(temporary).catch(error=>{if(error.code!=='ENOENT')throw error;});}
