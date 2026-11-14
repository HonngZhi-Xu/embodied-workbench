/** Local edge service or explicitly isolated browser demo. No cloud credentials. */
export const liveMode = new URLSearchParams(location.search).get('mode') === 'live';
export const embedded = new URLSearchParams(location.search).get('embedded') === '1';
const MAX_RECORD = 4 * 1024 * 1024, MAX_FILE = 20 * 1024 * 1024;
const kinds = new Set(['dataset','episode','preprocess','model','experiment','plan','conclusion','task','file','report','sensor','evaluation','log','feature','note']);
let database: Promise<IDBDatabase> | undefined;
function db() {
  if (!database) database = new Promise((resolve,reject) => {
    const request=indexedDB.open('embodied-m3025-browser-demo-v1',1);
    request.onupgradeneeded=()=>{request.result.createObjectStore('records',{keyPath:'id'});request.result.createObjectStore('files',{keyPath:'id'});};
    request.onerror=()=>reject(new Error('浏览器演示存储不可用；请检查隐私模式或存储配额。'));
    request.onsuccess=()=>resolve(request.result);
  });
  return database;
}
function request<T>(item:IDBRequest<T>):Promise<T> {return new Promise((resolve,reject)=>{item.onsuccess=()=>resolve(item.result);item.onerror=()=>reject(item.error||new Error('浏览器存储失败'));});}
function complete(tx:IDBTransaction) {return new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(tx.error||new Error('浏览器存储事务未完成'));});}
function validRecord(input:any) {
  if (!input||typeof input!=='object'||Array.isArray(input)||!kinds.has(input.kind)) throw new Error('记录类型无效。');
  if (typeof input.name!=='string'||!input.name.trim()||input.name.length>250) throw new Error('名称必须为 1–250 个字符。');
  if (!input.data||typeof input.data!=='object'||Array.isArray(input.data)) throw new Error('data 必须是 JSON 对象。');
  if (new TextEncoder().encode(JSON.stringify(input.data)).length>MAX_RECORD) throw new Error('记录超过 4 MB，请将长记录分成 Episode，原文件已保留。');
  const id=input.id||crypto.randomUUID();
  if(typeof id!=='string'||!/^[A-Za-z0-9_-]{1,150}$/.test(id)) throw new Error('记录 ID 格式无效。');
  const status=input.status||'未知/待确认';
  if(typeof status!=='string'||status.length>100) throw new Error('状态格式无效。');
  return {...input,id,name:input.name.trim(),status};
}
async function demoApi(url:string,options:any={}) {
  const route=new URL(url,location.origin),method=options.method||'GET';
  const storage=await db();
  if(route.pathname==='/api/records') {
    if(method==='GET') {
      const all:any[]=await request(storage.transaction('records').objectStore('records').getAll());
      return {records:all.filter(r=>!r.deleted).map(({deleted,...r})=>r),initialized:all.length>0};
    }
    if(method==='POST') {
      const value=validRecord(JSON.parse(options.body).record);
      const tx=storage.transaction('records','readwrite'),done=complete(tx),store=tx.objectStore('records');
      const existing:any=await request(store.get(value.id));
      if(existing?.deleted){tx.abort();await done.catch(()=>{});throw new Error('记录已删除，不得复用旧 ID，请新建记录。');}
      if(existing&&value.version!==existing.version) {tx.abort();await done.catch(()=>{});throw new Error('记录已在其他页面更新或缺少版本，请重新加载。');}
      if(existing?.kind==='file'&&value.kind!=='file') {tx.abort();await done.catch(()=>{});throw new Error('文件记录类型不能修改。');}
      if(value.kind==='file') {
        if(!existing||existing.kind!=='file') {tx.abort();await done.catch(()=>{});throw new Error('文件记录必须通过上传建立。');}
        value.data={...value.data,key:existing.data.key,size:existing.data.size,mime:existing.data.mime,sha256:existing.data.sha256};
      }
      const now=new Date().toISOString();
      const saved={...value,createdAt:existing?.createdAt||now,updatedAt:now,version:(existing?.version||0)+1};
      store.put(saved);await done;return {record:saved};
    }
    if(method==='DELETE') {
      const id=route.searchParams.get('id');
      const tx=storage.transaction('records','readwrite'),done=complete(tx),store=tx.objectStore('records');
      const existing:any=await request(store.get(id));
      if(!existing||existing.deleted){tx.abort();await done.catch(()=>{});throw new Error('记录不存在。');}
      store.put({...existing,deleted:true,version:existing.version+1,updatedAt:new Date().toISOString()});
      await done;return {deleted:id};
    }
  }
  if(route.pathname==='/api/files'&&method==='POST') {
    const file=options.body.get('file');
    if(!(file instanceof Blob)||!file.size||file.size>MAX_FILE) throw new Error('请选择不超过 20 MB 的非空文件。');
    const bytes=await file.arrayBuffer();
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');
    const id='file_'+crypto.randomUUID(),now=new Date().toISOString();
    const record={id,kind:'file',name:file.name||'未命名文件',status:'已导入',data:{size:file.size,mime:file.type||'application/octet-stream',key:id,sha256:hash,category:'data'},createdAt:now,updatedAt:now,version:1};
    const tx=storage.transaction(['records','files'],'readwrite'),done=complete(tx);
    tx.objectStore('files').put({id,blob:new Blob([bytes],{type:'application/octet-stream'})});
    tx.objectStore('records').put(record);await done;return {record};
  }
  throw new Error('浏览器演示不提供此操作；真实资产请使用本地工作台。');
}
export async function labApi(url:string,options:any={}) {
  if(!liveMode) return demoApi(url,options);
  const endpoint=url.replace(/^\/api\//,'/api/tactile-lab/');
  const response=await fetch(endpoint,options);
  let result:any;
  try{result=await response.json();}catch{throw new Error('本地触觉研究服务不可用，请同时更新并启动 edge_agent。');}
  if(!response.ok) throw new Error(result.error||'本地研究服务请求失败。');
  return result;
}
export async function viewOriginalFile(record:any) {
  let blob:Blob;
  if(liveMode) {
    const response=await fetch('/api/tactile-lab/files/'+encodeURIComponent(record.id));
    if(!response.ok) {let message='原文件不可用。';try{message=(await response.json()).error||message;}catch{}throw new Error(message);}
    blob=await response.blob();
  } else {
    const storage=await db();
    const saved:any=await request(storage.transaction('records').objectStore('records').get(record.id));
    const file:any=await request(storage.transaction('files').objectStore('files').get(record.id));
    if(!saved||saved.deleted||!file?.blob) throw new Error('原文件未保留在本浏览器，请重新上传；配置备份不包含原文件。');
    blob=file.blob;
  }
  const a=document.createElement('a'),url=URL.createObjectURL(blob);
  a.href=url;a.download=record.name||'attachment';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
