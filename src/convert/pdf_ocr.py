# -*- coding: utf-8 -*-
"""스캔(이미지) PDF → LLM 비전 전사 폴백.

kordoc(pdfjs)은 텍스트 레이어가 없는 스캔 PDF에서 빈 결과를 낸다(2026-07-29 실측:
한글 PDF 인쇄본도 이미지로만 저장되면 텍스트 0자). 로컬 OCR 동봉은 배제됐으므로
(영수증 OCR 조사 결론), 설정된 LLM 프로바이더의 비전 입력으로 전사한다.

pypdfium2로 페이지를 PNG 렌더 → base64 → llm.complete_text 1회 호출.
"""
import base64
import io

from src.logutil import log as _log

MAX_PAGES = 8        # 비용·토큰 상한 (초과분은 생략 표기)
RENDER_SCALE = 2.0   # 72dpi × 2 = 144dpi — 한글 문서 전사에 충분(실측)

_PROMPT = (
    "다음 이미지들은 한 문서(PDF)의 페이지 스캔이다. 보이는 모든 텍스트를 순서대로 "
    "Markdown으로 옮겨 적어라. 표는 Markdown 표로, 제목은 헤딩으로. "
    "내용을 요약·창작하지 말고 그대로 전사하라. 읽을 수 없는 부분은 [판독불가]로 표기."
)


def render_pdf_pages(path: str, max_pages: int = MAX_PAGES) -> tuple:
    """PDF → (base64 PNG 목록, 총 페이지 수). pypdfium2 미설치 시 ImportError."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    try:
        total = len(doc)
        images = []
        for i in range(min(total, max_pages)):
            bitmap = doc[i].render(scale=RENDER_SCALE)
            pil = bitmap.to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            images.append(base64.b64encode(buf.getvalue()).decode("ascii"))
        return images, total
    finally:
        doc.close()


def extract_scanned_pdf(path: str, provider: str, api_key: str, model: str) -> dict:
    """{ok, markdown, chars} 또는 {ok: False, error}."""
    try:
        images, total = render_pdf_pages(path)
    except ImportError:
        return {"ok": False, "error": "PDF 렌더 모듈(pypdfium2)이 없습니다."}
    except Exception as e:
        return {"ok": False, "error": f"PDF 렌더 실패: {e}"}
    if not images:
        return {"ok": False, "error": "PDF에 페이지가 없습니다."}

    from src.ai.llm import complete_text
    r = complete_text(provider, api_key, model, _PROMPT, images=images)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "AI 전사 실패")}
    md = (r.get("text") or "").strip()
    if not md:
        return {"ok": False, "error": "AI가 텍스트를 전사하지 못했습니다."}
    if total > len(images):
        md += f"\n\n[... 총 {total}페이지 중 {len(images)}페이지까지만 전사됨 ...]"
    _log(f"스캔 PDF 비전 전사 완료 [{path}] {len(md)}자 ({len(images)}/{total}p)")
    return {"ok": True, "markdown": md, "chars": len(md)}
