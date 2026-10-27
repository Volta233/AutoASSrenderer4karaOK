// Timeline rows and manual edits; the controller owns selection and autosave.
import { $, notify } from './ui.js';

const flags = {
  needs_review: '需要复核',
  missing_time: '缺少时间',
  unmatched_text: '文字未匹配',
  overlap_or_drift: '重叠/漂移',
  too_short: '过短',
  long_sustain_review: '长音，请试听',
  low_model_support: '模型支持较弱',
  auto_estimated: '自动补齐（估算）',
  estimated_time: '自动估算时间',
  partial_asr_match: '句内字词匹配找回，请试听',
  boundary_limited: '边界受音频定位约束，请试听',
  asr_unmatched: '音频识别未匹配，可继续输出',
  alignment_failed: '本行暂未定位，可继续输出',
};

export function createTimelineEditor({ getState, selectLine, markDirty, onTimingChange }) {
  function renderLines() {
    const { timeline, selected } = getState();
    const box = $('lineList');
    box.replaceChildren();
    if (!timeline) {
      box.textContent = '同步后显示歌词时间轴';
      return;
    }
    for (const line of timeline.lines) {
      const row = document.createElement('div');
      row.className =
        'lyricrow' +
        (line.id === selected ? ' selected' : '') +
        (line.flags.length ? ' review' : '');
      const head = document.createElement('div');
      head.className = 'row';
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.dataset.lineid = line.id;
      check.className = 'segmentCheck';
      const play = document.createElement('button');
      play.textContent = '▶';
      play.setAttribute('aria-label', '试听本句');
      play.disabled = line.start === null;
      play.onclick = () => {
        $('video').currentTime = line.start || 0;
        selectLine(line.id);
        renderUnits();
        box.querySelectorAll('.lyricrow').forEach((r) => r.classList.remove('selected'));
        row.classList.add('selected');
      };
      const tag = document.createElement('span');
      tag.className = 'flag';
      const estimated = line.units.some((u) => u.timing_source === 'estimated');
      tag.textContent = estimated ? '含估算' : line.flags.length ? '待校对' : '';
      tag.title = line.flags.map((f) => flags[f] || '待校对').join(' · ');
      head.append(check, play, tag);
      const text = document.createElement('textarea');
      text.rows = 1;
      text.value = line.text;
      text.onchange = () => {
        line.text = text.value;
        line.normalized = text.value.normalize('NFKC');
        line.normalization_changes = [];
        line.units = [];
        line.start = null;
        line.end = null;
        line.flags = ['needs_review'];
        markDirty();
        selectLine(line.id);
        renderUnits();
        tag.textContent = '文字已修改，请重新对齐';
      };
      const times = document.createElement('div');
      times.className = 'row';
      for (const name of ['start', 'end']) {
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '.01';
        input.min = '0';
        input.value = line[name] === null ? '' : line[name].toFixed(2);
        input.placeholder = name === 'start' ? '开始' : '结束';
        input.setAttribute('aria-label', name === 'start' ? '本句开始时间' : '本句结束时间');
        input.oninput = () => {
          setLineBounds(line, times, tag);
        };
        times.append(input);
      }
      const choose = document.createElement('button');
      choose.textContent = '逐字校正';
      choose.onclick = () => {
        selectLine(line.id);
        renderUnits();
      };
      const del = document.createElement('button');
      del.textContent = '删除';
      del.onclick = () => {
        timeline.lines = timeline.lines.filter((l) => l.id !== line.id);
        markDirty();
        renderLines();
        renderUnits();
      };
      times.append(choose, del);
      row.append(head, text, times);
      box.append(row);
    }
  }

  function setLineBounds(line, box, tag) {
    const { timeline } = getState();
    const inputs = box.querySelectorAll('input');
    const a = inputs[0].value,
      b = inputs[1].value;
    if ((a === '') !== (b === '')) {
      tag.textContent = '同时填写开始和结束即可手动定位；也可直接导出已有结果';
      return;
    }
    if (a === '' && b === '') {
      for (const u of line.units) {
        u.start = u.end = null;
        u.phones = [];
        u.readings = [];
      }
      line.start = line.end = null;
      line.flags = ['needs_review'];
      markDirty();
      renderUnits();
      onTimingChange();
      return;
    }
    const start = Number(a),
      end = Number(b);
    if (
      !(
        Number.isFinite(start) &&
        Number.isFinite(end) &&
        0 <= start &&
        start < end &&
        end <= timeline.duration
      )
    ) {
      notify('请填写视频范围内的开始与结束时间');
      return;
    }
    const valid = line.units.filter((u) => u.start !== null);
    if (!valid.length || line.display_kind === 'review') {
      line.display_kind = 'karaoke';
      line.completion_note = '用户手动定位整行';
      line.units = [
        {
          id: line.id + '-manual-' + crypto.randomUUID().slice(0, 8),
          text: line.text,
          normalized: line.normalized || line.text,
          timing_source: 'manual',
          source_start: 0,
          source_end: line.text.length,
          start,
          end,
          readings: [],
          phones: [],
          flags: [],
        },
      ];
      tag.textContent = '已手动设置整行时间，可导出或再细对齐';
    } else {
      valid[0].start = start;
      valid.at(-1).end = end;
      for (const u of [valid[0], valid.at(-1)]) {
        u.timing_source = 'manual';
        u.phones = [];
        u.readings = [];
      }
      tag.textContent = '已修改本行边界，可直接导出';
    }
    line.start = start;
    line.end = end;
    line.flags = ['needs_review'];
    markDirty();
    renderUnits();
    onTimingChange();
  }

  function renderUnits() {
    const { project, timeline, selected } = getState();
    const box = $('units');
    box.replaceChildren();
    const line = timeline?.lines.find((l) => l.id === selected);
    $('unitTitle').textContent = line ? line.text : '选择一句歌词';
    if (!line) return;
    for (const u of line.units) {
      const row = document.createElement('div');
      row.className = 'unit';
      const label = document.createElement('span');
      label.textContent = u.text;
      const small = document.createElement('small');
      small.textContent =
        u.timing_source === 'estimated' ? '估算' : u.timing_source === 'manual' ? '手动' : '';
      small.title = [
        u.readings.map((p) => p.text).join(' ') || u.normalized,
        ...u.flags.map((f) => flags[f] || '待校对'),
      ]
        .filter(Boolean)
        .join(' · ');
      label.append(small);
      row.append(label);
      for (const name of ['start', 'end']) {
        const input = document.createElement('input');
        input.type = 'number';
        input.min = '0';
        input.step = '.01';
        input.setAttribute('aria-label', `${u.text} ${name === 'start' ? '开始' : '结束'}时间`);
        input.value = u[name] === null ? '' : u[name].toFixed(3);
        input.disabled = project.busy;
        input.oninput = () => {
          u[name] = input.value === '' ? null : Number(input.value);
          u.phones = [];
          u.readings = [];
          markDirty();
        };
        row.append(input);
      }
      const b = document.createElement('button');
      b.textContent = '▶';
      b.setAttribute('aria-label', '试听字词');
      b.disabled = u.start === null;
      b.onclick = () => {
        $('video').currentTime = u.start || 0;
      };
      row.append(b);
      box.append(row);
    }
  }
  return { renderLines, renderUnits };
}
