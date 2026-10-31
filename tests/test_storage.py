"""存储层：命名归档与台账。"""

from __future__ import annotations

from datetime import date

import pytest

from cedas.domain.models import Case, Defendant
from cedas.storage.archive import (
    build_filename,
    sanitize,
)
from cedas.storage.db import Database


def test_sanitize_removes_illegal_chars():
    assert sanitize("盗窃/抢夺") == "盗窃-抢夺"
    assert sanitize('案号:2023*') == "案号-2023"


def test_sanitize_handles_reserved_names():
    assert sanitize("CON") == "_CON"


def test_sanitize_falls_back_for_empty():
    assert sanitize("   ") == "未命名"


def test_build_filename():
    name = build_filename("（2023）京0105刑初123号", "张三", "执行通知书", date(2023, 5, 1))
    assert name == "（2023）京0105刑初123号_张三_执行通知书_20230501.docx"


def test_build_filename_with_version():
    name = build_filename("（2023）京0105刑初123号", "张三", "执行通知书", date(2023, 5, 1), version=2)
    # 版本号在末尾：夹在类型与日期中间会让同案不同版本排不到一起（见 naming 模块）。
    assert name == "（2023）京0105刑初123号_张三_执行通知书_20230501_v2.docx"


def test_database_roundtrip(tmp_path):
    db_path = tmp_path / "test.db"

    case = Case()
    case.case_no.set("（2023）京0105刑初123号")
    case.court_name.set("北京市朝阳区人民法院")
    case.judgment_date.set("2023-05-01")

    with Database(db_path) as db:
        db.init_schema()
        case_id = db.add_case(case)

        defendant = Defendant(seq=1)
        defendant.name.set("张三")
        defendant.id_number.set("110101198503021234")
        db.add_defendant(case_id, defendant)

        rows = db.list_cases()
        assert len(rows) == 1
        assert rows[0]["case_no"] == "（2023）京0105刑初123号"
        assert rows[0]["defendant_count"] == 1


def test_database_is_idempotent_on_same_case(tmp_path):
    db_path = tmp_path / "test.db"
    case = Case()
    case.case_no.set("（2023）京0105刑初123号")

    with Database(db_path) as db:
        db.init_schema()
        first = db.add_case(case)
        second = db.add_case(case)
        assert first == second
        assert len(db.list_cases()) == 1


def test_database_search_by_defendant_name(tmp_path):
    db_path = tmp_path / "test.db"

    with Database(db_path) as db:
        db.init_schema()
        case = Case()
        case.case_no.set("（2023）京0105刑初1号")
        case_id = db.add_case(case)
        defendant = Defendant(seq=1)
        defendant.name.set("张三")
        db.add_defendant(case_id, defendant)

        assert len(db.list_cases("张")) == 1
        assert db.list_cases("李") == []


def test_case_without_case_no_is_rejected(tmp_path):
    with Database(tmp_path / "t.db") as db:
        db.init_schema()
        with pytest.raises(ValueError):
            db.add_case(Case())
