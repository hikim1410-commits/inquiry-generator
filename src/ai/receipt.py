# -*- coding: utf-8 -*-
"""영수증 LLM 비전 추출 — Windows OCR이 실패하거나 값이 어긋날 때의 폴백.

주 경로는 무료·오프라인인 Windows 내장 OCR(src/receipt/win_ocr.py)이다. 이 모듈은
그 결과가 정산에 쓰기 부족할 때만 호출된다 — 그래서 API 비용은 '어려운 영수증'에만 든다.
"""
from src.ai.llm import complete_json, PROVIDER_LABELS

# Gemini 방언 스키마 (llm.gemini_to_jsonschema가 타 프로바이더용으로 변환)
RECEIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "store_name": {"type": "STRING"},
        "biz_no": {"type": "STRING"},
        "tx_datetime": {"type": "STRING"},
        "supply_amount": {"type": "INTEGER"},
        "vat": {"type": "INTEGER"},
        "total_amount": {"type": "INTEGER"},
        "approval_no": {"type": "STRING"},
        "card_no_masked": {"type": "STRING"},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "qty": {"type": "INTEGER"},
                    "price": {"type": "INTEGER"},
                },
                "required": ["name", "price"],
            },
        },
    },
    "required": ["store_name", "tx_datetime", "total_amount", "items"],
}

RECEIPT_DIRECTIVE = (
    "이미지는 한국의 영수증 또는 카드매출전표다. 보이는 값만 정확히 옮겨 적어라.\n"
    "규칙:\n"
    "- 읽을 수 없는 문자열은 빈 문자열. supply_amount·vat·품목 qty는 못 읽으면 "
    "생략하거나 null(0/1로 채우지 마라. 0은 실제 0원일 때만).\n"
    "- 금액은 쉼표 없는 정수(원 단위)로 적는다. total_amount만 못 읽으면 0.\n"
    "- tx_datetime은 'YYYY-MM-DD HH:MM' 형식. 시각이 없으면 'YYYY-MM-DD'까지만.\n"
    "- biz_no는 '000-00-00000' 형식.\n"
    "- card_no_masked는 반드시 가운데를 가려 '1234-****-****-5678' 형태로만 적는다. "
    "전체 카드번호를 복원하거나 그대로 적지 마라.\n"
    "- items에는 품목명·수량·단가가 아닌 '금액'을 넣는다. 합계·부가세 줄은 items에 넣지 않는다.\n"
    "- 금액을 임의로 맞추려고 값을 조정하지 마라. 보이는 대로만 적는다."
)


def build_receipt_prompt(ocr_hint: str = "") -> str:
    """추출 프롬프트. OCR이 읽은 텍스트가 있으면 참고자료로만 덧붙인다.

    사용자·OCR 텍스트는 str.format을 통과시키지 않는다(회의록 모듈과 동일한 불변식).
    """
    prompt = RECEIPT_DIRECTIVE
    if ocr_hint:
        prompt += ("\n\n참고: 아래는 기기 내장 OCR이 읽은 텍스트다. 일부 글자가 틀렸거나 "
                   "빠졌을 수 있으니 이미지를 우선하고, 판독이 애매할 때만 참고하라.\n"
                   "--- OCR 텍스트 ---\n" + ocr_hint)
    return prompt


def _normalize_receipt(data: dict) -> dict:
    """스키마 밖 값·타입 흔들림을 정리한다. 예외 대신 안전한 기본값.

    supply_amount/vat/qty 미확인은 None(0은 면세·1개는 날조로 읽힘).
    total_amount만 못 읽으면 0 — FLAG_NO_TOTAL 분기가 그 값을 본다.
    """
    d = data if isinstance(data, dict) else {}

    def _i(v, missing=0):
        if v is None:
            return missing
        try:
            s = str(v).replace(",", "").strip()
            if not s:
                return missing
            return int(s)
        except (TypeError, ValueError):
            return missing

    def _s(v):
        return str(v).strip() if v is not None else ""

    items = []
    for it in (d.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = _s(it.get("name"))
        if not name:
            continue
        items.append({"name": name, "qty": _i(it.get("qty"), missing=None),
                      "price": _i(it.get("price"))})

    return {
        "store_name": _s(d.get("store_name")),
        "biz_no": _s(d.get("biz_no")),
        "tx_datetime": _s(d.get("tx_datetime")),
        "supply_amount": _i(d.get("supply_amount"), missing=None),
        "vat": _i(d.get("vat"), missing=None),
        "total_amount": _i(d.get("total_amount")),
        "approval_no": _s(d.get("approval_no")),
        "card_no_masked": mask_card(_s(d.get("card_no_masked"))),
        "items": items,
        "_flags": [],
    }


def mask_card(value: str) -> str:
    """카드번호를 앞 4 · 뒤 4만 남긴다. 모델이 전체번호를 뱉어도 여기서 막는다(FR-09)."""
    digits = [c for c in value if c.isdigit()]
    if len(digits) < 8:
        return value
    return f"{''.join(digits[:4])}-****-****-{''.join(digits[-4:])}"


# ── 공개 API ──

def extract_receipt(provider, images, api_key, model, timeout=90, ocr_hint=""):
    """영수증 이미지(base64 PNG 목록) → 추출 필드 {ok, data} / {ok: False, error}."""
    if not api_key:
        label = PROVIDER_LABELS.get(provider, provider)
        return {"ok": False, "error": f"{label} API 키가 없습니다. 설정에서 입력하세요."}
    if not images:
        return {"ok": False, "error": "추출할 이미지가 없습니다."}

    r = complete_json(provider, api_key, model, build_receipt_prompt(ocr_hint),
                      schema=RECEIPT_SCHEMA, timeout=timeout, temperature=0.0,
                      images=images)
    if not r.get("ok"):
        return r
    return {"ok": True, "data": _normalize_receipt(r.get("data") or {})}
