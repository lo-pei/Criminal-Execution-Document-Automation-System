"""SQLite 台账。

不用独立数据库服务（非目标）。单机单用户，SQLite 零配置即可。
文件与台账的一致性靠「相对路径 + 内容哈希」维持：文件被人为移动时能检出，
而不是静默指向不存在的路径。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ..domain.models import Case, Defendant, FieldValue

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no         TEXT NOT NULL UNIQUE,
    court_name      TEXT,
    judgment_date   TEXT,
    effective_date  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS defendants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    id_number       TEXT,
    in_custody      INTEGER NOT NULL DEFAULT 0,
    sentence_start  TEXT,
    sentence_end    TEXT,
    UNIQUE (case_id, seq)
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    defendant_id    INTEGER NOT NULL REFERENCES defendants(id) ON DELETE CASCADE,
    doc_type        TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    rel_path        TEXT NOT NULL,
    sha256          TEXT,
    generated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (defendant_id, doc_type, version)
);

CREATE TABLE IF NOT EXISTS field_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    defendant_id    INTEGER NOT NULL REFERENCES defendants(id) ON DELETE CASCADE,
    field           TEXT NOT NULL,
    value           TEXT,
    status          TEXT,
    page            INTEGER,
    paragraph_index INTEGER,
    snippet         TEXT,
    extractor       TEXT,
    confidence      REAL
);

CREATE TABLE IF NOT EXISTS rule_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    field           TEXT NOT NULL,
    sample_id       TEXT,
    snippet         TEXT,
    proposed_pattern TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_defendants_name ON defendants(name);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_field_records_field ON field_records(field);
"""


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _plain(field_value: FieldValue) -> Optional[str]:
    value = field_value.value
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class Database:
    """台账数据库。使用 ``with Database(path) as db:`` 管理生命周期。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def init_schema(self) -> Database:
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        return self

    def add_case(self, case: Case) -> int:
        """插入或更新案件，返回案件 id。案号为唯一键。"""
        case_no = _plain(case.case_no)
        if not case_no:
            raise ValueError("案号为空，无法入库")

        self._conn.execute(
            """
            INSERT INTO cases (case_no, court_name, judgment_date, effective_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(case_no) DO UPDATE SET
                court_name = excluded.court_name,
                judgment_date = excluded.judgment_date,
                effective_date = excluded.effective_date
            """,
            (case_no, _plain(case.court_name), _plain(case.judgment_date), _plain(case.effective_date)),
        )
        self._conn.commit()

        row = self._conn.execute("SELECT id FROM cases WHERE case_no = ?", (case_no,)).fetchone()
        if row is None:
            raise RuntimeError("案件写入失败")
        return int(row["id"])

    def add_defendant(self, case_id: int, defendant: Defendant) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO defendants
                (case_id, seq, name, id_number, in_custody, sentence_start, sentence_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id, seq) DO UPDATE SET
                name = excluded.name,
                id_number = excluded.id_number,
                in_custody = excluded.in_custody,
                sentence_start = excluded.sentence_start,
                sentence_end = excluded.sentence_end
            """,
            (
                case_id,
                defendant.seq,
                defendant.display_name,
                _plain(defendant.id_number),
                int(defendant.in_custody),
                _iso(defendant.span.start),
                _iso(defendant.span.end),
            ),
        )
        self._conn.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = self._conn.execute(
            "SELECT id FROM defendants WHERE case_id = ? AND seq = ?", (case_id, defendant.seq)
        ).fetchone()
        return int(row["id"])

    def record_field(self, defendant_id: int, field: str, fv: FieldValue) -> None:
        provenance = fv.provenance
        self._conn.execute(
            """
            INSERT INTO field_records
                (defendant_id, field, value, status, page, paragraph_index, snippet, extractor, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                defendant_id,
                field,
                _plain(fv),
                fv.status.value,
                provenance.page if provenance else None,
                provenance.paragraph_index if provenance else None,
                provenance.snippet if provenance else None,
                fv.extractor,
                fv.confidence,
            ),
        )

    def record_document(
        self,
        defendant_id: int,
        doc_type: str,
        rel_path: str,
        *,
        version: int = 1,
        sha256: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO documents (defendant_id, doc_type, version, rel_path, sha256)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(defendant_id, doc_type, version) DO UPDATE SET
                rel_path = excluded.rel_path,
                sha256 = excluded.sha256
            """,
            (defendant_id, doc_type, version, rel_path, sha256),
        )
        self._conn.commit()

    def list_cases(self, keyword: Optional[str] = None) -> list[dict[str, Any]]:
        """台账列表。支持按案号 / 法院 / 被告人姓名模糊检索。"""
        sql = """
            SELECT c.id, c.case_no, c.court_name, c.judgment_date,
                   COUNT(d.id) AS defendant_count
            FROM cases c
            LEFT JOIN defendants d ON d.case_id = c.id
        """
        params: tuple[Any, ...] = ()
        if keyword:
            sql += """
            WHERE c.case_no LIKE ? OR c.court_name LIKE ?
               OR c.id IN (SELECT case_id FROM defendants WHERE name LIKE ?)
            """
            like = f"%{keyword}%"
            params = (like, like, like)
        sql += " GROUP BY c.id ORDER BY c.judgment_date DESC, c.id DESC"

        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def count_documents(self, case_id: int) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS n FROM documents doc
            JOIN defendants d ON d.id = doc.defendant_id
            WHERE d.case_id = ?
            """,
            (case_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def close(self) -> None:
        self._conn.close()


__all__ = ["SCHEMA", "Database"]
