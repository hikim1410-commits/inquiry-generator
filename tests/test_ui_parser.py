"""ui/app.js 순수함수 셀프체크 — parseSectionsText/sectionsToText 왕복 안정성.

PRD_minutes_ux_overhaul 요구사항 2가 회귀 최우선으로 지목한 지점.
app.js에서 두 함수의 소스를 그대로 추출해 Node로 실행한다(복사본이 아닌 실물 검증).
네트워크 불필요 — node 실행 파일만 있으면 기본 스위트에서 함께 돈다.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "ui" / "app.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="Node.js 없음 — JS 파서 셀프체크 스킵")


def _extract_fn(src: str, name: str) -> str:
    """app.js에서 `function <name>` 선언 전체를 중괄호 짝 맞춰 추출."""
    i = src.index(f"function {name}")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(f"{name} 함수 추출 실패")


def _run_js(body: str) -> str:
    src = APP_JS.read_text(encoding="utf-8")
    prelude = (_extract_fn(src, "parseSectionsText") + "\n"
               + _extract_fn(src, "sectionsToText") + "\n")
    r = subprocess.run(["node", "-e", prelude + body],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=30)
    assert r.returncode == 0, f"node 실행 실패:\n{r.stderr}"
    return r.stdout.strip()


def _run_js_with(fn_names, body: str) -> str:
    """지정한 함수들만 추출해 prelude로 삼는 범용 버전 (_run_js는 섹션 파서 전용)."""
    src = APP_JS.read_text(encoding="utf-8")
    prelude = "\n".join(_extract_fn(src, name) for name in fn_names) + "\n"
    r = subprocess.run(["node", "-e", prelude + body],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=30)
    assert r.returncode == 0, f"node 실행 실패:\n{r.stderr}"
    return r.stdout.strip()


def test_roundtrip_text_stable():
    """정규형 텍스트: text → sections → text 완전 동일."""
    body = """
const text = ['# 논의 안건', '- 첫 항목', '  - 하위 항목', '', '- 둘째 항목'].join('\\n');
const rt = sectionsToText(parseSectionsText(text));
if (rt !== text) { console.error(JSON.stringify({text, rt})); process.exit(1); }
console.log('OK');
"""
    assert _run_js(body) == "OK"


def test_roundtrip_sections_stable():
    """sections → text → sections 의미 동일 (type/text 보존)."""
    body = """
const secs = [
  {type:'header', text:'회의 개요'},
  {type:'bullet', text:'항목 A'},
  {type:'sub',    text:'세부 a'},
  {type:'empty',  text:''},
  {type:'bullet', text:'항목 B'},
];
const rt = parseSectionsText(sectionsToText(secs));
const norm = a => a.map(s => s.type + '|' + s.text).join('\\n');
if (norm(rt) !== norm(secs)) { console.error(norm(rt)); process.exit(1); }
console.log('OK');
"""
    assert _run_js(body) == "OK"


def test_parser_edge_rules():
    """문법 규칙: '#'=header, 들여쓰기 2칸=sub, 1칸은 bullet로 강등, 빈 줄=empty, 마커 없는 줄=bullet."""
    body = """
const secs = parseSectionsText(
  ['# 제목', '- 항목', '  - 하위', ' - 한칸들여쓰기', '', '마커없는줄'].join('\\n'));
const types = secs.map(s => s.type).join(',');
const want = 'header,bullet,sub,bullet,empty,bullet';
if (types !== want) { console.error(types); process.exit(1); }
if (secs[3].text !== '한칸들여쓰기') { console.error(secs[3].text); process.exit(1); }
console.log('OK');
"""
    assert _run_js(body) == "OK"


def test_derive_custom_slots_multi_table_ids():
    """mnDeriveCustomSlots: 전 표 대상으로 '슬롯 없음+라벨 있음' 핀을 도출.
    id 규칙 — 표0은 레거시 형식("c행_열") 유지, 표1+는 표 포함 형식("c표_행_열").
    빈 라벨 핀·슬롯 핀은 표에 관계없이 제외된다."""
    body = """
const anns = [
  { table: 0, row: 3, col: 0, label: '부서' },
  { table: 1, row: 3, col: 0, label: '작성자' },
  { table: 1, row: 2, col: 1, label: '' },
  { table: 0, row: 4, col: 1, slot: 'content' },
];
const out = mnDeriveCustomSlots(anns);
const want = JSON.stringify([
  { id: 'c3_0',   label: '부서',   cell: [0, 3, 0] },
  { id: 'c1_3_0', label: '작성자', cell: [1, 3, 0] },
]);
if (JSON.stringify(out) !== want) { console.error(JSON.stringify(out)); process.exit(1); }
console.log('OK');
"""
    assert _run_js_with(["mnDeriveCustomSlots"], body) == "OK"


def test_derive_custom_slots_id_stable_across_relabel():
    """id는 좌표 기반이라 라벨을 바꿔도 동일 — 표0("c행_열")·표1+("c표_행_열") 모두 유지되어
    재편집 시 custom_fields 값이 이어진다."""
    body = """
const before0 = mnDeriveCustomSlots([{ table: 0, row: 2, col: 5, label: '담당' }]);
const after0  = mnDeriveCustomSlots([{ table: 0, row: 2, col: 5, label: '담당자' }]);
if (before0[0].id !== after0[0].id || before0[0].id !== 'c2_5') {
  console.error(before0[0].id, after0[0].id); process.exit(1);
}
const before1 = mnDeriveCustomSlots([{ table: 1, row: 2, col: 5, label: '담당' }]);
const after1  = mnDeriveCustomSlots([{ table: 1, row: 2, col: 5, label: '담당자' }]);
if (before1[0].id !== after1[0].id || before1[0].id !== 'c1_2_5') {
  console.error(before1[0].id, after1[0].id); process.exit(1);
}
console.log('OK');
"""
    assert _run_js_with(["mnDeriveCustomSlots"], body) == "OK"
