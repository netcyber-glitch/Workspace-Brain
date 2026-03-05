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
from pathlib import Path


def _preparse_root(argv: list[str]) -> str | None:
    """
    --root 값은 import 시점(모듈 상수 계산) 전에 필요할 수 있어,
    argparse 이전에 간단 파싱으로 환경변수를 먼저 세팅합니다.
    """
    if not argv:
        return None

    for a in argv:
        if isinstance(a, str) and a.startswith("--root="):
            v = a.split("=", 1)[1].strip().strip("\"").strip("'")
            return v or None

    try:
        i = argv.index("--root")
    except ValueError:
        return None

    if i + 1 >= len(argv):
        return None

    v = str(argv[i + 1]).strip().strip("\"").strip("'")
    if not v or v.startswith("--"):
        return None
    return v


_maybe_root = _preparse_root(list(sys.argv[1:]))
if _maybe_root:
    os.environ["WORKSPACE_BRAIN_ROOT"] = _maybe_root

from src.scanner.scanner import FileScanner  # noqa: E402
from src.utils.runtime import runtime_root, storage_root  # noqa: E402
from src.utils.settings import load_settings, resolve_enabled_projects  # noqa: E402
from src.db.init_db import init_db  # noqa: E402
from src.indexer.fts_indexer import rebuild_fts  # noqa: E402
from src.indexer.vector_indexer import DEFAULT_COLLECTION_NAME  # noqa: E402

CODE_ROOT = runtime_root()
STORE_ROOT = storage_root()
DEFAULT_DB_PATH = STORE_ROOT / "data" / "metadata.db"
DEFAULT_CHROMA_DIR = STORE_ROOT / "data" / "chroma_db"
DEFAULT_SNAPSHOT_ROOT = STORE_ROOT / "data" / "backups" / "chroma_snapshots"

os.chdir(str(CODE_ROOT))


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


def _configure_pipeline_from_settings(
    args: argparse.Namespace,
    settings: dict,
    *,
    db_path: Path,
    chroma_dir: Path,
) -> int | None:
    """
    settings.json의 pipeline preset을 읽어 scan_all 실행 플래그를 세팅합니다.
    - 반환값: None이면 계속 진행, int이면 즉시 종료(returncode)
    """
    pipeline = settings.get("pipeline")
    if not isinstance(pipeline, dict):
        pipeline = {}

    presets = pipeline.get("presets")
    if not isinstance(presets, dict):
        presets = {}

    if not presets:
        print("\n[실패] settings.json에 pipeline.presets가 없습니다.")
        return 2

    try:
        interactive = bool(sys.stdin.isatty())
    except Exception:
        interactive = False

    default_preset = str(pipeline.get("default_preset") or "").strip() or "incremental"
    preset_name = str(getattr(args, "pipeline_preset", "") or "").strip()

    # preset 선택(대화형)
    if not preset_name:
        if interactive:
            # incremental/full 우선, 그 외는 정렬
            order: list[str] = []
            for n in ("incremental", "full"):
                if n in presets:
                    order.append(n)
            for n in sorted([n for n in presets.keys() if n not in order]):
                order.append(n)

            if not order:
                preset_name = default_preset
            else:
                default_idx = order.index(default_preset) if default_preset in order else 0
                print("\nPipeline preset 선택:")
                for i, name in enumerate(order):
                    cfg = presets.get(name)
                    reset = bool(cfg.get("reset_index")) if isinstance(cfg, dict) else False
                    tag = " (reset-index)" if reset else ""
                    print(f"  {i}: {name}{tag}")
                try:
                    s = input(f"번호 입력(기본 {default_idx}): ").strip()
                except EOFError:
                    s = ""
                if not s:
                    preset_name = order[default_idx]
                else:
                    try:
                        idx = int(s)
                    except Exception:
                        idx = default_idx
                    preset_name = order[idx] if 0 <= idx < len(order) else order[default_idx]
        else:
            preset_name = default_preset

    if preset_name not in presets:
        print(f"\n[실패] pipeline preset을 찾을 수 없습니다: {preset_name}")
        return 2

    preset_cfg = presets.get(preset_name)
    if not isinstance(preset_cfg, dict):
        preset_cfg = {}

    # 실행 확인
    confirm_before_run = bool(pipeline.get("confirm_before_run", True))
    if confirm_before_run and not bool(getattr(args, "yes", False)):
        if not interactive:
            print("\n[실패] 비대화형 실행에서는 --yes 또는 --pipeline-preset을 함께 지정하세요.")
            return 2

        reset_index = bool(preset_cfg.get("reset_index", False))
        rebuild_fts = bool(preset_cfg.get("rebuild_fts", False))
        index_vectors = bool(preset_cfg.get("index_vectors", False))
        include_large_text = bool(preset_cfg.get("vector_include_large_text", False))
        build_chains = bool(preset_cfg.get("build_version_chains", False))

        print(f"\n선택된 preset: {preset_name}")
        print(f"  - db: {db_path}")
        print(f"  - chroma: {chroma_dir}")
        print(f"  - reset_index: {reset_index}")
        print(f"  - rebuild_fts: {rebuild_fts}")
        print(f"  - index_vectors: {index_vectors} (include_large_text={include_large_text})")
        print(f"  - build_version_chains: {build_chains}")

        try:
            ans = input("\n정말 실행할까요? (Y/N): ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("\n취소했습니다.")
            return 0

    # preset 적용
    args.reset_index = bool(preset_cfg.get("reset_index", False))
    args.rebuild_fts = bool(preset_cfg.get("rebuild_fts", False))
    args.index_vectors = bool(preset_cfg.get("index_vectors", False))
    args.vector_include_large_text = bool(preset_cfg.get("vector_include_large_text", False))
    args.build_version_chains = bool(preset_cfg.get("build_version_chains", False))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Workspace Brain 전체 스캔 실행")
    parser.add_argument("--root", type=str, default="", help="config/data 루트 오버라이드(예: D:\\WB_Data)")
    parser.add_argument("--settings", type=str, default="", help="설정 파일 경로(기본: config/settings.json)")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    parser.add_argument("--chroma-dir", type=str, default=str(DEFAULT_CHROMA_DIR), help="ChromaDB 영속 디렉터리")
    parser.add_argument("--snapshot-root", type=str, default="", help="Chroma 스냅샷 저장 폴더(비우면 settings.json 또는 기본값)")
    parser.add_argument("--pipeline", action="store_true", help="settings.json의 pipeline preset으로 인덱싱 단계 자동 실행(대화형 선택/확인)")
    parser.add_argument("--pipeline-preset", type=str, default="", help="pipeline preset 이름(예: incremental, full). 비우면 설정값/대화형 선택 사용")
    parser.add_argument("--yes", action="store_true", help="pipeline 확인 프롬프트 생략(자동화용)")
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

    if str(args.root or "").strip():
        os.environ["WORKSPACE_BRAIN_ROOT"] = str(args.root).strip()

    settings_path = Path(args.settings) if args.settings else None
    settings = load_settings(settings_path)
    projects = resolve_enabled_projects(settings)

    db_path = Path(str(args.db))
    chroma_dir = Path(str(args.chroma_dir))

    storage = settings.get("storage") if isinstance(settings.get("storage"), dict) else {}
    snapshot_root = str(args.snapshot_root or "").strip() or str(storage.get("snapshot_root") or DEFAULT_SNAPSHOT_ROOT)

    if bool(args.pipeline):
        rc = _configure_pipeline_from_settings(args, settings, db_path=db_path, chroma_dir=chroma_dir)
        if rc is not None:
            return int(rc)

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
        try:
            from src.indexer.vector_indexer import index_vectors
        except Exception as e:
            print(f"\n[실패] 벡터 인덱싱 모듈 로드 실패: {type(e).__name__}: {e}")
            return 2

        project = str(args.vector_project or "").strip() or None
        max_file_chars = 0 if bool(args.vector_include_large_text) else int(args.vector_max_file_chars)
        exts: set[str] = set()
        for part in str(args.vector_exts or "").split(","):
            p = part.strip().lower()
            if not p:
                continue
            exts.add(p if p.startswith(".") else f".{p}")

        try:
            _ = index_vectors(
                db_path=db_path,
                chroma_dir=chroma_dir,
                collection_name=str(DEFAULT_COLLECTION_NAME),
                project=project,
                include_exts=exts or None,
                limit_docs=int(args.vector_limit_docs) if int(args.vector_limit_docs) > 0 else None,
                chunk_max_chars=int(args.vector_chunk_max_chars),
                chunk_overlap=int(args.vector_chunk_overlap),
                max_file_chars=int(max_file_chars),
                batch_size=int(args.vector_batch_size),
                force=bool(args.vector_force),
                verbose=True,
            )
        except (ValueError, RuntimeError) as e:
            print(f"\n[실패] 벡터 인덱싱: {e}")
            return 2
        except Exception as e:
            print(f"\n[실패] 벡터 인덱싱: {type(e).__name__}: {e}")
            return 2

    con.close()

    if args.build_version_chains:
        vc_project = str(args.version_chain_project or "").strip() or None
        try:
            from build_version_chains import build_version_chains
        except Exception as e:
            print(f"\n[실패] version_chains 모듈 로드 실패: {type(e).__name__}: {e}")
            return 2

        rc = int(
            build_version_chains(
                db_path=db_path,
                project=vc_project,
                min_chain_size=int(args.version_chain_min_chain_size),
                dry_run=False,
                verbose=True,
                max_day_gap=int(args.version_chain_max_day_gap),
                filename_sim_threshold=float(args.version_chain_filename_sim_threshold),
                content_sim_threshold=float(args.version_chain_content_sim_threshold),
                no_content_sim=bool(args.version_chain_no_content_sim),
                require_content_sim=bool(args.version_chain_require_content_sim),
                max_embed_chars=int(args.version_chain_max_embed_chars),
                debug_edge_filter=False,
            )
            or 0
        )
        if rc != 0:
            print(f"\n[실패] version_chains 구축(returncode={rc})")
            return 2

    print("\n검증 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
