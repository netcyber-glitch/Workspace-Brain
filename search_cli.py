"""
search_cli.py
Workspace Brain — 검색 CLI (FTS5 / Vector / Hybrid)

예:
  python D:\\Workspace_Brain\\search_cli.py \"USI 대안\" --project MRA --limit 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.search.fts_search import search_fts
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


def _preview(path: Path, *, max_lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = []
    for line in text.splitlines():
        if line.strip() == "":
            continue
        lines.append(line.rstrip())
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace Brain 검색 (FTS5 / Vector / Hybrid)")
    p.add_argument("query", type=str, help="FTS5 query (예: 단어, \"구문\", AND/OR 등)")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    p.add_argument("--mode", type=str, default="fts", choices=["fts", "vector", "hybrid"], help="검색 모드")
    p.add_argument("--project", type=str, default="", help="프로젝트 필터(예: MRA)")
    p.add_argument("--limit", type=int, default=10, help="반환 개수")
    p.add_argument("--chroma-dir", type=str, default=str(DEFAULT_CHROMA_DIR), help="ChromaDB 영속 디렉터리")
    p.add_argument("--collection", type=str, default="workspace_brain_chunks", help="Chroma collection 이름")
    p.add_argument("--rrf-k", type=int, default=60, help="hybrid(RRF) 상수 k")
    p.add_argument("--fts-limit", type=int, default=30, help="hybrid에서 사용할 FTS 후보 개수")
    p.add_argument("--vector-limit", type=int, default=30, help="hybrid에서 사용할 Vector 후보(문서) 개수")
    p.add_argument("--vector-chunk-topk", type=int, default=80, help="Vector 검색 시 chunk 후보 개수")
    p.add_argument("--preview", action="store_true", help="각 결과의 상단 일부를 미리보기로 출력")
    p.add_argument("--preview-lines", type=int, default=8, help="미리보기 최대 줄 수")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB 파일이 없습니다: {db_path}")
        return 2

    project = args.project.strip() or None
    mode = str(args.mode or "fts").strip().lower()

    hits: list[object] = []

    try:
        if mode == "fts":
            hits = search_fts(
                db_path=db_path,
                query=args.query,
                project=project,
                limit=args.limit,
            )
        elif mode == "vector":
            from src.search.vector_search import search_vector

            hits = search_vector(
                db_path=db_path,
                chroma_dir=Path(args.chroma_dir),
                collection_name=str(args.collection),
                query=args.query,
                project=project,
                limit=args.limit,
                chunk_topk=int(args.vector_chunk_topk),
            )
        elif mode == "hybrid":
            from src.search.hybrid_search import hybrid_search

            hits, _, _ = hybrid_search(
                db_path=db_path,
                chroma_dir=Path(args.chroma_dir),
                query=args.query,
                project=project,
                limit=args.limit,
                fts_limit=int(args.fts_limit),
                vector_limit=int(args.vector_limit),
                vector_chunk_topk=int(args.vector_chunk_topk),
                rrf_k=int(args.rrf_k),
            )
        else:
            print(f"알 수 없는 mode: {mode}")
            return 2
    except (ValueError, RuntimeError) as e:
        print(str(e))
        return 2
    except Exception as e:
        print(f"검색 실패: {type(e).__name__}: {e}")
        return 2

    if not hits:
        print("검색 결과가 없습니다.")
        return 0

    print(f"\n[검색 결과] {len(hits)}건 (mode={mode})")
    for i, h in enumerate(hits, start=1):
        title = getattr(h, "title", "") or Path(getattr(h, "abs_path", "")).name
        proj = getattr(h, "project", "")
        rel_path = getattr(h, "rel_path", "")
        abs_path = getattr(h, "abs_path", "")
        score = getattr(h, "score", 0.0)

        print(f"\n{i}. ({proj}) {title}")
        if rel_path:
            print(f"   rel: {rel_path}")
        print(f"   abs: {abs_path}")
        print(f"   score: {float(score):.4f}")

        if mode == "vector":
            print(f"   best_chunk_index: {getattr(h, 'best_chunk_index', 0)}")
        if mode == "hybrid":
            fr = getattr(h, "fts_rank", None)
            vr = getattr(h, "vector_rank", None)
            print(f"   ranks: fts={fr}, vector={vr}")

        if args.preview:
            pv = _preview(Path(abs_path), max_lines=max(1, int(args.preview_lines)))
            if pv:
                print("   --- preview ---")
                for line in pv.splitlines():
                    print(f"   {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
