"""
src/search/fts_search.py
Workspace Brain — SQLite FTS5 키워드 검색
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FtsHit:
    doc_id: str
    score: float
    project: str
    title: str
    date_prefix: str
    rel_path: str
    abs_path: str


def search_fts(
    *,
    db_path: Path,
    query: str,
    project: str | None = None,
    limit: int = 10,
) -> list[FtsHit]:
    if not query or not str(query).strip():
        return []

    con = sqlite3.connect(str(db_path))
    try:
        sql = """
        SELECT
            d.doc_id,
            d.project,
            COALESCE(d.title, '') AS title,
            COALESCE(d.date_prefix, '') AS date_prefix,
            COALESCE(d.rel_path, '') AS rel_path,
            COALESCE(d.abs_path, '') AS abs_path,
            bm25(documents_fts) AS score
        FROM documents_fts
        JOIN documents d ON d.doc_id = documents_fts.doc_id
        WHERE documents_fts MATCH ?
          AND d.status = 'active'
        """
        params: list[object] = [query]

        if project:
            sql += " AND d.project = ?"
            params.append(project)

        sql += " ORDER BY score LIMIT ?"
        params.append(int(limit))

        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            raise ValueError(f"FTS 질의 오류: {e}") from e

        hits: list[FtsHit] = []
        for doc_id, proj, title, date_prefix, rel_path, abs_path, score in rows:
            hits.append(
                FtsHit(
                    doc_id=str(doc_id),
                    score=float(score) if score is not None else 0.0,
                    project=str(proj or ""),
                    title=str(title or ""),
                    date_prefix=str(date_prefix or ""),
                    rel_path=str(rel_path or ""),
                    abs_path=str(abs_path or ""),
                )
            )
        return hits
    finally:
        con.close()
