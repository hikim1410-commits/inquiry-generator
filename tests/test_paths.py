# -*- coding: utf-8 -*-
"""paths — 개발/PyInstaller frozen 경로 분기 (G004 테스트 공백 보강)."""
import os
import sys

from src import paths


def test_dev_mode_roots_are_project_root():
    assert paths.is_frozen() is False
    root = paths.resource_root()
    assert root == paths.data_root()
    # <루트>/src/paths.py 기준 두 단계 상위 = 저장소 루트 (templates 존재로 검증)
    assert os.path.isdir(os.path.join(root, "templates"))


def test_path_joins(tmp_path):
    assert paths.resource_path("ui", "index.html") == \
        os.path.join(paths.resource_root(), "ui", "index.html")
    assert paths.data_path("config.json") == \
        os.path.join(paths.data_root(), "config.json")


def test_frozen_resource_uses_meipass(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\fake\_internal", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\fake\app.exe")
    assert paths.is_frozen() is True
    assert paths.resource_root() == r"C:\fake\_internal"
    assert paths.data_root() == r"C:\fake"


def test_frozen_without_meipass_falls_back_to_exe_dir(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\fake\app.exe")
    assert paths.resource_root() == r"C:\fake"
    assert paths.data_root() == r"C:\fake"
