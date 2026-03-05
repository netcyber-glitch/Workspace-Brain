"""
chroma_health_check.py
Workspace Brain — ChromaDB health-check(별도 프로세스 권장)

목표:
- Chroma(rust backend)가 손상되면 Python 프로세스가 access violation으로 종료될 수 있어
  "헬스체크는 반드시 별도 프로세스"로 실행하는 것을 권장합니다.

출력:
- 정상: count 정수 1줄 출력, 종료코드 0
- 실패: 에러 요약 출력, 종료코드 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _require_deps() -> None:
    try:
        import chromadb  # noqa: F401
    except Exception as e:
        raise RuntimeError("필수 패키지가 없습니다: chromadb") from e


def main() -> int:
    p = argparse.ArgumentParser(description="ChromaDB health-check (count)")
    p.add_argument("--chroma-dir", type=str, required=True, help="ChromaDB 영속 디렉터리")
    p.add_argument("--collection", type=str, required=True, help="Chroma collection 이름")
    args = p.parse_args()

    chroma_dir = Path(args.chroma_dir)
    if not chroma_dir.exists():
        print(f"CHROMA_DIR_NOT_FOUND: {chroma_dir}")
        return 2

    _require_deps()
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(chroma_dir),
        settings=Settings(anonymized_telemetry=False),
    )

    try:
        try:
            col = client.get_collection(name=str(args.collection))
        except Exception as e:
            print(f"COLLECTION_NOT_FOUND: {args.collection} ({e})")
            return 2

        n = int(col.count())
        print(str(n))
        return 0
    finally:
        try:
            client._system.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

