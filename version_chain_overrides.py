"""
version_chain_overrides.py
Workspace Brain — 버전 체인 수동 오버라이드 관리 CLI

역할:
  - version_chain_overrides 테이블을 업데이트하여
    자동 체인 빌드 결과를 "강제 묶기/제외"로 보정합니다.

예:
  - 문서를 특정 수동 체인으로 고정(pin)
    D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\version_chain_overrides.py pin --path D:\\Workspace_Brain\\docs\\2026-03-03_Workspace_Brain_Master_Plan.md --key master_plan

  - 체인 빌드에서 제외(exclude)
    D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\version_chain_overrides.py exclude --path D:\\Workspace_Brain\\docs\\2026-03-03_Workspace_Brain_Master_Plan.md

  - 오버라이드 삭제(clear)
    D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\version_chain_overrides.py clear --path D:\\Workspace_Brain\\docs\\2026-03-03_Workspace_Brain_Master_Plan.md
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from src.db.init_db import init_db
from src.db.schema import normalize_path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "metadata.db"


@dataclass(frozen=True)
class _DocRef:
    doc_id: str
    abs_path: str
    rel_path: str
    project: str
    filename: str


def _resolve_doc(*, con: sqlite3.Connection, doc_id: str | None, path: str | None) -> _DocRef:
    if doc_id and str(doc_id).strip():
        row = con.execute(
            """
            SELECT doc_id, COALESCE(abs_path,''), COALESCE(rel_path,''), COALESCE(project,''), COALESCE(filename,'')
            FROM documents
            WHERE doc_id=?
            LIMIT 1
            """,
            (str(doc_id).strip(),),
        ).fetchone()
        if not row:
            raise SystemExit(f"문서를 찾을 수 없습니다(doc_id): {doc_id}")
        return _DocRef(doc_id=str(row[0]), abs_path=str(row[1]), rel_path=str(row[2]), project=str(row[3]), filename=str(row[4]))

    if path and str(path).strip():
        p = Path(str(path).strip())
        abs_norm = normalize_path(p)
        row = con.execute(
            """
            SELECT doc_id, COALESCE(abs_path,''), COALESCE(rel_path,''), COALESCE(project,''), COALESCE(filename,'')
            FROM documents
            WHERE abs_path=?
            LIMIT 1
            """,
            (abs_norm,),
        ).fetchone()
        if not row:
            raise SystemExit(f"문서를 찾을 수 없습니다(path): {abs_norm}")
        return _DocRef(doc_id=str(row[0]), abs_path=str(row[1]), rel_path=str(row[2]), project=str(row[3]), filename=str(row[4]))

    raise SystemExit("doc 선택이 필요합니다: --doc-id 또는 --path")


def _upsert_override(
    *,
    con: sqlite3.Connection,
    doc_id: str,
    manual_chain_key: str | None,
    exclude_from_chains: bool,
    note: str | None,
) -> None:
    now = time.time()
    con.execute(
        """
        INSERT INTO version_chain_overrides(doc_id, manual_chain_key, exclude_from_chains, note, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(doc_id) DO UPDATE SET
          manual_chain_key=excluded.manual_chain_key,
          exclude_from_chains=excluded.exclude_from_chains,
          note=excluded.note,
          updated_at=excluded.updated_at
        """,
        (
            str(doc_id),
            (str(manual_chain_key).strip() if manual_chain_key is not None and str(manual_chain_key).strip() else None),
            1 if bool(exclude_from_chains) else 0,
            (str(note).strip() if note is not None and str(note).strip() else None),
            float(now),
        ),
    )


def _cmd_list(*, con: sqlite3.Connection, project: str | None) -> int:
    sql = """
    SELECT
      d.project,
      d.rel_path,
      d.filename,
      o.doc_id,
      COALESCE(o.manual_chain_key,'') AS manual_chain_key,
      COALESCE(o.exclude_from_chains, 0) AS exclude_from_chains,
      COALESCE(o.note,'') AS note,
      COALESCE(o.updated_at, 0) AS updated_at
    FROM version_chain_overrides o
    JOIN documents d ON d.doc_id = o.doc_id
    WHERE d.status='active'
    """
    params: list[object] = []
    if project:
        sql += " AND d.project=?"
        params.append(str(project))
    sql += " ORDER BY d.project, d.rel_path"

    rows = con.execute(sql, params).fetchall()
    if not rows:
        print("(오버라이드 없음)")
        return 0

    for proj, rel_path, filename, doc_id, key, ex, note, updated_at in rows:
        ex_s = "EXCLUDE" if int(ex or 0) == 1 else "INCLUDE"
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(updated_at or 0))) if float(updated_at or 0) > 0 else "-"
        key_s = str(key or "").strip()
        note_s = str(note or "").strip()
        print(f"[{proj}] {rel_path}")
        print(f"  doc_id: {doc_id}")
        if key_s:
            print(f"  manual_chain_key: {key_s}")
        print(f"  status: {ex_s}")
        if note_s:
            print(f"  note: {note_s}")
        print(f"  updated_at: {ts}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace Brain version_chain_overrides 관리")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_list = sub.add_parser("list", help="오버라이드 목록 출력")
    sp_list.add_argument("--project", type=str, default="", help="프로젝트 필터(예: MRA)")

    def add_doc_ref_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--doc-id", type=str, default="", help="대상 doc_id")
        sp.add_argument("--path", type=str, default="", help="대상 파일 경로(abs). documents.abs_path 기준")

    sp_pin = sub.add_parser("pin", help="수동 체인 키로 고정(강제 묶기)")
    add_doc_ref_args(sp_pin)
    sp_pin.add_argument("--key", type=str, required=True, help="manual_chain_key")
    sp_pin.add_argument("--note", type=str, default="", help="메모")

    sp_ex = sub.add_parser("exclude", help="체인 빌드에서 제외")
    add_doc_ref_args(sp_ex)
    sp_ex.add_argument("--note", type=str, default="", help="메모")

    sp_in = sub.add_parser("include", help="제외 해제(체인 빌드에 포함)")
    add_doc_ref_args(sp_in)
    sp_in.add_argument("--note", type=str, default="", help="메모")

    sp_clear = sub.add_parser("clear", help="오버라이드 삭제")
    add_doc_ref_args(sp_clear)

    args = p.parse_args()
    db_path = Path(args.db)

    con = init_db(db_path=db_path)
    try:
        cmd = str(args.cmd)
        if cmd == "list":
            project = str(getattr(args, "project", "") or "").strip() or None
            return _cmd_list(con=con, project=project)

        doc = _resolve_doc(con=con, doc_id=str(getattr(args, "doc_id", "") or "").strip() or None, path=str(getattr(args, "path", "") or "").strip() or None)

        if cmd == "pin":
            _upsert_override(
                con=con,
                doc_id=doc.doc_id,
                manual_chain_key=str(getattr(args, "key")),
                exclude_from_chains=False,
                note=str(getattr(args, "note", "") or "").strip() or None,
            )
            con.commit()
            print(f"OK: pin doc_id={doc.doc_id} key={str(getattr(args, 'key'))}")
            return 0

        if cmd == "exclude":
            # exclude: exclude_from_chains만 1로 켜고, manual_chain_key는 유지(토글 UX)
            row = con.execute(
                "SELECT COALESCE(manual_chain_key,''), COALESCE(note,'') FROM version_chain_overrides WHERE doc_id=? LIMIT 1",
                (doc.doc_id,),
            ).fetchone()
            existing_key = str(row[0]) if row else ""
            existing_note = str(row[1]) if row else ""
            note = str(getattr(args, "note", "") or "").strip() or None
            _upsert_override(
                con=con,
                doc_id=doc.doc_id,
                manual_chain_key=existing_key or None,
                exclude_from_chains=True,
                note=note if note is not None else (existing_note.strip() or None),
            )
            con.commit()
            print(f"OK: exclude doc_id={doc.doc_id}")
            return 0

        if cmd == "include":
            # include: exclude_from_chains만 0으로 (manual_chain_key는 유지)
            row = con.execute(
                "SELECT COALESCE(manual_chain_key,''), COALESCE(note,'') FROM version_chain_overrides WHERE doc_id=? LIMIT 1",
                (doc.doc_id,),
            ).fetchone()
            existing_key = str(row[0]) if row else ""
            existing_note = str(row[1]) if row else ""
            note = str(getattr(args, "note", "") or "").strip() or None
            _upsert_override(
                con=con,
                doc_id=doc.doc_id,
                manual_chain_key=existing_key or None,
                exclude_from_chains=False,
                note=note if note is not None else (existing_note.strip() or None),
            )
            con.commit()
            print(f"OK: include doc_id={doc.doc_id}")
            return 0

        if cmd == "clear":
            con.execute("DELETE FROM version_chain_overrides WHERE doc_id=?", (doc.doc_id,))
            con.commit()
            print(f"OK: clear doc_id={doc.doc_id}")
            return 0

        raise SystemExit(f"알 수 없는 cmd: {cmd}")
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
