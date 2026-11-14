// Scientific boundary checks against the actual imported helpers; no React,
// server, sensor, generated data or training job is started by this suite.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';

const sourceRoot = new URL('../src/', import.meta.url);
const workbench = await import(new URL('lib/workbench.ts', sourceRoot));
async function pureHelpers(relative, endMarker, exports = '') {
  let source = await readFile(new URL(relative, sourceRoot), 'utf8');
  if (endMarker) {
    const end = source.indexOf(endMarker);
    assert(end > 0, `${relative}: pure-helper boundary is present`);
    source = source.slice(0, end);
  }
  source = source.replace(/^import[^\r\n]*[\r\n]+/gm, '') + exports;
  const js = stripTypeScriptTypes(source, {mode: 'strip'});
  return import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
}
const {analyzeM3025Force} = await pureHelpers('app/components/DataViews.tsx', 'export default function DataViews');
const {validCounts, validNumber, evaluationMetrics} = await pureHelpers('app/components/ResearchViews.tsx', 'const status =');
const pageSource = await readFile(new URL('app/page.tsx', sourceRoot), 'utf8');
const pageHelper = pageSource.slice(pageSource.lastIndexOf('export function successDisplay'));
assert(pageHelper.startsWith('export function successDisplay'));
const {successDisplay} = await import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(pageHelper, {mode: 'strip'})).toString('base64'));
const points = (vector) => Array.from({length: 77}, () => [...vector]);

test('CSV preserves quoted text and missing cells, rejects ambiguous columns', () => {
  assert.deepEqual(workbench.parseCSV('timestamp,label,value\r\n0,"contact, stable",\r\n1,"a""b",2\r\n2,"  ",'), [
    {timestamp: 0, label: 'contact, stable', value: null},
    {timestamp: 1, label: 'a"b', value: 2},
    {timestamp: 2, label: null, value: null}
  ]);
  assert.throws(() => workbench.parseCSV('t,t\n1,2'), /字段名/);
  assert.throws(() => workbench.parseCSV('t,v\n1,"broken'), /引号/);
  assert.throws(() => workbench.parseCSV('t,v\n1,2,3'), /多余字段/);
});

test('77×3 input is enforced and missing readings are not zero-coerced', () => {
  const raw = {timestamp: '1.5', forces: points([0, null, 3]), force: ['1', 2, '3']};
  raw.forces[0] = [true, '  ', []];
  raw.forces[1] = [false, {}, Infinity];
  const result = workbench.normalizeSamples([raw])[0];
  assert.deepEqual(result.forces[0], [null, null, null]);
  assert.deepEqual(result.forces[1], [null, null, null]);
  assert.deepEqual(result.forces[2], [0, null, 3]);
  assert.deepEqual(result.force, [1, 2, 3]);
  assert.equal(result.timestamp, 1.5);
  assert.throws(() => workbench.normalizeSamples([{forces: [[1, 2, 3]]}]), /77×3/);
  assert.throws(() => workbench.normalizeSamples([null]), /字段对象/);
  assert.equal(raw.forces[0][0], true, 'normalization does not mutate the source record');
});

test('flattened CSV sensor fields preserve unknown measurements', () => {
  const sample = workbench.normalizeSamples([{timestamp: false, f0_x: '2.5', f0_y: 0, f0_z: ''}])[0];
  assert.equal(sample.timestamp, null);
  assert.deepEqual(sample.forces[0], [2.5, 0, null]);
  assert.deepEqual(sample.forces[76], [null, null, null]);
});

test('counts points and an independently measured N resultant remain distinct', () => {
  const result = analyzeM3025Force([
    {timestamp: 0, forces: points([100, 0, 0]), force: [3, 4, 0]},
    {timestamp: 100, forces: points([100, 0, 0]), force: [6, 8, 0]},
  ], {forceUnit: 'counts', netForceUnit: 'N', timestampUnit: 'ms', allowVectorSum: true});
  assert.equal(result.peakMagnitude, 10);
  assert.equal(result.peakRate, 50);
  assert.equal(result.deviceFrames, 2);
  assert.equal(result.summedFrames, 0);
  assert.deepEqual(result.vectors[0], [3, 4, 0]);
});

test('77-point summation requires N units and explicit authorization', () => {
  const samples = [{timestamp: 0, forces: points([1, 0, 0])}];
  assert.equal(analyzeM3025Force(samples, {forceUnit: 'counts', netForceUnit: 'N', allowVectorSum: true}).peakMagnitude, null);
  assert.equal(analyzeM3025Force(samples, {forceUnit: 'N', allowVectorSum: false}).peakMagnitude, null);
  assert.equal(analyzeM3025Force(samples, {forceUnit: 'N', allowVectorSum: true}).peakMagnitude, 77);
  assert.equal(analyzeM3025Force([{...samples[0], pointForceUnit: 'counts'}], {forceUnit: 'N', allowVectorSum: true}).peakMagnitude, null);
  const missing = points([1, 0, 0]); missing[1][2] = null;
  assert.equal(analyzeM3025Force([{forces: missing}], {forceUnit: 'N', allowVectorSum: true}).peakMagnitude, null);
});

test('force units and timestamp units gate the corresponding physical quantity', () => {
  const samples = [{timestamp: 0, force: [0, 0, 0]}, {timestamp: 1, force: [3, 4, 0]}];
  const unknownForce = analyzeM3025Force(samples, {forceUnit: 'unknown', timestampUnit: 's'});
  assert.equal(unknownForce.peakMagnitude, null);
  assert.equal(unknownForce.peakRate, null);
  const unknownTime = analyzeM3025Force(samples, {netForceUnit: 'N'});
  assert.equal(unknownTime.peakMagnitude, 5);
  assert.equal(unknownTime.peakRate, null);
  assert.equal(unknownTime.timeUnitKnown, false);
});

test('missing frames, nonmonotonic times and long gaps do not create rate evidence', () => {
  const missing = analyzeM3025Force([{timestamp: 0, force: [0, 0, 0]}, {timestamp: 1}, {timestamp: 2, force: [20, 0, 0]}], {netForceUnit: 'N', timestampUnit: 's'});
  assert.equal(missing.validFrames, 2);
  assert.equal(missing.validIntervals, 0);
  assert.equal(missing.peakRate, null);
  const reverse = analyzeM3025Force([{timestamp: 2, force: [0, 0, 0]}, {timestamp: 1, force: [10, 0, 0]}], {netForceUnit: 'N', timestampUnit: 's'});
  assert.equal(reverse.peakRate, null);
  const gap = analyzeM3025Force([{timestamp: 0, force: [0, 0, 0]}, {timestamp: 2, force: [10, 0, 0]}], {netForceUnit: 'N', timestampUnit: 's', maxGapMs: 100});
  assert.equal(gap.peakRate, null);
  assert.equal(gap.skippedGaps, 1);
});

test('a change from device resultant to point sum is not a force-rate interval', () => {
  const result = analyzeM3025Force([{timestamp: 0, force: [1, 0, 0]}, {timestamp: 1, forces: points([1, 0, 0])}], {forceUnit: 'N', timestampUnit: 's', allowVectorSum: true});
  assert.equal(result.deviceFrames, 1);
  assert.equal(result.summedFrames, 1);
  assert.equal(result.peakRate, null);
});

test('aggregate count metrics require actual nonnegative integer counts', () => {
  assert.equal(validCounts(1, 2), true);
  assert.equal(validCounts('1', '2'), true);
  for (const [s, n] of [[true, 2], [1, true], ['', 2], [' ', 2], [[], 2], [0, 0], [-1, 2], [3, 2], [1.5, 2]]) {
    assert.equal(validCounts(s, n), false, `${JSON.stringify(s)} / ${JSON.stringify(n)} must be unknown`);
    assert.equal(successDisplay({successes: s, total: n}), '—');
  }
  assert.equal(successDisplay({successes: 0, total: 5}), '0.0%');
  assert.equal(successDisplay({successes: 2, total: 4}), '50.0%');
  for (const v of [true, false, [], {}, ' ', Infinity]) assert.equal(validNumber(v), false);
});

test('per-trial outcomes use known labels only; unknown is not a failure', () => {
  const normalized = workbench.normalizeLogRows([{success: null}, {success: ''}, {success: 1}, {success: 0}, {success: false}]);
  assert.deepEqual(normalized.metrics, {successes: 1, total: 3, trials: 3});
  assert.equal(normalized.failures.length, 2);
  const metrics = evaluationMetrics({data: {evaluations: [{success: null}, {success: 0}, {success: 1}]}});
  assert.equal(metrics.total, 2);
  assert.equal(metrics.successes, 1);
});

test('P95 uses nearest rank and rejects absent, negative or nonnumeric latencies', () => {
  const evaluations = Array.from({length: 20}, (_, i) => ({latency_ms: 20 - i, sync_error_ms: i % 2 ? -2 : 4}));
  evaluations.push({latency_ms: true}, {latency_ms: []}, {latency_ms: ' '}, {latency_ms: -3}, {latency_ms: null});
  const metrics = evaluationMetrics({data: {evaluations}});
  assert.equal(metrics.latencyP95Ms, 19);
  assert.match(metrics.latencyNote, /n=20/);
  assert.equal(metrics.syncErrorMs, 3);
  assert.match(metrics.syncMetric, /绝对均值/);
});

test('recovery excludes explicit nonattempts and does not fill missing time values', () => {
  const metrics = evaluationMetrics({data: {evaluations: [
    {recovery_attempted: false, recovery_success: false},
    {recovery_attempted: true, recovery_success: true, recovery_time_s: 2},
    {recovery_attempted: true, recovery_success: false},
    {recovery_attempted: true, recovery_success: true, recovery_time_s: 4},
    {recovery_attempted: true, recovery_success: null},
  ]}});
  assert.equal(metrics.recoveryTotal, 3);
  assert.equal(metrics.recoverySuccesses, 2);
  assert.equal(metrics.recoveryMedianS, 3);
  assert.equal(metrics.derivedRecoveryCount, 2);
});

test('starter configurations contain no invented training result or embedding', () => {
  const seeds = workbench.initialRecords();
  const experiments = seeds.filter(r => r.kind === 'experiment');
  assert.equal(experiments.length, 5);
  assert(experiments.every(r => r.data.isTemplate && !Object.keys(r.data.metrics).length && !r.data.curves.length));
  const model = seeds.find(r => r.kind === 'model');
  assert.deepEqual(model.data.tasks, ['Masked Force', 'Net Force']);
  assert.deepEqual(model.data.features, []);
  assert.equal(model.data.checkpoint, '');
  assert.equal(model.data.gruEnabled, false);
  assert.equal(model.data.dynamicsEnabled, false);
  assert.equal(seeds.find(r => r.kind === 'preprocess').data.fitSplit, 'train');
});

test('backup ID collisions remap linked records without fabricating attachments', () => {
  const prepared = workbench.prepareBackupImport({records: [
    {id: 'data-1', kind: 'dataset', name: 'D', data: {}},
    {id: 'exp-1', kind: 'experiment', name: 'E', data: {datasetId: 'data-1', fileIds: ['unrestored-file']}},
    {id: 'unrestored-file', kind: 'file', name: 'raw.csv', data: {size: 100}},
  ]}, [{id: 'data-1'}], 'imported-backup');
  assert.notEqual(prepared.records[0].id, 'data-1');
  assert.equal(prepared.records[1].data.datasetId, prepared.records[0].id);
  assert.deepEqual(prepared.records[1].data.fileIds, ['unrestored-file', 'imported-backup']);
  assert.equal(prepared.skippedFiles, 1);
});

test('generated reports preserve unknown metrics and do not claim executed training', () => {
  const records = [{id: 'bad', kind: 'experiment', name: 'invalid', status: '待补充实验', data: {metrics: {successes: true, total: 2}}}];
  const paper = workbench.reportText('paper', records);
  assert.match(paper, /\| invalid \[bad\] \| 待补充实验 \| — \|/);
  assert.match(paper, /不等同于训练执行/);
  assert(!paper.includes('50.0%'));
  assert.equal(new Set(['plan', 'week', 'stage', 'paper', 'tasks'].map(type => workbench.reportText(type, records))).size, 5);
});
