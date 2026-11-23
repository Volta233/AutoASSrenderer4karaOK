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

// Whitspace-insensitive text identity: adopted cards are recognised without a server round trip.
export function lyricKey(text) {
  return (text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .join('\n');
}

const ADVICE = { recommended: '建议优先试听', compare: '请确认演唱版本', review: '需核对差异' };

export function renderCandidates(project, { flush, applyProject, onAdopt }) {
  const box = $('candidates');
  // Lyrics and busy state change the adopted highlight and the button state.
  const signature = JSON.stringify([project.candidates, project.lyrics, project.busy]);
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
  const chosen = lyricKey((project.lyrics || []).map((line) => line.text).join('\n'));
  let more;
  for (const [index, c] of project.candidates.candidates.entries()) {
    if (index === 3) {
      more = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `更多版本（${project.candidates.candidates.length - 3}）`;
      more.append(summary);
      box.append(more);
    }
    const adopted = Boolean(chosen) && lyricKey(c.text) === chosen;
    const div = document.createElement('div');
    div.className = adopted ? 'candidate adopted' : 'candidate';
    const header = document.createElement('div');
    header.className = 'candidate-header';
    const title = document.createElement('strong');
    title.textContent = `${c.song} · ${c.artist || '未知歌手'}`;
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = ADVICE[c.recommendation] || '请确认演唱版本';
    title.append(badge);
    const button = document.createElement('button');
    button.className = 'adopt';
    button.textContent = adopted ? '已采用' : '采用';
    button.disabled = project.busy;
    button.onclick = handler(async () => {
      await flush();
      const updated = await api(`/projects/${project.id}/lyrics`, 'POST', {
        candidate: c.id,
      });
      applyProject(updated);
      if (onAdopt) onAdopt(updated, c);
      else notify('已采用歌词');
    });
    header.append(title, button);
    const metadata = document.createElement('p');
    metadata.className = 'candidate-meta';
    metadata.textContent = [
      c.provider || '未知来源',
      c.duration ? `${c.duration} 秒` : '时长未知',
      c.album,
    ]
      .filter(Boolean)
      .join(' · ');
    const excerpt = document.createElement('p');
    excerpt.className = 'candidate-excerpt';
    excerpt.textContent = c.text
      .split('\n')
      .filter((line) => line.trim())
      .slice(0, 2)
      .join('\n');
    const actions = document.createElement('div');
    actions.className = 'candidate-actions';
    let href = '';
    try {
      const url = new URL(c.source);
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password)
        href = url.href;
    } catch {
      href = '';
    }
    const lines = c.text.split('\n').filter((line) => line.trim()).length;
    if (href) {
      const src = document.createElement('a');
      src.href = href;
      src.target = '_blank';
      src.rel = 'noopener noreferrer';
      src.textContent = `查看来源 · ${c.provider || new URL(href).hostname}`;
      actions.append(src);
    }
    const count = document.createElement('span');
    count.className = 'muted';
    count.textContent = `${lines} 行 · ${
      c.evidence
        ? `歌名相似度 ${(c.evidence.title_similarity * 100).toFixed(0)}% / 歌手 ${
            c.evidence.artist_similarity == null
              ? '未填写'
              : (c.evidence.artist_similarity * 100).toFixed(0) + '%'
          } · 时长差 ${c.evidence.duration_difference_seconds ?? '未知'} 秒`
        : '尚无版本证据'
    }`;
    actions.append(count);
    const full = document.createElement('details');
    full.className = 'candidate-text';
    const fullSummary = document.createElement('summary');
    fullSummary.textContent = `预览全文（${lines} 行）`;
    const pre = document.createElement('pre');
    pre.textContent = c.text;
    full.append(fullSummary, pre);
    const notes = document.createElement('div');
    if (c.content_languages?.length) {
      const p = document.createElement('p');
      p.className = 'muted';
      p.textContent = `检测语言 ${c.content_languages.join(' / ')}；未比较实际演唱`;
      notes.append(p);
    }
    if (c.recommendation_reasons?.length) {
      const note = document.createElement('p');
      note.textContent = c.recommendation_reasons.join('；');
      notes.append(note);
    }
    for (const warning of c.warnings || []) {
      const note = document.createElement('p');
      note.className = 'flag';
      note.textContent = warning;
      notes.append(note);
    }
    const comparison = document.createElement('details');
    comparison.className = 'candidate-version';
    const comparisonTitle = document.createElement('summary');
    comparisonTitle.textContent = '版本信息';
    comparison.append(comparisonTitle, notes);
    div.append(header, metadata);
    if (c.requires_version_review) {
      const warning = document.createElement('p');
      warning.className = 'flag candidate-note';
      warning.textContent = '版本可能不同，请核对歌词。';
      div.append(warning);
    }
    div.append(excerpt, actions, full, comparison);
    (more || box).append(div);
  }
}

export function renderArtifacts(project) {
  const key = JSON.stringify([project.id, project.artifacts]);
  const box = $('artifacts');
  if (key === box.dataset.signature) return;
  box.dataset.signature = key;
  box.replaceChildren();
  for (const a of [...project.artifacts].reverse().filter((item) => item.kind === 'export')) {
    const div = document.createElement('div');
    div.className = 'artifact';
    const label = document.createElement('p');
    label.textContent = a.saved
      ? `成片已保存到 ${a.saved}`
      : a.save_error
        ? '成片已渲染，但未能写入下载目录'
        : '成片已渲染，保存在项目文件夹';
    div.append(label);
    if (a.save_error) {
      const warning = document.createElement('p');
      warning.className = 'flag';
      warning.textContent = a.save_error;
      div.append(warning);
    } else if (!a.saved) {
      const hint = document.createElement('p');
      hint.className = 'muted';
      hint.textContent = '在「设置」里填写本地下载目录后，导出会直接保存到该文件夹。';
      div.append(hint);
    }
    if (a.report) {
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = `${a.report.rendered_lines}/${a.report.total_lines} 行字幕 · ${a.report.estimated_lines || 0} 行含估算 · ${a.report.unmatched_lines} 行未定位${a.report.no_timed_subtitles ? ' · 无已定位字幕' : ''}`;
      div.append(note);
    }
    const bundle = document.createElement('p');
    bundle.className = 'muted';
    bundle.textContent = `字幕与项目文件：${a.bundle}`;
    div.append(bundle);
    box.append(div);
  }
}

export function renderHealth(h) {
  const missing = Object.entries(h.checks).filter(([k, c]) => !c.ok && !c.optional);
  $('health').textContent = missing.length
    ? '本地组件需安装：' + missing.map(([k, c]) => c.error || c.path).join('；')
    : '本地组件已就绪 · NVIDIA CUDA';
}
