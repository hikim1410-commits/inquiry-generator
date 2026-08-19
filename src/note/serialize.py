# -*- coding: utf-8 -*-
"""노트 요약 → 회의록 초안 입력 텍스트 (PRD_회의록노트 §6.4, NR-08).

기존 `minutes_draft`는 description 문자열 하나를 받는다. 새 엔드포인트를 만들지
않고 이 자리에 넣을 사람이 읽는 텍스트를 만든다. 전사본 원문(수만 자)의 1/20
이하로 줄어들어 첨부 분량 상한 문제가 구조적으로 사라진다.

근거 시각이 없는 항목(NO_EVIDENCE)은 괄호를 붙이지 않는다 — 0:00으로 찍으면
"회의 시작 시점"이라는 없는 정보가 생긴다.
"""
from __future__ import annotations

from src.ai.note import NO_EVIDENCE


def format_ts(t_ms) -> str:
    """밀리초 → mm:ss (1시간 이상이면 h:mm:ss). 근거 미확인이면 빈 문자열."""
    try:
        ms = int(float(t_ms))
    except (ValueError, TypeError):
        return ""
    if ms < 0 or ms == NO_EVIDENCE:
        return ""
    total = ms // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


def _suffix(t_ms) -> str:
    ts = format_ts(t_ms)
    return " (%s)" % ts if ts else ""


def speakers_line(speakers) -> str:
    """[{key,name,org}] → '내비온 김형일 / KIST 김종민'. 이름 없으면 key로."""
    out = []
    for sp in (speakers or []):
        if not isinstance(sp, dict):
            continue
        name = str(sp.get("name") or "").strip() or str(sp.get("key") or "").strip()
        if not name:
            continue
        org = str(sp.get("org") or "").strip()
        out.append("%s %s" % (org, name) if org else name)
    return " / ".join(out)


def to_minutes_input(summary, speakers=None, memo="") -> str:
    """요약 dict → minutes_draft description 텍스트."""
    summary = summary if isinstance(summary, dict) else {}
    blocks = []

    one = str(summary.get("one_liner") or "").strip()
    if one:
        blocks.append("[회의 요약]\n" + one)

    line = speakers_line(speakers)
    if line:
        blocks.append("[참석자]\n" + line)

    topics = []
    for t in (summary.get("topics") or []):
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        points = [str(p).strip() for p in (t.get("points") or []) if str(p).strip()]
        if not title and not points:
            continue
        rows = ["■ %s%s" % (title, _suffix(t.get("t_ms")))] if title else []
        rows += ["  - " + p for p in points]
        topics.append("\n".join(rows))
    if topics:
        blocks.append("[주제별 논의]\n" + "\n".join(topics))

    decisions = _lines(summary.get("decisions"), "text")
    if decisions:
        blocks.append("[결정사항]\n" + "\n".join(decisions))

    actions = []
    for a in (summary.get("action_items") or []):
        if not isinstance(a, dict):
            continue
        task = str(a.get("task") or "").strip()
        if not task:
            continue
        parts = [task]
        for key in ("owner", "due"):
            v = str(a.get(key) or "").strip()
            if v:
                parts.append(v)
        actions.append("- " + " — ".join(parts) + _suffix(a.get("t_ms")))
    if actions:
        blocks.append("[할 일]\n" + "\n".join(actions))

    issues = _lines(summary.get("open_issues"), "text")
    if issues:
        blocks.append("[미결 안건]\n" + "\n".join(issues))

    memo = str(memo or "").strip()
    if memo:
        blocks.append("[사용자 메모]\n" + memo)

    return "\n\n".join(blocks)


def _lines(rows, key) -> list:
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        text = str(row.get(key) or "").strip()
        if text:
            out.append("- " + text + _suffix(row.get("t_ms")))
    return out
