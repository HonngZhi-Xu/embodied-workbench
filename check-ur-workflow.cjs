const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
function element(key) {
  if (!elements.has(key)) elements.set(key, {
    innerHTML: '', value: '', checked: false, style: {}, classList: {toggle() {}},
    insertAdjacentHTML(_position, html) { this.innerHTML += html; },
    showModal() {}, close() {}
  });
  return elements.get(key);
}
const context = {
  console, document: {querySelector: element, querySelectorAll: () => []},
  location: {hash: '#ur5e'}, window: {addEventListener() {}, scrollTo() {}},
  localStorage: {getItem() {return null;}, setItem() {}},
  setInterval() {}, setTimeout() {}, clearTimeout() {}, URL, Blob, AbortSignal
};
vm.createContext(context);
for (const file of ['app.js','platform.js','camera-guard.js','ur-workflow.js']) {
  vm.runInContext(fs.readFileSync('dist/'+file, 'utf8'), context);
}
const run = code => vm.runInContext(code, context);
assert(element('#app').innerHTML.includes('现场工作台'));
run("location.hash='#ur-live';render()");
assert(element('#app').innerHTML.includes('u ↵ 进入下一条'));
run("uwDemo={id:'demo',running:true,aligned:false,align_prompt:true,phase:'waiting_align',log:[]}");
(async () => {
  await run("uwCaptureKey('align',true)");
  assert.equal(run('uwDemo.phase'), 'align_sent');
  await run("uwCaptureKey('ready',true)");
  await run("uwCaptureKey('start')");
  await run("uwCaptureKey('finish')");
  await run("uwCaptureKey('next')");
  assert.equal(run('uwDemo.phase'), 'next_sent');
  const command = run('uwTrainingCommand(uwDefaults)');
  assert(command.includes('train_gello_live_image_20hz'));
  assert(command.includes('gripper_position'));
  assert(command.includes('training.num_epochs=60'));
  assert(command.includes('${now:%Y.%m.%d}'));
  run("location.hash='#edge-train';render()");
  assert(element('#app').innerHTML.includes('训练项目目录'));
  run("pMode='live'");
  assert.equal(run('uwSession()'), null);
  console.log('PASS UR site controls, isolated demo, editable training and exact command fields.');
})().catch(error => { console.error(error); process.exitCode=1; });
