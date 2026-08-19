# -*- coding: utf-8 -*-
"""영수증 API 표면 — 카드 스키마, FLAG_TEXTS, 예외 비누출, 확정/내보내기 가드.

Api()는 실제 config.json 을 읽으므로 __init__ 을 우회하고 cfg 를 주입한다
(test_minutes_api 와 동일). COM/OCR 불필요 — extract_batch 는 테스트에서 스텁.
"""
import base64
import io
import json
import os

import pytest

from src.api import Api
from src.api.core import ApiCore
from src.api.receipt import FLAG_TEXTS, ReceiptApi, _flag_texts
from src.receipt import verify
from src.store import config_store as cs

_METHODS = (
    "pick_receipt_files", "add_receipts", "extract_receipts",
    "get_receipt_state", "get_receipt_image", "update_receipt",
    "remove_receipt", "clear_receipts", "confirm_receipts",
    "export_receipts", "mark_receipt_notice_seen",
)

_CARD_KEYS = {
    "id", "path", "name", "page_index", "page_total", "status", "source",
    "flags", "flag_texts", "fallback_reason", "error", "confirmed", "edited",
    "data",
}
_DATA_KEYS = {
    "store_name", "biz_no", "tx_datetime", "total_amount", "supply_amount",
    "vat", "card_no_masked", "approval_no", "items",
}


def _api(cfg=None):
    api = Api.__new__(Api)
    api.cfg = cfg if cfg is not None else {
        "ai": {"provider": "gemini"},
        "gemini": {"api_key_enc": "", "model": "gemini-flash-latest"},
        "receipt": {"notice_shown": False},
    }
    api._window = None
    api._receipts = []
    api._receipt_seq = 0
    return api


def _touch(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(content)
    return path


def _png_bytes(w=1200, h=800, color=(200, 10, 10)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _ok_res(path, **over):
    data = {
        "store_name": "가게", "biz_no": "123-45-67890",
        "tx_datetime": "2026-06-11 14:32",
        "total_amount": 24000, "supply_amount": 21818, "vat": 2182,
        "card_no_masked": "1234-****-****-5678", "approval_no": "30251234",
        "items": [{"name": "커피", "qty": 2, "price": 9000},
                  {"name": "빵", "qty": 1, "price": 15000}],
    }
    out = {"ok": True, "path": path, "source": "win_ocr", "data": data,
           "flags": [], "fallback_reason": "", "error": ""}
    out.update(over)
    return out


def _err_res(path, error="인식 실패"):
    return {"ok": False, "path": path, "source": "win_ocr", "data": {},
            "flags": ["no_total"], "fallback_reason": "", "error": error}


@pytest.fixture(autouse=True)
def _no_config_disk(monkeypatch):
    monkeypatch.setattr(cs, "save_config", lambda cfg: None)


def test_api_exposes_eleven_receipt_methods():
    for name in _METHODS:
        assert hasattr(Api, name), name
        assert callable(getattr(Api, name))


def test_receipt_mixin_sits_before_apicore():
    mro = Api.__mro__
    assert mro.index(ReceiptApi) < mro.index(ApiCore)


def test_flag_texts_covers_engine_flags_and_unknown():
    assert FLAG_TEXTS["sum_mismatch"] == "항목 합계가 총액과 다릅니다"
    assert FLAG_TEXTS["vat_mismatch"] == "공급가액+부가세가 합계와 다릅니다"
    assert FLAG_TEXTS["no_total"] == "총액을 읽지 못했습니다"
    assert FLAG_TEXTS["no_items"] == "품목을 읽지 못했습니다"
    assert FLAG_TEXTS["refund_suspected"] == "환불·마이너스 금액으로 보입니다"
    assert FLAG_TEXTS["vat_separate"] == "부가세 별도 표기입니다"
    for const in (verify.FLAG_SUM, verify.FLAG_VAT, verify.FLAG_NO_TOTAL,
                  verify.FLAG_NO_ITEMS, verify.FLAG_REFUND, verify.FLAG_VAT_SEPARATE):
        assert const in FLAG_TEXTS
    assert _flag_texts(["sum_mismatch", "mystery_flag"]) == [
        "항목 합계가 총액과 다릅니다", "mystery_flag"]


def test_add_skips_unsupported_and_duplicates(tmp_path):
    api = _api()
    jpg = _touch(str(tmp_path / "a.jpg"))
    r = api.add_receipts([jpg, jpg, str(tmp_path / "note.txt"), "b.gif"])
    assert r["ok"]
    json.dumps(r)
    assert len(r["items"]) == 1
    assert r["items"][0]["id"] == "r1"
    assert r["items"][0]["status"] == "pending"
    assert r["items"][0]["source"] == ""
    assert r["items"][0]["data"]["supply_amount"] is None
    assert r["items"][0]["data"]["vat"] is None
    reasons = {s["name"]: s["reason"] for s in r["skipped"]}
    assert reasons["a.jpg"] == "이미 추가된 파일입니다."
    assert reasons["note.txt"] == "지원하지 않는 형식입니다."
    assert reasons["b.gif"] == "지원하지 않는 형식입니다."
    r2 = api.add_receipts([jpg])
    assert r2["skipped"] and r2["skipped"][0]["reason"] == "이미 추가된 파일입니다."


def test_pending_card_schema(tmp_path):
    api = _api()
    p = _touch(str(tmp_path / "x.png"))
    card = api.add_receipts([p])["items"][0]
    assert set(card) >= _CARD_KEYS
    assert set(card["data"]) >= _DATA_KEYS
    assert card["confirmed"] is False and card["edited"] is False
    assert card["page_index"] == 1 and card["page_total"] == 1
    assert card["flags"] == [] and card["flag_texts"] == []


def test_extract_error_path_does_not_raise(tmp_path, monkeypatch):
    """실제 이미지 없이 오류 경로 — 예외가 dict 로만 돌아온다."""
    api = _api()
    missing = str(tmp_path / "nope.jpg")  # 파일 없음
    r = api.add_receipts([missing])
    assert r["ok"] and r["items"]

    def boom(paths, **kw):
        raise RuntimeError("ocr down")

    monkeypatch.setattr("src.api.receipt.extract.extract_batch", boom)
    out = api.extract_receipts()
    json.dumps(out)
    assert out["ok"] is True
    assert out["items"][0]["status"] == "error"
    assert "ocr down" in out["items"][0]["error"]


def test_extract_maps_card_and_keeps_none(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "r.jpg"))
    api.add_receipts([p])

    def batch(paths, **kw):
        return [_ok_res(paths[0], data={
            "store_name": "가게", "biz_no": "", "tx_datetime": "2026-06-11",
            "total_amount": 1000, "supply_amount": None, "vat": None,
            "card_no_masked": "1234567812345678", "approval_no": "",
            "items": [{"name": "커피", "qty": None, "price": 1000}],
            "_flags": ["vat_separate"],
        }, flags=["vat_separate"])]

    monkeypatch.setattr("src.api.receipt.extract.extract_batch", batch)
    r = api.extract_receipts()
    card = r["items"][0]
    assert card["status"] == "ok"
    assert card["source"] == "win_ocr"
    assert card["data"]["supply_amount"] is None
    assert card["data"]["vat"] is None
    assert card["data"]["items"][0]["qty"] is None
    assert card["data"]["total_amount"] == 1000
    assert card["data"]["card_no_masked"] == "1234-****-****-5678"
    assert "vat_separate" in card["flags"]
    assert FLAG_TEXTS["vat_separate"] in card["flag_texts"]
    assert "_flags" not in card["data"]
    json.dumps(r)


def test_extract_pdf_expands_pages(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "multi.pdf"))
    api.add_receipts([p])

    def batch(paths, **kw):
        return [
            _ok_res(paths[0], page=1, page_total=2),
            _ok_res(paths[0], page=2, page_total=2),
        ]

    monkeypatch.setattr("src.api.receipt.extract.extract_batch", batch)
    r = api.extract_receipts()
    assert len(r["items"]) == 2
    assert [c["page_index"] for c in r["items"]] == [1, 2]
    assert r["items"][0]["id"] == "r1"
    assert r["items"][1]["id"] == "r2"
    assert r["items"][0]["page_total"] == 2


def test_progress_skipped_when_window_none(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.jpg"))
    api.add_receipts([p])
    monkeypatch.setattr("src.api.receipt.extract.extract_batch",
                        lambda paths, **kw: [_err_res(paths[0])])
    r = api.extract_receipts()
    assert r["ok"]


def test_progress_notifies_per_file(tmp_path, monkeypatch):
    api = _api()
    a = _touch(str(tmp_path / "a.jpg"))
    b = _touch(str(tmp_path / "b.jpg"))
    api.add_receipts([a, b])
    seen = []

    class W:
        def evaluate_js(self, src):
            seen.append(src)

    api._window = W()
    monkeypatch.setattr("src.api.receipt.extract.extract_batch",
                        lambda paths, **kw: [_err_res(paths[0])])
    api.extract_receipts()
    assert any("onReceiptProgress" in s for s in seen)
    assert any('"total": 2' in s or '"total":2' in s for s in seen)


def test_update_reverify_and_edited_keeps_none(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.jpg"))
    api.add_receipts([p])
    monkeypatch.setattr("src.api.receipt.extract.extract_batch",
                        lambda paths, **kw: [_ok_res(paths[0])])
    api.extract_receipts()
    r = api.update_receipt({
        "id": "r1",
        "data": {"supply_amount": None, "vat": "", "total_amount": 9000,
                 "items": [{"name": "커피", "qty": "", "price": 9000}]},
    })
    assert r["ok"]
    item = r["item"]
    assert item["edited"] is True
    assert item["data"]["supply_amount"] is None
    assert item["data"]["vat"] is None
    assert item["data"]["items"][0]["qty"] is None
    assert "sum_mismatch" not in item["flags"]  # 9000==9000
    # 총액과 품목은 맞지만 공급가/부가세 None 이면 vat 대조 스킵
    json.dumps(r)


def test_update_flags_sum_mismatch(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.jpg"))
    api.add_receipts([p])
    monkeypatch.setattr("src.api.receipt.extract.extract_batch",
                        lambda paths, **kw: [_ok_res(paths[0])])
    api.extract_receipts()
    r = api.update_receipt({
        "id": "r1",
        "data": {"total_amount": 24000,
                 "items": [{"name": "커피", "qty": 1, "price": 100}]},
    })
    assert "sum_mismatch" in r["item"]["flags"]
    assert FLAG_TEXTS["sum_mismatch"] in r["item"]["flag_texts"]


def test_update_unknown_id():
    r = _api().update_receipt({"id": "r9", "data": {}})
    assert r["ok"] is False and r["error"]


def test_confirm_blocks_non_ok(tmp_path, monkeypatch):
    api = _api()
    a = _touch(str(tmp_path / "ok.jpg"))
    b = _touch(str(tmp_path / "bad.jpg"))
    api.add_receipts([a, b])

    def batch(paths, **kw):
        p = paths[0]
        if p.endswith("ok.jpg"):
            return [_ok_res(p)]
        return [_err_res(p)]

    monkeypatch.setattr("src.api.receipt.extract.extract_batch", batch)
    api.extract_receipts()
    r = api.confirm_receipts()
    assert r["ok"]
    assert r["confirmed"] == 1
    assert r["blocked"] == 1
    state = api.get_receipt_state()
    by_name = {c["name"]: c for c in state["items"]}
    assert by_name["ok.jpg"]["confirmed"] is True
    assert by_name["bad.jpg"]["confirmed"] is False


def test_confirm_blocks_pending(tmp_path):
    api = _api()
    api.add_receipts([_touch(str(tmp_path / "p.jpg"))])
    r = api.confirm_receipts()
    assert r["confirmed"] == 0 and r["blocked"] == 1


def test_export_requires_confirmed():
    r = _api().export_receipts()
    assert r["ok"] is False
    assert r["error"] == "확정된 영수증이 없습니다."


def test_export_calls_xlsx_after_confirm(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.jpg"))
    api.add_receipts([p])
    monkeypatch.setattr("src.api.receipt.extract.extract_batch",
                        lambda paths, **kw: [_ok_res(paths[0])])
    api.extract_receipts()
    api.confirm_receipts()
    called = {}

    def fake_export(items, out_path):
        called["n"] = len(items)
        called["path"] = out_path
        return {"ok": True, "path": out_path, "count": len(items), "error": ""}

    class W:
        def create_file_dialog(self, *a, **k):
            return [str(tmp_path / "out.xlsx")]

    api._window = W()
    monkeypatch.setattr("src.api.receipt._export_xlsx", fake_export)
    r = api.export_receipts()
    assert r["ok"] and r["count"] == 1
    assert called["n"] == 1


def test_pick_cancel_is_ok_empty():
    api = _api()

    class W:
        def create_file_dialog(self, *a, **k):
            return None

    api._window = W()
    r = api.pick_receipt_files()
    assert r["ok"] is True and r["paths"] == []


def test_remove_and_clear(tmp_path):
    api = _api()
    a = _touch(str(tmp_path / "a.jpg"))
    b = _touch(str(tmp_path / "b.jpg"))
    api.add_receipts([a, b])
    r = api.remove_receipt("r1")
    assert r["ok"] and len(r["items"]) == 1 and r["items"][0]["id"] == "r2"
    assert api.remove_receipt("r1")["ok"] is False
    assert api.clear_receipts()["ok"] is True
    assert api.get_receipt_state()["items"] == []


def test_notice_persists_via_save_config(monkeypatch):
    saved = {}

    def save(cfg):
        saved.update(cfg)

    monkeypatch.setattr(cs, "save_config", save)
    api = _api()
    st = api.get_receipt_state()
    assert st["ok"] and st["notice_shown"] is False
    assert api.mark_receipt_notice_seen()["ok"]
    assert saved.get("receipt", {}).get("notice_shown") is True
    assert api.get_receipt_state()["notice_shown"] is True


def test_get_receipt_image_long_edge(tmp_path):
    api = _api()
    p = _touch(str(tmp_path / "big.png"), _png_bytes(1200, 800))
    api.add_receipts([p])
    r = api.get_receipt_image("r1")
    assert r["ok"]
    assert r["data_uri"].startswith("data:image/png;base64,")
    raw = base64.b64decode(r["data_uri"].split(",", 1)[1])
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    assert max(im.size) == 900
    assert im.size == (900, 600)


def test_get_receipt_image_missing_id():
    r = _api().get_receipt_image("r9")
    assert r["ok"] is False


def test_extract_passes_allow_fallback_false_without_key(tmp_path, monkeypatch):
    api = _api()
    p = _touch(str(tmp_path / "a.jpg"))
    api.add_receipts([p])
    seen = {}

    def batch(paths, **kw):
        seen.update(kw)
        return [_ok_res(paths[0])]

    monkeypatch.setattr("src.api.receipt.extract.extract_batch", batch)
    api.extract_receipts()
    assert seen.get("allow_fallback") is False
