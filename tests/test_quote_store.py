# -*- coding: utf-8 -*-
"""quote_store — 파일 명명 규칙·JSON 저장/로드 (G004 테스트 공백 보강)."""
import json
import os
from datetime import datetime

from src.store import quote_store as qs


# ---------- sanitize_filename ----------

def test_sanitize_removes_invalid_chars():
    assert qs.sanitize_filename('a\\b/c:d*e?f"g<h>i|j') == "a b c d e f g h i j"


def test_sanitize_collapses_whitespace_and_strips():
    assert qs.sanitize_filename("  용역   이름\t점검\n ") == "용역 이름 점검"


def test_sanitize_empty_falls_back_to_muje():
    assert qs.sanitize_filename("") == "무제"
    assert qs.sanitize_filename('///:::***') == "무제"
    assert qs.sanitize_filename(None) == "무제"


def test_sanitize_truncates_to_max_len():
    long = "가" * 100
    out = qs.sanitize_filename(long)
    assert len(out) == 60
    assert qs.sanitize_filename(long, max_len=10) == "가" * 10


# ---------- base_filename / quote_paths ----------

def test_base_filename_formats_date_yymmdd():
    assert qs.base_filename("라이다 분석", "2026-06-10") == "견적서_라이다 분석_260610"


def test_base_filename_bad_date_falls_back_to_today():
    out = qs.base_filename("용역", "not-a-date")
    today = datetime.now().strftime("%y%m%d")
    assert out == f"견적서_용역_{today}"


def test_quote_paths_extensions(tmp_path):
    p = qs.quote_paths(str(tmp_path), "용역", "2026-01-05")
    assert p["hwp"].endswith("견적서_용역_260105.hwpx")
    assert p["pdf"].endswith(".pdf")
    assert p["json"].endswith(".quote.json")
    assert os.path.dirname(p["hwp"]) == str(tmp_path)


# ---------- save_quote / load_quote ----------

def _quote(name="테스트 용역", date="2026-06-10"):
    return {"doc": {"service_name": name, "date": date},
            "labor": [], "expenses": []}


def test_save_load_roundtrip(tmp_path):
    path = qs.save_quote(str(tmp_path), _quote())
    assert os.path.basename(path) == "견적서_테스트 용역_260610.quote.json"
    loaded = qs.load_quote(path)
    assert loaded["doc"]["service_name"] == "테스트 용역"
    assert loaded["schema_version"] == qs.SCHEMA_VERSION
    assert loaded["meta"]["created"] and loaded["meta"]["modified"]


def test_save_preserves_created_updates_modified(tmp_path):
    q = _quote()
    path = qs.save_quote(str(tmp_path), q)
    first = qs.load_quote(path)
    again = dict(first)
    again["meta"] = dict(first["meta"], modified="1999-01-01T00:00:00")
    qs.save_quote(str(tmp_path), again)
    second = qs.load_quote(path)
    assert second["meta"]["created"] == first["meta"]["created"]
    assert second["meta"]["modified"] != "1999-01-01T00:00:00"


def test_save_does_not_mutate_caller_dict(tmp_path):
    q = _quote()
    qs.save_quote(str(tmp_path), q)
    assert "schema_version" not in q


def test_save_creates_missing_folder(tmp_path):
    folder = os.path.join(str(tmp_path), "새폴더")
    path = qs.save_quote(folder, _quote())
    assert os.path.isfile(path)


def test_saved_json_is_utf8_readable(tmp_path):
    path = qs.save_quote(str(tmp_path), _quote(name="한글 이름"))
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["doc"]["service_name"] == "한글 이름"
