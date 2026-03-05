"""
validate_index.py
Workspace Brain — 인덱스 정합성 점검(원본 ↔ SQLite ↔ Chroma)

목표(MVP):
- 문서 수/청크 수/Chroma count를 빠르게 확인
- 샘플 기반으로 SQLite chunks ↔ Chroma 벡터 누락을 탐지
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from src.indexer.vector_indexer import DEFAULT_COLLECTION_NAME
from src.utils.runtime import storage_root


STORE_ROOT = storage_root()
DEFAULT_DB_PATH = STORE_ROOT / "data" / "metadata.db"
DEFAULT_CHROMA_DIR = STORE_ROOT / "data" / "chroma_db"

if hasattr(sys.stdout, "reconfigure"):
    try:
        # Windows(cp949) 콘솔에서도 유니코드 출력으로 죽지 않도록 보호
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except Exception:
        pass


def _require_deps():
    try:
        import chromadb  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "필수 패키지가 없습니다. `.venv`에서 실행하거나, `pip install chromadb`를 먼저 실행하세요."
        ) from e


def _parse_exts(s: str) -> set[str]:
    out: set[str] = set()
    for part in (s or "").split(","):
        p = part.strip()
        if not p:
            continue
        out.add(p if p.startswith(".") else f".{p}")
    return {x.lower() for x in out}


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace Brain 인덱스 정합성 점검")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    p.add_argument("--chroma-dir", type=str, default=str(DEFAULT_CHROMA_DIR), help="ChromaDB 영속 디렉터리")
    p.add_argument("--collection", type=str, default=DEFAULT_COLLECTION_NAME, help="Chroma collection 이름")
    p.add_argument("--project", type=str, default="", help="프로젝트 필터(예: MRA)")
    p.add_argument("--exts", type=str, default=".md,.txt", help="벡터 인덱싱 대상 확장자(쉼표 구분)")
    p.add_argument("--sample", type=int, default=100, help="샘플 검사 개수")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB 파일이 없습니다: {db_path}")
        return 2

    project = str(args.project or "").strip() or None
    exts = _parse_exts(args.exts)
    sample_n = max(0, int(args.sample))

    con = sqlite3.connect(str(db_path))
    try:
        # 문서 통계
        base_sql = "FROM documents WHERE status='active'"
        params: list[object] = []
        if project:
            base_sql += " AND project = ?"
            params.append(project)

        total_docs = con.execute(f"SELECT COUNT(*) {base_sql}", params).fetchone()[0]

        elig_sql = base_sql
        elig_params = list(params)
        if exts:
            placeholders = ",".join(["?"] * len(exts))
            elig_sql += f" AND lower(ext) IN ({placeholders})"
            elig_params.extend(sorted(exts))

        eligible_docs = con.execute(f"SELECT COUNT(*) {elig_sql}", elig_params).fetchone()[0]
        docs_with_chunks = con.execute(
            f"""
            SELECT COUNT(DISTINCT d.doc_id)
            FROM documents d
            JOIN chunks c ON c.doc_id = d.doc_id AND c.status='active'
            WHERE d.status='active'
              AND c.status='active'
              {"AND d.project = ?" if project else ""}
              {"AND lower(d.ext) IN (" + ",".join(["?"]*len(exts)) + ")" if exts else ""}
            """,
            ([project] if project else []) + (sorted(exts) if exts else []),
        ).fetchone()[0]

        active_chunks = con.execute(
            f"""
            SELECT COUNT(*)
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE c.status='active'
              AND d.status='active'
              {"AND d.project = ?" if project else ""}
              {"AND lower(d.ext) IN (" + ",".join(["?"]*len(exts)) + ")" if exts else ""}
            """,
            ([project] if project else []) + (sorted(exts) if exts else []),
        ).fetchone()[0]

        print("\n=== SQLite 요약 ===")
        print(f"  active_docs:        {int(total_docs)}")
        print(f"  eligible_docs(ext): {int(eligible_docs)}  exts={','.join(sorted(exts)) if exts else '(all)'}")
        print(f"  docs_with_chunks:   {int(docs_with_chunks)}")
        print(f"  active_chunks:      {int(active_chunks)}")

        # 청크 없는 eligible 문서(상위 N)
        if eligible_docs > 0:
            miss = con.execute(
                f"""
                SELECT d.project, COALESCE(d.rel_path,''), d.abs_path
                FROM documents d
                LEFT JOIN (
                  SELECT DISTINCT doc_id FROM chunks WHERE status='active'
                ) c ON c.doc_id = d.doc_id
                WHERE d.status='active'
                  {"AND d.project = ?" if project else ""}
                  {"AND lower(d.ext) IN (" + ",".join(["?"]*len(exts)) + ")" if exts else ""}
                  AND c.doc_id IS NULL
                ORDER BY d.abs_path
                LIMIT 20
                """,
                ([project] if project else []) + (sorted(exts) if exts else []),
            ).fetchall()
            if miss:
                print("\n  [청크 누락(상위 20)]")
                for proj, rel, ap in miss:
                    print(f"    - ({proj}) {rel}  |  {ap}")
            else:
                print("\n  청크 누락: 없음(상위 20 기준)")

        # Chroma 점검
        chroma_dir = Path(args.chroma_dir)
        if not chroma_dir.exists():
            print("\n=== Chroma ===")
            print(f"  chroma_dir 없음: {chroma_dir}")
            return 0

        _require_deps()
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        try:
            try:
                collection = client.get_collection(name=str(args.collection))
            except Exception as e:
                print("\n=== Chroma ===")
                print(f"  collection 없음: {args.collection} ({e})")
                return 0

            c_count = int(collection.count())
            print("\n=== Chroma ===")
            print(f"  collection: {args.collection}")
            print(f"  count:      {c_count}")

            # 샘플: SQLite chunks → Chroma 존재 여부
            if sample_n > 0 and active_chunks > 0:
                sample_rows = con.execute(
                    f"""
                    SELECT c.chunk_id
                    FROM chunks c
                    JOIN documents d ON d.doc_id = c.doc_id
                    WHERE c.status='active'
                      AND d.status='active'
                      {"AND d.project = ?" if project else ""}
                      {"AND lower(d.ext) IN (" + ",".join(["?"]*len(exts)) + ")" if exts else ""}
                    ORDER BY RANDOM()
                    LIMIT ?
                    """,
                    ([project] if project else []) + (sorted(exts) if exts else []) + [sample_n],
                ).fetchall()
                sample_ids = [str(r[0]) for r in sample_rows]
                got = collection.get(ids=sample_ids, include=[])
                found_ids = set(got.get("ids") or [])
                missing = [cid for cid in sample_ids if cid not in found_ids]

                print("\n  [샘플 검사: SQLite(active chunks) → Chroma]")
                print(f"    sample:  {len(sample_ids)}")
                print(f"    found:   {len(found_ids)}")
                print(f"    missing: {len(missing)}")
                if missing[:10]:
                    print("    missing_top10:")
                    for cid in missing[:10]:
                        print(f"      - {cid}")

            # 샘플: Chroma peek → SQLite 존재 여부
            if sample_n > 0 and c_count > 0:
                peek = collection.peek(min(50, sample_n))
                peek_ids = [str(x) for x in (peek.get("ids") or [])]
                if peek_ids:
                    placeholders = ",".join(["?"] * len(peek_ids))
                    rows = con.execute(
                        f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({placeholders})",
                        peek_ids,
                    ).fetchall()
                    exist = {str(r[0]) for r in rows}
                    stale = [cid for cid in peek_ids if cid not in exist]

                    print("\n  [샘플 검사: Chroma(peek) → SQLite]")
                    print(f"    peek:   {len(peek_ids)}")
                    print(f"    exist:  {len(exist)}")
                    print(f"    stale:  {len(stale)}")
                    if stale[:10]:
                        print("    stale_top10:")
                        for cid in stale[:10]:
                            print(f"      - {cid}")

            print("\n완료.")
            return 0
        finally:
            try:
                client._system.stop()
            except Exception:
                pass
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
