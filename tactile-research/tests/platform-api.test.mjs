import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';

const filename=new URL('../src/platform-api.ts',import.meta.url);
const source=stripTypeScriptTypes(await readFile(filename,'utf8'),{mode:'transform'}).replaceAll('export ','');
function moduleWith(search,fetch) {
 const context={location:{search,origin:'http://127.0.0.1:8787'},URL,URLSearchParams,Blob,TextEncoder,crypto:globalThis.crypto,fetch,setTimeout,console};
 vm.createContext(context);
 vm.runInContext(source+'\nglobalThis.apiExports={labApi,liveMode,embedded};',context);
 return context.apiExports;
}
test('live API stays same origin and uses tactile namespace',async()=>{
 const calls=[];
 const api=moduleWith('?mode=live&embedded=1',async(...args)=>{calls.push(args);return {ok:true,json:async()=>({records:[]})};});
 assert.equal(api.liveMode,true);assert.equal(api.embedded,true);
 const result=await api.labApi('/api/records');
 assert.deepEqual(result,{records:[]});
 assert.equal(calls[0][0],'/api/tactile-lab/records');
 await api.labApi('/api/import-asset',{method:'POST',body:'{"asset_id":"external-test","episode_index":0}'});
 assert.equal(calls[1][0],'/api/tactile-lab/import-asset');
 assert.equal(calls[1][1].method,'POST');
});
test('local errors surface actual server reason, do not fall back to demo',async()=>{
 const api=moduleWith('?mode=live',async()=>({ok:false,json:async()=>({error:'数据正在采集中，禁止读取'})}));
 await assert.rejects(()=>api.labApi('/api/import-asset'),/采集中/);
});
test('missing backend has explicit update instruction',async()=>{
 const api=moduleWith('?mode=live',async()=>({ok:false,json:async()=>{throw new Error('HTML response');}}));
 await assert.rejects(()=>api.labApi('/api/records'),/同时更新并启动 edge_agent/);
});
test('demo mode never falls through to network or cloud writes',async()=>{
 let network=0;const api=moduleWith('?mode=demo',async()=>{network++;throw new Error('Unexpected');});
 assert.equal(api.liveMode,false);
 await assert.rejects(()=>api.labApi('/api/records')); // no IndexedDB in Node, never a network fallback
 assert.equal(network,0);
});
