"""Check the served ES-module graph and browser/export timing agreement without a browser."""

import json
from pathlib import Path
import re
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from ktv_app.backend.app import create_app
from ktv_app import __version__
from ktv_app.config import Settings
from ktv_app.lyrics import preprocess
from ktv_app.subtitles import display_windows
from ktv_app.timeline import Timeline, Style, Unit, quality

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def test_lyric_candidates_keep_failure_diagnostics_and_fold_other_versions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional locally; CI installs it for frontend checks")
    script = """
import assert from 'node:assert/strict';
class Element {
  children = []; dataset = {}; textContent = '';
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
}
const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  createElement(tag) { return Object.assign(new Element(), {tag}); },
};
const { renderCandidates } = await import(MODULE_URL);
const attempts = [{provider:'LRCLIB',query:{track_name:'千本樱'},status:'failed',http_status:503}];
renderCandidates({candidates:{actual_query_attempts:attempts,candidates:[],retrieval_status:'unavailable'}}, {});
let box = elements.get('candidates');
assert.equal(box.children[0].tag, 'details');
assert.match(box.children[0].children[1].textContent, /503/);
assert.match(box.children[1].textContent, /来源暂不可用/);
renderCandidates({candidates:{candidates:[],verification:{web:{sources:[
 {title:'Actual source',url:'https://example.com/lyrics'},
 {title:'Unsafe',url:'javascript:alert(1)'}
]}}}}, {});
const sources = box.children.at(-1);
assert.equal(sources.tag, 'details');
assert.equal(sources.children.length, 2);
assert.equal(sources.children[1].children[0].href, 'https://example.com/lyrics');
const candidate = {id:'1',song:'千本桜',artist:'初音ミク',score:1,provider:'网易云音乐',
 source:'https://music.163.com/song?id=1',text:'original',recommendation:'compare',
 recommendation_reasons:['未填写歌手'],evidence:{title_similarity:1,artist_similarity:null}};
renderCandidates({candidates:{actual_query_attempts:attempts,candidates:Array(5).fill(candidate)}}, {});
assert.equal(box.children.filter(n => n.className === 'candidate').length, 3);
const more = box.children.at(-1);
assert.equal(more.tag, 'details');
assert.equal(more.children.length, 3);
assert.match(more.children[0].textContent, /更多版本（2）/);
const card = box.children[1];
assert.equal(card.children[0].children[0].textContent, '千本桜 · 初音ミク');
assert.equal(card.children[0].children[0].children[0].textContent, '确认版本');
assert.equal(card.children.at(-1).tag, 'details');
assert.equal(card.children.at(-1).children[0].textContent, '版本信息');
""".replace("MODULE_URL", json.dumps((FRONTEND / "project-views.js").as_uri()))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_page_serves_all_explicit_module_dependencies(tmp_path):
    with TestClient(create_app(Settings(projects=tmp_path / "projects"))) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'type="module"' in page.text
        for path in FRONTEND.glob("*.js"):
            response = client.get("/" + path.name)
            assert response.status_code == 200
            for dependency in re.findall(
                r"from\s+['\"]\./([^'\"]+)['\"]", response.text
            ):
                assert client.get("/" + dependency).status_code == 200


def test_extracted_editor_keeps_manual_times_and_reads_current_selection():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional locally; CI installs it for frontend checks")
    module = (FRONTEND / "timeline-editor.js").as_uri()
    script = """
import assert from 'node:assert/strict';
class Element {
  children = [];
  value = '';
  style = {};
  dataset = {};
  setAttribute(name, value) { this[name] = value; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelectorAll() { return this.children.filter(n => n.tag === 'input'); }
}
const elements = new Map();
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  createElement(tag) { return Object.assign(new Element(), { tag }); },
};
const { createTimelineEditor } = await import(MODULE_URL);
const unit = {text:'Hello', start:1, end:2, phones:['old'], readings:['old'], flags:[]};
const line = {id:'first', text:'Hello', normalized:'Hello', start:1, end:2, units:[unit], flags:[]};
let state = {project:{busy:false}, timeline:{duration:30, lines:[line]}, selected:'first'};
let changes = 0, refreshes = 0;
const editor = createTimelineEditor({
  getState: () => state,
  selectLine: id => { state.selected = id; },
  markDirty: () => changes++,
  onTimingChange: () => refreshes++,
});
editor.renderLines();
const row = elements.get('lineList').children[0];
const times = row.children[2];
times.children[0].value = '3';
times.children[1].value = '4';
times.children[0].oninput();
assert.equal(unit.start, 3);
assert.equal(unit.end, 4);
assert.equal(unit.timing_source, 'manual');
assert.deepEqual(unit.phones, []);
assert.deepEqual(unit.readings, []);
assert.equal(changes, 1);
assert.equal(refreshes, 1);
state = {...state, selected:null};
editor.renderUnits();
assert.equal(elements.get('units').children.length, 0);
""".replace("MODULE_URL", json.dumps(module))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["single", "double"])
def test_preview_windows_match_export_after_module_extraction(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional locally; CI installs it for frontend checks")
    lines = preprocess("春天你好\nHello music!\n再次相遇\nLong pause\n末句", "zh")
    for line, start in zip(lines, [2, 5, 8, 20, 24]):
        line.units = [
            Unit(
                id=line.id + "_0",
                text=line.text,
                source_start=0,
                source_end=len(line.text),
                start=start,
                end=start + 2,
            )
        ]
    # Completion is audible/visible evidence of estimation, not a reason to drop the row.
    lines[2].units[0].timing_source = "estimated"
    lines[4].display_kind = "review"
    timeline = quality(Timeline(duration=30, lines=lines))
    style = Style(mode=mode, lead=1, tail=0.5, auto_complete=True)
    module = (FRONTEND / "display-timing.js").as_uri()
    script = f"""
import {{ overlayWindows }} from {json.dumps(module)};
let input = '';
for await (const chunk of process.stdin) input += chunk;
const {{timeline, style}} = JSON.parse(input);
console.log(JSON.stringify(Object.fromEntries(overlayWindows(timeline, style))));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(
            dict(timeline=timeline.model_dump(), style=style.model_dump())
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    preview = json.loads(result.stdout)
    exported = display_windows(timeline, style)
    assert preview.keys() == exported.keys()
    for line_id, window in preview.items():
        assert window["slot"] == exported[line_id]["slot"]
        assert window["start"] == pytest.approx(exported[line_id]["start"])
        assert window["end"] == pytest.approx(exported[line_id]["end"])
