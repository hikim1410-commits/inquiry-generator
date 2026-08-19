# -*- coding: utf-8 -*-
"""회의 노트 AI 요약 — NOTE_SUMMARY_SCHEMA / 프롬프트 / 정규화.

`src/ai/minutes.py`와 동형이다. 멀티 프로바이더 공통으로 llm.complete_json
(schema=NOTE_SUMMARY_SCHEMA)을 경유하고, HTTP·재시도·오류 매핑은 llm이 담당한다.

핵심 불변식: **모든 항목에 근거 시각 t_ms를 요구한다.** 모델이 시각을 못 대면
NO_EVIDENCE(-1)로 정규화되고, 화면은 그 항목을 "근거 미확인"으로 표시한다
(PRD_회의록노트 §6.3, R-01). 요약 환각을 사용자가 의심할 수 있게 만드는 장치다.
"""
from src.ai.llm import complete_json, PROVIDER_LABELS

# ── 스키마 ─────────────────────────────────────────────────────────────────────

# 근거 시각을 못 댄 항목의 표시값. UI는 이 값이면 "근거 미확인" 배지를 단다.
NO_EVIDENCE = -1

_T_MS = {
    "type": "INTEGER",
    "description": (
        "근거가 되는 발언의 시작 시각(밀리초). 전사본의 [mm:ss] 표기를 밀리초로 "
        "환산한다. 해당 발언을 특정할 수 없으면 -1."
    ),
}

NOTE_SUMMARY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "one_liner": {
            "type": "STRING",
            "description": "회의 전체를 한 문장으로. 40자 내외 개조식.",
        },
        "topics": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING", "description": "주제 제목. 간결하게."},
                    "t_ms": _T_MS,
                    "points": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "그 주제의 요지 2~5개. 개조식 한 줄씩.",
                    },
                },
                "required": ["title", "t_ms", "points"],
            },
            "description": "주제별 논의. 회의 진행 순서대로.",
        },
        "keywords": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "핵심어 5~15개. 고유명사·기술용어·금액 항목 우선.",
        },
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING", "description": "확정된 사항 한 줄."},
                    "t_ms": _T_MS,
                },
                "required": ["text", "t_ms"],
            },
            "description": "회의에서 확정된 사항만. 논의 중인 안건은 open_issues로.",
        },
        "action_items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "task": {"type": "STRING", "description": "할 일 한 줄."},
                    "owner": {
                        "type": "STRING",
                        "description": "담당자. 발언에 나온 경우만. 없으면 빈 문자열.",
                    },
                    "due": {
                        "type": "STRING",
                        "description": "기한. 발언에 나온 표현 그대로(예: '9/4'). 없으면 빈 문자열.",
                    },
                    "t_ms": _T_MS,
                },
                "required": ["task", "owner", "due", "t_ms"],
            },
            "description": "후속 조치. 담당자·기한은 절대 추측하지 않는다.",
        },
        "open_issues": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING", "description": "결론 없이 넘어간 안건 한 줄."},
                    "t_ms": _T_MS,
                },
                "required": ["text", "t_ms"],
            },
            "description": "결론이 나지 않은 안건.",
        },
    },
    "required": [
        "one_liner", "topics", "keywords",
        "decisions", "action_items", "open_issues",
    ],
}

# 기초 지침(디렉티브) — 설정 화면에서 사용자가 교체 가능한 부분 (UI 노출용 공개 상수).
# 자리표시자를 두지 않는다: 사용자 편집 텍스트와 동일하게 .format을 통과하지 않기 때문.
NOTE_DIRECTIVE_DEFAULT = """\
너는 회의 전사본을 정리하는 회의 기록 전문가다.
아래 전사본을 분석해 회의 노트 요약 JSON을 작성하라.

## 필수 규칙
1. 모든 항목에 근거 시각 t_ms(밀리초)를 붙인다. 전사본의 [mm:ss] 표기를 환산한다.
   해당 발언을 특정할 수 없으면 t_ms를 -1로 둔다 — 그럴듯한 시각을 지어내지 마라.
2. 추측 금지. 담당자·기한·금액·기관명은 발언에 나온 것만 쓴다.
   문맥상 그럴듯하다는 이유로 채우지 않는다. 없으면 빈 문자열로 둔다.
3. 전사본에 없는 사실을 만들지 마라. 요약은 압축이지 보완이 아니다.
4. 확정된 것만 decisions에 넣는다. "검토하기로 했다" 수준은 open_issues다.
5. 개조식 한국어. 문어체 금지. 한 항목 한 줄.
6. 전사 오류로 보이는 고유명사·약어는 원문 표기를 그대로 남긴다."""

# 데이터 블록 — 시스템이 항상 자동 첨부 (사용자 편집 불가 → 지침이 어떻든 요약 기능 유지).
_NOTE_DATA_TMPL = """## 전사본
{transcript}

위 필수 규칙을 반드시 준수하여 JSON으로만 답하라.
"""

# 구간 분할 요약의 병합 단계 지침 (§7.4). summarize.py가 directive로 넘긴다.
NOTE_MERGE_DIRECTIVE = """\
너는 회의 전사본을 정리하는 회의 기록 전문가다.
아래는 같은 회의를 구간별로 나누어 요약한 부분 결과들이다.
이것을 하나의 회의 노트 요약 JSON으로 병합하라.

## 필수 규칙
1. 부분 요약에 붙은 t_ms 값을 그대로 보존한다. 새로 계산하거나 지어내지 마라.
2. 같은 내용이 여러 구간에 걸쳐 나오면 하나로 합치고, 가장 이른 t_ms를 쓴다.
3. 부분 요약에 없는 사실을 추가하지 마라.
4. 뒤 구간에서 결론이 난 안건은 open_issues에서 빼고 decisions로 옮긴다.
5. 개조식 한국어. 문어체 금지. 한 항목 한 줄."""


def build_note_prompt(transcript: str, directive=None) -> str:
    """directive: 사용자 지정 기초 지침 (없으면 내장 기본).
    불변식: 사용자 텍스트는 str.format을 절대 통과하지 않는다 ({} 포함 안전)."""
    head = str(directive or NOTE_DIRECTIVE_DEFAULT).strip()
    return head + "\n\n" + _NOTE_DATA_TMPL.format(transcript=transcript.strip())


def _one_line(v, limit):
    """단일행 슬롯. 공백류를 한 칸으로 접고 길이를 자른다."""
    return " ".join(str(v or "").split())[:limit]


def _t_ms(v):
    """근거 시각. 음수·비수치·누락은 전부 NO_EVIDENCE."""
    try:
        n = int(float(v))
    except (ValueError, TypeError):
        return NO_EVIDENCE
    return n if n >= 0 else NO_EVIDENCE


def _normalize_note(data: dict) -> dict:
    """스키마 강제 + 안전 클램프. 형이 틀린 항목은 버린다."""
    data = data if isinstance(data, dict) else {}

    out = {
        "one_liner":    _one_line(data.get("one_liner"), 200),
        "topics":       [],
        "keywords":     [],
        "decisions":    [],
        "action_items": [],
        "open_issues":  [],
    }

    for t in (data.get("topics") or []):
        if not isinstance(t, dict):
            continue
        title = _one_line(t.get("title"), 120)
        # 문자열은 iterable이라 가드 없이는 글자 단위로 쪼개진다 — 리스트만 인정
        raw_pts = t.get("points")
        points = [_one_line(p, 300)
                  for p in (raw_pts if isinstance(raw_pts, list) else [])]
        points = [p for p in points if p]
        if not title and not points:
            continue
        out["topics"].append({
            "title": title,
            "t_ms": _t_ms(t.get("t_ms")),
            "points": points,
        })

    seen = set()
    raw_kws = data.get("keywords")
    for k in (raw_kws if isinstance(raw_kws, list) else []):
        s = _one_line(k, 40)
        if s and s not in seen:
            seen.add(s)
            out["keywords"].append(s)

    for key in ("decisions", "open_issues"):
        for row in (data.get(key) or []):
            if not isinstance(row, dict):
                continue
            text = _one_line(row.get("text"), 300)
            if text:
                out[key].append({"text": text, "t_ms": _t_ms(row.get("t_ms"))})

    for row in (data.get("action_items") or []):
        if not isinstance(row, dict):
            continue
        task = _one_line(row.get("task"), 300)
        if not task:
            continue
        out["action_items"].append({
            "task": task,
            "owner": _one_line(row.get("owner"), 60),
            "due": _one_line(row.get("due"), 60),
            "t_ms": _t_ms(row.get("t_ms")),
        })

    return out


# ── 공개 API ─────────────────────────────────────────────────────────────────

def summarize_note(provider: str, transcript: str, api_key: str, model: str,
                   timeout: int = 120, directive=None) -> dict:
    """프로바이더 공통 회의 노트 요약.
    반환: {ok, summary?: normalized NOTE_SUMMARY_SCHEMA dict, error?, model_error?}
    """
    if not api_key:
        label = PROVIDER_LABELS.get(provider, provider)
        return {"ok": False, "error": f"{label} API 키가 설정되지 않았습니다. 설정 화면에서 입력하세요."}

    prompt = build_note_prompt(transcript, directive=directive)
    r = complete_json(provider, api_key, model, prompt,
                      schema=NOTE_SUMMARY_SCHEMA, timeout=timeout)
    if not r.get("ok"):
        return r
    return {"ok": True, "summary": _normalize_note(r["data"])}
