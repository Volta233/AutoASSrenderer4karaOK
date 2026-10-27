// Pure timing rules mirrored by subtitles.py; no DOM or network access.
function displayedBounds(line, style) {
  const units = line.units.filter(
    (u) => u.start !== null && (style.auto_complete || u.timing_source !== 'estimated'),
  );
  return units.length
    ? {
        start: Math.min(...units.map((u) => u.start)),
        end: Math.max(...units.map((u) => u.end)),
      }
    : null;
}

function lyricWeight(text) {
  return [...text.normalize('NFKC')].filter((c) => /[\p{L}\p{N}]/u.test(c)).length;
}

function sweepMidpoint(line, bounds, s) {
  const target = lyricWeight(line.text) / 2;
  for (const u of line.units) {
    if (u.start === null || (!s.auto_complete && u.timing_source === 'estimated')) continue;
    const before = lyricWeight(line.text.slice(0, u.source_start)),
      weight = lyricWeight(line.text.slice(u.source_start, u.source_end));
    if (weight && before <= target && target <= before + weight)
      return u.start + ((u.end - u.start) * (target - before)) / weight;
  }
  return (bounds.start + bounds.end) / 2;
}
export function overlayWindows(timeline, s) {
  const rows = timeline.lines
    .map((line) => ({
      line,
      bounds: displayedBounds(line, s),
    }))
    .filter((r) => r.bounds && r.line.display_kind !== 'review');
  const result = new Map();
  let cursor = 0;
  const starts = rows.map((r, i) => {
    let start =
      i < 2
        ? Math.max(0, rows[0].bounds.start - s.lead)
        : Math.max(
            0,
            Math.min(r.bounds.start, sweepMidpoint(rows[i - 1].line, rows[i - 1].bounds, s)),
          );
    if (i && r.bounds.start - rows[i - 1].bounds.end > 4)
      start = Math.max(start, r.bounds.start - 2);
    return start;
  });
  rows.forEach((r, i) => {
    const start = s.mode === 'double' ? starts[i] : Math.max(cursor, 0, r.bounds.start - s.lead);
    let end =
      s.mode === 'double'
        ? i + 2 < rows.length
          ? starts[i + 2]
          : i + 1 < rows.length
            ? Math.max(r.bounds.end, sweepMidpoint(rows[i + 1].line, rows[i + 1].bounds, s))
            : r.bounds.end + s.tail
        : Math.min(r.bounds.end + s.tail, rows[i + 1]?.bounds.start ?? Infinity);
    if (s.mode === 'double' && i + 1 < rows.length && rows[i + 1].bounds.start - r.bounds.end > 4)
      end = Math.min(end, r.bounds.end + Math.max(0.5, s.tail));
    if (
      s.mode === 'double' &&
      i + 2 < rows.length &&
      rows[i + 2].bounds.start - rows[i + 1].bounds.end > 4
    )
      end = Math.min(end, Math.max(r.bounds.end, rows[i + 1].bounds.end) + Math.max(0.5, s.tail));
    result.set(r.line.id, {
      start,
      end: Math.min(timeline.duration, end),
      slot: s.mode === 'double' ? i % 2 : 0,
    });
    cursor = end;
  });
  timeline.lines
    .filter((l) => l.display_kind === 'review')
    .forEach((l) => {
      const b = displayedBounds(l, s);
      if (b)
        result.set(l.id, {
          ...b,
          slot: 'review',
        });
    });
  return result;
}
