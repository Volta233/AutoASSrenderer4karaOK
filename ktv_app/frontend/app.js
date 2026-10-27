// Four-step UI controller: project state, editing, view updates and user actions.
import { $, api, notify, handler } from './ui.js';
import { overlayWindows } from './display-timing.js';
import { initSettings } from './settings.js';
import {
  renderAlignmentStatus,
  renderCandidates,
  renderArtifacts,
  renderHealth,
} from './project-views.js';
import { createTimelineEditor } from './timeline-editor.js';

let currentStep = 1,
  locatorReady = false;
let project = null,
  timeline = null,
  selected = null,
  dirty = false,
  saving = false,
  saveTimer = null,
  peaks = null,
  playbackFailed = false,
  playbackRequested = false,
  currentPlayback = '';
const stages = {
  created: '已创建',
  queued: '等待处理',
  media: '提取音频',
  media_ready: '音频就绪',
  search: '联网查询歌词',
  choose_lyrics: '等待确认歌词',
  lyrics_ready: '歌词就绪',
  complete: '自动补齐中',
  align: '识别与细对齐中',
  aligned: '时间轴已生成',
  ready: '就绪',
  review: '待校对',
  edited: '修改已保存',
  render: '视频渲染中',
  playback: '准备兼容视频播放',
  failed: '处理失败',
  interrupted: '任务中断',
};

const { renderLines, renderUnits } = createTimelineEditor({
  getState: () => ({ project, timeline, selected }),
  selectLine: (id) => {
    selected = id;
  },
  markDirty,
  onTimingChange: () => renderAlignmentStatus(project, timeline),
});

function fileURL(name) {
  return `/api/projects/${project.id}/files/${name.split('/').map(encodeURIComponent).join('/')}`;
}
async function listProjects() {
  const list = await api('/projects');
  const options = list.map((p) => {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = (p.song || '未命名歌曲') + ' · ' + new Date(p.created * 1000).toLocaleString();
    return o;
  });
  $('projectList').replaceChildren(new Option('打开项目…', ''), ...options);
  if (project) $('projectList').value = project.id;
}

function applyProject(p, refresh = true) {
  project = p;
  $('workspace').hidden = false;
  $('projectTitle').textContent = (p.song || '未命名歌曲') + (p.artist ? ' · ' + p.artist : '');
  $('stage').textContent = stages[p.stage] || p.stage;
  $('stage').className = p.busy ? 'busy' : '';
  $('error').hidden = !p.error;
  $('error').textContent = p.error || '';
  $('logs').textContent = p.events
    .map((e) => new Date(e.time * 1000).toLocaleTimeString() + ' ' + e.message)
    .join('\n');
  document.querySelectorAll('#workspace button').forEach((b) => (b.disabled = p.busy));
  if (refresh) {
    timeline = p.timeline || null;
    renderLines();
    renderUnits();
    $('diagnostics').textContent = JSON.stringify(timeline?.diagnostics || {}, null, 2);
  }
  renderAlignmentStatus(project, timeline);
  renderCandidates(project, { flush, applyProject });
  renderArtifacts(project, fileURL);
  refreshSteps();
  if (timeline) $('timelineDownload').href = fileURL('timeline.json');
}

function refreshSteps() {
  const steps = project?.workflow?.steps || [];
  document.querySelectorAll('[data-step]').forEach((b) => {
    const i = Number(b.dataset.step);
    b.disabled = i > 1 && !steps[i - 1]?.available;
    b.classList.toggle('active', i === currentStep);
    b.setAttribute('aria-current', i === currentStep ? 'step' : 'false');
    b.querySelector('i').textContent = steps[i - 1]?.complete ? '✓' : '';
  });
  document.querySelectorAll('[data-next]').forEach((b) => {
    const i = Number(b.dataset.next);
    b.disabled = !!project?.busy || (i > 1 && !steps[i - 1]?.available);
  });
  $('create').disabled = !!project?.busy;
  $('saveInfo').hidden = !project;
  $('saveInfo').disabled = !!project?.busy;
  $('retryMedia').hidden = !project;
  $('retryMedia').disabled = !!project?.busy;
  $('mediaSummary').textContent = project?.media
    ? `${project.media.duration.toFixed(1)} 秒 · 视频已就绪`
    : '';
  $('lyricsSummary').textContent = project?.lyrics?.length
    ? `已保存 ${project.lyrics.length} 行`
    : '尚未保存';
  $('renderSnapshot').textContent = timeline
    ? `${timeline.lines.length} 行歌词 · 时间轴已保存`
    : '';
}
async function showStep(step) {
  if (step > 1 && !project?.workflow?.steps?.[step - 1]?.available) {
    notify('请先完成前一步');
    return;
  }
  await flush();
  currentStep = step;
  document.querySelectorAll('.step-panel').forEach((p) => (p.hidden = p.id !== 'step' + step));
  refreshSteps();
  if (project) {
    sessionStorage.setItem('ktvWorkflow', project.id);
    sessionStorage.setItem('ktvStep', String(step));
  }
  window.scrollTo({
    top: 0,
    behavior: 'smooth',
  });
}

async function loadProject(pid) {
  await flush();
  dirty = false;
  selected = null;
  peaks = null;
  applyProject(await api('/projects/' + pid));
  playbackFailed = false;
  playbackRequested = false;
  currentPlayback = project.playback || '';
  $('videoPath').value = project.video;
  $('videoFile').value = '';
  $('video').src = currentPlayback ? fileURL(currentPlayback) : `/api/projects/${pid}/video`;
  $('lyrics').value = (project.lyrics || project.timeline?.lines || [])
    .map((l) => l.text)
    .join('\n');
  for (const key of ['song', 'artist', 'language']) $(key).value = project[key];
  const opts = timeline?.diagnostics?.alignment_options || {};
  $('vocalStart').value = opts.vocal_start ?? '';
  $('vocalEnd').value = opts.vocal_end ?? '';
  for (const key of ['font', 'size', 'position', 'base', 'highlight', 'tail'])
    $(key).value = project.style[key];
  await loadWave();
  $('projectList').value = pid;
  updateModelHint();
}
async function loadWave() {
  if (!project?.media) return;
  try {
    peaks = await fetch(fileURL('waveform.json')).then((r) => r.json());
    drawWave();
  } catch {}
}

function markDirty() {
  dirty = true;
  $('saveState').textContent = '有修改，正在自动保存…';
  clearTimeout(saveTimer);
  saveTimer = setTimeout(handler(flush), 700);
}
async function flush() {
  clearTimeout(saveTimer);
  if (saving) {
    await saving;
    return flush();
  }
  if (!dirty || !timeline) return;
  const snapshot = structuredClone(timeline);
  dirty = false;
  saving = (async () => {
    try {
      const p = await api(`/projects/${project.id}/timeline`, 'PUT', {
        revision: project.revision,
        timeline: snapshot,
      });
      project = p;
      if (!dirty) {
        timeline = p.timeline;
        $('saveState').textContent = '已自动保存';
        $('diagnostics').textContent = JSON.stringify(timeline.diagnostics, null, 2);
      } else timeline.revision = p.revision;
    } catch (e) {
      dirty = true;
      $('saveState').textContent = '保存失败，请修正时间后重试';
      throw e;
    } finally {
      saving = false;
    }
  })();
  await saving;
  if (dirty) await flush();
}

function styleValues() {
  return {
    ...project.style,
    auto_complete: true,
    mode: 'double',
    font: $('font').value,
    size: Number($('size').value),
    position: Number($('position').value),
    base: $('base').value,
    highlight: $('highlight').value,
    lead: 0,
    tail: Number($('tail').value),
  };
}
async function saveStyle() {
  project = await api(`/projects/${project.id}/style`, 'PUT', styleValues());
}
async function task(action, options = {}) {
  await flush();
  if (action === 'search')
    options = {
    };
  if (action === 'segment')
    options = {
      ...options,
    };
  if (['align', 'complete'].includes(action)) options = alignmentOptions();
  if (action === 'export') await saveStyle();
  const step = {
    media: 1,
    search: 2,
    align: 3,
    segment: 3,
    complete: 3,
    export: 4,
  }[action];
  applyProject(
    await api(
      step ? `/workflows/${project.id}/steps/${step}` : `/projects/${project.id}/tasks`,
      'POST',
      {
        action,
        options,
      },
    ),
    false,
  );
}

function drawWave() {
  const canvas = $('wave'),
    ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!peaks) return;
  const duration = project.media.duration;
  ctx.fillStyle = '#5f7694';
  const top = canvas.height / 2;
  peaks.peaks.forEach((p, i) => {
    const x =
      ((peaks.origin + (i / peaks.peaks.length) * peaks.duration) / duration) * canvas.width;
    ctx.fillRect(
      x,
      top - p * top,
      Math.max(1, ((peaks.duration / duration) * canvas.width) / peaks.peaks.length),
      p * canvas.height,
    );
  });
  if (timeline) {
    ctx.fillStyle = '#ffce4b';
    for (const l of timeline.lines)
      if (l.start !== null)
        ctx.fillRect(
          (l.start / duration) * canvas.width,
          5,
          Math.max(2, ((l.end - l.start) / duration) * canvas.width),
          6,
        );
  }
  ctx.fillStyle = '#fff';
  ctx.fillRect(($('video').currentTime / duration) * canvas.width, 0, 2, canvas.height);
}

function drawOverlay() {
  const ctx = $('overlay').getContext('2d');
  ctx.clearRect(0, 0, 1280, 720);
  if (!timeline || !$('overlayEnabled').checked) {
    requestAnimationFrame(drawOverlay);
    return;
  }
  const now = $('video').currentTime,
    s = styleValues(),
    windows = overlayWindows(timeline, s);
  ctx.font = `bold ${s.size}px "${s.font.replaceAll('"', '')}",sans-serif`;
  ctx.textBaseline = 'bottom';
  ctx.lineJoin = 'round';
  timeline.lines.forEach((line) => {
    const w = windows.get(line.id);
    if (!w || now < w.start || now >= w.end) return;
    const review = line.display_kind === 'review',
      y = review ? 100 : 720 * s.position - (s.mode === 'double' ? (1 - w.slot) * s.size * 1.4 : 0),
      x = (1280 - ctx.measureText(line.text).width) / 2;
    ctx.strokeStyle = '#121212';
    ctx.lineWidth = s.outline * 2;
    ctx.strokeText(line.text, x, y);
    ctx.fillStyle = s.base;
    ctx.fillText(line.text, x, y);
    if (review) return;
    for (const u of line.units) {
      if (
        u.start === null ||
        now < u.start ||
        (!s.auto_complete && u.timing_source === 'estimated')
      )
        continue;
      const a = x + ctx.measureText(line.text.slice(0, u.source_start)).width,
        b = x + ctx.measureText(line.text.slice(0, u.source_end)).width,
        p = Math.min(1, (now - u.start) / (u.end - u.start));
      ctx.save();
      ctx.beginPath();
      ctx.rect(a, y - s.size * 1.4, (b - a) * p, s.size * 1.6);
      ctx.clip();
      ctx.fillStyle = s.highlight;
      ctx.fillText(line.text, x, y);
      ctx.restore();
    }
  });
  requestAnimationFrame(drawOverlay);
}
$('create').onclick = handler(async () => {
  await flush();
  $('create').disabled = true;
  try {
    let p;
    if ($('videoFile').files[0]) {
      const data = new FormData();
      data.append('video', $('videoFile').files[0]);
      for (const k of ['song', 'artist', 'language']) data.append(k, $(k).value);
      p = await api('/upload', 'POST', data);
    } else
      p = await api('/projects', 'POST', {
        video: $('videoPath').value,
        song: $('song').value,
        artist: $('artist').value,
        language: $('language').value,
      });
    await loadProject(p.id);
    await listProjects();
    await showStep(1);
    await task('media');
  } finally {
    $('create').disabled = !!project?.busy;
  }
});
$('projectList').onchange = handler(async () => {
  if ($('projectList').value) {
    await loadProject($('projectList').value);
    await showStep(project.workflow?.recommended_step || 1);
  }
});
document
  .querySelectorAll('[data-task]')
  .forEach((b) => (b.onclick = handler(() => task(b.dataset.task))));
$('lyricsFile').onchange = handler(async () => {
  const file = $('lyricsFile').files[0];
  if (!file) return;
  const data = new FormData();
  data.append('file', file);
  const result = await api('/lyrics/import', 'POST', data);
  $('lyrics').value = result.text;
  notify('已导入，请确认并保存歌词');
});
$('useLyrics').onclick = handler(async () => {
  await flush();
  const updated = await api(`/projects/${project.id}/lyrics`, 'POST', {
    text: $('lyrics').value,
  });
  applyProject(updated);
  $('lyrics').value = updated.lyrics.map((line) => line.text).join('\n');
  notify('歌词已保存');
});
$('undo').onclick = handler(async () => {
  await flush();
  applyProject(await api(`/projects/${project.id}/undo`, 'POST', {}));
  notify('已撤销最近一次修改');
});
$('addLine').onclick = () => {
  if (!timeline) {
    notify('请先导入歌词并完成一次对齐');
    return;
  }
  const id = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  timeline.lines.push({
    id,
    text: '新增歌词',
    normalized: '新增歌词',
    language: project.language === 'auto' ? 'zh' : project.language,
    source_line: timeline.lines.length,
    start: null,
    end: null,
    units: [],
    flags: ['needs_review'],
  });
  selected = id;
  markDirty();
  renderLines();
  renderUnits();
};
$('shift').onclick = handler(async () => {
  await flush();
  applyProject(
    await api(`/projects/${project.id}/shift`, 'POST', {
      seconds: Number($('shiftSeconds').value),
      revision: project.revision,
    }),
  );
  notify('时间轴已平移');
});
$('segment').onclick = handler(async () => {
  const ids = [...document.querySelectorAll('.segmentCheck:checked')].map((c) => c.dataset.lineid);
  await task('segment', {
    start: Number($('segmentStart').value),
    end: Number($('segmentEnd').value),
    line_ids: ids,
  });
});

function renderOptions() {
  const options = {
    crf: Number($('crf').value),
    preset: $('preset').value,
    output_dir: $('outputDir').value,
  };
  if ($('resolution').value) {
    [options.width, options.height] = $('resolution').value.split('x').map(Number);
  }
  return options;
}
$('export').onclick = handler(() => task('export', renderOptions()));
$('wave').onclick = (e) => {
  if (project?.media) {
    const r = $('wave').getBoundingClientRect();
    $('video').currentTime = ((e.clientX - r.left) / r.width) * project.media.duration;
  }
};
$('video').ontimeupdate = () => {
  $('time').textContent = $('video').currentTime.toFixed(2) + ' 秒';
  drawWave();
};
$('video').onloadedmetadata = () => {
  const v = $('video');
  if (v.videoWidth && v.videoHeight)
    $('playerBox').style.aspectRatio = `${v.videoWidth}/${v.videoHeight}`;
};
window.addEventListener('beforeunload', (e) => {
  if (dirty || saving) {
    e.preventDefault();
    e.returnValue = '';
  }
});
async function poll() {
  if (!project || dirty || saving) return;
  try {
    const p = await api('/projects/' + project.id);
    if (dirty || saving || p.id !== project.id || p.revision < project.revision) return;
    const changed = p.revision !== project.revision;
    const editing =
      $('workspace').contains(document.activeElement) &&
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    applyProject(p, changed);
    if ($('saveState').textContent.startsWith('连接中断')) $('saveState').textContent = '已连接';
    if (!peaks && p.media) await loadWave();
    if (p.playback && currentPlayback !== p.playback) {
      currentPlayback = p.playback;
      $('video').src = fileURL(p.playback);
      playbackFailed = false;
    }
    if (playbackFailed && !playbackRequested && !p.busy) {
      playbackRequested = true;
      await task('playback');
    }
  } catch (e) {
    $('saveState').textContent = '连接中断，正在重试';
  }
}
handler(async () => {
  await listProjects();
  const h = await api('/health');
  locatorReady = !!h.locator_ready;
  updateModelHint();
  renderHealth(h);
  setInterval(poll, 1500);
  requestAnimationFrame(drawOverlay);
  const last = sessionStorage.getItem('ktvWorkflow');
  if (last) {
    try {
      await loadProject(last);
      await showStep(
        Number(sessionStorage.getItem('ktvStep')) || project.workflow.recommended_step,
      );
    } catch {
      sessionStorage.removeItem('ktvWorkflow');
    }
  }
})();

$('video').onerror = () => {
  if (!currentPlayback) {
    playbackFailed = true;
    notify('当前浏览器不支持输入编码，正在准备兼容播放副本…');
  } else notify('浏览器播放副本失败，请查看处理日志。');
};

function alignmentOptions() {
  return {
    vocal_start: $('vocalStart').value === '' ? null : Number($('vocalStart').value),
    vocal_end: $('vocalEnd').value === '' ? null : Number($('vocalEnd').value),
  };
}

function updateModelHint() {
  $('modelHint').textContent = locatorReady
    ? 'Kim 人声提取 → 歌词识别 → TIFA 同步'
    : '识别组件未安装，请按安装说明准备。';
  $('modelHint').closest('details').open = !locatorReady;
}

document
  .querySelectorAll('[data-step],[data-next]')
  .forEach((b) => (b.onclick = handler(() => showStep(Number(b.dataset.step || b.dataset.next)))));
$('newWorkflow').onclick = handler(async () => {
  await flush();
  project = null;
  timeline = null;
  selected = null;
  peaks = null;
  $('video').pause();
  $('video').removeAttribute('src');
  $('workspace').hidden = true;
  $('projectList').value = '';
  for (const key of ['song', 'artist', 'videoPath']) $(key).value = '';
  $('videoFile').value = '';
  $('language').value = 'auto';
  sessionStorage.removeItem('ktvWorkflow');
  sessionStorage.removeItem('ktvStep');
  await showStep(1);
});
$('saveInfo').onclick = handler(async () => {
  await flush();
  applyProject(
    await api(`/workflows/${project.id}/information`, 'PUT', {
      song: $('song').value,
      artist: $('artist').value,
      language: $('language').value,
    }),
  );
  notify('歌曲信息已保存，查询结果将在重新查询后更新');
});

initSettings({ onHealth: renderHealth });
