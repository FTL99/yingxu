import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/writerSceneTables.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { writerSceneTables } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'));
const scene = `**S1｜开场（0:00–0:03，2 镜）**
地点：房间｜资产：CHAR_A。
作用：钩子。可见动作：抬头，看向窗外。
情绪：惊醒。旁白：「醒来了。」字幕（行动指向）：「开始」。
声音：风声。
风险：**高**，曝光待测试。关联 hypothesis_test。
观众知道：第一人称｜观众期待：发生什么｜变化：平静→惊醒｜铺垫：风声。
`;

test('legacy scenes become rows and keep all production and narrative information', () => {
  const input = '# 脚本\n\n## 场景清单\n\n' + scene + '\n## 下一个问题\n\n保持原句。';
  const result = writerSceneTables(input);
  assert.equal((result.match(/^\| ---/gm) || []).length, 3);
  for (const value of ['0:00–0:03，2 镜', 'CHAR_A', '抬头，看向窗外', '「醒来了。」', '「开始」', '**高**', 'hypothesis_test', '发生什么', '平静→惊醒', '风声', '保持原句。']) assert.ok(result.includes(value), value);
  assert.equal(writerSceneTables(result), result);
});

test('handles multiple scenes and optional pool labels without merging their contents', () => {
  const second = scene.replaceAll('S1', 'S2').replace('**\n地点', '**·A 池\n地点').replace('房间', '甲板').replace('醒来了', '出发了');
  const result = writerSceneTables('## 场景清单\n\n' + scene + '\n' + second);
  assert.equal((result.match(/\| S1/g) || []).length, 3);
  assert.equal((result.match(/\| S2/g) || []).length, 3);
  assert.ok(result.includes('·A 池'));
  assert.ok(result.includes('甲板'));
  assert.ok(result.includes('出发了'));
});

test('literal pipes cannot create extra table columns', () => {
  const result = writerSceneTables('## 场景清单\n\n' + scene.replace('抬头，看向窗外', '看 A | B'));
  assert.ok(result.includes('看 A \\| B'));
});

test('already formatted tables, code, unrelated text and incomplete scenes stay verbatim', () => {
  for (const input of [scene, '## 场景清单\n\n| 场景 | 地点 |\n|---|---|\n|S1|房间|', '## 场景清单\n\n```md\n' + scene + '```', '## 场景清单\n\n**S1｜开场**\n未知：保留全部', '## 场景清单\n\n请先确认。\n' + scene]) assert.equal(writerSceneTables(input), input);
});
