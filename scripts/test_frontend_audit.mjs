// Component logic checks with a deterministic hook/timer harness. Not browser E2E.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source=name=>fs.readFileSync(new URL('../apps/web/src/'+name,import.meta.url),'utf8');
const compile=name=>ts.transpileModule(source(name),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
function module(name,require,extras={}){const exports={};new Function('exports','require',...Object.keys(extras),compile(name))(exports,require,...Object.values(extras));return exports;}
const api=module('api.ts',()=>({}));
const originalFetch=globalThis.fetch;
try {
  for(const body of ['<html>unexpected</html>','null','42','"text"']){
    globalThis.fetch=async()=>new Response(body,{status:200});
    await assert.rejects(api.api('/test'),e=>e instanceof api.ApiError&&e.code==='INVALID_RESPONSE');
  }
  for(const value of [{success:true},[{id:'one'}]]){
    globalThis.fetch=async()=>new Response(JSON.stringify(value),{status:200});
    assert.deepEqual(await api.api('/test'),value);
  }
} finally {globalThis.fetch=originalFetch;}
const dates=module('form-utils.ts',()=>({}));
const dateFormat=module('date-format.ts',()=>({}));
assert.equal(dateFormat.formatPortalDate(new Date('2026-11-02T21:30:00Z'),'uz',true),'3-noyabr 2026-yil');
assert.equal(dateFormat.formatPortalDate(new Date('1994-11-03T12:00:00Z'),'uz',false),'3-noyabr');
assert.equal(dateFormat.formatPortalDate(new Date('invalid'),'uz',true),'—');
const locales=module('locale-preference.ts',()=>({}));
assert.equal(locales.readLocalePreference(()=>'ru'),'ru');
assert.equal(locales.readLocalePreference(()=>'en'),'en');
assert.equal(locales.chooseLocale('en','uz'),'en');
assert.equal(dateFormat.formatPortalDate(new Date('2026-11-02T21:30:00Z'),'en',true),'3 November 2026');
const english=module('i18n.en.ts',()=>({})).en;
const i18n=module('i18n.ts',name=>name==='./i18n.en'?{en:english}:{});
assert.deepEqual(Object.keys(english).sort(),Object.keys(i18n.ru).sort());
assert.ok(Object.values(english).every(value=>value.length>0&&!/[А-Яа-яЁё]/.test(value)));
assert.equal(i18n.errorText('INVALID_CREDENTIALS','en'),'Incorrect username or password.');
assert.equal(locales.readLocalePreference(()=>{throw new Error('storage denied');}),null);
assert.equal(locales.chooseLocale('ru','uz'),'ru','login/MFA must preserve an explicit browser preference');
assert.equal(locales.chooseLocale(null,'ru'),'ru','server preference is the fallback');
assert.equal(dates.toTashkentInput('2026-11-01T21:30:00Z'),'2026-11-02T02:30');
assert.equal(dates.toTashkentInput(null),'');
assert.equal(dates.toTashkentInput('invalid'),'');
assert.equal(dates.fromTashkentInput('2026-11-02T02:30','2026-11-01T21:30:23.123Z'),'2026-11-01T21:30:23.123Z');
assert.equal(dates.fromTashkentInput('','2026-11-01T21:30:00Z'),null);
function harness(apiCall,data=null){
  const slots=[],effects=[],timers=new Map();let cursor=0,id=0;
  const react={useState(initial){const n=cursor++;if(!(n in slots))slots[n]=initial;return [slots[n],v=>{slots[n]=typeof v==='function'?v(slots[n]):v;}];},useRef(initial){const n=cursor++;return slots[n]??(slots[n]={current:initial});},useId(){cursor++;return 'test-id';},useEffect(fn,deps){const n=cursor++;const old=slots[n];if(!old||deps.some((v,i)=>v!==old.deps[i])){old?.cleanup?.();slots[n]={deps};effects.push(()=>{slots[n].cleanup=fn();});}}};
  react.useCallback=fn=>fn;react.lazy=()=>()=>null;
  const window={confirm:()=>false,addEventListener(){},removeEventListener(){}};
  let components;
  const require=name=>name==='react'?react:name==='react/jsx-runtime'?{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}:name==='./api'?{...api,api:apiCall}:name==='./context'?{usePortal:()=>({t:k=>k,locale:'ru',open(){},onSaved(){}})}:name==='./i18n'?{errorText:k=>k}:name==='./ui'?components:name==='./date-format'?dateFormat:name==='./form-utils'?dates:name==='./hooks'?{useData:()=>({data,loading:false,error:null})}:name==='react-router-dom'?{useNavigate:()=>()=>{}}:new Proxy({},{get:(_,key)=>key});
  components=module('ui.tsx',require,{setTimeout:fn=>{timers.set(++id,fn);return id;},clearTimeout:n=>timers.delete(n),window,document:{activeElement:null}});
  const forms=module('forms.tsx',require);
  return {components,forms,window,render(fn,props){cursor=0;const tree=fn(props);while(effects.length)effects.shift()();return tree;},async tick(){const pending=[...timers.values()];timers.clear();for(const fn of pending)await fn();}};
}
function find(node,predicate){if(!node||typeof node!=='object')return; if(predicate(node))return node;for(const c of [node.props?.children].flat(Infinity)){const found=find(c,predicate);if(found)return found;}}
let resolveStatus,reset=0,ready=[];
const h=harness(async(path,method)=>method==='POST'?{id:'A',securityStatus:'QUARANTINED'}:new Promise(resolve=>{resolveStatus=resolve;}));
const props={purpose:'DOCUMENT',onReset:()=>reset++,onReady:f=>ready.push(f.id)};
let tree=h.render(h.components.UploadBox,props);
find(tree,n=>n.type==='input').props.onChange({target:{files:[new File(['a'],'A.pdf')],value:'A'}});
await new Promise(resolve=>setImmediate(resolve));
tree=h.render(h.components.UploadBox,props);assert.equal(reset,1);
const inFlight=h.tick();
find(tree,n=>n.type==='input').props.onChange({target:{files:[new File(['b'],'B.pdf')],value:'B'}});
resolveStatus({id:'A',securityStatus:'APPROVED'});await inFlight;
assert.deepEqual(ready,[],'old response must not approve replaced file');assert.equal(reset,2);
const failure=harness(async(path,method)=>{if(method==='POST')return {id:'F',securityStatus:'QUARANTINED'};throw new Error('offline');});
tree=failure.render(failure.components.UploadBox,props);
find(tree,n=>n.type==='input').props.onChange({target:{files:[new File(['x'],'x.pdf')],value:'x'}});
await new Promise(resolve=>setImmediate(resolve));failure.render(failure.components.UploadBox,props);await failure.tick();
tree=failure.render(failure.components.UploadBox,props);
assert.ok(find(tree,n=>n.type===failure.components.ErrorPanel&&typeof n.props.retry==='function'),'poll error needs retry');
const picker=harness(()=>{});let selected;
const pp={label:'Department',options:[{id:'1',name:'One'},{id:'2',name:'Two'}],value:'',required:true,onChange:v=>selected=v};
tree=picker.render(picker.components.Picker,pp);find(tree,n=>n.props?.role==='combobox').props.onFocus();
tree=picker.render(picker.components.Picker,pp);let input=find(tree,n=>n.props?.role==='combobox');
assert.equal(input.props['aria-label'],'Department');assert.ok(find(tree,n=>n.props?.id===input.props['aria-activedescendant']));
input.props.onKeyDown({key:'ArrowDown',preventDefault(){}});tree=picker.render(picker.components.Picker,pp);input=find(tree,n=>n.props?.role==='combobox');
input.props.onKeyDown({key:'Enter',preventDefault(){}});assert.equal(selected,'2');
const dialog=harness(()=>{});let closed=0;const dp={title:'Edit',protectChanges:true,onClose:()=>closed++};
tree=dialog.render(dialog.components.Dialog,dp);tree.props.onInputCapture();tree=dialog.render(dialog.components.Dialog,dp);
tree.props.onCancel({preventDefault(){}});assert.equal(closed,0);
tree=dialog.render(dialog.components.Dialog,dp);assert.ok(find(tree,n=>n.props?.role==='alertdialog'));find(tree,n=>n.props?.['data-keep-editing']).props.onClick();tree=dialog.render(dialog.components.Dialog,dp);assert.equal(closed,0);tree.props.onCancel({preventDefault(){}});tree=dialog.render(dialog.components.Dialog,dp);find(tree,n=>n.props?.['data-discard']).props.onClick();assert.equal(closed,1);
tree=dialog.render(dialog.components.Dialog,{...dp,busy:true});tree.props.onCancel({preventDefault(){}});assert.equal(closed,1);
const version=harness(()=>{}),vp={document:{id:'D',versions:[]}};
tree=version.render(version.forms.VersionForm,vp);
find(tree,n=>n.type===version.components.UploadBox).props.onReady({id:'A'});
tree=version.render(version.forms.VersionForm,vp);
assert.equal(find(tree,n=>n.type==='button'&&n.props.className==='button').props.disabled,false);
find(tree,n=>n.type===version.components.UploadBox).props.onReset();tree=version.render(version.forms.VersionForm,vp);
assert.equal(find(tree,n=>n.type==='button'&&n.props.className==='button').props.disabled,true);
const preview={id:'preview-A',mapping:{},errors:[],rows:[],headers:[],total:0};
const imp=harness(async()=>preview);
tree=imp.render(imp.forms.ImportForm,{});find(tree,n=>n.type===imp.components.UploadBox).props.onReady({id:'A'});
await new Promise(resolve=>setImmediate(resolve));tree=imp.render(imp.forms.ImportForm,{});
assert.ok(find(tree,n=>n.type==='button'&&n.props.className==='button'));
find(tree,n=>n.type===imp.components.UploadBox).props.onReset();tree=imp.render(imp.forms.ImportForm,{});
assert.equal(find(tree,n=>n.type==='button'&&n.props.className==='button'),undefined,'old import commit must disappear');
let newsPayload;
for (const category of ['NEWS','COMPANY','ANNOUNCEMENT']) {
  const categories=harness(()=>{});
  const select=categories.render(categories.forms.CategorySelect,{value:category,onChange(){},kind:'news'});
  const options=select.props.children;
  assert.equal(options.filter(option=>option.props.children==='companyNews').length,1,'company category must have one visible option');
  assert.ok(options.some(option=>option.props.value===category),'editing must preserve the stored category');
}
const news={id:'N',title:'Title',body:'<p>Text</p>',publishedAt:'2026-11-01T21:30:23.123Z',expiresAt:'2026-12-01T21:30:00Z',language:'ru'};
const nh=harness(async(path,method,body)=>{newsPayload=body;return {};},news);
nh.render(nh.forms.NewsForm,{initial:news});tree=nh.render(nh.forms.NewsForm,{initial:news});
await find(tree,n=>n.type==='form').props.onSubmit({preventDefault(){}});
assert.equal(newsPayload.publishedAt,news.publishedAt);assert.equal(newsPayload.expiresAt,news.expiresAt);
find(tree,n=>n.type==='input'&&n.props.type==='datetime-local'&&n.props.value==='2026-12-02T02:30').props.onChange({target:{value:''}});
tree=nh.render(nh.forms.NewsForm,{initial:news});await find(tree,n=>n.type==='form').props.onSubmit({preventDefault(){}});assert.equal(newsPayload.expiresAt,null);
let employeePayload;
const employeeForm=harness(async(path,method,body)=>{employeePayload=body;return {id:'E'};});
const employeeProps={initial:{id:'E',displayName:'Synthetic name',positionTitle:'Role',officeId:'O',unitId:'U'}};
tree=employeeForm.render(employeeForm.forms.EmployeeForm,employeeProps);
find(tree,n=>n.type==='input'&&n.props.type==='date').props.onInput({currentTarget:{value:'1994-11-03'}});
tree=employeeForm.render(employeeForm.forms.EmployeeForm,employeeProps);
await find(tree,n=>n.type==='form').props.onSubmit({preventDefault(){}});
assert.equal(employeePayload.birthday,'1994-11-03','date input must reach the submitted employee payload');
assert.deepEqual(dates.splitSnippet('a needle b',[2,8]),['a ','needle',' b']);
for(const bad of [undefined,[],[3],[5,5],[-1,2],[2,99],[1.5,3]])assert.equal(dates.splitSnippet('a needle b',bad),null,'untrusted match ranges fall back to plain text');
const marked=h.components.Snippet({text:'x <b>needle</b> y',match:[2,5]});
assert.equal(find(marked,n=>n.type==='mark').props.children,'<b>','snippet text stays a React text node, never markup');
console.log('PASS: API responses; locale across login/MFA; Tashkent dates and birthday input; upload races/retry; versions/import; news dates; picker ARIA; close guards. Component logic only, not browser E2E.');
