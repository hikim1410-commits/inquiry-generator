# -*- coding: utf-8 -*-
import copy
import os
import shutil
import zipfile
import xml.etree.ElementTree as ET

import pytest

_HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
_HS = '{http://www.hancom.co.kr/hwpml/2011/section}'


@pytest.fixture
def make_multi_table_tpl():
    """표준 회의록 양식에 표0의 완전한 사본을 표1로 추가한 hwpx 생성기.

    deepcopy 방식이라 header.xml의 스타일 ID 카탈로그를 건드리지 않고도
    100% 유효한 두 번째 표를 얻는다(합성 최소 hwpx의 참조 깨짐 위험 회피).
    표0을 감싸는 <hp:p>(표 문단)를 통째로 복제해 <hs:sec> 끝에 형제로 추가.
    """
    def _make(dst_dir: str) -> str:
        # hwpx_minutes를 먼저 import — 모듈이 수행하는 ET.register_namespace 부수효과를
        # 그대로 공유해 재직렬화 시 hp/hs 접두어가 보존되게 한다(생성 엔진과 동일 조건).
        from src.minutes.hwpx_minutes import TEMPLATE_MINUTES  # noqa: F401
        dst = os.path.join(dst_dir, "multi_table.hwpx")
        shutil.copy2(TEMPLATE_MINUTES, dst)

        tmp = os.path.join(dst_dir, "_mt_extract")
        with zipfile.ZipFile(dst) as zf:
            names = zf.namelist()
            zf.extractall(tmp)

        xml_path = os.path.join(tmp, "Contents", "section0.xml")
        ET.register_namespace('hp', _HP.strip('{}'))
        ET.register_namespace('hs', _HS.strip('{}'))
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # 표0을 담은 최상위 <hp:p> 탐색(직계 p 중 하위에 tbl을 가진 첫 번째)
        parent = {ch: pa for pa in root.iter() for ch in pa}
        first_tbl = root.find(f'.//{_HP}tbl')
        p = first_tbl
        while p is not None and not (p.tag == f'{_HP}p' and parent.get(p) is root):
            p = parent.get(p)
        assert p is not None, "표0 문단을 찾지 못함"
        root.append(copy.deepcopy(p))
        tree.write(xml_path, encoding="UTF-8", xml_declaration=True)

        # 재압축 — mimetype 첫 엔트리·STORED 유지
        with zipfile.ZipFile(dst, 'w') as zf:
            mt = os.path.join(tmp, "mimetype")
            zf.write(mt, "mimetype", compress_type=zipfile.ZIP_STORED)
            for name in names:
                if name == "mimetype":
                    continue
                fp = os.path.join(tmp, *name.split("/"))
                if os.path.isfile(fp):
                    zf.write(fp, name, compress_type=zipfile.ZIP_DEFLATED)
        return dst
    return _make
