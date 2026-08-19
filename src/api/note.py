# -*- coding: utf-8 -*-
"""회의 노트 js_api — 요약 생성·조회·편집, .note.json 저장, 회의록 초안 연결.

`SttApi`와 같은 객체에 믹스인된다. 전사 세션(세그먼트·화자 매핑·메타)이 재료라
세션 접근은 전부 SttApi의 `_stt_copy_session` / `_stt_lock`을 경유한다 —
잠금이 하나여야 전사 갱신과 요약 저장이 어긋나지 않는다.

요약은 동기 호출이다(pywebview js_api는 호출마다 스레드를 쓰므로 UI가 멎지
않는다). 구간 분할 요약(§7.4)도 같은 호출 안에서 순차 수행된다.
"""
import os
import traceback
from datetime import datetime

from src.store import config_store as cs

from ._common import _err


class NoteApi:
    """회의 노트 — SttApi 세션 위의 경험 계층 (PRD_회의록노트 Phase 1)."""

    # ================= 내부 상태 =================

    def _note_state(self):
        self._stt_state()
        if not hasattr(self, "_note_summary"):
            self._note_summary = None          # 정규화된 NOTE_SUMMARY_SCHEMA dict
        if not hasattr(self, "_note_summary_meta"):
            self._note_summary_meta = {}       # provider/model/generated_at/edited/chunked
        return self

    def _note_notice_shown(self):
        return bool((self.cfg or {}).get("note", {}).get("notice_shown", False))

    def _note_summary_payload(self):
        """현재 세션 요약 응답 본문(잠금 안에서 복사)."""
        self._note_state()
        with self._stt_lock:
            summary = dict(self._note_summary) if self._note_summary else None
            meta = dict(self._note_summary_meta or {})
        return {
            "ok": True,
            "summary": summary,
            "summary_meta": meta,
            "notice_shown": self._note_notice_shown(),
            "error": "",
        }

    # ================= 요약 =================

    def summarize_note_session(self, payload=None):
        """현재 전사 세션을 요약한다. 반환: get_note_summary()와 동일 형."""
        try:
            self._note_state()
            segs, names, _files, _meta = self._stt_copy_session()
            if not segs:
                return _err("요약할 전사본이 없습니다. 먼저 음성 파일을 전사하세요.")
            with self._stt_lock:
                gen = getattr(self, "_stt_gen", 0)

            provider = cs.get_provider(self.cfg)
            api_key = cs.get_ai_key(self.cfg, provider)
            model = cs.get_ai_model(self.cfg, provider)

            from src.note import summarize as note_summarize
            from src.stt import serialize as stt_serialize
            mapped = stt_serialize.apply_speaker_names(segs, names)
            r = note_summarize.summarize(mapped, provider, api_key, model)
            if not r.get("ok"):
                out = _err(r.get("error") or "요약 생성에 실패했습니다.")
                if r.get("model_error"):
                    out["model_error"] = r["model_error"]
                return out

            with self._stt_lock:
                # LLM이 도는 수십 초 사이 새 전사가 시작됐으면 옛 회의 요약이다 — 폐기
                if getattr(self, "_stt_gen", 0) != gen:
                    return _err("전사 세션이 바뀌어 요약을 폐기했습니다. 다시 생성하세요.")
                self._note_summary = r["summary"]
                self._note_summary_meta = {
                    "provider": provider,
                    "model": model,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "edited_by_user": False,
                    "chunked": bool(r.get("chunked")),
                }
            return self._note_summary_payload()
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    def get_note_summary(self):
        """세션에 보관된 요약과 메타. 요약이 없으면 summary=None."""
        try:
            return self._note_summary_payload()
        except Exception as e:
            return _err(e)

    def update_note_summary(self, payload=None):
        """사용자가 고친 요약 전체를 반영한다(edited_by_user=True)."""
        try:
            self._note_state()
            payload = payload or {}
            summary = payload.get("summary")
            if not isinstance(summary, dict):
                return _err("요약 데이터가 올바르지 않습니다.")
            from src.ai.note import _normalize_note
            clean = _normalize_note(summary)
            with self._stt_lock:
                # 새 전사가 요약을 비웠으면 이 편집은 버려진 요약의 것이다 — 거부
                if self._note_summary is None:
                    return _err("전사 세션이 바뀌어 수정을 반영하지 않았습니다. 요약을 다시 생성하세요.")
                self._note_summary = clean
                meta = dict(self._note_summary_meta or {})
                meta["edited_by_user"] = True
                self._note_summary_meta = meta
            return self._note_summary_payload()
        except Exception as e:
            return _err(e)

    # ================= 회의록 초안 연결 (NR-08) =================

    def note_to_minutes(self):
        """요약이 있으면 요약 텍스트(§6.4), 없으면 전사본 전문으로 폴백."""
        try:
            self._note_state()
            with self._stt_lock:
                summary = dict(self._note_summary) if self._note_summary else None
            # 참석자·메모만으로도 text는 차므로, 폴백 판정은 요약 본문의 실질 여부로 한다
            substantive = summary and any(
                summary.get(k) for k in ("one_liner", "topics", "decisions",
                                         "action_items", "open_issues"))
            if not substantive:
                return self.transcript_to_minutes()

            segs, names, _files, meta = self._stt_copy_session()
            from src.note import serialize as note_serialize
            text = note_serialize.to_minutes_input(
                summary, self._note_speakers(segs, names),
                memo=str((meta or {}).get("memo") or ""))
            if not text:
                return self.transcript_to_minutes()
            return {"ok": True, "text": text, "description": text,
                    "chars": len(text), "from_summary": True, "error": ""}
        except Exception as e:
            return _err(e)

    # ================= .note.json 저장 =================

    def save_note(self, payload=None):
        """세션(제목·메모·키워드·화자·요약·북마크 없음)을 .note.json으로 저장."""
        try:
            self._note_state()
            payload = payload or {}
            segs, names, _files, meta = self._stt_copy_session()
            meta = meta or {}
            with self._stt_lock:
                summary = dict(self._note_summary) if self._note_summary else {}
                summary_meta = dict(self._note_summary_meta or {})

            from .stt import _sidecar_base
            base = _sidecar_base(payload, meta)
            if not base:
                return _err("저장할 폴더를 알 수 없습니다. 작업 폴더를 먼저 선택하세요.")

            audio_path, duration = self._stt_audio_source()
            kws = meta.get("keywords")
            note = {
                "title": str(meta.get("title") or ""),
                "created_at": str(meta.get("created_at") or ""),
                "duration_sec": float(meta.get("duration_sec") or duration or 0.0),
                "source": self._note_source(base, audio_path),
                "speakers": self._note_speakers(segs, names),
                "summary": summary,
                "summary_meta": summary_meta,
                "tags": [str(k) for k in kws] if isinstance(kws, list) else [],
                "links": self._note_links(base),
                "memo": str(meta.get("memo") or ""),
            }
            from src.note import store as note_store
            path = note_store.save_note(base, note)
            return {"ok": True, "path": path, "error": ""}
        except Exception as e:
            return _err(e, traceback=traceback.format_exc())

    # ================= 외부 전송 고지 (영수증 선례) =================

    def mark_note_notice_seen(self):
        try:
            self._note_state()
            self.cfg.setdefault("note", {})["notice_shown"] = True
            cs.save_config(self.cfg)
            return {"ok": True}
        except Exception as e:
            return _err(e)

    # ================= 내부 =================

    @staticmethod
    def _note_speakers(segs, names):
        """세그먼트의 화자 키 + 매핑 실명 → [{key,name,org}] (등장 순서)."""
        keys = []
        for seg in (segs or []):
            k = str((seg or {}).get("speaker") or "").strip()
            if k and k not in keys:
                keys.append(k)
        names = names or {}
        return [{"key": k, "name": str(names.get(k) or "").strip(), "org": ""}
                for k in keys]

    @staticmethod
    def _note_source(base, audio_path):
        if not audio_path:
            return {"kind": "text", "audio_path": "", "audio_kept": False}
        kept = os.path.exists(audio_path)
        rel = audio_path
        try:
            if os.path.dirname(os.path.abspath(audio_path)) == \
                    os.path.dirname(os.path.abspath(base)):
                rel = os.path.basename(audio_path)
        except Exception:
            pass
        return {"kind": "audio", "audio_path": rel, "audio_kept": kept}

    @staticmethod
    def _note_links(base):
        """같은 베이스명의 형제 파일 중 실제로 있는 것만 파일명으로 기록."""
        out = {"transcript": "", "minutes_json": "", "hwpx": ""}
        for key, tail in (("transcript", ".transcript.json"),
                          ("minutes_json", ".minutes.json"),
                          ("hwpx", ".hwpx")):
            p = base + tail
            if os.path.exists(p):
                out[key] = os.path.basename(p)
        return out
