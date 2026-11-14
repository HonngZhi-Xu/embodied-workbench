import {build} from 'esbuild';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const outdir=fileURLToPath(new URL('../dist/tactile-lab/',import.meta.url));
await mkdir(outdir,{recursive:true});
await build({entryPoints:[fileURLToPath(new URL('./src/main.tsx',import.meta.url))],bundle:true,outdir,entryNames:'app',assetNames:'assets/[name]-[hash]',minify:true,sourcemap:false,jsx:'automatic',target:['es2022'],define:{'process.env.NODE_ENV':'"production"'},legalComments:'linked'});
await writeFile(outdir+'/index.html','<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>M3025 触觉研究 · 具身工坊</title><link rel="stylesheet" href="./app.css"></head><body><div id="root"></div><script type="module" src="./app.js"></script></body></html>');
const notices=[];
for(const [name,file] of [['react','LICENSE'],['react-dom','LICENSE'],['lucide-react','LICENSE'],['esbuild','LICENSE.md']]) {
  notices.push('=== '+name+' ===\n'+await readFile(new URL('./node_modules/'+name+'/'+file,import.meta.url),'utf8'));
}
await writeFile(outdir+'/THIRD_PARTY_LICENSES.txt',notices.join('\n\n'));
console.log('Built '+outdir);
