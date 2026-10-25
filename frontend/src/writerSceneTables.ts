/** Display legacy writer scene paragraphs as tables without changing saved text. */
export function writerSceneTables(text: string): string {
  const heading = /^#{1,6}\s+场景清单[^\n]*\n/gm.exec(text);
  if (!heading) return text;
  const start = heading.index + heading[0].length;
  const tail = text.slice(start);
  const end = tail.search(/^#{1,6}\s+/m);
  const section = end < 0 ? tail : tail.slice(0, end);
  // Only the established legacy format is converted. Markdown tables and other
  // layouts pass through, as do code examples and ambiguous/incomplete content.
  if (/^\s*```|^\s*\|/m.test(section)) return text;
  const headers = [...section.matchAll(/^\*\*(S\d+)[｜|]\s*(.+?)\*\*([^\n]*)$/gm)];
  if (!headers.length || section.slice(0, headers[0].index).trim()) return text;
  const scenes = headers.map((header, i) => {
    const block = section.slice(header.index! + header[0].length, headers[i + 1]?.index ?? section.length).trim();
    const labels = [...block.matchAll(/(?:^|[。\n｜|]\s*)(地点|资产|作用|可见动作|情绪|旁白|台词|落点|字幕(?:（[^）]+）)?|声音|风险|观众知道|观众期待|期待|变化|铺垫|回报)[：:]\s*/g)];
    const fields: Record<string, string> = {};
    for (let n = 0; n < labels.length; n++) {
      const label = labels[n];
      const key = label[1];
      const value = block.slice(label.index! + label[0].length, labels[n + 1]?.index ?? block.length).trim();
      fields[key] = fields[key] ? fields[key] + ' / ' + value : value;
    }
    const timing = /[（(]([^（）()]*)[）)]/.exec(header[2]);
    return {
      id: header[1],
      name: header[2].replace(timing?.[0] || /$^/, '').trim() + header[3],
      timing: timing?.[1] || '未标注',
      fields,
      extra: labels.length ? block.slice(0, labels[0].index).trim() : block,
    };
  });
  // A malformed scene stays verbatim rather than losing its unknown content.
  if (scenes.some(s => !s.fields['地点'] || !s.fields['可见动作'])) return text;
  const cell = (value: string) => value.trim().replace(/\r?\n/g, ' ').replace(/\|/g, '\\|') || '—';
  const table = (columns: string[], rows: string[][]) =>
    ['| ' + columns.join(' | ') + ' |', '| ' + columns.map(() => '---').join(' | ') + ' |',
      ...rows.map(row => '| ' + row.map(cell).join(' | ') + ' |')].join('\n');
  const labelled = (fields: Record<string, string>, keys: string[]) =>
    keys.filter(k => fields[k]).map(k => k + '：' + fields[k]).join(' ');
  const readable = '\n### 场景与剧情\n\n' + table(
    ['场景', '时间 / 镜数', '地点', '画面动作', '旁白 / 对白 / 字幕', '情绪'],
    scenes.map(s => [s.id + '｜' + s.name, s.timing, s.fields['地点'], s.fields['可见动作'],
      labelled(s.fields, ['旁白', '台词', '落点', ...Object.keys(s.fields).filter(k => k.startsWith('字幕'))]), s.fields['情绪'] || ''])
  ) + '\n\n### 声音与制作风险\n\n' + table(
    ['场景', '声音', '制作风险', '资产引用'],
    scenes.map(s => [s.id, s.fields['声音'] || '', s.fields['风险'] || '', s.fields['资产'] || ''])
  ) + '\n\n### 叙事作用\n\n' + table(
    ['场景', '作用', '观众知道 / 期待', '情绪变化', '铺垫 / 回报'],
    scenes.map(s => [s.id, s.fields['作用'] || '', labelled(s.fields, ['观众知道', '观众期待', '期待']),
      s.fields['变化'] || '', labelled(s.fields, ['铺垫', '回报']) + (s.extra ? ' 补充：' + s.extra : '')])
  ) + '\n\n';
  return text.slice(0, start) + readable + (end < 0 ? '' : tail.slice(end));
}
