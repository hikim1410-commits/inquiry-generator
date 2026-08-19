# -*- coding: utf-8 -*-
"""영수증 도메인 — 세션 카드 목록, 추출/검토/확정, Excel 내보내기 연결."""
import base64
import copy
import io
import json
import os
import traceback

from src.ai.receipt import mask_card
from src.logutil import log as _log
from src.receipt import extract, verify
from src.store import config_store as cs

from ._common import _err, _file_dialog

# 지원 확장자 (소문자). add_receipts 가 이 집합 밖을 skipped 로 돌린다.
_SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".pdf"}
_PREVIEW_LONG_EDGE = 900  # get_receipt_image 미리보기 긴 변

# WA 소유 단일 매핑 — UI/Excel 이 이 문구만 소비한다. 미지 flag 는 원문을 쓴다.
FLAG_TEXTS = {
    "sum_mismatch": "항목 합계가 총액과 다릅니다",
    "vat_mismatch": "공급가액+부가세가 합계와 다릅니다",
    "no_total": "총액을 읽지 못했습니다",
    "no_items": "품목을 읽지 못했습니다",
    "refund_suspected": "환불·마이너스 금액으로 보입니다",
    "vat_separate": "부가세 별도 표기입니다",
}

# 검증이 다시 계산하지 않는 파서 플래그 — 편집 후에도 유지한다.
_PRESERVE_FLAGS = ("refund_suspected", "vat_separate")


def _flag_texts(flags):
    return [FLAG_TEXTS.get(f, str(f)) for f in (flags or []) if f]


def _as_amount(v, missing=None):
    """금액/수량. 빈값·None 은 missing 그대로(0으로 날조하지 않는다). 실제 0은 0."""
    if v is None or v == "":
        return missing
    if isinstance(v, bool):
        return missing
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            s = str(v).replace(",", "").strip()
            if not s:
                return missing
            return int(float(s))
        except (TypeError, ValueError):
            return missing


def _blank_data():
    return {
        "store_name": "", "biz_no": "", "tx_datetime": "",
        "total_amount": 0, "supply_amount": None, "vat": None,
        "card_no_masked": "", "approval_no": "",
        "items": [],
    }


def _norm_item(it):
    if not isinstance(it, dict):
        return {"name": "", "qty": None, "price": 0}
    return {
        "name": str(it.get("name") or ""),
        "qty": _as_amount(it.get("qty"), missing=None),
        "price": _as_amount(it.get("price"), missing=0),
    }


def _data_from_extract(raw):
    """엔진 data → card.data. _flags 등 내부 키는 빼고, None 금액은 None 으로 둔다."""
    raw = raw if isinstance(raw, dict) else {}
    items = [_norm_item(it) for it in (raw.get("items") or []) if isinstance(it, dict)]
    items = [it for it in items if it["name"]]
    return {
        "store_name": str(raw.get("store_name") or ""),
        "biz_no": str(raw.get("biz_no") or ""),
        "tx_datetime": str(raw.get("tx_datetime") or ""),
        "total_amount": _as_amount(raw.get("total_amount"), missing=0),
        "supply_amount": _as_amount(raw.get("supply_amount"), missing=None),
        "vat": _as_amount(raw.get("vat"), missing=None),
        "card_no_masked": mask_card(str(raw.get("card_no_masked") or "")),
        "approval_no": str(raw.get("approval_no") or ""),
        "items": items,
    }


def _path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _export_xlsx(items, out_path):
    from src.receipt.export_xlsx import export_receipts as fn
    return fn(items, out_path)


class ReceiptApi:
    """세션 메모리 `self._receipts` 카드 목록. 엔진(src/receipt)은 호출만 한다."""

    def _rc_state(self):
        if getattr(self, "_receipts", None) is None:
            self._receipts = []
        if not hasattr(self, "_receipt_seq"):
            self._receipt_seq = 0
        if not hasattr(self, "_window"):
            self._window = None
        if not hasattr(self, "cfg") or self.cfg is None:
            self.cfg = {}
        return self._receipts

    def _rc_new_id(self):
        self._rc_state()
        self._receipt_seq += 1
        return f"r{self._receipt_seq}"

    def _rc_find(self, rid):
        for c in self._rc_state():
            if c.get("id") == rid:
                return c
        return None

    def _rc_items(self):
        return copy.deepcopy(self._rc_state())

    def _rc_notice_shown(self):
        return bool((self.cfg or {}).get("receipt", {}).get("notice_shown", False))

    def _rc_progress(self, done, total, name):
        window = getattr(self, "_window", None)
        if window is None:
            return
        try:
            payload = json.dumps(
                {"done": int(done), "total": int(total), "name": str(name or "")},
                ensure_ascii=False)
            window.evaluate_js(
                f"window.onReceiptProgress && window.onReceiptProgress({payload})")
        except Exception:
            pass

    def _rc_blank_card(self, path):
        abs_path = os.path.abspath(path)
        return {
            "id": self._rc_new_id(),
            "path": abs_path,
            "name": os.path.basename(abs_path),
            "page_index": 1,
            "page_total": 1,
            "status": "pending",
            "source": "",
            "flags": [],
            "flag_texts": [],
            "fallback_reason": "",
            "error": "",
            "confirmed": False,
            "edited": False,
            "data": _blank_data(),
        }

    def _rc_flags_for(self, data, extra=None, preserve=None):
        probe = dict(data)
        merged = []
        for f in list(preserve or []) + list(extra or []):
            if f and f not in merged:
                merged.append(f)
        probe["_flags"] = merged
        return [f for f in verify.verify(probe) if f]

    def _rc_apply_extract(self, rid, path, res, edited=False, confirmed=False):
        raw = (res or {}).get("data") or {}
        data = _data_from_extract(raw)
        extra = list((res or {}).get("flags") or []) + list(raw.get("_flags") or [])
        flags = self._rc_flags_for(data, extra=extra)
        page = (res or {}).get("page_index")
        if page is None:
            page = (res or {}).get("page")
        ok = bool((res or {}).get("ok"))
        return {
            "id": rid,
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "page_index": int(page or 1),
            "page_total": int((res or {}).get("page_total") or 1),
            "status": "ok" if ok else "error",
            "source": str((res or {}).get("source") or ""),
            "flags": flags,
            "flag_texts": _flag_texts(flags),
            "fallback_reason": str((res or {}).get("fallback_reason") or ""),
            "error": str((res or {}).get("error") or ""),
            "confirmed": bool(confirmed) if ok else False,
            "edited": bool(edited),
            "data": data,
        }

    def _preview_png(self, path, page_index):
        from PIL import Image, ImageOps

        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(path)
            try:
                idx = max(0, int(page_index or 1) - 1)
                if idx >= len(doc):
                    raise ValueError("해당 페이지를 찾을 수 없습니다.")
                im = doc[idx].render(scale=2.0).to_pil()
            finally:
                doc.close()
        else:
            with Image.open(path) as src:
                try:
                    rotated = ImageOps.exif_transpose(src)
                    im = rotated if rotated is not None else src
                except Exception:
                    im = src
                im = im.copy()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h) or 1
        if longest > _PREVIEW_LONG_EDGE:
            scale = _PREVIEW_LONG_EDGE / float(longest)
            im = im.resize((max(1, int(round(w * scale))),
                            max(1, int(round(h * scale)))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64

    # ================= 파일 선택/추가 =================
    def pick_receipt_files(self):
        """파일 선택 대화상자. 취소도 ok:True + 빈 목록."""
        try:
            import webview
            result = self._window.create_file_dialog(
                _file_dialog(webview, "OPEN"),
                allow_multiple=True,
                file_types=(
                    "영수증 (*.jpg;*.jpeg;*.png;*.bmp;*.tif;*.tiff;*.pdf)",
                    "모든 파일 (*.*)",
                ),
            )
            if result:
                return {"ok": True, "paths": list(result)}
            return {"ok": True, "paths": []}
        except Exception as e:
            return _err(e)

    def add_receipts(self, paths):
        try:
            items = self._rc_state()
            skipped = []
            if isinstance(paths, str):
                paths = [paths]
            existing = {_path_key(c["path"]) for c in items}
            seen = set(existing)
            for raw in paths or []:
                path = str(raw or "").strip()
                if not path:
                    continue
                name = os.path.basename(path)
                ext = os.path.splitext(path)[1].lower()
                if ext not in _SUPPORTED_EXT:
                    skipped.append({"name": name, "reason": "지원하지 않는 형식입니다."})
                    continue
                key = _path_key(path)
                if key in seen:
                    skipped.append({"name": name, "reason": "이미 추가된 파일입니다."})
                    continue
                seen.add(key)
                items.append(self._rc_blank_card(path))
            return {"ok": True, "items": self._rc_items(), "skipped": skipped}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    # ================= 추출 =================
    def extract_receipts(self, ids=None):
        try:
            cards = self._rc_state()
            if ids is None:
                selected = list(cards)
            else:
                idset = {str(i) for i in (ids or [])}
                selected = [c for c in cards if c.get("id") in idset]
            paths, seen = [], set()
            for c in selected:
                key = _path_key(c["path"])
                if key in seen:
                    continue
                seen.add(key)
                paths.append(c["path"])
            if not paths:
                return {"ok": True, "items": self._rc_items()}

            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            model = cs.get_ai_model(self.cfg, provider)
            allow_fallback = bool(api_key)

            results_by_path = {}
            total = len(paths)
            for i, path in enumerate(paths):
                self._rc_progress(i, total, os.path.basename(path))
                try:
                    batch = extract.extract_batch(
                        [path], provider=provider, api_key=api_key, model=model,
                        allow_fallback=allow_fallback)
                except Exception as e:
                    _log(f"영수증 추출 예외({os.path.basename(path)}): {e}")
                    batch = [{
                        "ok": False, "path": path, "source": "",
                        "data": {}, "flags": [], "fallback_reason": "",
                        "error": str(e),
                    }]
                if not isinstance(batch, list):
                    batch = [batch]
                results_by_path[_path_key(path)] = batch
            self._rc_progress(total, total, os.path.basename(paths[-1]))

            new_list = []
            replaced = set()
            for c in cards:
                key = _path_key(c["path"])
                if key not in results_by_path:
                    new_list.append(c)
                    continue
                if key in replaced:
                    continue
                replaced.add(key)
                old_for_path = [x for x in cards if _path_key(x["path"]) == key]
                batch = results_by_path[key]
                if not batch:
                    new_list.extend(old_for_path)
                    continue
                for i, res in enumerate(batch):
                    old = old_for_path[i] if i < len(old_for_path) else {}
                    rid = old.get("id") or self._rc_new_id()
                    new_list.append(self._rc_apply_extract(
                        rid, (res or {}).get("path") or c["path"], res,
                        edited=False, confirmed=False))
            self._receipts = new_list
            return {"ok": True, "items": self._rc_items()}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def get_receipt_state(self):
        try:
            self._rc_state()
            return {"ok": True, "items": self._rc_items(),
                    "notice_shown": self._rc_notice_shown()}
        except Exception as e:
            return _err(e)

    def get_receipt_image(self, rid):
        try:
            card = self._rc_find(rid)
            if not card:
                return _err("영수증을 찾을 수 없습니다.")
            path = card.get("path") or ""
            if not os.path.isfile(path):
                return _err("파일을 찾을 수 없습니다.")
            data_uri = self._preview_png(path, card.get("page_index") or 1)
            return {"ok": True, "data_uri": data_uri}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def update_receipt(self, payload):
        try:
            payload = payload or {}
            rid = payload.get("id")
            patch = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            card = self._rc_find(rid)
            if not card:
                return _err("영수증을 찾을 수 없습니다.")
            data = dict(card.get("data") or _blank_data())
            for k, v in patch.items():
                if k == "items":
                    data["items"] = [_norm_item(it) for it in (v or [])]
                elif k == "card_no_masked":
                    data[k] = mask_card("" if v is None else str(v))
                elif k in ("supply_amount", "vat"):
                    data[k] = _as_amount(v, missing=None)
                elif k == "total_amount":
                    data[k] = _as_amount(v, missing=None)
                elif k in ("store_name", "biz_no", "tx_datetime", "approval_no"):
                    data[k] = "" if v is None else str(v)
            preserve = [f for f in (card.get("flags") or []) if f in _PRESERVE_FLAGS]
            flags = self._rc_flags_for(data, preserve=preserve)
            card["data"] = data
            card["flags"] = flags
            card["flag_texts"] = _flag_texts(flags)
            card["edited"] = True
            return {"ok": True, "item": copy.deepcopy(card)}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def remove_receipt(self, rid):
        try:
            cards = self._rc_state()
            nxt = [c for c in cards if c.get("id") != rid]
            if len(nxt) == len(cards):
                return _err("영수증을 찾을 수 없습니다.")
            self._receipts = nxt
            return {"ok": True, "items": self._rc_items()}
        except Exception as e:
            return _err(e)

    def clear_receipts(self):
        try:
            self._rc_state()
            self._receipts = []
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def confirm_receipts(self, ids=None):
        try:
            cards = self._rc_state()
            idset = None if ids is None else {str(i) for i in (ids or [])}
            confirmed = blocked = 0
            for c in cards:
                if idset is not None and c.get("id") not in idset:
                    continue
                if c.get("status") != "ok":
                    blocked += 1
                    continue
                c["confirmed"] = True
                confirmed += 1
            return {"ok": True, "confirmed": confirmed, "blocked": blocked}
        except Exception as e:
            return _err(e)

    def export_receipts(self):
        try:
            items = [c for c in self._rc_state() if c.get("confirmed")]
            if not items:
                return _err("확정된 영수증이 없습니다.")
            import webview
            result = self._window.create_file_dialog(
                _file_dialog(webview, "SAVE"),
                save_filename="영수증정산.xlsx",
                file_types=("Excel 파일 (*.xlsx)",),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else str(result)
            if path and not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            res = _export_xlsx(items, path)
            if not res.get("ok"):
                return _err(res.get("error") or "내보내기 실패")
            return {"ok": True, "path": res.get("path") or path,
                    "count": int(res.get("count") if res.get("count") is not None
                                 else len(items))}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def mark_receipt_notice_seen(self):
        try:
            self._rc_state()
            self.cfg.setdefault("receipt", {})["notice_shown"] = True
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)
