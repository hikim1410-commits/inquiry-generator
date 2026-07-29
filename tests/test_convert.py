# -*- coding: utf-8 -*-
"""src/convert 단위 테스트 (subprocess 모킹) + kordoc 실변환 통합 테스트(-m node)."""
import json
import os
import subprocess

import pytest

from src.convert import kordoc
from src.convert.attach import merge_attachments


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_HWP = os.path.join(BASE, "내비온_견적서_저가의 고효율 라이다 센서 사업 타당성 분석 용역.hwp")
SAMPLE_XLSX = os.path.join(BASE, "용역비용 계산(이윤없는 버전).xlsx")


def _fake_proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _install_fake_kordoc(runtime_dir, version="3.0.0"):
    """런타임 폴더에 가짜 kordoc + pdfjs-dist 설치 흔적 생성."""
    pkg = os.path.join(runtime_dir, "node_modules", "kordoc")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"version": version, "bin": {"kordoc": "./dist/cli.js"}}, f)
    os.makedirs(os.path.join(runtime_dir, "node_modules", "pdfjs-dist"), exist_ok=True)


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
    assert ki["installed"] and ki["version"] == "3.0.0"


def test_kordoc_missing_without_pdfjs(tmp_path, monkeypatch):
    """kordoc만 있고 pdfjs-dist가 없으면 미설치로 판정 (PDF 변환 불능 상태)."""
    monkeypatch.setattr(kordoc, "_runtime_dir", lambda: str(tmp_path))
    pkg = tmp_path / "node_modules" / "kordoc"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"version": "3.0.0"}', encoding="utf-8")
    assert not kordoc.kordoc_installed()["installed"]


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
