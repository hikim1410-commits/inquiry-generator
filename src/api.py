# -*- coding: utf-8 -*-
"""pywebview js_api — UI와 엔진/저장소/HWP워커/AI를 잇는 단일 계약 지점.

모든 메서드는 JSON 직렬화 가능한 dict를 주고받는다.
계산은 전부 Python 엔진에서 수행 (JS 산수 금지 원칙).
"""
import os
import traceback
from datetime import date as _date

from src.engine.calc import (LaborRow, ExpenseRow, calculate, budget_guide,
                             fmt_won, fmt_pct, fmt_num, fmt_rate,
                             round_half_up, parse_leading_num)
from src.engine.goalseek import goal_seek, goal_seek_labor
from src.engine.money_kor import amount_kor
from src.hwp.field_map import build_render_plan
from src.scan.hwp_scan import scan_folder as _scan_folder
from src.store import config_store as cs
from src.store import quote_store as qs
from src.ai import engine as ai_engine
from src.ai import llm as ai_llm
from src.drive import gdrive
from src.logutil import log as _log


def _err(msg, **kw):
    return {"ok": False, "error": str(msg), **kw}


def _file_dialog(webview_mod, kind):
    """pywebview 6.x: webview.FileDialog.{OPEN,SAVE,FOLDER} enum.
    requirements.txt는 >=5.0(느슨)이라 구버전 상수(OPEN_DIALOG 등)로 폴백."""
    fd = getattr(webview_mod, "FileDialog", None)
    return getattr(fd, kind) if fd else getattr(webview_mod, f"{kind}_DIALOG")


# AI 초안 기초 지침 최대 길이 (config 비대화·실수 붙여넣기 방지)
_AI_PROMPT_MAX = 8000


def _parse_labor(items):
    rows = []
    for it in items or []:
        rows.append(LaborRow(
            grade=str(it.get("grade", "")),
            unit_price=float(it.get("unit_price") or 0),
            count=float(it.get("count") or 0),
            rate=float(it.get("rate") or 0),
            months=float(it.get("months") or 0),
        ))
    return rows


def _num_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_expenses(items):
    rows = []
    for it in items or []:
        raw_details = it.get("details")
        if isinstance(raw_details, str):
            raw_details = raw_details.split("\n")
        details = [str(d).strip() for d in (raw_details or []) if str(d).strip()]
        qty = _num_or_none(it.get("qty"))
        # 폴백: qty 없이 수량 표기만 있으면 표기에서 숫자 추출 (구버전/AI 초안 호환)
        if qty is None:
            qty = parse_leading_num(it.get("qty_text"))
        rows.append(ExpenseRow(
            name=str(it.get("name", "")).strip(),
            details=details,
            qty_text=str(it.get("qty_text", "")).strip(),
            unit_price=_num_or_none(it.get("unit_price")),
            qty=qty,
            extra1=_num_or_none(it.get("extra1")),
            extra2=_num_or_none(it.get("extra2")),
        ))
    return rows


def _display(result):
    """QuoteResult → UI 표시 모델."""
    r = result
    def block(x):
        return {"won": fmt_won(x), "pct": fmt_pct(r.ratio(x)), "raw": x}
    labor = []
    for row in r.labor_rows:
        labor.append({
            "grade": row.grade,
            "cnt": f"{fmt_num(row.count)}명" if row.count else "-",
            "price": fmt_won(row.unit_price),
            "months": f"{fmt_num(row.months)}개월" if row.months else "-",
            "rate": fmt_rate(row.rate) if row.rate else "-",
            "amt": fmt_won(row.amount),
            "pct": fmt_pct(r.ratio(row.amount)),
            "active": row.count > 0,
        })
    expenses = []
    for e in r.expense_rows:
        expenses.append({
            "name": e.name, "details": e.details, "qty_text": e.qty_text or "-",
            "price": fmt_won(e.unit_price) if e.unit_price is not None else "-",
            "amt": fmt_won(e.amount),
            "pct": fmt_pct(r.ratio(e.amount)),
            "active": bool(e.name.strip()),
        })
    return {
        "labor": labor, "expenses": expenses,
        "labor_sum": block(r.labor_total),
        "exp_sum": block(r.expense_total),
        "subtotal": block(r.direct),
        "mgmt": block(r.mgmt),
        "profit": block(r.profit),
        "supply": block(r.supply),
        "vat": block(r.vat),
        "total": block(r.total),
        "trim": block(r.trim),
        "final": block(r.final),
        "final_won_int": round_half_up(r.final),
        "amount_kor": amount_kor(round_half_up(r.final)),
        "profit_on": r.profit_on,
    }


def _parse_quote(payload):
    labor = _parse_labor(payload.get("labor"))
    expenses = _parse_expenses(payload.get("expenses"))
    profit_on = bool(payload.get("options", {}).get("profit", True))
    trim = float(payload.get("trim") or 0)
    return labor, expenses, profit_on, trim


# 종료/구형 Gemini 모델 — 자동으로 최신 별칭으로 치환 (저장된 config 치유)
_DEPRECATED_MODELS = {
    "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-flash-8b",
    "gemini-1.5-flash-002", "gemini-pro", "gemini-1.0-pro",
}


class Api:
    def __init__(self):
        self._window = None
        self._worker = None
        self.cfg = cs.load_config()
        self._migrate_model()
        # 구버전 last_folder → doc_types.quote.folder 1회 이관
        try:
            if cs.migrate_doc_type_folders(self.cfg):
                cs.save_config(self.cfg)
        except Exception:
            pass
        # 회의록 preset 시딩·정규화 (적대리뷰 #4: _merge 비의존 전용 마이그레이션)
        try:
            if cs.migrate_minutes_presets(self.cfg):
                cs.save_config(self.cfg)
        except Exception:
            pass

    def _migrate_model(self):
        """구형/종료 모델이 저장돼 있으면 최신 별칭으로 교체 (AI 호출 404 예방).
        손상된 config(gemini가 dict 아님)에서도 기동이 막히지 않도록 방어."""
        g = self.cfg.get("gemini")
        if not isinstance(g, dict):
            return
        if g.get("model", "") in _DEPRECATED_MODELS:
            g["model"] = "gemini-flash-latest"
            try:
                cs.save_config(self.cfg)
            except Exception:
                pass

    def attach_window(self, window):
        self._window = window
        # 페이지 로드 후 드롭존에 네이티브 DnD 핸들러 등록 (실패해도 파일선택 버튼으로 동작)
        window.events.loaded += self._wire_dropzones

    # 드롭존 셀렉터 → JS 통지 시 zone 식별자
    _DROPZONES = (("#ai-dropzone", "ai"), ("#minutes-dropzone", "minutes"))

    def _wire_dropzones(self):
        """pywebview DOM 이벤트로 drop을 받아야 파일 전체 경로(pywebviewFullPath)가
        주입된다 (edgechromium은 Python측 리스너가 있어야 경로를 축적함)."""
        import functools
        try:
            from webview.dom import DOMEventHandler
        except Exception as e:
            _log(f"드롭존 등록 불가(webview.dom 없음): {e}")
            return
        for selector, zone in self._DROPZONES:
            try:
                el = self._window.dom.get_element(selector)
                if el is None:
                    continue
                el.on("drop", DOMEventHandler(
                    functools.partial(self._on_drop, zone),
                    prevent_default=True, stop_propagation=True))
                _log(f"드롭존 핸들러 등록: {selector}")
            except Exception as e:
                _log(f"드롭존 등록 실패({selector}): {e}")

    def _on_drop(self, zone, event):
        """pywebview가 별도 스레드에서 호출. 경로 추출·통지만 하고 즉시 반환
        (변환은 JS가 call('convert_files')로 표준 경로 재요청)."""
        try:
            import json as _json
            files = (event.get("dataTransfer") or {}).get("files") or []
            paths, unmatched = [], []
            for f in files:
                p = f.get("pywebviewFullPath")
                if p:
                    paths.append(p)
                else:
                    unmatched.append(f.get("name", "?"))
            payload = _json.dumps(
                {"zone": zone, "paths": paths, "unmatched": unmatched},
                ensure_ascii=False)
            self._window.evaluate_js(
                f"window.onNativeFilesDropped && window.onNativeFilesDropped({payload})")
        except Exception as e:
            _log(f"드롭 처리 예외: {e}\n{traceback.format_exc()}")

    def _get_worker(self):
        if self._worker is None:
            from src.hwp.hwp_writer import HwpWorker
            self._worker = HwpWorker()
        return self._worker

    def shutdown(self):
        if self._worker:
            self._worker.shutdown()

    # ================= 설정 =================
    def get_config(self):
        cfg = self.cfg
        ai_keys = {p: bool(cs.get_ai_key(cfg, p)) for p in cs.AI_PROVIDERS}
        ai_models = {p: cs.get_ai_model(cfg, p) for p in cs.AI_PROVIDERS}
        return {"ok": True, "config": {
            "company": cfg["company"],
            "unit_prices": cfg["unit_prices"],
            "default_price_year": cfg["default_price_year"],
            # AI (멀티 프로바이더)
            "ai_provider": cs.get_provider(cfg),
            "ai_providers": list(cs.AI_PROVIDERS),
            "ai_provider_labels": ai_llm.PROVIDER_LABELS,
            "ai_keys_set": ai_keys,
            "ai_models": ai_models,
            "ai_default_models": ai_llm.DEFAULT_MODELS,
            "gemini_models": cs.GEMINI_MODELS,    # gemini 큐레이트 드롭다운용
            # AI 초안 기초 지침 (빈 문자열 = 기본 지침 사용) + 기본값(복원·비교용)
            "ai_prompts": {t: cs.get_ai_prompt(cfg, t)
                           for t in cs.AI_PROMPT_DOC_TYPES},
            "ai_prompt_defaults": dict(ai_engine.DIRECTIVE_DEFAULTS),
            "max_counts": cfg.get("labor", {}).get("max_counts", {}),
            "labor_ratio": cs.get_labor_ratio(cfg),
            "last_folder": cfg.get("last_folder", ""),
            # 문서 유형별 작업 폴더 (UI 시딩용 — 폴백 적용된 실효값)
            "doc_folders": {
                "quote": self._doc_folder("quote"),
                "minutes": self._doc_folder("minutes"),
            },
            "keep_il": cfg.get("money", {}).get("keep_il", True),
            "tutorial_seen": bool(cfg.get("tutorial", {}).get("seen", False)),
        }}

    # ---- 멀티 프로바이더 AI ----
    def set_ai_provider(self, provider):
        try:
            cs.set_provider(self.cfg, provider)
            return {"ok": True, "provider": cs.get_provider(self.cfg)}
        except Exception as e:
            return _err(e)

    def set_ai_key(self, provider, key):
        try:
            cs.set_ai_key(self.cfg, provider, key or "")
            return {"ok": True, "key_set": bool(key)}
        except Exception as e:
            return _err(e)

    def set_ai_model(self, provider, model):
        try:
            cs.set_ai_model(self.cfg, provider, model)
            return {"ok": True, "model": cs.get_ai_model(self.cfg, provider)}
        except Exception as e:
            return _err(e)

    def set_ai_prompt(self, doc_type, text):
        """AI 초안 기초 지침 저장. 빈 값·기본값과 동일하면 오버라이드 해제("")
        — 사용자가 그대로 저장해도 향후 기본 지침 개선이 계속 반영되게."""
        try:
            if doc_type not in cs.AI_PROMPT_DOC_TYPES:
                return _err(f"알 수 없는 문서 유형: {doc_type}")
            norm = str(text or "").replace("\r\n", "\n").strip()
            if len(norm) > _AI_PROMPT_MAX:
                return _err(f"프롬프트가 너무 깁니다 ({_AI_PROMPT_MAX:,}자 이내로 입력하세요).")
            if norm == ai_engine.DIRECTIVE_DEFAULTS.get(doc_type, "").strip():
                norm = ""
            cs.set_ai_prompt(self.cfg, doc_type, norm)
            return {"ok": True, "doc_type": doc_type,
                    "custom": bool(norm), "text": norm}
        except Exception as e:
            return _err(e)

    def validate_ai_key(self, provider):
        key = cs.get_ai_key(self.cfg, provider)
        if not key:
            return _err("저장된 API 키가 없습니다.")
        return ai_engine.validate_key(provider, key)

    def list_ai_models(self, provider):
        key = cs.get_ai_key(self.cfg, provider)
        if not key:
            return _err("먼저 API 키를 저장하세요.")
        return ai_engine.list_models(provider, key)

    def set_config(self, partial):
        try:
            allowed = {"company", "unit_prices", "default_price_year", "last_folder"}
            for k, v in (partial or {}).items():
                if k in allowed:
                    self.cfg[k] = v
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def suggest_quote_no(self, year=None):
        y = str(year or _date.today().year)
        return {"ok": True, "quote_no": cs.next_quote_no(self.cfg, y, peek=False)}

    # ================= 폴더/파일 =================
    def _doc_folder(self, doc_type="quote"):
        """문서 유형별 작업 폴더. doc_types[t].folder가 비면 last_folder 폴백
        (minutes도 폴백 — 기존 회의록이 견적 폴더에 생성돼 온 연속성)."""
        f = (self.cfg.get("doc_types", {}).get(doc_type, {}) or {}).get("folder", "")
        return f or self.cfg.get("last_folder", "")

    def pick_doc_folder(self, doc_type="quote"):
        """폴더 선택 다이얼로그 → doc_types[t].folder 저장.
        quote는 last_folder도 동기 기록(하위호환)."""
        try:
            import webview
            res = self._window.create_file_dialog(_file_dialog(webview, "FOLDER"))
            if not res:
                return {"ok": False, "cancelled": True}
            folder = res[0] if isinstance(res, (list, tuple)) else str(res)
            self.cfg.setdefault("doc_types", {}).setdefault(doc_type, {})["folder"] = folder
            if doc_type == "quote":
                self.cfg["last_folder"] = folder
            cs.save_config(self.cfg)
            return {"ok": True, "folder": folder, "doc_type": doc_type}
        except Exception as e:
            return _err(e)

    def pick_folder(self):
        return self.pick_doc_folder("quote")

    def scan_folder(self, folder=None):
        try:
            folder = folder or self._doc_folder("quote")
            if not folder or not os.path.isdir(folder):
                return {"ok": True, "folder": "", "quotes": [], "stats": self._stats([])}
            metas = [m.to_dict() for m in _scan_folder(folder)]
            # 짝 없는 .quote.json (생성 전 저장본)도 카드로
            hwp_jsons = {m["json_path"] for m in metas if m["json_path"]}
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".quote.json"):
                    continue
                jpath = os.path.join(folder, name)
                if jpath in hwp_jsons:
                    continue
                try:
                    q = qs.load_quote(jpath)
                    doc = q.get("doc", {})
                    snap = q.get("snapshot", {})
                    metas.append({
                        "path": jpath, "filename": name,
                        "service_name": doc.get("service_name", name),
                        "recipient": doc.get("recipient", ""),
                        "amount": snap.get("final_won_int"),
                        "date": doc.get("date", ""), "quote_no": doc.get("quote_no", ""),
                        "source": "json", "editable": True, "json_path": jpath,
                        "mtime": os.path.getmtime(jpath), "error": "",
                    })
                except Exception:
                    pass
            metas.sort(key=lambda m: m.get("mtime", 0), reverse=True)
            return {"ok": True, "folder": folder, "quotes": metas,
                    "stats": self._stats(metas)}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def _stats(self, metas):
        today = _date.today()
        this_month = [m for m in metas
                      if (m.get("date") or "").startswith(f"{today.year}-{today.month:02d}")]
        amounts = [m["amount"] for m in metas if m.get("amount")]
        return {
            "total": len(metas),
            "this_month": len(this_month),
            "sum_amount": f"{sum(amounts):,}" if amounts else "0",
            "editable": sum(1 for m in metas if m.get("editable")),
        }

    def open_file(self, path):
        try:
            if not os.path.exists(path):
                return _err(f"파일이 없습니다: {path}")
            os.startfile(path)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def open_sibling_pdf(self, hwp_path):
        pdf = os.path.splitext(hwp_path)[0] + ".pdf"
        if os.path.exists(pdf):
            os.startfile(pdf)
            return {"ok": True}
        return _err("같은 이름의 PDF가 없습니다.")

    def open_external(self, url):
        """외부 URL을 기본 브라우저로 연다 (웹뷰 이탈 방지)."""
        try:
            if not str(url).startswith(("http://", "https://")):
                return _err("허용되지 않은 URL입니다.")
            import webbrowser
            webbrowser.open(url)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    # ================= 견적 데이터 =================
    def new_quote(self):
        year = self.cfg["default_price_year"]
        prices = self.cfg["unit_prices"].get(year, {})
        today = _date.today().isoformat()
        return {"ok": True, "quote": {
            "doc": {
                "recipient": "", "quote_no": "",
                "ref_name": "", "ref_tel": "",
                "date": today,
                "service_name": "", "service_period": "계약일로부터 3주일",
            },
            "options": {"profit": True, "price_year": year},
            "labor": [
                {"grade": g, "unit_price": prices.get(g, 0),
                 "count": 0, "rate": 0, "months": 0}
                for g in ["책임연구원", "연구원", "연구보조원", "보조원"]
            ],
            "expenses": [
                {"name": "전문가 활용비", "details": [], "qty_text": "",
                 "unit_price": None, "qty": None},
                {"name": "문헌구입비", "details": [], "qty_text": "",
                 "unit_price": None, "qty": None},
            ],
            "goal": {"target": None, "mode": "labor_first"},
            "trim": 0,
        }}

    def calc(self, payload):
        try:
            labor, expenses, profit_on, trim = _parse_quote(payload)
            result = calculate(labor, expenses, profit_on, trim)
            disp = _display(result)
            target = payload.get("goal", {}).get("target")
            guide = None
            if target:
                labor_ratio = cs.get_labor_ratio(self.cfg)
                g = budget_guide(float(target), profit_on, labor_ratio=labor_ratio)
                guide = {
                    "budget": fmt_won(g.budget),
                    "vat": fmt_won(g.vat), "cost": fmt_won(g.cost),
                    "profit": fmt_won(g.profit), "mgmt": fmt_won(g.mgmt),
                    "direct": fmt_won(g.direct),
                    "labor_target": fmt_won(g.labor_target),
                    "expense_target": fmt_won(g.expense_target),
                    "labor_gap_raw": result.labor_total - g.labor_target,
                    "labor_gap": fmt_won(abs(result.labor_total - g.labor_target)),
                    "exp_gap_raw": result.expense_total - g.expense_target,
                    "exp_gap": fmt_won(abs(result.expense_total - g.expense_target)),
                    "final_gap_raw": result.final - float(target),
                    "final_gap": fmt_won(abs(result.final - float(target))),
                }
            return {"ok": True, "display": disp, "guide": guide}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def goal_seek(self, payload):
        try:
            labor, expenses, profit_on, _ = _parse_quote(payload)
            goal = payload.get("goal", {})
            target = goal.get("target")
            if not target:
                return _err("목표 금액을 입력하세요.")
            target = float(target)
            locked = [i for i, it in enumerate(payload.get("labor") or [])
                      if it.get("locked")]

            # 인건비 자동조정(권장) — 책임 1명·10% 고정, 보조원 명수 탄력, 만원미만 절삭
            if goal.get("mode") == "labor_first":
                mc = self.cfg.get("labor", {}).get("max_counts", {})
                res = goal_seek_labor(target, labor, expenses, profit_on,
                                      max_counts=mc, locked=locked)
                if not res.ok:
                    return _err(res.error, warnings=res.warnings)
                for r, rate, cnt in zip(labor, res.rates, res.counts):
                    r.rate, r.count = rate, cnt
                result = calculate(labor, expenses, profit_on, trim=res.trim)
                return {"ok": True, "rates": res.rates, "counts": res.counts,
                        "trim": res.trim, "warnings": res.warnings,
                        "display": _display(result)}

            # 균등/비율 모드 — 만원미만 자동 절삭으로 목표금액 정확히 일치
            res = goal_seek(target, labor, expenses, profit_on,
                            mode=goal.get("mode", "uniform"),
                            locked=locked)
            if not res.ok:
                return _err(res.error, warnings=res.warnings)
            result = calculate(labor, expenses, profit_on, trim=res.trim)
            return {"ok": True,
                    "rates": res.rates, "trim": res.trim,
                    "warnings": res.warnings,
                    "display": _display(result)}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def set_max_counts(self, counts):
        """직급별 최대 인원(전역 기본값) 저장."""
        try:
            cur = self.cfg.setdefault("labor", {}).setdefault("max_counts", {})
            for g, v in (counts or {}).items():
                try:
                    cur[g] = max(1, int(v))
                except (TypeError, ValueError):
                    pass
            cs.save_config(self.cfg)
            return {"ok": True, "max_counts": cur}
        except Exception as e:
            return _err(e)

    def set_labor_ratio(self, ratio):
        """인건비 목표 비율 저장 (목표금액 대비, 0.1~0.9)."""
        try:
            r = max(0.1, min(0.9, float(ratio)))
            cs.set_labor_ratio(self.cfg, r)
            return {"ok": True, "labor_ratio": r}
        except (TypeError, ValueError):
            return _err("유효한 비율값이 아닙니다")
        except Exception as e:
            return _err(e)

    def set_tutorial_seen(self, seen=True):
        """튜토리얼 1회 노출 플래그 저장 (완료·건너뛰기·ESC 공통)."""
        try:
            self.cfg.setdefault("tutorial", {})["seen"] = bool(seen)
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    # ================= 저장/불러오기 =================
    def save_quote(self, payload, folder=None):
        try:
            folder = folder or self._doc_folder("quote")
            if not folder or not os.path.isdir(folder):
                return _err("먼저 작업 폴더를 선택하세요.")
            labor, expenses, profit_on, trim = _parse_quote(payload)
            result = calculate(labor, expenses, profit_on, trim)
            payload = dict(payload)
            payload["snapshot"] = {
                "final_won_int": round_half_up(result.final),
                "labor_total": round_half_up(result.labor_total),
                "expense_total": round_half_up(result.expense_total),
            }
            path = qs.save_quote(folder, payload)
            return {"ok": True, "path": path}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def load_quote(self, path):
        try:
            return {"ok": True, "quote": qs.load_quote(path)}
        except Exception as e:
            return _err(e)

    def delete_quote(self, payload):
        """견적 삭제. 기본은 재편집 데이터(.quote.json)만 제거.
        also_files=True일 때만 폴더의 실제 .hwp/.pdf 파일까지 삭제."""
        try:
            path = (payload.get("path") or "")
            json_path = (payload.get("json_path") or "")
            source = payload.get("source")
            also = bool(payload.get("also_files"))
            if source == "json" and path.endswith(".quote.json"):
                json_path = json_path or path
            removed = []
            # 1) 재편집 데이터 제거
            if json_path and os.path.exists(json_path):
                os.remove(json_path)
                removed.append(os.path.basename(json_path))
            # 2) 실제 파일은 명시적 동의가 있을 때만
            if also:
                base = None
                if json_path.endswith(".quote.json"):
                    base = json_path[:-len(".quote.json")]
                elif path.endswith(".hwp"):
                    base = path[:-4]
                elif path:
                    base = os.path.splitext(path)[0]
                for ext in (".hwp", ".pdf"):
                    f = (base + ext) if base else ""
                    if f and os.path.exists(f):
                        os.remove(f)
                        removed.append(os.path.basename(f))
            if not removed:
                return _err("삭제할 항목이 없습니다. (외부 HWP는 '파일도 삭제'를 체크해야 제거됩니다)")
            return {"ok": True, "removed": removed}
        except Exception as e:
            return _err(e)

    # ================= Google Drive =================
    def drive_status(self):
        st = gdrive.status()
        d = self.cfg.get("drive", {})
        st["ok"] = True
        st["folder"] = d.get("folder", "")
        st["auto"] = bool(d.get("auto", False))
        return st

    def drive_connect(self):
        return gdrive.connect()

    def drive_disconnect(self):
        return gdrive.disconnect()

    def set_drive_options(self, payload):
        try:
            d = self.cfg.setdefault("drive", {})
            if "folder" in payload:
                d["folder"] = str(payload.get("folder") or "").strip()
            if "auto" in payload:
                d["auto"] = bool(payload.get("auto"))
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def drive_upload(self, paths):
        folder = self.cfg.get("drive", {}).get("folder", "")
        return gdrive.upload_files(paths, folder)

    # ================= HWP/PDF 생성 =================
    def generate(self, payload, make_pdf=True):
        try:
            folder = self._doc_folder("quote")
            if not folder or not os.path.isdir(folder):
                return _err("먼저 작업 폴더를 선택하세요.")
            doc = payload.get("doc", {})
            if not doc.get("service_name", "").strip():
                return _err("용역명을 입력하세요.")
            labor, expenses, profit_on, trim = _parse_quote(payload)
            if not any(r.count > 0 for r in labor):
                return _err("최소 1개 직급에 인원을 입력하세요.")
            result = calculate(labor, expenses, profit_on, trim)
            plan = build_render_plan(doc, result, company=self.cfg.get("company"))

            paths = qs.quote_paths(folder, doc.get("service_name", ""),
                                   doc.get("date", ""))
            rep = self._get_worker().generate(
                {"fields": plan.fields, "labor_used": plan.labor_used,
                 "exp_used": plan.exp_used, "show_trim": plan.show_trim},
                paths["hwp"], paths["pdf"] if make_pdf else None)
            if not rep.get("ok"):
                _log(f"견적서 생성 실패: {rep.get('error', '생성 실패')}")
                return _err(rep.get("error", "생성 실패"))

            # 재편집용 JSON 자동 저장 (생성 시점에 캡처한 folder 사용 — 도중 폴더 변경 방지)
            self.save_quote(payload, folder=folder)
            # Drive 자동 업로드 (옵션 ON + 연결됨)
            drive = None
            try:
                if self.cfg.get("drive", {}).get("auto") and gdrive.status().get("connected"):
                    drive = self.drive_upload([p for p in (rep.get("hwp"), rep.get("pdf")) if p])
            except Exception as e:
                drive = {"ok": False, "error": str(e)}
            return {"ok": True, "hwp": rep.get("hwp"), "pdf": rep.get("pdf"),
                    "pdf_error": rep.get("pdf_error", ""),
                    "warnings": plan.warnings,
                    "final": fmt_won(result.final),
                    "drive": drive}
        except Exception as e:
            _log(f"견적서 생성 예외: {e}\n{traceback.format_exc()}")
            return _err(e, traceback=traceback.format_exc())

    # ================= AI 초안 =================
    def ai_draft(self, params):
        try:
            from src.convert import merge_attachments
            desc = (params.get("description") or "").strip()
            attachments = params.get("attachments") or []
            target = params.get("target")
            profit_on = bool(params.get("profit", True))
            if len(desc) < 10 and not attachments:
                return _err("용역 설명을 10자 이상 입력하거나 과업지시서 파일을 첨부하세요.")
            if not target:
                return _err("목표 금액을 입력하세요.")
            target = float(target)

            attach_warnings = []
            if attachments:
                if not desc:
                    desc = "아래 첨부 문서(과업지시서)를 분석하여 견적을 구성하세요."
                desc, attach_warnings = merge_attachments(desc, attachments)

            provider = cs.get_provider(self.cfg)
            key = cs.get_ai_key(self.cfg, provider)
            model = cs.get_ai_model(self.cfg, provider)
            year = self.cfg["default_price_year"]
            prices = self.cfg["unit_prices"].get(year, {})
            g = budget_guide(target, profit_on,
                             labor_ratio=cs.get_labor_ratio(self.cfg))

            res = ai_engine.draft_quote(
                provider, description=desc, target=int(target), profit_on=profit_on,
                expense_budget=int(max(0, g.expense_target)),
                price_table=prices, year=year, api_key=key, model=model,
                directive=cs.get_ai_prompt(self.cfg, "quote") or None)
            if not res.get("ok"):
                return res

            draft = res["draft"]
            # 초안 → 견적 payload
            by_grade = {p["grade"]: p for p in draft["personnel"]}
            labor_items = []
            for grade in ["책임연구원", "연구원", "연구보조원", "보조원"]:
                p = by_grade.get(grade)
                labor_items.append({
                    "grade": grade, "unit_price": prices.get(grade, 0),
                    "count": p["count"] if p else 0,
                    "rate": p["weight"] if p else 0,
                    "months": p["months"] if p else 0,
                })
            exp_items = [{
                "name": e["name"], "details": e["details"],
                "qty_text": e["qty_text"], "unit_price": e["unit_price"],
                "qty": e["qty"],
            } for e in draft["expenses"]]

            quote = {
                "doc": {"recipient": draft.get("recipient", ""),
                        "quote_no": "", "ref_name": "", "ref_tel": "",
                        "date": _date.today().isoformat(),
                        "service_name": draft.get("service_name", ""),
                        "service_period": draft["period_text"] or "계약일로부터 3주일"},
                "options": {"profit": profit_on, "price_year": year},
                "labor": labor_items, "expenses": exp_items,
                "goal": {"target": int(target), "mode": "ratio"},
                "trim": 0,
            }
            # 목표금액 정합 (비율유지 모드 — 만원미만 자동 절삭)
            labor, expenses, _, _ = _parse_quote(quote)
            gs_res = goal_seek(target, labor, expenses, profit_on,
                               mode="ratio")
            warnings = attach_warnings + list(gs_res.warnings)
            if gs_res.ok:
                for item, rate in zip(quote["labor"], gs_res.rates):
                    item["rate"] = rate
                quote["trim"] = gs_res.trim
            else:
                warnings.append(f"참여율 자동 역산 실패: {gs_res.error}")
            return {"ok": True, "quote": quote,
                    "rationale": draft.get("rationale", ""),
                    "warnings": warnings}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    # ================= 템플릿 관리 =================
    def scan_template(self, hwp_path: str) -> dict:
        """HWP 템플릿을 COM으로 스캔 → AI 매핑 → .fieldmap.json 캐시.

        반환: {ok, is_standard, max_labor, max_exp, fields, unknown, missing,
               field_map, unmapped, fieldmap_path?, ai_used, ai_error?}
        """
        try:
            from src.ai.template_mapper import (map_unknown_fields, save_fieldmap,
                                                load_fieldmap)
            if not os.path.exists(hwp_path):
                return _err(f"파일을 찾을 수 없습니다: {hwp_path}")

            scan = self._get_worker().scan_fields(hwp_path)
            if not scan.get("ok"):
                return scan

            result = {
                "ok": True,
                "is_standard": scan["is_standard"],
                "max_labor": scan["max_labor"],
                "max_exp": scan["max_exp"],
                "fields": scan["fields"],
                "unknown": scan["unknown"],
                "missing": scan["missing"],
                "ai_used": False,
            }

            if scan["is_standard"]:
                # 표준 템플릿 → fieldmap 불필요, 캐시 저장
                fm_path = save_fieldmap(hwp_path, scan, {"field_map": {}, "unmapped": []})
                result["fieldmap_path"] = fm_path
                result["field_map"] = {}
                result["unmapped"] = []
                return result

            # 비표준 필드 있음 → 선택된 AI 프로바이더로 매핑 시도
            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            map_r = map_unknown_fields(scan["unknown"], provider, api_key,
                                       cs.get_ai_model(self.cfg, provider))
            result["ai_used"] = True
            if not map_r.get("ok"):
                result["ai_error"] = map_r.get("error", "")
            result["field_map"] = map_r.get("field_map", {})
            result["unmapped"] = map_r.get("unmapped", scan["unknown"])

            # AI 매핑 실패 시 기존 정상 캐시를 빈 매핑으로 덮어쓰지 않는다
            # (재스캔 중 일시 오류·쿼터 초과로 좋은 매핑이 유실되는 것 방지)
            if map_r.get("ok") or not load_fieldmap(hwp_path):
                result["fieldmap_path"] = save_fieldmap(hwp_path, scan, map_r)
            return result
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def get_active_template(self) -> dict:
        """현재 활성 템플릿 경로 + fieldmap 정보 반환."""
        from src.hwp.hwp_writer import TEMPLATE_DEFAULT
        from src.ai.template_mapper import load_fieldmap
        fm = load_fieldmap(TEMPLATE_DEFAULT)
        return {
            "ok": True,
            "template_path": TEMPLATE_DEFAULT,
            "template_name": os.path.basename(TEMPLATE_DEFAULT),
            "is_standard": fm.get("is_standard", True) if fm else True,
            "has_fieldmap": bool(fm),
            "max_labor": fm.get("max_labor", 4),
            "max_exp": fm.get("max_exp", 8),
        }

    def pick_template_file(self) -> dict:
        """파일 선택 대화상자로 HWP 파일 경로 반환 (스캔은 scan_template로 별도)."""
        try:
            import webview
            result = self._window.create_file_dialog(
                _file_dialog(webview, "OPEN"),
                allow_multiple=False,
                file_types=("HWP 파일 (*.hwp)",),
            )
            if result:
                return {"ok": True, "path": result[0]}
            return {"ok": False, "cancelled": True}
        except Exception as e:
            return _err(e)

    # ================= 문서 변환 (kordoc) =================
    def convert_status(self):
        """변환 엔진 상태 — 드롭존 활성/비활성 판단용.
        {ok, state, ready, node:{found,version,ok}, kordoc:{installed,version}}"""
        try:
            from src import convert
            return convert.status()
        except Exception as e:
            return _err(e)

    def _notify_convert_progress(self, info: dict):
        """설치/변환 진행 상황을 UI overlay로 통지 (실패해도 변환은 계속)."""
        try:
            import json as _json
            payload = _json.dumps(info, ensure_ascii=False)
            self._window.evaluate_js(
                f"window.__convertProgress && window.__convertProgress({payload})")
        except Exception:
            pass

    def convert_files(self, paths):
        """파일 경로 목록 → Markdown 일괄 변환 (kordoc 미설치 시 1회 자동 설치).

        반환 {ok: True, results: [...], installed_now: bool}
        — 개별 파일 실패는 results 안의 ok=False로 표현.
        전체 ok=False는 한 건도 진행 불가한 상황(Node 없음 등)에만."""
        try:
            from src import convert
            paths = [str(p) for p in (paths or []) if p]
            if not paths:
                return _err("변환할 파일이 없습니다.")

            installed_now = False
            # kordoc 필요 여부: 패스스루(txt/md) 외 형식이 하나라도 있으면 부트스트랩
            needs_kordoc = any(
                os.path.splitext(p)[1].lower() not in convert.PASSTHROUGH_EXTS
                for p in paths)
            if needs_kordoc:
                ens = convert.ensure_kordoc(progress_cb=self._notify_convert_progress)
                if not ens.get("ok"):
                    return _err(ens.get("error", "변환 도구 준비 실패"),
                                error_code=ens.get("error_code", ""))
                installed_now = bool(ens.get("installed_now"))

            results = convert.convert_many(
                paths, progress_cb=self._notify_convert_progress)
            self._notify_convert_progress({"phase": "done"})
            return {"ok": True, "results": results, "installed_now": installed_now}
        except Exception as e:
            _log(f"convert_files 예외: {e}\n{traceback.format_exc()}")
            return _err(e, traceback=traceback.format_exc())

    def pick_convert_files(self):
        """변환할 문서 파일 다중 선택 대화상자 (드래그앤드롭 폴백 겸 1급 경로)."""
        try:
            import webview
            result = self._window.create_file_dialog(
                _file_dialog(webview, "OPEN"),
                allow_multiple=True,
                file_types=(
                    "문서 파일 (*.hwp;*.hwpx;*.hml;*.pdf;*.docx;*.xlsx;*.xls;*.txt;*.md)",
                    "모든 파일 (*.*)",
                ),
            )
            if result:
                return {"ok": True, "paths": list(result)}
            return {"ok": False, "cancelled": True}
        except Exception as e:
            return _err(e)

    # ================= 진단 =================
    def diagnose(self):
        info = {"ok": True}
        try:
            import winreg
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Classes\HWPFrame.HwpObject")
                info["hwp_com"] = True
            except OSError:
                info["hwp_com"] = False
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                   r"Software\HNC\HwpAutomation\Modules")
                info["security_module"] = bool(winreg.QueryValueEx(
                    k, "FilePathCheckerModule")[0])
            except OSError:
                info["security_module"] = False
        except Exception:
            info["hwp_com"] = None
        from src.hwp.hwp_writer import TEMPLATE_DEFAULT
        info["template"] = os.path.exists(TEMPLATE_DEFAULT)
        _prov = cs.get_provider(self.cfg)
        info["ai_provider"] = _prov
        info["ai_provider_label"] = ai_llm.PROVIDER_LABELS.get(_prov, _prov)
        info["ai_key"] = bool(cs.get_ai_key(self.cfg, _prov))
        info["folder"] = self._doc_folder("quote")
        info["folder_ok"] = os.path.isdir(info["folder"]) if info["folder"] else False
        try:
            info["drive_connected"] = gdrive.status().get("connected", False)
        except Exception:
            info["drive_connected"] = False
        try:
            from src import convert
            ni = convert.node_info()
            info["node"] = ni["version"] if ni["found"] else False
            info["node_bundled"] = ni.get("bundled", False)
            ki = convert.kordoc_installed()
            info["kordoc"] = ki["version"] if ki["installed"] else False
        except Exception:
            info["node"] = None
            info["kordoc"] = None
        return info

    def diagnose_hwp_session(self):
        """실제 한글 구동 테스트 (느림 — 버튼으로만)."""
        try:
            return self._get_worker().diagnose()
        except Exception as e:
            return _err(e)

    # ================= 회의록 AI 초안 =================
    def minutes_draft(self, params):
        """회의 메모/첨부 → AI 회의록 초안.

        params: {description, attachments?, hints?:{date?, place?}}
        반환: {ok, draft: MINUTES_SCHEMA, warnings?}
        """
        try:
            from src.convert import merge_attachments
            desc = (params.get("description") or "").strip()
            attachments = params.get("attachments") or []
            hints = params.get("hints") or {}

            if len(desc) < 5 and not attachments:
                return _err("회의 메모를 5자 이상 입력하거나 녹음·메모 파일을 첨부하세요.")

            attach_warnings = []
            if attachments:
                if not desc:
                    desc = "아래 첨부 파일(회의 녹음/메모)을 분석해 회의록을 작성하세요."
                desc, attach_warnings = merge_attachments(desc, attachments)

            # 힌트(일시·장소)를 프롬프트 앞에 추가
            hint_lines = []
            if hints.get("date"):
                hint_lines.append(f"회의 일시: {hints['date']}")
            if hints.get("place"):
                hint_lines.append(f"회의 장소: {hints['place']}")
            if hint_lines:
                desc = "\n".join(hint_lines) + "\n\n" + desc

            provider = cs.get_provider(self.cfg)
            key = cs.get_ai_key(self.cfg, provider)
            model = cs.get_ai_model(self.cfg, provider)

            res = ai_engine.draft_minutes(
                provider, description=desc, api_key=key, model=model,
                directive=cs.get_ai_prompt(self.cfg, "minutes") or None)
            if not res.get("ok"):
                return res

            return {"ok": True, "draft": res["draft"], "warnings": attach_warnings}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def generate_minutes(self, payload):
        """검토 완료된 MINUTES_SCHEMA → HWPX 파일 생성.

        payload: {data: MINUTES_SCHEMA, out_folder?: str, out_path?: str}
        반환: {ok, path}
        """
        try:
            from src.minutes import build_minutes
            data = payload.get("data") or {}
            if not data.get("meeting_topic"):
                return _err("회의주제를 입력하세요.")

            out_path = payload.get("out_path")
            if not out_path:
                folder = payload.get("out_folder") or self._doc_folder("minutes")
                if folder and os.path.isdir(folder):
                    import re
                    topic = re.sub(r'[\\/:*?"<>|]', "_", data.get("meeting_topic", "회의록"))[:20]
                    date_raw = (data.get("meeting_date") or "")[:10]
                    date_tag = re.sub(r"\D", "", date_raw)[:8]
                    out_path = os.path.join(folder, f"회의록_{topic}_{date_tag}.hwpx")

            tpl = cs.get_minutes_tpl(self.cfg) or None
            # 커스텀 양식이면 AI 분석 cell_map 적용 (없으면 표준 좌표)
            cell_map = None
            custom_slots = None
            if tpl:
                from src.ai.minutes_template_mapper import load_minutes_fieldmap
                fm = load_minutes_fieldmap(tpl)
                if fm and not fm.get("is_standard"):
                    cell_map = fm.get("cell_map") or None
                # 커스텀 정적 슬롯(9-a ii)은 표준/커스텀 무관하게 적용
                custom_slots = fm.get("custom_slots") or None if fm else None
            res = build_minutes(data, template_hwpx=tpl, out_path=out_path or None,
                                cell_map=cell_map, custom_slots=custom_slots)
            if not res.get("ok"):
                return _err(res.get("error", "HWPX 생성 실패"))

            # 재편집용 사이드카 — 실패해도 생성 자체는 성공 처리(경고만)
            json_path, warn = "", ""
            try:
                from src.store import minutes_store as ms
                json_path = ms.save_minutes(res["path"], data)
            except Exception as e:
                warn = f"재편집 데이터 저장 실패: {e}"
                _log(warn)
            # Drive 자동 업로드 (옵션 ON + 연결됨) — 회의록은 HWPX 1개만
            drive = None
            try:
                if self.cfg.get("drive", {}).get("auto") and gdrive.status().get("connected"):
                    drive = self.drive_upload([res["path"]])
            except Exception as e:
                drive = {"ok": False, "error": str(e)}
            out = {"ok": True, "path": res["path"], "json_path": json_path,
                   "drive": drive}
            if warn:
                out["warning"] = warn
            return out
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def scan_minutes_folder(self, folder=None):
        """회의록 폴더 스캔 — .hwpx 전수 + 고아 .minutes.json 카드화."""
        try:
            from src.scan import hwpx_scan as hx
            folder = folder or self._doc_folder("minutes")
            if not folder or not os.path.isdir(folder):
                return {"ok": True, "folder": "", "minutes": [],
                        "stats": self._minutes_stats([])}
            metas = [m.to_dict() for m in hx.scan_folder(folder)]
            # 짝 없는 .minutes.json (hwpx 삭제·이동 후 남은 재편집본)도 카드로
            hwpx_jsons = {m["json_path"] for m in metas if m["json_path"]}
            for name in sorted(os.listdir(folder)):
                if not name.endswith(".minutes.json"):
                    continue
                jpath = os.path.join(folder, name)
                if jpath in hwpx_jsons:
                    continue
                try:
                    from src.store import minutes_store as ms
                    d = ms.load_minutes(jpath).get("data", {})
                    from src.scan.hwpx_scan import _date_to_iso
                    metas.append({
                        "path": jpath, "filename": name,
                        "business_name": d.get("business_name", ""),
                        "topic": d.get("meeting_topic", name),
                        "date": d.get("meeting_date", ""),
                        "date_iso": _date_to_iso(d.get("meeting_date", "")),
                        "place": d.get("meeting_place", ""),
                        "total_count": d.get("total_count"),
                        "source": "json", "editable": True, "json_path": jpath,
                        "mtime": os.path.getmtime(jpath), "error": "",
                    })
                except Exception:
                    pass
            metas.sort(key=lambda m: m.get("mtime", 0), reverse=True)
            return {"ok": True, "folder": folder, "minutes": metas,
                    "stats": self._minutes_stats(metas)}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def _minutes_stats(self, metas):
        today = _date.today()
        prefix = f"{today.year}-{today.month:02d}"
        return {
            "total": len(metas),
            "this_month": sum(1 for m in metas
                              if (m.get("date_iso") or "").startswith(prefix)),
            "editable": sum(1 for m in metas if m.get("editable")),
        }

    def load_minutes(self, path):
        """사이드카 → 재편집용 MINUTES_SCHEMA data 반환."""
        try:
            from src.store import minutes_store as ms
            store = ms.load_minutes(path)
            data = store.get("data")
            if not isinstance(data, dict):
                return _err("재편집 데이터 형식이 올바르지 않습니다.")
            return {"ok": True, "data": data}
        except FileNotFoundError:
            return _err(f"재편집 데이터가 없습니다: {path}")
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def delete_minutes(self, payload):
        """회의록 삭제. 기본은 재편집 데이터(.minutes.json)만 제거.
        also_files=True일 때만 실제 .hwpx 파일까지 삭제 (delete_quote 미러)."""
        try:
            path = (payload.get("path") or "")
            json_path = (payload.get("json_path") or "")
            also = bool(payload.get("also_files"))
            if path.endswith(".minutes.json"):
                json_path = json_path or path
            removed = []
            if json_path and os.path.exists(json_path):
                os.remove(json_path)
                removed.append(os.path.basename(json_path))
            if also:
                hwpx = ""
                if json_path.endswith(".minutes.json"):
                    hwpx = json_path[:-len(".minutes.json")] + ".hwpx"
                elif path.lower().endswith(".hwpx"):
                    hwpx = path
                if hwpx and os.path.exists(hwpx):
                    os.remove(hwpx)
                    removed.append(os.path.basename(hwpx))
            if not removed:
                return _err("삭제할 항목이 없습니다. (외부 HWPX는 '파일도 삭제'를 체크해야 제거됩니다)")
            return {"ok": True, "removed": removed}
        except Exception as e:
            return _err(e)

    def get_minutes_template(self):
        """현재 활성 회의록 양식 정보 (커스텀 우선, 없으면 내장) + AI 분석 상태."""
        try:
            from src.minutes.hwpx_minutes import TEMPLATE_MINUTES
            from src.ai.minutes_template_mapper import load_minutes_fieldmap
            custom = cs.get_minutes_tpl(self.cfg)
            if custom and os.path.isfile(custom):
                fm = load_minutes_fieldmap(custom)
                return {"ok": True, "name": os.path.basename(custom),
                        "path": custom, "exists": True, "is_custom": True,
                        "has_fieldmap": bool(fm),
                        "is_standard": fm.get("is_standard", False) if fm else False,
                        "mapped": len((fm.get("cell_map") or {})) if fm else 0,
                        "unmapped": fm.get("unmapped", []) if fm else []}
            return {"ok": True, "name": os.path.basename(TEMPLATE_MINUTES),
                    "path": TEMPLATE_MINUTES, "exists": os.path.isfile(TEMPLATE_MINUTES),
                    "is_custom": False, "has_fieldmap": False, "is_standard": True}
        except Exception as e:
            return _err(e)

    def scan_minutes_template(self, hwpx_path: str) -> dict:
        """회의록 HWPX 양식의 표 구조를 스캔 → AI로 슬롯별 셀좌표 매핑 → 캐시.

        반환: {ok, is_standard, cell_map, unmapped, slot_labels, ai_used, ai_error?, fieldmap_path}
        """
        try:
            from src.scan.hwpx_scan import scan_hwpx_grid
            from src.ai.minutes_template_mapper import (
                map_minutes_cells, save_minutes_fieldmap, load_minutes_fieldmap,
                is_standard_map, MINUTES_SLOTS)
            if not os.path.isfile(hwpx_path):
                return _err(f"파일을 찾을 수 없습니다: {hwpx_path}")

            grid = scan_hwpx_grid(hwpx_path)
            if not grid.get("ok"):
                return grid

            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            map_r = map_minutes_cells(grid["cells"], provider, api_key,
                                      cs.get_ai_model(self.cfg, provider))
            result = {
                "ok": True,
                "ai_used": True,
                "cell_map": map_r.get("cell_map", {}),
                "unmapped": map_r.get("unmapped", []),
                "slot_labels": MINUTES_SLOTS,
                "is_standard": is_standard_map(map_r.get("cell_map", {})),
                "grid": grid,  # 병합셀(colspan/rowspan) 포함 — UI 격자 재구성용
            }
            if not map_r.get("ok"):
                result["ai_error"] = map_r.get("error", "")

            # AI 실패 시 기존 정상 캐시 보호 (scan_template와 동일 규칙)
            if map_r.get("ok") or not load_minutes_fieldmap(hwpx_path):
                result["fieldmap_path"] = save_minutes_fieldmap(hwpx_path, map_r)
            return result
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def scan_minutes_grid(self, template_path: str) -> dict:
        """양식 표 격자만 추출(AI 호출 없음) — 오프라인 전용 시각 격자 경로.

        AI 매핑(scan_minutes_template)과 분리해, AI 키 부재·지연·실패와 무관하게
        병합셀(colspan/rowspan) 포함 격자를 항상 반환(적대리뷰 #6).
        반환: {ok, row_cnt, col_cnt, cells:[{row,col,text,colspan,rowspan}], error?}
        """
        try:
            from src.scan.hwpx_scan import scan_hwpx_grid
            if not os.path.isfile(template_path):
                return _err(f"파일을 찾을 수 없습니다: {template_path}")
            return scan_hwpx_grid(template_path)
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def map_minutes_form(self, template_path: str) -> dict:
        """양식 AI 통합 분석 — 표준 7슬롯 + 커스텀 라벨 핀을 1회 호출로.

        scan_minutes_template(표준)·auto_label_minutes_form(커스텀)의 통합 대체.
        fieldmap 캐시 저장·AI 실패 시 기존 정상 캐시 보호 규칙은 종전과 동일.
        반환: {ok, ai_used, cell_map, unmapped, pins(+nx,ny), warnings,
               slot_labels, is_standard, grid, fieldmap_path?, ai_error?}
        """
        try:
            from src.scan.hwpx_scan import scan_hwpx_grid
            from src.ai.minutes_template_mapper import (
                map_minutes_form as _map_form, save_minutes_fieldmap,
                load_minutes_fieldmap, is_standard_map, MINUTES_SLOTS)
            if not os.path.isfile(template_path):
                return _err(f"파일을 찾을 수 없습니다: {template_path}")

            grid = scan_hwpx_grid(template_path)
            if not grid.get("ok"):
                return grid

            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            map_r = _map_form(grid["cells"], provider, api_key,
                              cs.get_ai_model(self.cfg, provider))

            by_cell = {(c.get("table", 0), c["row"], c["col"]): c
                       for c in grid["cells"]}
            pins = []
            for p in map_r.get("pins", []):
                c = by_cell.get((p["table"], p["row"], p["col"]))
                pin = dict(p)
                if c:                                  # 셀 중심 — 프론트 핀 배치용
                    pin["nx"] = c["nx"] + c["nw"] / 2
                    pin["ny"] = c["ny"] + c["nh"] / 2
                pins.append(pin)

            result = {
                "ok": True, "ai_used": True,
                "cell_map": map_r.get("cell_map", {}),
                "unmapped": map_r.get("unmapped", []),
                "pins": pins,
                "warnings": map_r.get("warnings", []),
                "slot_labels": MINUTES_SLOTS,
                "is_standard": is_standard_map(map_r.get("cell_map", {})),
                "grid": grid,
            }
            if not map_r.get("ok"):
                result["ai_error"] = map_r.get("error", "")
            if map_r.get("ok") or not load_minutes_fieldmap(template_path):
                result["fieldmap_path"] = save_minutes_fieldmap(template_path, map_r)
            return result
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def auto_label_minutes_form(self, template_path: str) -> dict:
        """양식의 라벨 칸을 AI로 식별 → 입력 칸에 항목명 핀 자동 생성.

        scan_hwpx_grid로 grid를 얻어 auto_label_cells(현재 provider/key/model) 호출,
        각 pin에 해당 셀 중심 nx,ny를 채워 프론트가 바로 핀 배치 가능하게 한다.
        반환: {ok, pins:[{table,row,col,label,nx,ny}], error?}. 키 없으면 ok:False+안내.
        """
        try:
            from src.scan.hwpx_scan import scan_hwpx_grid
            from src.ai.minutes_template_mapper import auto_label_cells
            if not os.path.isfile(template_path):
                return _err(f"파일을 찾을 수 없습니다: {template_path}")
            grid = scan_hwpx_grid(template_path)
            if not grid.get("ok"):
                return grid
            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            res = auto_label_cells(grid["cells"], provider, api_key,
                                   cs.get_ai_model(self.cfg, provider))
            if not res.get("ok"):
                return _err(res.get("error", "자동 인식 실패"), pins=[])
            by_cell = {(c.get("table", 0), c["row"], c["col"]): c
                       for c in grid["cells"]}
            pins = []
            for p in res["pins"]:
                c = by_cell.get((p["table"], p["row"], p["col"]))
                pin = dict(p)
                if c:                     # 셀 중심 — 프론트 핀 배치용
                    pin["nx"] = c["nx"] + c["nw"] / 2
                    pin["ny"] = c["ny"] + c["nh"] / 2
                pins.append(pin)
            return {"ok": True, "pins": pins}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def save_minutes_cellmap(self, template_path: str, cell_map: dict = None,
                             custom_slots=None, annotations=None) -> dict:
        """사용자 편집본(cell_map + custom_slots + annotations)을 fieldmap v2로 저장.

        반환: {ok, path, cell_map, custom_slots, annotations, unmapped,
               is_standard, warnings}
        """
        try:
            from src.ai.minutes_template_mapper import (
                save_minutes_cellmap as _save)
            if not os.path.isfile(template_path):
                return _err(f"파일을 찾을 수 없습니다: {template_path}")
            res = _save(template_path, cell_map or {}, custom_slots, annotations)
            res["ok"] = True
            return res
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def load_minutes_cellmap(self, template_path: str) -> dict:
        """디스크에 저장된 fieldmap을 편집기 초기값으로 로드(저장본 우선 복원).

        save_minutes_cellmap의 역연산 — 앱 재시작 후 매핑 편집기를 다시 열 때
        AI 재제안이 아니라 사용자가 저장한 cell_map이 그대로 복원되게 한다.
        저장본이 없으면 has_fieldmap=False + 빈 격자.

        반환: {ok, has_fieldmap, cell_map, custom_slots, annotations,
               unmapped, is_standard, version}
        """
        try:
            from src.ai.minutes_template_mapper import load_minutes_fieldmap
            fm = load_minutes_fieldmap(template_path) if template_path else {}
            if not fm:
                return {"ok": True, "has_fieldmap": False, "cell_map": {},
                        "custom_slots": [], "annotations": [], "unmapped": [],
                        "is_standard": False, "version": 0}
            return {
                "ok": True,
                "has_fieldmap": True,
                "cell_map": fm.get("cell_map") or {},
                "custom_slots": fm.get("custom_slots") or [],
                "annotations": fm.get("annotations") or [],
                "unmapped": fm.get("unmapped") or [],
                "is_standard": bool(fm.get("is_standard", False)),
                "version": fm.get("version", 1),
            }
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def pick_minutes_template_file(self) -> dict:
        """파일 선택 대화상자로 HWPX 파일 경로 반환."""
        try:
            import webview
            result = self._window.create_file_dialog(
                _file_dialog(webview, "OPEN"),
                allow_multiple=False,
                file_types=("HWPX 파일 (*.hwpx)", "모든 파일 (*.*)"),
            )
            if result:
                return {"ok": True, "path": result[0]}
            return {"ok": False, "cancelled": True}
        except Exception as e:
            return _err(e)

    def set_minutes_template(self, path: str) -> dict:
        """커스텀 회의록 양식 경로 저장. path='' 이면 기본 복원."""
        try:
            path = (path or "").strip()
            if path and not os.path.isfile(path):
                return _err("파일을 찾을 수 없습니다")
            cs.set_minutes_tpl(self.cfg, path)
            return {"ok": True, "path": path}
        except Exception as e:
            return _err(e)

    # ================= 회의록 양식 Preset (B-1/B-2/B-3) =================

    def list_minutes_presets(self) -> dict:
        """보관 중인 양식 preset 목록 + 활성 표시 + 매핑 상태 배지(갤러리용).

        활성 여부는 template_path(단일 활성 출처)에서 파생. fieldmap_path는 파생값.
        """
        try:
            from src.ai.minutes_template_mapper import (_fieldmap_path,
                                                        load_minutes_fieldmap)
            presets = cs.get_minutes_presets(self.cfg)
            active_tpl = cs.get_minutes_tpl(self.cfg)
            out = []
            for p in presets:
                tpl = p.get("template_path", "")
                if p.get("is_builtin"):
                    active = not active_tpl
                else:
                    active = bool(active_tpl) and active_tpl == tpl
                exists = bool(tpl) and os.path.isfile(tpl)
                fm = load_minutes_fieldmap(tpl) if exists else {}
                out.append({
                    **p,
                    "active": active,
                    "exists": exists,
                    "fieldmap_path": _fieldmap_path(tpl) if tpl else "",
                    "has_fieldmap": bool(fm),
                    "is_standard": fm.get("is_standard", p.get("is_builtin", False))
                                   if fm else p.get("is_builtin", False),
                    "mapped": len(fm.get("cell_map") or {}) if fm else 0,
                    "unmapped": fm.get("unmapped", []) if fm else [],
                })
            return {"ok": True, "presets": out,
                    "gallery_autoshow": cs.get_minutes_gallery_autoshow(self.cfg)}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def add_minutes_preset(self, path: str, name: str = None) -> dict:
        """양식 파일을 앱 폴더로 복사(9-c)하고 preset 등록."""
        try:
            path = (path or "").strip()
            if not path or not os.path.isfile(path):
                return _err("파일을 찾을 수 없습니다")
            stored = cs.copy_minutes_template(path)
            preset = cs.add_minutes_preset(self.cfg, stored, name)
            return {"ok": True, "preset": preset}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def select_minutes_preset(self, preset_id: str) -> dict:
        """활성 preset 지정 — template_path 동기화(단일 출처)."""
        try:
            preset = cs.select_minutes_preset(self.cfg, preset_id)
            return {"ok": True, "preset": preset,
                    "template_path": cs.get_minutes_tpl(self.cfg)}
        except Exception as e:
            return _err(e)

    def delete_minutes_preset(self, preset_id: str, also_files: bool = False) -> dict:
        """preset 등록 해제(내장 거부). also_files=True면 사본 파일·fieldmap까지 삭제
        (delete_minutes의 별도 동의 패턴 미러). 활성 삭제 시 template_path 내장 폴백."""
        try:
            target = next((p for p in cs.get_minutes_presets(self.cfg)
                           if p.get("id") == preset_id), None)
            removed = cs.delete_minutes_preset(self.cfg, preset_id)  # 내장·미존재 거부
            if also_files and target and not target.get("is_builtin"):
                from src.ai.minutes_template_mapper import _fieldmap_path
                tpl = target.get("template_path", "")
                for f in (tpl, _fieldmap_path(tpl) if tpl else ""):
                    if f and os.path.exists(f):
                        try:
                            os.remove(f)
                        except OSError:
                            pass
            return {"ok": True, "removed": removed}
        except Exception as e:
            return _err(e)

    def rename_minutes_preset(self, preset_id: str, name: str) -> dict:
        """preset 이름 변경(내장 거부)."""
        try:
            preset = cs.rename_minutes_preset(self.cfg, preset_id, name)
            return {"ok": True, "preset": preset}
        except Exception as e:
            return _err(e)

    def set_minutes_gallery_autoshow(self, on: bool) -> dict:
        """갤러리 자동 표시 토글 저장(9-d)."""
        try:
            cs.set_minutes_gallery_autoshow(self.cfg, bool(on))
            return {"ok": True, "gallery_autoshow": bool(on)}
        except Exception as e:
            return _err(e)

    # ================= 버전 / 자동 업데이트 =================

    def get_app_version(self) -> dict:
        from src.version import __version__, GITHUB_REPO
        return {"ok": True, "version": __version__, "repo": GITHUB_REPO}

    def check_update(self) -> dict:
        try:
            from src.update import updater
            return updater.check_latest()
        except Exception as e:
            return _err(e)

    def start_update(self, asset_url: str = "", asset_size: int = 0) -> dict:
        try:
            from src.update import updater
            from src.paths import is_frozen
            if not is_frozen():
                return {"ok": False, "error": "개발 모드에서는 업데이트 적용이 지원되지 않습니다."}
            # 안전 가드: 정말로 새 버전인지 재확인 (동일/구버전 업데이트 방지)
            chk = updater.check_latest()
            if not chk.get("ok"):
                return {"ok": False, "error": chk.get("error", "업데이트 확인 실패")}
            if not chk.get("has_update"):
                return {"ok": False, "error": "이미 최신 버전입니다."}
            url = chk.get("asset_url") or asset_url
            if not url:
                return {"ok": False, "error": "다운로드할 파일을 찾을 수 없습니다."}
            updater.start(url, int(chk.get("asset_size") or asset_size or 0))
            return {"ok": True}
        except Exception as e:
            return _err(e)

    def update_status(self) -> dict:
        try:
            from src.update import updater
            s = updater.status()
            return {"ok": True, **s}
        except Exception as e:
            return _err(e)

    def apply_update(self) -> dict:
        try:
            from src.update import updater
            import sys as _sys
            exe = _sys.executable
            install_dir = os.path.dirname(os.path.abspath(exe))
            app_exe_name = os.path.basename(exe)
            result = updater.apply(os.getpid(), install_dir, app_exe_name)
            if result.get("ok") and self._window:
                self._window.destroy()
            return result
        except Exception as e:
            return _err(e)
