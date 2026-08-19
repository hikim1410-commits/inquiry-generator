# -*- coding: utf-8 -*-
"""src/convert 단위 테스트 (subprocess 모킹) + kordoc 실변환 통합 테스트(-m node)."""
import json
import os
import subprocess

import pytest

from src.convert import kordoc
from src.convert.attach import (
    merge_attachments, MAX_ATTACH_TOTAL, MAX_TRANSCRIPT_TOTAL,
)


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_HWP = os.path.join(BASE, "내비온_견적서_저가의 고효율 라이다 센서 사업 타당성 분석 용역.hwp")
SAMPLE_XLSX = os.path.join(BASE, "용역비용 계산(이윤없는 버전).xlsx")


def _fake_proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _install_fake_kordoc(runtime_dir, version="4.7.1"):
    """런타임 폴더에 가짜 kordoc 설치 흔적 생성."""
    pkg = os.path.join(runtime_dir, "node_modules", "kordoc")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"version": version, "bin": {"kordoc": "./dist/cli.js"}}, f)


# ================= 환경 감지 =================

def test_node_missing(monkeypatch):
    monkeypatch.setattr(kordoc.shutil, "which", lambda name: None)
    monkeypatch.setattr(kordoc, "_bundled_node", lambda: "")
    ni = kordoc.node_info()
    assert ni == {"found": False, "path": "", "version": "", "major": 0, "ok": False,
                  "bundled": False}
    st = kordoc.status()
    assert st["state"] == kordoc.STATE_NODE_MISSING and not st["ready"]


def test_node_version_parse(monkeypatch):
    monkeypatch.setattr(kordoc.shutil, "which", lambda name: "C:/nodejs/node.exe")
    monkeypatch.setattr(kordoc, "_run",
                        lambda cmd, timeout, cwd=None: _fake_proc(stdout="v24.14.1\n"))
    ni = kordoc.node_info()
    assert ni["major"] == 24 and ni["ok"]


def test_node_too_old(monkeypatch, tmp_path):
    monkeypatch.setattr(kordoc.shutil, "which", lambda name: "C:/nodejs/node.exe")
    monkeypatch.setattr(kordoc, "_run",
                        lambda cmd, timeout, cwd=None: _fake_proc(stdout="v16.20.0\n"))
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    assert kordoc.status()["state"] == kordoc.STATE_NODE_TOO_OLD


def test_kordoc_installed_detect(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    assert not kordoc.kordoc_installed()["installed"]
    _install_fake_kordoc(str(tmp_path))
    ki = kordoc.kordoc_installed()
    assert ki["installed"] and ki["version"] == "4.7.1" and not ki["outdated"]


def test_kordoc_outdated_detect(tmp_path, monkeypatch):
    """최소 버전 미만(구버전 v3)은 outdated — 설치는 유효로 본다."""
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path), version="3.18.1")
    ki = kordoc.kordoc_installed()
    assert ki["installed"] and ki["outdated"]


# ================= 부트스트랩 =================

def test_ensure_kordoc_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})
    monkeypatch.setattr(kordoc, "npm_path", lambda: "npm.cmd")
    monkeypatch.setattr(kordoc, "_run",
                        lambda cmd, timeout, cwd=None: _fake_proc(
                            returncode=1, stderr="npm error code ENOTFOUND registry"))
    r = kordoc.ensure_kordoc()
    assert not r["ok"] and r["error_code"] == "install_offline"


def test_ensure_kordoc_already_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path))
    called = []
    monkeypatch.setattr(kordoc, "_run",
                        lambda *a, **kw: called.append(1) or _fake_proc())
    r = kordoc.ensure_kordoc()
    assert r["ok"] and not r["installed_now"] and not called


def test_ensure_kordoc_node_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": False, "path": "", "version": "", "major": 0,
                                 "ok": False})
    r = kordoc.ensure_kordoc()
    assert not r["ok"] and r["error_code"] == kordoc.STATE_NODE_MISSING


def test_ensure_kordoc_upgrade_fails_keeps_old(tmp_path, monkeypatch):
    """구버전 설치본이 있는데 갱신이 실패하면(오프라인) 기존 버전으로 계속 쓴다."""
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path), version="3.18.1")
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})
    monkeypatch.setattr(kordoc, "_npm_install",
                        lambda spec: {"ok": False, "error": "네트워크 오류",
                                      "error_code": "install_offline"})
    r = kordoc.ensure_kordoc()
    assert r["ok"] and r["version"] == "3.18.1" and r["upgrade_failed"]


# ================= 사용자 엔진 업데이트 =================

def _ready_node(monkeypatch):
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})


def test_latest_version_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(kordoc, "_npm_base_cmd", lambda: ["npm.cmd"])
    monkeypatch.setattr(kordoc, "_run",
                        lambda cmd, timeout, cwd=None: _fake_proc(stdout="4.7.1\n"))
    assert kordoc.latest_version() == {"ok": True, "version": "4.7.1"}


def test_update_kordoc_noop_when_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path), version="4.7.1")
    _ready_node(monkeypatch)
    monkeypatch.setattr(kordoc, "latest_version",
                        lambda: {"ok": True, "version": "4.7.1"})
    monkeypatch.setattr(kordoc, "_npm_install",
                        lambda spec: pytest.fail("최신인데 재설치를 시도했다"))
    r = kordoc.update_kordoc()
    assert r["ok"] and r["updated"] is False and r["version"] == "4.7.1"


def test_update_kordoc_rollback_on_smoke_failure(tmp_path, monkeypatch):
    """새 버전이 변환에 실패하면 직전 버전으로 되돌린다 (엔진 벽돌 방지)."""
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path), version="4.7.1")
    _ready_node(monkeypatch)
    monkeypatch.setattr(kordoc, "latest_version",
                        lambda: {"ok": True, "version": "5.0.0"})
    installs = []

    def fake_install(spec):
        installs.append(spec)
        _install_fake_kordoc(str(tmp_path),
                             version="5.0.0" if spec.endswith("latest") else spec.split("@")[-1])
        return {"ok": True}
    monkeypatch.setattr(kordoc, "_npm_install", fake_install)
    monkeypatch.setattr(kordoc, "_smoke_test",
                        lambda: {"ok": False, "error": "변환에 실패했습니다"})
    r = kordoc.update_kordoc()
    assert not r["ok"] and r["version"] == "4.7.1"
    assert installs == [kordoc.KORDOC_LATEST_SPEC, "kordoc@4.7.1"]
    assert kordoc.kordoc_installed()["version"] == "4.7.1"


def test_update_kordoc_success(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path), version="4.5.0")
    _ready_node(monkeypatch)
    monkeypatch.setattr(kordoc, "latest_version",
                        lambda: {"ok": True, "version": "4.7.1"})
    monkeypatch.setattr(kordoc, "_npm_install",
                        lambda spec: (_install_fake_kordoc(str(tmp_path), "4.7.1"),
                                      {"ok": True})[1])
    monkeypatch.setattr(kordoc, "_smoke_test", lambda: {"ok": True})
    r = kordoc.update_kordoc()
    assert r["ok"] and r["updated"] and r["version"] == "4.7.1" and r["previous"] == "4.5.0"


# ================= 변환 =================

def test_convert_unsupported_ext(tmp_path):
    p = tmp_path / "데이터.zip"
    p.write_bytes(b"PK")
    r = kordoc.convert_file(str(p))
    assert not r["ok"] and r["error_code"] == "unsupported"


def test_convert_file_missing():
    r = kordoc.convert_file(str(os.path.join("없는폴더", "없는파일.hwp")))
    assert not r["ok"] and r["error_code"] == "file_missing"


def test_convert_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path))
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})

    def boom(cmd, timeout, cwd=None):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(kordoc, "_run", boom)
    p = tmp_path / "큰파일.hwp"
    p.write_bytes(b"\x00" * 10)
    r = kordoc.convert_file(str(p))
    assert not r["ok"] and r["error_code"] == "timeout"


def test_convert_success_reads_md(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path))
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})
    made_dirs = []

    def fake_run(cmd, timeout, cwd=None):
        # cmd = [node, cli.js, 입력, -o, out_md, --silent]
        out_md = cmd[cmd.index("-o") + 1]
        made_dirs.append(os.path.dirname(out_md))
        with open(out_md, "w", encoding="utf-8") as f:
            f.write("# 과업지시서\n\n![image](img_001.jpg)\n\n\n\n내용입니다.  \n")
        return _fake_proc()
    monkeypatch.setattr(kordoc, "_run", fake_run)

    p = tmp_path / "과업 지시서.hwp"
    p.write_bytes(b"\x00")
    r = kordoc.convert_file(str(p))
    assert r["ok"]
    assert "# 과업지시서" in r["markdown"] and "내용입니다." in r["markdown"]
    assert "![image]" not in r["markdown"]          # 이미지 링크 제거
    assert "\n\n\n" not in r["markdown"]            # 빈 줄 압축
    assert r["chars"] == len(r["markdown"])
    assert not os.path.exists(made_dirs[0])          # 임시 폴더 정리됨


def test_convert_failed_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path))
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})
    monkeypatch.setattr(kordoc, "_run",
                        lambda cmd, timeout, cwd=None: _fake_proc(
                            returncode=1, stderr=" FAIL\n  → 지원하지 않는 파일 형식입니다.\n"))
    p = tmp_path / "깨진문서.hwpx"
    p.write_bytes(b"\x00")
    r = kordoc.convert_file(str(p))
    assert not r["ok"] and r["error_code"] == "convert_failed"
    assert "지원하지 않는 파일 형식" in r["error"]


def test_convert_empty_md(tmp_path, monkeypatch):
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    _install_fake_kordoc(str(tmp_path))
    monkeypatch.setattr(kordoc, "node_info",
                        lambda: {"found": True, "path": "node", "version": "v24.0.0",
                                 "major": 24, "ok": True})

    def fake_run(cmd, timeout, cwd=None):
        out_md = cmd[cmd.index("-o") + 1]
        with open(out_md, "w", encoding="utf-8") as f:
            f.write("![image](a.jpg)\n\n")          # 정리 후 빈 결과
        return _fake_proc()
    monkeypatch.setattr(kordoc, "_run", fake_run)
    p = tmp_path / "스캔본.pdf"
    p.write_bytes(b"%PDF")
    r = kordoc.convert_file(str(p))
    assert not r["ok"] and r["error_code"] == "empty_output"


def test_passthrough_txt_cp949(tmp_path):
    p = tmp_path / "녹음본.txt"
    p.write_bytes("회의 녹음 내용입니다. 참석자: 김형일".encode("cp949"))
    r = kordoc.convert_file(str(p))
    assert r["ok"] and "김형일" in r["markdown"]


def test_passthrough_md_utf8(tmp_path):
    p = tmp_path / "메모.md"
    p.write_text("# 회의 메모\n- 안건 1", encoding="utf-8")
    r = kordoc.convert_file(str(p))
    assert r["ok"] and "안건 1" in r["markdown"]


def test_convert_many_progress(tmp_path, monkeypatch):
    seen = []
    p1 = tmp_path / "a.txt"; p1.write_text("내용A", encoding="utf-8")
    p2 = tmp_path / "b.txt"; p2.write_text("내용B", encoding="utf-8")
    rs = kordoc.convert_many([str(p1), str(p2)], progress_cb=seen.append)
    assert [r["ok"] for r in rs] == [True, True]
    assert [(s["i"], s["total"]) for s in seen] == [(1, 2), (2, 2)]


def test_audio_exts_match_prd():
    assert kordoc.AUDIO_EXTS == {
        ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov",
    }


def test_audio_exts_route_to_stt(tmp_path):
    """음성·영상 9종 + 대문자 .M4A 는 변환하지 않고 STT 라우팅 신호를 낸다."""
    exts = [".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov",
            ".M4A"]
    for ext in exts:
        p = tmp_path / f"녹음{ext}"
        p.write_bytes(b"\x00")
        r = kordoc.convert_file(str(p))
        assert r["ok"] is False
        assert r["error_code"] == "needs_stt", ext
        assert "STT" in r["error"] or "전사" in r["error"]


def test_non_audio_ext_keeps_existing_route(tmp_path):
    """음성이 아닌 확장자는 종전 경로(unsupported / passthrough)를 유지한다."""
    z = tmp_path / "데이터.zip"
    z.write_bytes(b"PK")
    r = kordoc.convert_file(str(z))
    assert not r["ok"] and r["error_code"] == "unsupported"

    t = tmp_path / "메모.txt"
    t.write_text("본문", encoding="utf-8")
    r = kordoc.convert_file(str(t))
    assert r["ok"] and "본문" in r["markdown"]
    assert r.get("error_code") != "needs_stt"

    m = tmp_path / "메모.md"
    m.write_text("# 메모", encoding="utf-8")
    r = kordoc.convert_file(str(m))
    assert r["ok"] and r.get("error_code") != "needs_stt"


# ================= 첨부 병합 =================

def test_merge_attachments_basic():
    merged, warns = merge_attachments("용역 설명", [
        {"name": "과업.hwp", "markdown": "# 과업\n내용"},
        {"name": "예산.xlsx", "markdown": "|표|"},
    ])
    assert merged.startswith("용역 설명")
    assert "===== 첨부 문서 1: 과업.hwp =====" in merged
    assert "===== 첨부 문서 2: 예산.xlsx =====" in merged
    assert warns == []


def test_merge_attachments_truncate():
    long_md = "가" * 1000
    merged, warns = merge_attachments("설명", [{"name": "a.hwp", "markdown": long_md}],
                                      max_total=500)
    assert "[... 분량 초과로 이하" in merged
    assert len(warns) == 1 and "절단" in warns[0]


def test_merge_attachments_skip_rest():
    merged, warns = merge_attachments("설명", [
        {"name": "a.hwp", "markdown": "가" * 900},
        {"name": "b.hwp", "markdown": "나" * 900},
    ], max_total=1000)
    assert "첨부 문서 1" in merged and "첨부 문서 2" not in merged
    assert warns and "제외" in warns[0]


def test_merge_attachments_invalid_items():
    merged, warns = merge_attachments("설명만", [None, {"name": "x"}, {"markdown": "  "}])
    assert merged == "설명만" and warns == []


def test_merge_attachments_no_desc():
    merged, _ = merge_attachments("", [{"name": "a.txt", "markdown": "본문"}])
    assert merged.startswith("===== 첨부 문서 1")


def test_merge_attachments_no_kind_keeps_document_cap():
    """kind 없는 기존 호출은 문서 상한(3만 자)을 그대로 쓴다."""
    md = "가" * (MAX_ATTACH_TOTAL + 1)
    merged, warns = merge_attachments("설명", [{"name": "a.hwp", "markdown": md}])
    assert "[... 분량 초과로 이하" in merged
    assert len(warns) == 1 and "절단" in warns[0]
    assert f"{MAX_ATTACH_TOTAL:,}자" in warns[0]
    assert "후반부" not in warns[0]


def test_merge_attachments_transcript_fits_cap():
    """전사본 kind 는 120,000자까지 잘리지 않는다."""
    md = "가" * MAX_TRANSCRIPT_TOTAL
    merged, warns = merge_attachments("", [
        {"name": "회의.txt", "markdown": md, "kind": "transcript"},
    ])
    assert "[... 분량 초과로 이하" not in merged
    assert md in merged
    assert warns == []


def test_merge_attachments_transcript_overflow_warns_tail():
    """전사본이 상한을 넘으면 후반부 잘림을 알리는 경고가 나온다."""
    md = "가" * (MAX_TRANSCRIPT_TOTAL + 50)
    merged, warns = merge_attachments("", [
        {"name": "회의.txt", "markdown": md, "kind": "transcript"},
    ])
    assert "[... 분량 초과로 이하" in merged
    assert len(warns) == 1
    assert "후반부" in warns[0]
    assert f"{MAX_TRANSCRIPT_TOTAL:,}자" in warns[0]


# ================= 통합 (-m node: 실제 kordoc 설치+변환) =================

@pytest.mark.node
class TestRealKordoc:
    @pytest.fixture(autouse=True)
    def runtime(self, tmp_path_factory, monkeypatch):
        rt = str(tmp_path_factory.mktemp("kordoc-runtime"))
        monkeypatch.setattr(kordoc, "_runtime_dir", lambda: rt)
        r = kordoc.ensure_kordoc()
        assert r["ok"], f"kordoc 설치 실패: {r}"

    @pytest.mark.skipif(not os.path.exists(SAMPLE_HWP), reason="샘플 hwp 없음")
    def test_real_hwp(self):
        r = kordoc.convert_file(SAMPLE_HWP)
        assert r["ok"], r.get("error")
        assert "라이다" in r["markdown"]
        assert "22,000,000" in r["markdown"]

    @pytest.mark.skipif(not os.path.exists(SAMPLE_XLSX), reason="샘플 xlsx 없음")
    def test_real_xlsx(self):
        r = kordoc.convert_file(SAMPLE_XLSX)
        assert r["ok"], r.get("error")
        assert r["chars"] > 100


# ---- 스캔 PDF 비전 폴백 배선 ----

def test_vision_fallback_without_key_keeps_failure_with_guidance():
    """AI 키가 없으면 전사 시도 없이 안내 메시지로 교체된 실패 결과를 반환."""
    from src.api import Api
    api = Api.__new__(Api)   # __init__(설정 로드) 생략 — cfg만 주입
    api.cfg = {}
    res = {"ok": False, "path": "스캔.pdf", "name": "스캔.pdf", "markdown": "",
           "chars": 0, "error": "empty", "error_code": "empty_output"}
    out = api._vision_pdf_fallback(dict(res))
    assert out["ok"] is False
    assert "API 키" in out["error"]
