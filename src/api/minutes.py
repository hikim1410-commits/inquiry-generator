# -*- coding: utf-8 -*-
"""회의록 도메인 — AI 초안, HWPX 생성, 폴더 스캔, 양식 매핑, Preset 갤러리."""
import os
import traceback
from datetime import date as _date

from src.store import config_store as cs
from src.ai import engine as ai_engine
from src.drive import gdrive
from src.logutil import log as _log

from ._common import _err, _file_dialog


class MinutesApi:
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

    def scan_minutes_grid(self, template_path: str) -> dict:
        """양식 표 격자만 추출(AI 호출 없음) — 오프라인 전용 시각 격자 경로.

        AI 매핑(map_minutes_form)과 분리해, AI 키 부재·지연·실패와 무관하게
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

        레거시 개별 엔드포인트 2종(표준 매핑·커스텀 라벨링)의 통합 대체.
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

    def save_minutes_cellmap(self, template_path: str, cell_map: dict = None,
                             custom_slots=None, annotations=None) -> dict:
        """사용자 편집본(cell_map + custom_slots + annotations)을 fieldmap v3로 저장.

        좌표는 항상 [table,row,col] 3요소로 정규화 저장(2요소 입력은 표0 승격).
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
