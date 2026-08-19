# -*- coding: utf-8 -*-
"""앱 버전 단일 출처(single source of truth).

설정 화면 표기 · EXE 버전 리소스(navion_quote.spec) · 자동 업데이트 비교가
모두 이 모듈을 참조한다 → 버전 드리프트 방지.

릴리스 시: __version__ 을 올리고, GitHub Release 태그를 'v{__version__}' 규약으로
만든다 (예: __version__ = "1.2.1" → 태그 v1.2.1). 절차는 docs/RELEASE.md 참조.
"""

__version__ = "1.8.0"

# 자동 업데이트가 조회할 GitHub 저장소 ("owner/repo"). 공개 저장소 전제.
GITHUB_REPO = "bbiyakfather/inquiry-generator"


def parse_ver(s):
    """'v1.2.0' / '1.2.0' / '1.2.0-beta' → (1, 2, 0) 튜플. 비교용.

    선행 v/V, '-' 이후 프리릴리스, '+' 이후 빌드메타는 무시.
    숫자 아닌 조각은 0으로 취급, 항상 길이 3 튜플 반환.
    """
    s = str(s or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in s.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest, current=None):
    """latest 가 current 보다 높은 버전이면 True."""
    if current is None:
        current = __version__
    return parse_ver(latest) > parse_ver(current)


def version_tuple4():
    """EXE 버전 리소스용 4요소 튜플 (major, minor, patch, 0)."""
    return parse_ver(__version__) + (0,)
