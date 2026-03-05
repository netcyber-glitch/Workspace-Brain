"""
src/db/tags.py
Workspace Brain — 수동 태그(문서 라벨) 저장/조회 유틸
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_TAG_SPLIT_RE = re.compile(r"[,\s]+")
_TAG_NORM_RE = re.compile(r"[^0-9a-zA-Z가-힣_\-]+")


def normalize_tag(tag: str) -> str:
    t = (tag or "").strip().lower()
    t = _TAG_NORM_RE.sub("_", t)
    t = re.sub(r"[\s_]+", "_", t).strip("_")
    return t


def parse_tags(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for part in _TAG_SPLIT_RE.split(raw):
        t = normalize_tag(part)
        if not t:
            continue
        out.append(t)
    # 순서 보존 dedup
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq


@dataclass(frozen=True)
class TagOpResult:
    inserted: int = 0
    deleted: int = 0


def get_manual_tags_for_docs(*, db_path: Path, doc_ids: list[str]) -> dict[str, list[str]]:
    if not doc_ids:
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join(["?"] * len(doc_ids))
        rows = con.execute(
            f"SELECT doc_id, tag FROM doc_tags WHERE doc_id IN ({placeholders}) ORDER BY tag",
            doc_ids,
        ).fetchall()
        out: dict[str, list[str]] = {d: [] for d in doc_ids}
        for doc_id, tag in rows:
            did = str(doc_id)
            if did not in out:
                out[did] = []
            out[did].append(str(tag))
        return out
    finally:
        con.close()


def get_distinct_manual_tags(*, db_path: Path, project: str | None = None, limit: int = 5000) -> list[str]:
    con = sqlite3.connect(str(db_path))
    try:
        sql = """
        SELECT DISTINCT t.tag
        FROM doc_tags t
        JOIN documents d ON d.doc_id = t.doc_id
        WHERE d.status = 'active'
        """
        params: list[object] = []
        if project:
            sql += " AND d.project = ?"
            params.append(project)
        sql += " ORDER BY t.tag LIMIT ?"
        params.append(int(limit))
        rows = con.execute(sql, params).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]
    finally:
        con.close()


def add_manual_tags(*, db_path: Path, doc_ids: list[str], tags: list[str]) -> TagOpResult:
    doc_ids_norm = [str(d) for d in (doc_ids or []) if str(d).strip()]
    tags_norm = [normalize_tag(t) for t in (tags or []) if normalize_tag(t)]
    if not doc_ids_norm or not tags_norm:
        return TagOpResult(inserted=0, deleted=0)

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        before = con.total_changes
        rows = [(doc_id, tag) for doc_id in doc_ids_norm for tag in tags_norm]
        cur.executemany("INSERT OR IGNORE INTO doc_tags(doc_id, tag) VALUES (?, ?)", rows)
        con.commit()
        inserted = int(con.total_changes - before)
        return TagOpResult(inserted=inserted, deleted=0)
    finally:
        con.close()


def remove_manual_tags(*, db_path: Path, doc_ids: list[str], tags: list[str]) -> TagOpResult:
    doc_ids_norm = [str(d) for d in (doc_ids or []) if str(d).strip()]
    tags_norm = [normalize_tag(t) for t in (tags or []) if normalize_tag(t)]
    if not doc_ids_norm or not tags_norm:
        return TagOpResult(inserted=0, deleted=0)

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        before = con.total_changes
        rows = [(doc_id, tag) for doc_id in doc_ids_norm for tag in tags_norm]
        cur.executemany("DELETE FROM doc_tags WHERE doc_id = ? AND tag = ?", rows)
        con.commit()
        deleted = int(con.total_changes - before)
        return TagOpResult(inserted=0, deleted=deleted)
    finally:
        con.close()

