const fs=require('fs');
const vm=require('vm');
const assert=require('assert');
const storage=new Map();
function browser() {
  const elements=new Map();
  const element=key=>{
    if(!elements.has(key)) elements.set(key,{innerHTML:'',value:'',checked:false,style:{},
      classList:{toggle(){}},insertAdjacentHTML(_position,html){this.innerHTML+=html},showModal(){},close(){}});
    return elements.get(key);
  };
  const context={console,document:{querySelector:element,querySelectorAll:()=>[]},location:{hash:'#ur5e'},
    window:{addEventListener(){},scrollTo(){}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
    setInterval(){},setTimeout(){},clearTimeout(){},URL,Blob,AbortSignal};
  vm.createContext(context);
  for(const file of ['app.js','platform.js','camera-guard.js','ur-workflow.js','ur-experience.js'])
    vm.runInContext(fs.readFileSync('dist/'+file,'utf8'),context);
  return {element,run:code=>vm.runInContext(code,context)};
}
(async()=>{
  const first=browser();
  assert(first.element('#app').innerHTML.includes('先配置你的工位'));
  first.run("location.hash='#ur-settings';render()");
  const draft=first.run('uxDraft()');
  for(const [key,value] of Object.entries(draft)) first.element('#ux-'+key).value=String(value);
  await first.run('uxSaveProfile()');
  first.run("location.hash='#ur5e';render()");
  assert(first.element('#app').innerHTML.includes('选择今天要做的工作'));
  const nav=first.element('#nav').innerHTML;
  assert(nav.indexOf('href="#ur-live"')<nav.indexOf('href="#edge-data"'));
  assert(nav.indexOf('href="#edge-data"')<nav.indexOf('href="#edge-train"'));
  assert(nav.indexOf('href="#edge-train"')<nav.indexOf('工位设置'));
  const second=browser();
  assert(second.element('#app').innerHTML.includes('选择今天要做的工作'));
  second.run("location.hash='#ur-live';render()");
  assert(!second.element('#app').innerHTML.includes('采集脚本绝对路径'));
  assert(second.element('#app').innerHTML.includes('u ↵ 进入下一条'));
  second.run("location.hash='#ur-settings';render()");
  assert(second.element('#app').innerHTML.includes('配置已保存'));
  assert(!second.element('#app').innerHTML.includes('id="ux-name"'));
  second.run("location.hash='#edge-train';render()");
  assert(second.element('#app').innerHTML.includes('高级训练参数'));
  second.run("pMode='live';location.hash='#ur5e';render()");
  assert(second.element('#app').innerHTML.includes('先配置你的工位'));
  console.log('PASS first setup, saved reload, primary navigation, daily capture and demo/live isolation.');
})().catch(error=>{console.error(error);process.exitCode=1});
