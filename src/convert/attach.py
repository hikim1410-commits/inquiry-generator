# -*- coding: utf-8 -*-
"""첨부 문서(Markdown) ↔ AI 입력 병합 — 순수 함수.

변환된 md를 과업지시서 설명과 합쳐 AI 프롬프트로 보낼 본문을 만든다.
총량 상한을 두어 토큰 폭주를 막고, 잘린 사실은 warnings로 UI에 알린다.
"""

MAX_ATTACH_TOTAL = 30_000   # 문서 첨부 합산 글자수 상한 (한국어 ≈ 30k~45k 토큰, 전 프로바이더 안전권)
MAX_TRANSCRIPT_TOTAL = 120_000  # 전사본 전용 상한 — 2시간 회의(~48k자)가 잘리지 않게
_MIN_KEEP = 200             # 이만큼도 못 싣는 문서는 통째로 생략
_KIND_TRANSCRIPT = "transcript"


def merge_attachments(description, attachments, max_total=MAX_ATTACH_TOTAL):
    """(병합된 description, warnings) 반환.

    attachments: [{"name": str, "markdown": str, "kind"?: "document"|"transcript"}]
    — 잘못된 항목은 무시. kind 가 없으면 기존과 같이 문서 상한(max_total)을 쓴다.
    transcript 항목은 MAX_TRANSCRIPT_TOTAL 을 쓰며, 문서와 예산을 따로 센다.
    형식:
        {description}

        ===== 첨부 문서 1: 과업지시서.hwp =====
        {markdown}
    """
    desc = (description or "").strip()
    warnings = []
    valid = []
    for a in attachments or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip() or "이름없음"
        md = a.get("markdown")
        if isinstance(md, str) and md.strip():
            kind = _KIND_TRANSCRIPT if a.get("kind") == _KIND_TRANSCRIPT else "document"
            valid.append((name, md.strip(), kind))

    if not valid:
        return desc, warnings

    parts = [desc] if desc else []
    used = {"document": 0, _KIND_TRANSCRIPT: 0}
    truncated = {"document": 0, _KIND_TRANSCRIPT: 0}
    skipped = {"document": 0, _KIND_TRANSCRIPT: 0}
    for i, (name, md, kind) in enumerate(valid, start=1):
        cap = MAX_TRANSCRIPT_TOTAL if kind == _KIND_TRANSCRIPT else max_total
        remain = cap - used[kind]
        if remain < _MIN_KEEP:
            skipped[kind] += 1
            continue
        body = md
        if len(body) > remain:
            cut = len(body) - remain
            body = body[:remain] + f"\n\n[... 분량 초과로 이하 {cut:,}자 생략 ...]"
            truncated[kind] += 1
        used[kind] += min(len(md), remain)
        parts.append(f"===== 첨부 문서 {i}: {name} =====\n{body}")

    if truncated["document"] or skipped["document"]:
        detail = []
        if truncated["document"]:
            detail.append(f"{truncated['document']}건 일부 절단")
        if skipped["document"]:
            detail.append(f"{skipped['document']}건 제외")
        warnings.append(
            f"첨부 분량이 상한({max_total:,}자)을 초과하여 {', '.join(detail)}되었습니다. "
            "핵심 문서를 먼저 첨부하세요.")
    if truncated[_KIND_TRANSCRIPT] or skipped[_KIND_TRANSCRIPT]:
        detail = []
        if truncated[_KIND_TRANSCRIPT]:
            detail.append(f"{truncated[_KIND_TRANSCRIPT]}건 일부 절단")
        if skipped[_KIND_TRANSCRIPT]:
            detail.append(f"{skipped[_KIND_TRANSCRIPT]}건 제외")
        warnings.append(
            f"회의 후반부가 잘렸습니다. 전사본 분량이 상한({MAX_TRANSCRIPT_TOTAL:,}자)을 "
            f"초과하여 {', '.join(detail)}되었습니다.")

    return "\n\n".join(parts), warnings
