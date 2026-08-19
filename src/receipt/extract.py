# -*- coding: utf-8 -*-
"""영수증 추출 오케스트레이션 — Windows OCR 주 경로 + LLM 비전 폴백.

흐름:
    이미지 → Windows OCR(무료·오프라인) → 필드 파싱 → 금액 검증
                                              │
                          검증 통과 ───────────┴──── 그대로 확정 후보
                          검증 실패/필수 필드 누락 ── LLM 비전 재추출(폴백)

폴백을 '검증 실패'로 거는 이유: Windows OCR은 글자를 그럴듯하게 읽고도 품목을 통째로
누락하거나(실측 "생수") 쉼표를 흘린다. 그 손상은 대개 '항목합 ≠ 총액'으로 드러나므로,
정산 정합성 규칙(FR-05)이 곧 품질 게이트 역할을 한다.

어느 경로로 나온 값이든 사용자 확정 전에는 산출물에 반영하지 않는다(FR-07).
"""
import base64
import os

from src.logutil import log as _log
from src.receipt import parse, verify, win_ocr

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_LONG_EDGE = 1600  # 비전 전송 긴 변 상한 — 휴대폰 원본이 수 MB PNG가 되는 것을 막는다

SOURCE_OCR = "win_ocr"
SOURCE_LLM = "llm"


def _encode_png(im) -> str:
    """EXIF 회전을 반영하고 긴 변을 상한으로 줄인 뒤 PNG base64로 만든다."""
    import io

    from PIL import Image, ImageOps

    # 휴대폰 사진의 Orientation을 무시하면 OCR과 비전이 서로 다른 방향을 본다.
    try:
        rotated = ImageOps.exif_transpose(im)
        if rotated is not None:
            im = rotated
    except Exception:
        pass
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    longest = max(w, h)
    if longest > _LONG_EDGE:
        scale = _LONG_EDGE / float(longest)
        im = im.resize((max(1, int(round(w * scale))),
                        max(1, int(round(h * scale)))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _b64_png(path: str) -> str:
    """어떤 이미지 형식이든 PNG로 정규화해 base64로 만든다.

    파일 바이트를 그대로 보내면서 mime을 image/png로 선언하면 Anthropic·Gemini가
    거부한다. 휴대폰 영수증 사진은 대부분 JPEG라 폴백이 통째로 실패한다.
    """
    from PIL import Image

    with Image.open(path) as im:
        return _encode_png(im)


def _limit_b64(b64: str) -> str:
    """렌더된 PNG(PDF 페이지)에도 같은 긴 변 상한을 적용한다."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(b64))) as im:
        return _encode_png(im)


def _receipt_images(path: str):
    """페이지별 PNG 목록과 실제 총 페이지 수.

    이미지는 1장. PDF는 페이지마다 1장 — 호출부가 페이지=1영수증으로 쪼갠다.
    렌더러 상한을 넘는 페이지는 목록에 없고 total로만 알 수 있다.
    """
    if os.path.splitext(path)[1].lower() == ".pdf":
        from src.convert.pdf_ocr import render_pdf_pages
        images, total = render_pdf_pages(path)
        return [_limit_b64(b) for b in images], total
    return [_b64_png(path)], 1


def _with_page(result, page_index, page_total):
    if page_total is not None:
        result["page"] = page_index + 1
        result["page_total"] = page_total
    return result


def needs_fallback(data: dict, flags: list) -> str:
    """폴백이 필요하면 사유를, 아니면 빈 문자열을 돌려준다."""
    # parse가 _flags에만 남긴 환불·부가세별도·빈품목을 verify 리스트와 합친다.
    # 파서가 플래그만 붙이고 여기가 무시하면 폴백이 안 열린다.
    merged = list(flags or [])
    for f in (data.get("_flags") or []):
        if f and f not in merged:
            merged.append(f)
    if merged:
        return "금액 정합성 불일치"
    if not data.get("total_amount"):
        return "총액 미인식"
    if not data.get("store_name"):
        return "상호 미인식"
    if not data.get("tx_datetime"):
        return "거래일시 미인식"
    return ""


def extract_one(path: str, ocr_result: dict = None, provider: str = "",
                api_key: str = "", model: str = "", allow_fallback: bool = True,
                page_image: str = None, page_index: int = 0,
                page_total: int = None) -> dict:
    """영수증 1건 추출. ocr_result를 주면 배치에서 이미 돌린 OCR을 재사용한다.

    page_image가 있으면 그 한 장을 이 영수증으로 본다(PDF 페이지 분리).
    반환: {ok, path, source, data, flags, fallback_reason, error}
    """
    if ocr_result is None:
        try:
            ocr_result = win_ocr.ocr_images([path])[0]
        except win_ocr.OcrUnavailable as e:
            ocr_result = {"ok": False, "lines": [], "error": str(e)}

    if ocr_result.get("ok"):
        data = parse.parse_lines(ocr_result.get("lines") or [])
        flags = verify.verify(data)
        reason = needs_fallback(data, flags)
    else:
        # OCR 실패는 사유가 비어 있어도 실패다 — 빈 사유를 통과로 읽으면
        # 'ok=True, data={}'라는 조용한 성공이 만들어진다.
        data = {}
        flags = [verify.FLAG_NO_TOTAL]
        reason = ocr_result.get("error") or "인식 실패"

    if not reason:
        return _with_page(
            {"ok": True, "path": path, "source": SOURCE_OCR, "data": data,
             "flags": flags, "fallback_reason": "", "error": ""},
            page_index, page_total)

    if not (allow_fallback and api_key):
        # 폴백 불가 — OCR 결과를 그대로 넘기되 왜 미덥지 않은지 남긴다
        if data:
            data["_flags"] = flags
        return _with_page(
            {"ok": bool(data), "path": path, "source": SOURCE_OCR, "data": data,
             "flags": flags, "fallback_reason": reason,
             "error": "" if data else (ocr_result.get("error") or "인식 실패")},
            page_index, page_total)

    from src.ai.receipt import extract_receipt
    try:
        if page_image:
            images = [page_image]
        else:
            pages, total = _receipt_images(path)
            if page_total is None:
                page_total = total
            # 여러 페이지를 한 영수증으로 묶지 않는다 — 지정한 한 장만.
            if len(pages) > 1:
                pick = page_index if 0 <= page_index < len(pages) else 0
                images = [pages[pick]]
            else:
                images = pages
    except ImportError:
        return _with_page(
            {"ok": False, "path": path, "source": SOURCE_OCR, "data": data,
             "flags": flags, "fallback_reason": reason,
             "error": "PDF 렌더 모듈(pypdfium2)이 없습니다."},
            page_index, page_total)
    except Exception as e:
        return _with_page(
            {"ok": False, "path": path, "source": SOURCE_OCR, "data": data,
             "flags": flags, "fallback_reason": reason,
             "error": f"이미지 준비 실패: {e}"},
            page_index, page_total)

    hint = "\n".join(ocr_result.get("lines") or [])
    r = extract_receipt(provider, images, api_key, model, ocr_hint=hint)
    if not r.get("ok"):
        # 폴백까지 실패 — OCR 초안은 data에 남기되 ok는 False다.
        # 폴백을 탄 이유가 검증 실패인데 LLM이 죽으면 그 불량 data가
        # ok=True가 되어 확정 후보로 올라가는 위장을 막는다.
        if data:
            data["_flags"] = flags
        return _with_page(
            {"ok": False, "path": path, "source": SOURCE_OCR, "data": data,
             "flags": flags, "fallback_reason": reason,
             "error": r.get("error") or "AI 추출 실패"},
            page_index, page_total)

    llm_data = r["data"]
    llm_flags = verify.verify(llm_data)
    llm_data["_flags"] = llm_flags
    _log(f"영수증 LLM 폴백 [{os.path.basename(path)}] 사유={reason}")
    return _with_page(
        {"ok": True, "path": path, "source": SOURCE_LLM, "data": llm_data,
         "flags": llm_flags, "fallback_reason": reason, "error": ""},
        page_index, page_total)


def _extract_pdf_pages(path, ocr_result, provider, api_key, model, allow_fallback):
    """PDF 페이지마다 영수증 1건. 상한 초과분은 오류 건으로 남긴다."""
    try:
        images, total = _receipt_images(path)
    except ImportError:
        return [{"ok": False, "path": path, "source": SOURCE_OCR, "data": {},
                 "flags": [verify.FLAG_NO_TOTAL],
                 "fallback_reason": ocr_result.get("error") or "",
                 "error": "PDF 렌더 모듈(pypdfium2)이 없습니다."}]
    except Exception as e:
        return [{"ok": False, "path": path, "source": SOURCE_OCR, "data": {},
                 "flags": [verify.FLAG_NO_TOTAL],
                 "fallback_reason": ocr_result.get("error") or "",
                 "error": f"이미지 준비 실패: {e}"}]
    if not images:
        return [{"ok": False, "path": path, "source": SOURCE_OCR, "data": {},
                 "flags": [verify.FLAG_NO_TOTAL], "fallback_reason": "",
                 "error": "PDF에 페이지가 없습니다."}]
    out = []
    for i, img in enumerate(images):
        # 페이지마다 ocr_result를 복사한다 — 하류가 _flags를 써넣어도 서로 안 섞인다.
        out.append(extract_one(path, ocr_result=dict(ocr_result), provider=provider,
                               api_key=api_key, model=model,
                               allow_fallback=allow_fallback,
                               page_image=img, page_index=i, page_total=total))
    if total > len(images):
        out.append({
            "ok": False, "path": path, "source": SOURCE_OCR, "data": {},
            "flags": [], "fallback_reason": "",
            "error": f"PDF {total}페이지 중 {len(images)}페이지만 처리됨 (페이지 상한 초과)",
            "page": None, "page_total": total,
        })
    return out


def extract_batch(paths: list, provider: str = "", api_key: str = "", model: str = "",
                  allow_fallback: bool = True) -> list:
    """여러 건 추출. OCR은 한 번의 배치로 처리해 PowerShell 기동 비용을 상쇄한다.

    PDF는 Windows OCR이 직접 못 읽으므로 OCR 배치에서 제외하고, 페이지마다
    영수증 1건으로 나눠 폴백 경로로 보낸다. 반환은 기존과 같이 결과 dict 목록.
    """
    ocr_targets = [p for p in paths if os.path.splitext(p)[1].lower() in _IMAGE_EXT]
    ocr_map = {}
    if ocr_targets:
        try:
            for r in win_ocr.ocr_images(ocr_targets):
                ocr_map[os.path.normcase(os.path.abspath(r["path"]))] = r
        except win_ocr.OcrUnavailable as e:
            _log(f"Windows OCR 사용 불가 — 전건 LLM 폴백: {e}")
            for p in ocr_targets:
                ocr_map[os.path.normcase(os.path.abspath(p))] = {
                    "ok": False, "lines": [], "error": str(e)}

    out = []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        ext = os.path.splitext(p)[1].lower()
        miss = ("PDF는 내장 OCR 대상이 아닙니다." if ext == ".pdf"
                else f"내장 OCR이 지원하지 않는 형식입니다({ext or '확장자 없음'}).")
        # 같은 경로를 여러 번 넘긴 경우 결과 dict를 공유하면 하류에서 _flags를
        # 써넣을 때 서로 오염된다 — 건별로 복사해 넘긴다.
        got = dict(ocr_map[key]) if key in ocr_map else {"ok": False, "lines": [],
                                                         "error": miss}
        if ext == ".pdf":
            out.extend(_extract_pdf_pages(p, got, provider, api_key, model,
                                          allow_fallback))
        else:
            out.append(extract_one(p, ocr_result=got, provider=provider, api_key=api_key,
                                   model=model, allow_fallback=allow_fallback))
    ocr_n = sum(1 for r in out if r["source"] == SOURCE_OCR and r["ok"])
    llm_n = sum(1 for r in out if r["source"] == SOURCE_LLM)
    _log(f"영수증 추출 {len(out)}건 — 내장 OCR {ocr_n} / LLM 폴백 {llm_n}")
    return out
