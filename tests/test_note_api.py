# -*- coding: utf-8 -*-
"""노트 API 표면 — 요약 생성·편집·초안 연결·.note.json 저장 (네트워크 무의존).

Api()는 실제 config.json 을 읽으므로 __init__ 을 우회하고 cfg 를 주입한다
(test_stt_api 와 동일). LLM 은 src.note.summarize.summarize_note 를 monkeypatch.
"""
import json
import os

from src.api import Api
from src.ai.note import NO_EVIDENCE

_METHODS = (
    "summarize_note_session", "get_note_summary", "update_note_summary",
    "note_to_minutes", "save_note", "mark_note_notice_seen",
)

_SEGS = [
    {"start": 0.0, "end": 4.0, "text": "안건은 단가입니다.", "speaker": "화자1"},
    {"start": 5.0, "end": 9.0, "text": "18만원으로 합의합니다.", "speaker": "화자2"},
]

_SUMMARY = {
    "one_liner": "라이다 단가 합의",
    "topics": [{"title": "단가", "t_ms": 0, "points": ["18만원 접점"]}],
    "keywords": ["라이다"],
    "decisions": [{"text": "단가 18만원", "t_ms": 5000}],
    "action_items": [{"task": "계약 초안", "owner": "김형일", "due": "9/4",
                      "t_ms": 5000}],
    "open_issues": [],
}


def _api(segs=None, names=None, meta=None):
    api = Api.__new__(Api)
    api.cfg = {}
    api._window = None
    api._stt_state()
    api._stt_put_session(segments=list(segs or []), names=dict(names or {}),
                         meta=dict(meta or {}), files=[])
    return api


def _ok_summarize(monkeypatch, calls=None):
    from src.note import summarize as SU

    def fake(segments, provider, api_key, model, timeout=120,
             directive=None, chunk_chars=SU.CHUNK_CHARS, on_progress=None):
        if calls is not None:
            calls.append(segments)
        return {"ok": True, "summary": dict(_SUMMARY),
                "chunked": False, "chunks": 1}

    monkeypatch.setattr(SU, "summarize", fake)


class TestSurface:
    def test_methods_exposed_on_api(self):
        api = _api()
        for name in _METHODS:
            assert callable(getattr(api, name)), name


class TestSummarize:
    def test_no_segments(self):
        r = _api().summarize_note_session()
        assert r["ok"] is False and "전사본" in r["error"]

    def test_success_stores_summary_and_meta(self, monkeypatch):
        calls = []
        _ok_summarize(monkeypatch, calls)
        api = _api(_SEGS, names={"화자1": "내비온 김형일"})
        r = api.summarize_note_session()
        assert r["ok"] is True
        assert r["summary"]["one_liner"] == "라이다 단가 합의"
        assert r["summary_meta"]["edited_by_user"] is False
        assert r["summary_meta"]["generated_at"]
        # 화자 매핑이 적용된 세그먼트가 요약 입력이어야 한다
        assert calls[0][0]["speaker"] == "내비온 김형일"

    def test_failure_passes_error(self, monkeypatch):
        from src.note import summarize as SU
        monkeypatch.setattr(SU, "summarize",
                            lambda *a, **k: {"ok": False, "error": "키 없음",
                                             "chunked": False, "chunks": 1})
        r = _api(_SEGS).summarize_note_session()
        assert r["ok"] is False and r["error"] == "키 없음"

    def test_get_before_summarize_is_none(self):
        r = _api(_SEGS).get_note_summary()
        assert r["ok"] is True and r["summary"] is None


class TestUpdate:
    def test_update_marks_edited_and_normalizes(self, monkeypatch):
        _ok_summarize(monkeypatch)
        api = _api(_SEGS)
        api.summarize_note_session()
        edited = dict(_SUMMARY)
        edited["decisions"] = [{"text": "단가  18만원\n확정"}]   # t_ms 없음 + 개행
        r = api.update_note_summary({"summary": edited})
        assert r["ok"] is True
        assert r["summary_meta"]["edited_by_user"] is True
        assert r["summary"]["decisions"][0] == {
            "text": "단가 18만원 확정", "t_ms": NO_EVIDENCE}

    def test_update_rejects_non_dict(self):
        r = _api().update_note_summary({"summary": "문자열"})
        assert r["ok"] is False


class TestToMinutes:
    def test_without_summary_falls_back_to_transcript(self):
        api = _api(_SEGS)
        r = api.note_to_minutes()
        assert r["ok"] is True
        assert "[회의 전사본]" in r["text"]          # transcript_to_minutes 경로
        assert not r.get("from_summary")

    def test_with_summary_uses_summary_text(self, monkeypatch):
        _ok_summarize(monkeypatch)
        api = _api(_SEGS, names={"화자1": "내비온 김형일"},
                   meta={"memo": "개인 메모"})
        api.summarize_note_session()
        r = api.note_to_minutes()
        assert r["ok"] is True and r["from_summary"] is True
        assert "[회의 요약]" in r["text"]
        assert "라이다 단가 합의" in r["text"]
        assert "내비온 김형일" in r["text"]           # 화자 매핑 반영
        assert "[사용자 메모]\n개인 메모" in r["text"]

    def test_no_session_at_all(self):
        r = _api().note_to_minutes()
        assert r["ok"] is False


class TestSaveNote:
    def test_save_writes_sidecar(self, monkeypatch, tmp_path):
        _ok_summarize(monkeypatch)
        api = _api(_SEGS, names={"화자1": "내비온 김형일"},
                   meta={"title": "협의 3차", "keywords": ["라이다"],
                         "memo": "메모", "duration_sec": 9.0})
        api.summarize_note_session()
        r = api.save_note({"folder": str(tmp_path), "stem": "협의3차"})
        assert r["ok"] is True
        with open(r["path"], encoding="utf-8") as fp:
            saved = json.load(fp)
        assert saved["title"] == "협의 3차"
        assert saved["summary"]["one_liner"] == "라이다 단가 합의"
        assert saved["tags"] == ["라이다"]
        assert [s["key"] for s in saved["speakers"]] == ["화자1", "화자2"]
        assert saved["speakers"][0]["name"] == "내비온 김형일"
        assert saved["source"]["kind"] == "text"     # 세션에 오디오 없음

    def test_save_records_existing_sibling_links(self, monkeypatch, tmp_path):
        _ok_summarize(monkeypatch)
        api = _api(_SEGS)
        api.summarize_note_session()
        base = str(tmp_path / "회의")
        for tail in (".transcript.json", ".hwpx"):
            with open(base + tail, "w", encoding="utf-8") as fp:
                fp.write("{}")
        r = api.save_note({"folder": str(tmp_path), "stem": "회의"})
        with open(r["path"], encoding="utf-8") as fp:
            saved = json.load(fp)
        assert saved["links"]["transcript"] == "회의.transcript.json"
        assert saved["links"]["hwpx"] == "회의.hwpx"
        assert saved["links"]["minutes_json"] == ""

    def test_save_without_folder_fails(self):
        r = _api(_SEGS).save_note({})
        assert r["ok"] is False and "폴더" in r["error"]


class TestSessionReset:
    def test_new_transcription_discards_previous_summary(self, monkeypatch, tmp_path):
        # A회의 요약이 B회의 세션에 새어 들어가면 안 된다
        _ok_summarize(monkeypatch)
        api = _api(_SEGS)
        api.summarize_note_session()
        assert api.get_note_summary()["summary"] is not None
        api._stt_run = lambda paths: None            # 실제 엔진 실행은 생략
        wav = tmp_path / "b회의.wav"
        wav.write_bytes(b"x")
        r = api.start_transcribe({"paths": [str(wav)]})
        assert r["ok"] is True
        assert api.get_note_summary()["summary"] is None


class TestGenerationGuard:
    def test_inflight_summary_is_discarded_when_new_transcription_starts(
            self, monkeypatch, tmp_path):
        # LLM이 도는 사이 start_transcribe가 끼어들면 옛 회의 요약을 폐기해야 한다
        from src.note import summarize as SU
        api = _api(_SEGS)
        api._stt_run = lambda paths: None
        wav = tmp_path / "b회의.wav"
        wav.write_bytes(b"x")

        def slow_llm(*a, **k):
            # 요약 응답이 오기 전에 새 전사가 시작된 상황을 재현
            assert api.start_transcribe({"paths": [str(wav)]})["ok"] is True
            return {"ok": True, "summary": dict(_SUMMARY),
                    "chunked": False, "chunks": 1}

        monkeypatch.setattr(SU, "summarize", slow_llm)
        r = api.summarize_note_session()
        assert r["ok"] is False and "세션이 바뀌어" in r["error"]
        assert api.get_note_summary()["summary"] is None   # 새 세션은 오염되지 않음

    def test_update_after_session_reset_is_rejected(self, monkeypatch, tmp_path):
        _ok_summarize(monkeypatch)
        api = _api(_SEGS)
        api.summarize_note_session()
        api._stt_run = lambda paths: None
        wav = tmp_path / "b회의.wav"
        wav.write_bytes(b"x")
        api.start_transcribe({"paths": [str(wav)]})      # 요약이 비워진다
        r = api.update_note_summary({"summary": dict(_SUMMARY)})
        assert r["ok"] is False
        assert api.get_note_summary()["summary"] is None


class TestEmptySummaryFallback:
    def test_substance_free_summary_falls_back_to_transcript(self, monkeypatch):
        # 참석자·메모만으로 text가 차더라도 요약 본문이 비면 전사본 전문으로
        from src.note import summarize as SU
        monkeypatch.setattr(SU, "summarize",
                            lambda *a, **k: {"ok": True, "summary": {
                                "one_liner": "", "topics": [], "keywords": ["k"],
                                "decisions": [], "action_items": [],
                                "open_issues": []},
                                "chunked": False, "chunks": 1})
        api = _api(_SEGS, names={"화자1": "김형일"}, meta={"memo": "메모"})
        api.summarize_note_session()
        r = api.note_to_minutes()
        assert r["ok"] is True
        assert "[회의 전사본]" in r["text"]
        assert not r.get("from_summary")


class TestNotice:
    def test_notice_flag_roundtrip(self, monkeypatch):
        from src.store import config_store as cs
        monkeypatch.setattr(cs, "save_config", lambda cfg: None)
        api = _api(_SEGS)
        assert api.get_note_summary()["notice_shown"] is False
        assert api.mark_note_notice_seen()["ok"] is True
        assert api.get_note_summary()["notice_shown"] is True
