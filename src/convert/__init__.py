# -*- coding: utf-8 -*-
"""문서 → Markdown 변환 계층 (kordoc 연동 + 첨부 병합).

향후 다중 양식(회의록 등) 기능의 공용 '읽기 입구'. api.py만 이 모듈을 import한다.
"""
from src.convert.kordoc import (  # noqa: F401
    status, ensure_kordoc, convert_file, convert_many,
    node_info, kordoc_installed, latest_version, update_kordoc,
    SUPPORTED_EXTS, PASSTHROUGH_EXTS,
    STATE_READY, STATE_NODE_MISSING, STATE_NODE_TOO_OLD, STATE_KORDOC_MISSING,
)
from src.convert.attach import merge_attachments, MAX_ATTACH_TOTAL  # noqa: F401
