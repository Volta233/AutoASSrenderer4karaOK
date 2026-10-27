// Candidate evidence, timing status and artifact views receive explicit project snapshots.
import { $, api, notify, handler } from './ui.js';

export function renderAlignmentStatus(project, timeline) {
  const box = $('alignmentStatus'),
    metrics = $('timingEvidence');
  metrics.replaceChildren();
  if (!timeline) {
    box.textContent = project.busy ? '正在处理…' : '准备就绪';
    return;
  }
  const evidence = project.workflow?.timing_evidence;
  if (evidence) {
    for (const [label, key] of [
      ['原生词时间覆盖率', 'model_coverage'],
      ['手动定位覆盖率', 'manual_coverage'],
      ['估算占比', 'estimated_coverage'],
      ['无时间占比', 'missing_coverage'],
    ]) {
      const div = document.createElement('div');
      div.className = 'metric';
      const v = document.createElement('strong');
      v.textContent = (evidence[key] * 100).toFixed(1) + '%';
      const caption = document.createElement('span');
      caption.textContent = label;
      div.append(v, caption);
      metrics.append(div);
    }
  }
  const located = timeline.lines.filter((l) => Number.isFinite(l.start)).length,
    estimated = timeline.lines.filter((l) =>
      l.units.some((u) => u.timing_source === 'estimated'),
    ).length;
  const warnings = timeline.diagnostics?.warnings || [];
  const status = [`${located}/${timeline.lines.length} 行已定位`];
  if (estimated) status.push(`${estimated} 行含估算，待试听`);
  if (located < timeline.lines.length) status.push(`${timeline.lines.length - located} 行未定位`);
  if (evidence?.conflicting_units) status.push(`${evidence.conflicting_units} 处时间冲突`);
  if (warnings.length) status.push('有处理提示，查看详情');
  box.textContent = status.join(' · ');
}

export function renderCandidates(project, { flush, applyProject }) {
  const box = $('candidates');
  const signature = JSON.stringify(project.candidates);
  if (box.dataset.signature === signature) return;
  box.dataset.signature = signature;
  box.replaceChildren();
  if (!project.candidates) return;
  const attempts = project.candidates.actual_query_attempts || [];
  if (attempts.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = '搜索记录';
    details.className = 'retrieval-details';
    details.append(summary);
    for (const a of attempts) {
      const row = document.createElement('p');
      row.className = 'muted';
      row.textContent = `${a.provider || 'LRCLIB'} · ${Object.entries(a.query)
        .map(([k, v]) => `${k}=${v}`)
        .join(
          ' · ',
        )} · ${a.status === 'failed' ? '来源失败' : `返回 ${a.records} 条 / 可用歌词 ${a.candidates} 条`}${a.http_status ? ` · HTTP ${a.http_status}` : ''}`;
      details.append(row);
    }
    box.append(details);
  }
  const lookup = project.candidates.verification || {};
  if (lookup.retrieval_warning) {
    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = lookup.retrieval_warning;
    box.append(note);
  }
  if (!project.candidates.candidates.length) {
    const message = document.createElement('p');
    message.textContent =
      project.candidates.retrieval_status === 'unavailable'
        ? '歌词来源暂不可用，请重试或导入文件。'
        : '未找到歌词，请核对歌名、歌手或导入文件。';
    box.append(message);
    const sources = lookup.web?.sources || [];
    if (sources.length) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = '网页来源（需自行核对并导入歌词）';
      details.append(summary);
      for (const source of sources) {
        let url;
        try {
          url = new URL(source.url);
        } catch {
          continue;
        }
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
          continue;
        const row = document.createElement('p');
        const link = document.createElement('a');
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = source.title || url.hostname;
        row.append(link);
        details.append(row);
      }
      box.append(details);
    }
    return;
  }
  let more;
  for (const [index, c] of project.candidates.candidates.entries()) {
    if (index === 3) {
      more = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `更多版本（${project.candidates.candidates.length - 3}）`;
      more.append(summary);
      box.append(more);
    }
    const div = document.createElement('div');
    div.className = 'candidate';
    const title = document.createElement('strong');
    const header = document.createElement('div');
    header.className = 'candidate-header';
    const advice =
      { recommended: '建议试听', compare: '确认版本', review: '核对差异' }[c.recommendation] ||
      '确认版本';
    title.textContent = `${c.song} · ${c.artist || '未知歌手'}`;
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = advice;
    title.append(badge);
    const src = document.createElement('a');
    src.href = c.source;
    src.target = '_blank';
    src.rel = 'noopener';
    src.textContent = c.provider || '查看来源';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = '查看歌词';
    const pre = document.createElement('pre');
    pre.textContent = c.text;
    details.append(summary, pre);
    const metadata = document.createElement('p');
    metadata.className = 'candidate-meta';
    metadata.textContent = [c.album, c.duration ? `${c.duration} 秒` : '时长未知']
      .filter(Boolean)
      .join(' · ');
    const warningsNode = document.createElement('div');
    if (c.evidence) {
      const p = document.createElement('p');
      p.className = 'muted';
      const e = c.evidence;
      p.textContent = `名称相似度：歌名 ${(e.title_similarity * 100).toFixed(0)}% / 歌手 ${e.artist_similarity == null ? '未填写' : (e.artist_similarity * 100).toFixed(0) + '%'} · 时长差 ${e.duration_difference_seconds ?? '未知'} 秒 · ${c.content_languages?.length ? '检测语言 ' + c.content_languages.join(' / ') + ' · ' : ''}尚未比较实际演唱`;
      warningsNode.append(p);
    }
    if (c.recommendation_reasons?.length) {
      const note = document.createElement('p');
      note.textContent = c.recommendation_reasons.join('；');
      warningsNode.append(note);
    }
    for (const warning of c.warnings || []) {
      const note = document.createElement('p');
      note.className = 'flag';
      note.textContent = warning;
      warningsNode.append(note);
    }
    const button = document.createElement('button');
    button.textContent = '采用';
    button.disabled = project.busy;
    button.onclick = handler(async () => {
      await flush();
      const updated = await api(`/projects/${project.id}/lyrics`, 'POST', {
        candidate: c.id,
      });
      applyProject(updated);
      $('lyrics').value = updated.lyrics.map((line) => line.text).join('\n');
      notify('已采用歌词');
    });
    header.append(title, button);
    const comparison = document.createElement('details');
    const comparisonTitle = document.createElement('summary');
    comparisonTitle.textContent = '版本信息';
    comparison.append(comparisonTitle, src, warningsNode);
    div.append(header, metadata);
    if (c.requires_version_review) {
      const warning = document.createElement('p');
      warning.className = 'flag candidate-note';
      warning.textContent = '版本可能不同，请核对歌词。';
      div.append(warning);
    }
    div.append(details, comparison);
    (more || box).append(div);
  }
}

export function renderArtifacts(project, fileURL) {
  const key = JSON.stringify([project.id, project.artifacts]);
  const box = $('artifacts');
  if (key === box.dataset.signature) return;
  box.dataset.signature = key;
  box.replaceChildren();
  for (const a of [...project.artifacts].reverse().filter((item) => item.kind === 'export')) {
    const div = document.createElement('div');
    div.className = 'artifact';
    const label = document.createElement('p');
    label.textContent = `视频已导出${a.exported ? ' · ' + a.exported : ''}`;
    div.append(label);
    if (a.report) {
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = `${a.report.rendered_lines}/${a.report.total_lines} 行字幕 · ${a.report.estimated_lines || 0} 行含估算 · ${a.report.unmatched_lines} 行未定位${a.report.no_timed_subtitles ? ' · 无已定位字幕' : ''}`;
      div.append(note);
    }
    const extra = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = '字幕与项目文件';
    extra.append(summary);
    for (const [index, [name, path]] of [
      ['下载视频', a.path],
      ['ASS', a.bundle + '/lyrics.ass'],
      ['逐字 JSON', a.bundle + '/timeline.json'],
      ['增强 LRC', a.bundle + '/lyrics.lrc'],
      ['原始歌词', a.bundle + '/lyrics.original.txt'],
      ['流程记录', a.bundle + '/project.json'],
      ['完整歌词', a.bundle + '/lyrics.full.txt'],
      ['未匹配清单', a.bundle + '/unmatched.json'],
      ['导出说明', a.bundle + '/export_report.json'],
      ...(a.report?.auto_complete !== undefined
        ? [['估算时间清单', a.bundle + '/estimated.json']]
        : []),
    ].entries()) {
      const link = document.createElement('a');
      link.textContent = name;
      link.href = fileURL(path);
      link.download = path.split('/').at(-1);
      (index === 0 ? div : extra).append(link);
    }
    div.append(extra);
    box.append(div);
  }
}

export function renderHealth(h) {
  const missing = Object.entries(h.checks).filter(([k, c]) => !c.ok && !c.optional);
  $('health').textContent = missing.length
    ? '本地组件需安装：' + missing.map(([k, c]) => c.error || c.path).join('；')
    : '本地组件已就绪 · NVIDIA CUDA';
}
