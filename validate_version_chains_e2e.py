"""
validate_version_chains_e2e.py
Workspace Brain — version_chain_overrides + version_chains E2E 검증 스크립트

목표:
  - 오버라이드 저장(pin/exclude/include/clear) → 버전 체인 재구축(build_version_chains)
    → version_chains 반영까지 "한 번에" 자동 점검합니다.

안전 기본값:
  - 기본은 DB를 SQLite backup API로 임시 복제한 뒤, 복제본에서만 테스트합니다.
  - 실 DB를 직접 건드리려면 --in-place 를 명시해야 합니다.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "metadata.db"
DEFAULT_TMP_DIR = ROOT / "tmp"

# repo 루트 기준 import 보장
sys.path.insert(0, str(ROOT))

from build_version_chains import build_version_chains  # noqa: E402
from src.ui.backend import (  # noqa: E402
    clear_version_chain_override,
    exclude_from_version_chains,
    get_version_chain_override,
    include_in_version_chains,
    pin_version_chain_doc,
)


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except Exception:
        pass


def _sha256_null(*parts: str) -> str:
    combined = "\x00".join([str(p) for p in parts])
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _backup_sqlite_db(*, src_db: Path, dst_db: Path) -> None:
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(src_db))
    try:
        dst = sqlite3.connect(str(dst_db))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


@dataclass(frozen=True)
class _DocPick:
    project: str
    doc_a: str
    doc_b: str
    filename_a: str
    filename_b: str


def _pick_two_docs(*, con: sqlite3.Connection, project: str | None) -> _DocPick:
    proj = str(project or "").strip() or None
    if proj is None:
        row = con.execute(
            """
            SELECT project
            FROM documents
            WHERE status='active' AND COALESCE(project,'') <> ''
            GROUP BY project
            HAVING COUNT(*) >= 2
            ORDER BY COUNT(*) ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            raise RuntimeError("활성 문서가 2개 이상인 project를 찾지 못했습니다.")
        proj = str(row[0])

    rows = con.execute(
        """
        SELECT doc_id, COALESCE(filename,''), COALESCE(date_prefix,''), COALESCE(indexed_at, 0)
        FROM documents
        WHERE status='active' AND project=?
        ORDER BY COALESCE(date_prefix,'') DESC, COALESCE(indexed_at, 0) DESC
        LIMIT 2
        """,
        (proj,),
    ).fetchall()
    if len(rows) < 2:
        raise RuntimeError(f"project={proj} 에서 문서 2개를 찾지 못했습니다.")
    (a_id, a_fn, _, _), (b_id, b_fn, _, _) = rows[0], rows[1]
    return _DocPick(project=proj, doc_a=str(a_id), doc_b=str(b_id), filename_a=str(a_fn), filename_b=str(b_fn))


def _count_rows(*, con: sqlite3.Connection, sql: str, params: tuple) -> int:
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _rebuild_chains(*, db_path: Path, project: str, min_chain_size: int, verbose: bool) -> None:
    rc = int(
        build_version_chains(
            db_path=Path(db_path),
            project=str(project),
            min_chain_size=int(min_chain_size),
            dry_run=False,
            verbose=bool(verbose),
            max_day_gap=14,
            filename_sim_threshold=0.70,
            content_sim_threshold=0.75,
            no_content_sim=True,
            require_content_sim=False,
            max_embed_chars=12000,
            debug_edge_filter=False,
        )
        or 0
    )
    _assert(rc == 0, f"build_version_chains 실패(rc={rc})")


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace Brain version_chains E2E 검증(오버라이드 포함)")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    p.add_argument("--project", type=str, default="", help="프로젝트(비우면 자동 선택)")
    p.add_argument("--doc-a", type=str, default="", help="테스트 doc_id A(비우면 자동 선택)")
    p.add_argument("--doc-b", type=str, default="", help="테스트 doc_id B(비우면 자동 선택)")
    p.add_argument("--manual-key", type=str, default="e2e_chain_test", help="pin에 사용할 manual_chain_key")
    p.add_argument("--min-chain-size", type=int, default=2, help="버전 체인 최소 문서 수")
    p.add_argument("--in-place", action="store_true", help="실 DB에 직접 적용(주의)")
    p.add_argument("--keep-copy", action="store_true", help="임시 복제 DB를 삭제하지 않고 유지")
    p.add_argument("--verbose", action="store_true", help="상세 로그")
    args = p.parse_args()

    src_db = Path(args.db)
    if not src_db.exists():
        print(f"[실패] DB 파일이 없습니다: {src_db}")
        return 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    work_db = src_db if bool(args.in_place) else (DEFAULT_TMP_DIR / f"e2e_version_chains_{ts}.db")
    if work_db != src_db:
        _backup_sqlite_db(src_db=src_db, dst_db=work_db)

    con = sqlite3.connect(str(work_db))
    try:
        # 최소 스키마 확인
        tables = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        need = {"documents", "version_chains", "version_chain_overrides"}
        missing = sorted([t for t in need if t not in tables])
        if missing:
            print(f"[실패] 필수 테이블이 없습니다: {', '.join(missing)}")
            return 2

        proj = str(args.project or "").strip() or None
        a = str(args.doc_a or "").strip() or None
        b = str(args.doc_b or "").strip() or None

        if a and b:
            # 두 doc_id에서 project 확인(같은 프로젝트여야 함)
            row = con.execute(
                "SELECT COALESCE(project,'') FROM documents WHERE doc_id=? LIMIT 1",
                (a,),
            ).fetchone()
            if not row or not str(row[0]).strip():
                raise RuntimeError(f"doc_id A를 찾지 못했습니다: {a}")
            proj_a = str(row[0]).strip()
            row = con.execute(
                "SELECT COALESCE(project,''), COALESCE(filename,'') FROM documents WHERE doc_id=? LIMIT 1",
                (b,),
            ).fetchone()
            if not row or not str(row[0]).strip():
                raise RuntimeError(f"doc_id B를 찾지 못했습니다: {b}")
            proj_b = str(row[0]).strip()
            _assert(proj_a == proj_b, f"doc_a/doc_b 프로젝트가 다릅니다: {proj_a} vs {proj_b}")
            proj = proj_a
            row_a = con.execute(
                "SELECT COALESCE(filename,'') FROM documents WHERE doc_id=? LIMIT 1",
                (a,),
            ).fetchone()
            fn_a = str(row_a[0] if row_a else "")
            fn_b = str(row[1] or "")
            pick = _DocPick(project=proj, doc_a=a, doc_b=b, filename_a=fn_a, filename_b=fn_b)
        else:
            pick = _pick_two_docs(con=con, project=proj)

        project = pick.project
        doc_a = pick.doc_a
        doc_b = pick.doc_b
        key = str(args.manual_key or "").strip()
        _assert(bool(key), "manual-key가 비어 있습니다.")

        print("\n=== E2E 대상 ===")
        print(f"  src_db:  {src_db}")
        print(f"  work_db: {work_db}  {'(in-place)' if work_db == src_db else '(복제본)'}")
        print(f"  project: {project}")
        print(f"  doc_a:   {doc_a}  ({pick.filename_a})")
        print(f"  doc_b:   {doc_b}  ({pick.filename_b})")
        print(f"  key:     {key}")

        # 0) 초기 정리
        clear_version_chain_override(db_path=work_db, doc_id=doc_a)
        clear_version_chain_override(db_path=work_db, doc_id=doc_b)

        # 1) pin
        print("\n[1] pin 2건")
        pin_version_chain_doc(db_path=work_db, doc_id=doc_a, manual_chain_key=key)
        pin_version_chain_doc(db_path=work_db, doc_id=doc_b, manual_chain_key=key)

        ov_a = get_version_chain_override(db_path=work_db, doc_id=doc_a)
        ov_b = get_version_chain_override(db_path=work_db, doc_id=doc_b)
        _assert(ov_a is not None and ov_a.manual_chain_key == key and not ov_a.exclude_from_chains, "pin(A) 저장 검증 실패")
        _assert(ov_b is not None and ov_b.manual_chain_key == key and not ov_b.exclude_from_chains, "pin(B) 저장 검증 실패")
        print("  OK: version_chain_overrides(pin) 저장")

        # 2) 체인 재구축 후 수동 체인 반영 확인
        print("\n[2] build_version_chains (pin 반영)")
        _rebuild_chains(db_path=work_db, project=project, min_chain_size=int(args.min_chain_size), verbose=bool(args.verbose))

        manual_chain_id = _sha256_null("manual", str(project), str(key))
        rows = con.execute(
            "SELECT doc_id, version_order FROM version_chains WHERE chain_id=? ORDER BY version_order ASC",
            (manual_chain_id,),
        ).fetchall()
        doc_ids = {str(r[0]) for r in rows}
        orders = sorted([int(r[1] or 0) for r in rows])
        _assert(doc_ids == {doc_a, doc_b}, f"수동 체인 doc set 불일치: {doc_ids}")
        _assert(orders == [1, 2], f"수동 체인 version_order 불일치: {orders}")
        print("  OK: version_chains에 수동 체인 반영")

        # 3) exclude 토글: 키는 유지되어야 함
        print("\n[3] exclude → rebuild → include → rebuild")
        exclude_from_version_chains(db_path=work_db, doc_id=doc_b)
        ov_b2 = get_version_chain_override(db_path=work_db, doc_id=doc_b)
        _assert(ov_b2 is not None and ov_b2.manual_chain_key == key and ov_b2.exclude_from_chains, "exclude 저장/키 유지 검증 실패")

        _rebuild_chains(db_path=work_db, project=project, min_chain_size=int(args.min_chain_size), verbose=bool(args.verbose))
        rows2 = con.execute(
            "SELECT COUNT(*) FROM version_chains WHERE chain_id=?",
            (manual_chain_id,),
        ).fetchone()
        _assert(int(rows2[0] or 0) == 0, "exclude 후에도 수동 체인이 남아 있습니다(예상: 0)")
        cnt_b = _count_rows(con=con, sql="SELECT COUNT(*) FROM version_chains WHERE doc_id=?", params=(doc_b,))
        _assert(cnt_b == 0, f"exclude 된 doc_b가 version_chains에 남아 있습니다: {cnt_b}")
        print("  OK: exclude 적용(체인 제외)")

        include_in_version_chains(db_path=work_db, doc_id=doc_b)
        ov_b3 = get_version_chain_override(db_path=work_db, doc_id=doc_b)
        _assert(ov_b3 is not None and ov_b3.manual_chain_key == key and not ov_b3.exclude_from_chains, "include 저장/키 유지 검증 실패")

        _rebuild_chains(db_path=work_db, project=project, min_chain_size=int(args.min_chain_size), verbose=bool(args.verbose))
        rows3 = con.execute(
            "SELECT doc_id, version_order FROM version_chains WHERE chain_id=? ORDER BY version_order ASC",
            (manual_chain_id,),
        ).fetchall()
        doc_ids3 = {str(r[0]) for r in rows3}
        orders3 = sorted([int(r[1] or 0) for r in rows3])
        _assert(doc_ids3 == {doc_a, doc_b}, f"include 후 수동 체인 doc set 불일치: {doc_ids3}")
        _assert(orders3 == [1, 2], f"include 후 수동 체인 version_order 불일치: {orders3}")
        print("  OK: include 적용(체인 복구)")

        # 4) clear
        print("\n[4] clear → rebuild (수동 체인 제거)")
        clear_version_chain_override(db_path=work_db, doc_id=doc_a)
        clear_version_chain_override(db_path=work_db, doc_id=doc_b)
        _assert(get_version_chain_override(db_path=work_db, doc_id=doc_a) is None, "clear(A) 검증 실패")
        _assert(get_version_chain_override(db_path=work_db, doc_id=doc_b) is None, "clear(B) 검증 실패")

        _rebuild_chains(db_path=work_db, project=project, min_chain_size=int(args.min_chain_size), verbose=bool(args.verbose))
        cnt_manual = _count_rows(con=con, sql="SELECT COUNT(*) FROM version_chains WHERE chain_id=?", params=(manual_chain_id,))
        _assert(cnt_manual == 0, "clear 후에도 수동 체인(chain_id=manual...)이 남아 있습니다")
        print("  OK: clear 적용(수동 체인 제거)")

        print("\n=== 결과: OK ===")
        if work_db != src_db and not bool(args.keep_copy):
            try:
                con.close()
            except Exception:
                pass
            try:
                work_db.unlink()
                print(f"(임시 DB 삭제) {work_db}")
            except Exception:
                pass
        else:
            if work_db != src_db:
                print(f"(임시 DB 유지) {work_db}")
        return 0
    except AssertionError as e:
        print(f"\n=== 결과: FAIL ===\n- {e}")
        if work_db != src_db:
            print(f"(참고: work_db={work_db})")
        return 3
    except Exception as e:
        print(f"\n=== 결과: ERROR ===\n- {type(e).__name__}: {e}")
        if work_db != src_db:
            print(f"(참고: work_db={work_db})")
        return 2
    finally:
        try:
            con.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
