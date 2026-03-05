"""
index_vectors.py
Workspace Brain — 벡터 인덱싱(청킹 + 임베딩 + ChromaDB) 실행 CLI

권장:
  D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\index_vectors.py --project MRA --exts .md,.txt
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from src.indexer.vector_indexer import index_vectors, DEFAULT_COLLECTION_NAME
from src.utils.runtime import is_frozen, runtime_root, storage_root, tool_cmd


CODE_ROOT = runtime_root()
STORE_ROOT = storage_root()
DEFAULT_DB_PATH = STORE_ROOT / "data" / "metadata.db"
DEFAULT_CHROMA_DIR = STORE_ROOT / "data" / "chroma_db"
DEFAULT_SNAPSHOT_ROOT = STORE_ROOT / "data" / "backups" / "chroma_snapshots"


def _parse_exts(s: str) -> set[str]:
    out: set[str] = set()
    for part in (s or "").split(","):
        p = part.strip()
        if not p:
            continue
        out.add(p if p.startswith(".") else f".{p}")
    return out


def _health_check(*, chroma_dir: Path, collection: str, verbose: bool) -> tuple[bool, int | None]:
    """
    Chroma health-check는 access violation 가능성이 있어 별도 프로세스로 실행합니다.
    반환: (ok, count)
    """
    target = CODE_ROOT / ("chroma_health_check.exe" if is_frozen() else "chroma_health_check.py")
    if not target.exists():
        if verbose:
            print(f"[health-check] 실행 파일 없음(스킵): {target}")
        return (False, None)

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")

    cmd = tool_cmd(root=CODE_ROOT, stem="chroma_health_check", script_name="chroma_health_check.py") + [
        "--chroma-dir",
        str(chroma_dir),
        "--collection",
        str(collection),
    ]
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    out = (r.stdout or "").strip()
    if r.returncode != 0:
        if verbose and out:
            print(f"[health-check] 실패(returncode={r.returncode})\n{out}")
        return (False, None)

    last = out.splitlines()[-1].strip() if out else ""
    try:
        return (True, int(last))
    except Exception:
        if verbose:
            print(f"[health-check] 출력 파싱 실패: {out}")
        return (False, None)


def _snapshot_chroma_dir(*, chroma_dir: Path, snapshot_root: Path, verbose: bool) -> Path | None:
    if not chroma_dir.exists():
        return None
    snapshot_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d_%H%M%S")
    dst = snapshot_root / f"{chroma_dir.name}_snapshot_{ts}"
    try:
        shutil.copytree(chroma_dir, dst)
    except Exception as e:
        if verbose:
            print(f"[snapshot] 실패: {e}")
        return None
    if verbose:
        print(f"[snapshot] 저장: {dst}")
    return dst


def _run_index_vectors_direct(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB 파일이 없습니다: {db_path}")
        return 2

    chroma_dir = Path(args.chroma_dir)
    exts = _parse_exts(args.exts)
    project = args.project.strip() or None
    limit_docs = int(args.limit_docs) if int(args.limit_docs) > 0 else None

    index_vectors(
        db_path=db_path,
        chroma_dir=chroma_dir,
        collection_name=args.collection,
        project=project,
        include_exts=exts,
        limit_docs=limit_docs,
        chunk_max_chars=int(args.chunk_max_chars),
        chunk_overlap=int(args.chunk_overlap),
        max_file_chars=int(args.max_file_chars),
        batch_size=int(args.batch_size),
        force=bool(args.force),
        verbose=bool(args.verbose),
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace Brain 벡터 인덱싱(ChromaDB)")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    p.add_argument("--chroma-dir", type=str, default=str(DEFAULT_CHROMA_DIR), help="ChromaDB 영속 디렉터리")
    p.add_argument("--collection", type=str, default=DEFAULT_COLLECTION_NAME, help="Chroma collection 이름")
    p.add_argument("--project", type=str, default="", help="프로젝트 필터(예: MRA)")
    p.add_argument("--limit-docs", type=int, default=0, help="문서 처리 상한(0이면 무제한)")
    p.add_argument("--exts", type=str, default=".md,.txt", help="포함 확장자(쉼표 구분)")
    p.add_argument("--chunk-max-chars", type=int, default=1400, help="청크 최대 길이(문자)")
    p.add_argument("--chunk-overlap", type=int, default=200, help="청크 겹침 길이(문자)")
    p.add_argument("--max-file-chars", type=int, default=200000, help="파일 내용 최대 길이(문자). 0이면 제한 없음")
    p.add_argument("--include-large-text", action="store_true", help="대형 텍스트도 인덱싱(= --max-file-chars 0)")
    p.add_argument("--batch-size", type=int, default=64, help="임베딩/업서트 배치 크기")
    p.add_argument("--force", action="store_true", help="기존 chunk_id가 있어도 재임베딩/재업서트")
    p.add_argument("--verbose", action="store_true", help="진행 로그 출력")

    # 안전 실행 옵션(기본 ON)
    p.add_argument("--no-snapshot", action="store_true", help="Chroma 스냅샷/백업을 생략")
    p.add_argument("--snapshot-root", type=str, default=str(DEFAULT_SNAPSHOT_ROOT), help="Chroma 스냅샷 저장 폴더")
    p.add_argument("--no-health-check", action="store_true", help="Chroma health-check(count)를 생략")

    # 내부용: 직접 실행(별도 프로세스에서 호출)
    p.add_argument("--_direct", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()

    if bool(args.include_large_text):
        args.max_file_chars = 0

    # 직접 실행(=Chroma 접근을 현재 프로세스에서 수행)
    if bool(args._direct):
        return _run_index_vectors_direct(args)

    # 안전 실행: (1) 스냅샷/백업 (2) health-check (3) 별도 프로세스로 인덱싱 (4) health-check
    chroma_dir = Path(args.chroma_dir)
    snapshot_root = Path(args.snapshot_root)

    snapshot_path: Path | None = None
    if not bool(args.no_snapshot):
        snapshot_path = _snapshot_chroma_dir(chroma_dir=chroma_dir, snapshot_root=snapshot_root, verbose=bool(args.verbose))

    if not bool(args.no_health_check):
        ok, cnt = _health_check(chroma_dir=chroma_dir, collection=str(args.collection), verbose=bool(args.verbose))
        if bool(args.verbose):
            print(f"[health-check:before] ok={ok} count={cnt if cnt is not None else '-'}")

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONFAULTHANDLER", "1")

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_direct",
        "--db",
        str(args.db),
        "--chroma-dir",
        str(args.chroma_dir),
        "--collection",
        str(args.collection),
        "--project",
        str(args.project),
        "--limit-docs",
        str(args.limit_docs),
        "--exts",
        str(args.exts),
        "--chunk-max-chars",
        str(args.chunk_max_chars),
        "--chunk-overlap",
        str(args.chunk_overlap),
        "--max-file-chars",
        str(args.max_file_chars),
        "--batch-size",
        str(args.batch_size),
    ]
    if bool(args.force):
        cmd.append("--force")
    if bool(args.verbose):
        cmd.append("--verbose")

    r = subprocess.run(cmd, env=env)

    if not bool(args.no_health_check):
        ok, cnt = _health_check(chroma_dir=chroma_dir, collection=str(args.collection), verbose=bool(args.verbose))
        if bool(args.verbose):
            print(f"[health-check:after] ok={ok} count={cnt if cnt is not None else '-'}")
        if not ok:
            # 실패 시 스냅샷으로 복구(가능한 경우)
            if snapshot_path and snapshot_path.exists():
                ts = time.strftime("%Y-%m-%d_%H%M%S")
                failed_dst = snapshot_root / f"{chroma_dir.name}_failed_{ts}"
                try:
                    if chroma_dir.exists():
                        shutil.move(chroma_dir, failed_dst)
                    shutil.copytree(snapshot_path, chroma_dir)
                    if bool(args.verbose):
                        print(f"[restore] 실패 인덱스 이동: {failed_dst}")
                        print(f"[restore] 스냅샷 복구: {snapshot_path} -> {chroma_dir}")
                except Exception as e:
                    print(f"[restore] 복구 실패: {e}")
            return 2

    return int(r.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
