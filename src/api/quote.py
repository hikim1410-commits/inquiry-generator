# -*- coding: utf-8 -*-
"""견적 도메인 — 계산/역산, 폴더 스캔, 저장/삭제, HWP 생성, AI 초안, 템플릿."""
import os
import traceback
from datetime import date as _date

from src.engine.calc import calculate, budget_guide, fmt_won, round_half_up
from src.engine.goalseek import goal_seek, goal_seek_labor
from src.hwp.field_map import build_render_plan
from src.scan.hwp_scan import scan_folder as _scan_folder
from src.store import config_store as cs
from src.store import quote_store as qs
from src.ai import engine as ai_engine
from src.drive import gdrive
from src.logutil import log as _log

from ._common import _err, _parse_quote, _display, _file_dialog


class QuoteApi:
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
