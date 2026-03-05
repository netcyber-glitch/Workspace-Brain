"""
scan_all.py — 4개 프로젝트 전체 스캔 실행 및 검증
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import sqlite3
sys.path.insert(0, 'D:/Workspace_Brain')

from src.scanner.scanner import FileScanner
from src.utils.settings import load_settings, resolve_enabled_projects
from src.db.init_db import init_db
from src.indexer.fts_indexer import rebuild_fts
from src.indexer.vector_indexer import DEFAULT_COLLECTION_NAME
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "metadata.db"
DEFAULT_CHROMA_DIR = ROOT / "data" / "chroma_db"
DEFAULT_SNAPSHOT_ROOT = ROOT / "data" / "backups" / "chroma_snapshots"

os.chdir(str(ROOT))


def _reset_index(db_path: Path = DEFAULT_DB_PATH, chroma_dir: Path = DEFAULT_CHROMA_DIR) -> None:
    """
    인덱스를 "영구 삭제" 후 재초기화합니다.
    - SQLite: metadata.db (+ wal/shm)
    - Chroma: data/chroma_db/
    """
    for suffix in ("", "-wal", "-shm"):
        p = db_path.parent / f"{db_path.name}{suffix}"
        if p.exists():
            p.unlink()
            print(f"deleted: {p}")

    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)
        print(f"deleted: {chroma_dir}")
    chroma_dir.mkdir(parents=True, exist_ok=True)
    print(f"recreated: {chroma_dir}")

    init_db(db_path=db_path).close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Workspace Brain 전체 스캔 실행")
    parser.add_argument("--settings", type=str, default="", help="설정 파일 경로(기본: config/settings.json)")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    parser.add_argument("--chroma-dir", type=str, default=str(DEFAULT_CHROMA_DIR), help="ChromaDB 영속 디렉터리")
    parser.add_argument("--snapshot-root", type=str, default="", help="Chroma 스냅샷 저장 폴더(비우면 settings.json 또는 기본값)")
    parser.add_argument("--reset-index", action="store_true", help="기존 인덱스 영구 삭제 후 재초기화")
    parser.add_argument("--rebuild-fts", action="store_true", help="SQLite FTS5(키워드) 인덱스 재구축")
    parser.add_argument("--index-vectors", action="store_true", help="청킹+임베딩+ChromaDB 벡터 인덱싱 실행")
    parser.add_argument("--vector-project", type=str, default="", help="벡터 인덱싱 프로젝트 필터(예: MRA)")
    parser.add_argument("--vector-exts", type=str, default=".md,.txt", help="벡터 인덱싱 포함 확장자(쉼표 구분)")
    parser.add_argument("--vector-limit-docs", type=int, default=0, help="벡터 인덱싱 문서 상한(0이면 무제한)")
    parser.add_argument("--vector-chunk-max-chars", type=int, default=1400, help="벡터 인덱싱 청크 최대 길이(문자)")
    parser.add_argument("--vector-chunk-overlap", type=int, default=200, help="벡터 인덱싱 청크 겹침(문자)")
    parser.add_argument("--vector-max-file-chars", type=int, default=200000, help="벡터 인덱싱 파일 최대 길이(문자). 0이면 제한 없음")
    parser.add_argument("--vector-include-large-text", action="store_true", help="대형 텍스트도 벡터 인덱싱(= vector-max-file-chars 0)")
    parser.add_argument("--vector-batch-size", type=int, default=64, help="벡터 인덱싱 배치 크기")
    parser.add_argument("--vector-force", action="store_true", help="벡터 인덱싱 강제(재임베딩/재업서트)")

    # 버전 체인(타임라인) 자동 구축
    parser.add_argument("--build-version-chains", action="store_true", help="스캔 후 version_chains 자동 재구축")
    parser.add_argument("--version-chain-project", type=str, default="", help="버전 체인 구축 프로젝트 필터(예: MRA)")
    parser.add_argument("--version-chain-min-chain-size", type=int, default=2, help="버전 체인 최소 문서 수")
    parser.add_argument("--version-chain-max-day-gap", type=int, default=14, help="버전 후보 최대 날짜 차이(일)")
    parser.add_argument("--version-chain-filename-sim-threshold", type=float, default=0.70, help="파일명 유사도 임계값(0~1)")
    parser.add_argument("--version-chain-content-sim-threshold", type=float, default=0.75, help="내용 코사인 유사도 임계값(0~1)")
    parser.add_argument("--version-chain-no-content-sim", action="store_true", help="내용 유사도 계산 비활성화(파일명/날짜만 사용)")
    parser.add_argument("--version-chain-require-content-sim", action="store_true", help="내용 유사도를 계산할 수 없으면 후보 제외")
    parser.add_argument("--version-chain-max-embed-chars", type=int, default=12000, help="내용 유사도 계산용 최대 텍스트 길이(문자)")
    args = parser.parse_args()

    settings_path = Path(args.settings) if args.settings else None
    settings = load_settings(settings_path)
    projects = resolve_enabled_projects(settings)

    db_path = Path(str(args.db))
    chroma_dir = Path(str(args.chroma_dir))

    storage = settings.get("storage") if isinstance(settings.get("storage"), dict) else {}
    snapshot_root = str(args.snapshot_root or "").strip() or str(storage.get("snapshot_root") or DEFAULT_SNAPSHOT_ROOT)

    if args.reset_index:
        _reset_index(db_path=db_path, chroma_dir=chroma_dir)

    with FileScanner(db_path=db_path, settings=settings) as scanner:
        _ = scanner.scan_multiple(projects, verbose=True)
        deleted = scanner.mark_deleted()
        print(f"\nsoft_delete: {deleted}건")

    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT COUNT(*), SUM(file_size) FROM documents WHERE status='active'"
    ).fetchone()
    q = con.execute("SELECT COUNT(*) FROM quarantine_log").fetchone()[0]

    print("\n=== 최종 요약 ===")
    print(f"  활성 문서: {row[0]}건")
    print(f"  총 크기: {row[1]/1024:.1f} KB" if row[1] else "  총 크기: 0 KB")
    print(f"  격리 기록: {q}건")

    # 프로젝트별 통계
    print("\n  [프로젝트별]")
    cur = con.execute(
        "SELECT project, COUNT(*) as cnt FROM documents WHERE status='active' GROUP BY project ORDER BY cnt DESC"
    )
    for proj, cnt in cur.fetchall():
        print(f"    {proj}: {cnt}건")

    if args.rebuild_fts:
        _ = rebuild_fts(db_path=db_path, verbose=True)

    if args.index_vectors:
        project = str(args.vector_project or "").strip() or None
        max_file_chars = 0 if bool(args.vector_include_large_text) else int(args.vector_max_file_chars)
        cmd = [
            sys.executable,
            str((ROOT / "index_vectors.py").resolve()),
            "--db",
            str(db_path),
            "--chroma-dir",
            str(chroma_dir),
            "--collection",
            str(DEFAULT_COLLECTION_NAME),
            "--project",
            str(project or ""),
            "--limit-docs",
            str(int(args.vector_limit_docs) if int(args.vector_limit_docs) > 0 else 0),
            "--exts",
            str(args.vector_exts or ""),
            "--chunk-max-chars",
            str(int(args.vector_chunk_max_chars)),
            "--chunk-overlap",
            str(int(args.vector_chunk_overlap)),
            "--max-file-chars",
            str(int(max_file_chars)),
            "--batch-size",
            str(int(args.vector_batch_size)),
            "--snapshot-root",
            str(snapshot_root),
            "--verbose",
        ]
        if bool(args.vector_force):
            cmd.append("--force")

        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONFAULTHANDLER", "1")

        r = subprocess.run(cmd, env=env)
        if int(r.returncode or 0) != 0:
            print(f"\n[실패] 벡터 인덱싱(returncode={r.returncode})")
            return 2

    con.close()

    if args.build_version_chains:
        vc_project = str(args.version_chain_project or "").strip() or None
        vc_cmd = [
            sys.executable,
            str((ROOT / "build_version_chains.py").resolve()),
            "--db",
            str(db_path),
            "--min-chain-size",
            str(int(args.version_chain_min_chain_size)),
            "--max-day-gap",
            str(int(args.version_chain_max_day_gap)),
            "--filename-sim-threshold",
            str(float(args.version_chain_filename_sim_threshold)),
            "--content-sim-threshold",
            str(float(args.version_chain_content_sim_threshold)),
            "--max-embed-chars",
            str(int(args.version_chain_max_embed_chars)),
            "--verbose",
        ]
        if vc_project:
            vc_cmd.extend(["--project", str(vc_project)])
        if bool(args.version_chain_no_content_sim):
            vc_cmd.append("--no-content-sim")
        if bool(args.version_chain_require_content_sim):
            vc_cmd.append("--require-content-sim")

        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONFAULTHANDLER", "1")

        r = subprocess.run(vc_cmd, env=env)
        if int(r.returncode or 0) != 0:
            print(f"\n[실패] version_chains 구축(returncode={r.returncode})")
            return 2

    print("\n검증 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
