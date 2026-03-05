"""
src/indexer/fts_indexer.py
Workspace Brain — SQLite FTS5(키워드) 인덱서

- documents_fts 테이블을 재구축(rebuild)하거나
- 단일 문서의 FTS 엔트리를 upsert/delete 합니다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.db.schema import DDL_FTS5


@dataclass(frozen=True)
class FtsRebuildResult:
    indexed: int
    skipped: int
    missing_files: int


def _read_text_lossy(path: Path) -> str:
    """
    파일을 텍스트로 읽습니다.
    - 기본은 UTF-8
    - 실패해도 전체가 죽지 않도록 errors='replace'
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

def _is_contentless_delete_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "contentless" in msg and "fts5" in msg and "delete" in msg


def _delete_all_fts_contentless(con: sqlite3.Connection) -> None:
    """
    FTS5 contentless 테이블은 일반 DELETE가 막혀 있습니다.
    delete-all 커맨드로 비웁니다.
    """
    con.execute("INSERT INTO documents_fts(documents_fts) VALUES ('delete-all')")


def _delete_doc_id_contentless(con: sqlite3.Connection, *, doc_id: str) -> None:
    """
    contentless FTS5에서 doc_id에 해당하는 rowid를 찾아 delete 커맨드로 제거합니다.
    """
    rowids = con.execute(
        "SELECT rowid FROM documents_fts WHERE doc_id = ?",
        (doc_id,),
    ).fetchall()
    for (rowid,) in rowids:
        con.execute(
            "INSERT INTO documents_fts(documents_fts, rowid) VALUES ('delete', ?)",
            (rowid,),
        )


def ensure_joinable_fts_table(con: sqlite3.Connection, *, verbose: bool = True) -> None:
    """
    검색 결과를 documents 테이블과 join하기 위해서는 doc_id 값이 FTS에 저장되어야 합니다.

    과거 스키마(content='')로 생성된 contentless FTS는 doc_id가 NULL로 나오므로,
    발견 시 documents_fts만 드랍 후 "저장형" FTS로 재생성합니다.
    (documents 테이블 등 원본 메타데이터는 유지)
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='documents_fts' AND type='table' LIMIT 1"
    ).fetchone()
    if not row or not row[0]:
        con.executescript(DDL_FTS5)
        con.commit()
        return

    sql = str(row[0]).lower().replace(" ", "")
    if "content=''" in sql:
        if verbose:
            print("[FTS] contentless 스키마 감지 → documents_fts 재생성(저장형)")
        con.execute("DROP TABLE IF EXISTS documents_fts;")
        con.executescript(DDL_FTS5)
        con.commit()


def upsert_fts(con: sqlite3.Connection, *, doc_id: str, title: str, content: str) -> None:
    """
    doc_id 기준으로 FTS 엔트리를 1개로 유지합니다.
    (FTS5는 UNIQUE 제약이 없으므로, 선삭제 후 삽입 방식)
    """
    delete_fts(con, doc_id=doc_id)
    con.execute(
        "INSERT INTO documents_fts(doc_id, title, content) VALUES (?, ?, ?)",
        (doc_id, title or "", content or ""),
    )


def delete_fts(con: sqlite3.Connection, *, doc_id: str) -> None:
    try:
        con.execute("DELETE FROM documents_fts WHERE doc_id = ?", (doc_id,))
    except sqlite3.OperationalError as e:
        if _is_contentless_delete_error(e):
            _delete_doc_id_contentless(con, doc_id=doc_id)
            return
        raise


def rebuild_fts(
    *,
    db_path: Path,
    project: str | None = None,
    batch_size: int = 200,
    verbose: bool = True,
) -> FtsRebuildResult:
    """
    documents(메타)에서 active 문서 전체를 읽어 documents_fts를 재구축합니다.

    주의:
    - 문서 원본은 수정하지 않습니다(읽기만).
    - DB 내부 FTS 인덱스만 재생성합니다.
    """
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON;")

    ensure_joinable_fts_table(con, verbose=verbose)

    if verbose:
        print("\n[FTS 재구축] 시작")

    try:
        con.execute("DELETE FROM documents_fts;")
    except sqlite3.OperationalError as e:
        if _is_contentless_delete_error(e):
            _delete_all_fts_contentless(con)
        else:
            raise
    con.commit()

    if project:
        cur = con.execute(
            "SELECT doc_id, title, abs_path FROM documents WHERE status='active' AND project=? ORDER BY indexed_at",
            (project,),
        )
    else:
        cur = con.execute(
            "SELECT doc_id, title, abs_path FROM documents WHERE status='active' ORDER BY indexed_at"
        )

    indexed = 0
    skipped = 0
    missing = 0
    batch: list[tuple[str, str, str]] = []

    for doc_id, title, abs_path in cur.fetchall():
        p = Path(abs_path)
        if not p.exists():
            missing += 1
            continue
        content = _read_text_lossy(p)
        if not content and not title:
            skipped += 1
            continue

        batch.append((doc_id, title or "", content or ""))
        if len(batch) >= batch_size:
            con.executemany(
                "INSERT INTO documents_fts(doc_id, title, content) VALUES (?, ?, ?)",
                batch,
            )
            con.commit()
            indexed += len(batch)
            if verbose:
                print(f"  ... {indexed}건 인덱싱 중")
            batch.clear()

    if batch:
        con.executemany(
            "INSERT INTO documents_fts(doc_id, title, content) VALUES (?, ?, ?)",
            batch,
        )
        con.commit()
        indexed += len(batch)

    con.close()

    if verbose:
        print("[FTS 재구축] 완료")
        print(f"  indexed: {indexed}건")
        print(f"  skipped: {skipped}건")
        print(f"  missing_files: {missing}건")

    return FtsRebuildResult(indexed=indexed, skipped=skipped, missing_files=missing)
