"""
src/scanner/scanner.py
Workspace Brain — 파일 스캐너 & 메타데이터 추출기

기능:
  - 지정된 루트 폴더를 재귀 스캔하여 텍스트 파일을 탐지
  - doc_id(SSOT §3.0.1), SHA-256, mtime/ctime, date_prefix 추출
  - 결과를 SQLite documents 테이블에 INSERT OR REPLACE (증분 인덱싱)
  - 파서 실패 / 접근 불가 파일 → quarantine_log에 격리 기록 (원본 불변)
  - rename/move 감지: 해시 동일 + 경로 변경 → 경로 UPDATE (버전 체인 유지)
"""

import os
import re
import sqlite3
import sys
import time
import logging
from pathlib import Path
from typing import Iterator

def _runtime_root() -> Path:
    if bool(getattr(sys, "frozen", False)):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


ROOT = _runtime_root()
if not bool(getattr(sys, "frozen", False)):
    sys.path.insert(0, str(ROOT))

from src.db.schema import (
    INDEXER_VERSION,
    EMBED_MODEL_ID,
    make_doc_id,
    compute_file_hash,
    normalize_path,
)
from src.utils.settings import load_settings
from src.indexer.fts_indexer import upsert_fts, delete_fts
from src.utils.runtime import storage_root

# ─── 설정 ────────────────────────────────────────────────────────────────────

# 인덱싱 대상 확장자
DEFAULT_SUPPORTED_EXTENSIONS: set[str] = {
    ".md", ".txt", ".json", ".py", ".ts", ".js",
    ".html", ".css", ".yaml", ".yml", ".toml", ".ini", ".cfg",
}

# 무조건 건너뛸 폴더 패턴 (이름 기준)
DEFAULT_SKIP_DIRS: set[str] = {
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv", ".env",
    "dist", "build", ".next", ".nuxt",
    "benchmarks",
    "runtime",
    "logs",
    "snapshots",
    "playwright-report",
    "test-results",
    "coverage",
    "tmp",
    "third_party",
    ".agent",
    ".history",
    ".vscode",
    ".github",
    "nppBackup",
    "_backup",
    "_deprecated",
    "_archives",
    "_quarantine",
}

# 접두어 기반 스킵 (예: dist-mobile, .tmp.filter-rewrite)
DEFAULT_SKIP_DIR_PREFIXES: tuple[str, ...] = (
    "dist",
    "build",
    ".tmp",
)

# 파일 크기 상한 (이 이상이면 건너뜀, 기본 10MB)
DEFAULT_MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024

# 날짜 접두어 정규식
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_?")

STORE_ROOT = storage_root()
DB_PATH = STORE_ROOT / "data" / "metadata.db"

logger = logging.getLogger("workspace_brain.scanner")


# ─── 유틸 함수 ───────────────────────────────────────────────────────────────

def _extract_title(path: Path) -> str:
    """파일의 첫 번째 H1 헤딩을 제목으로 추출. 없으면 파일명(확장자 제외)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
    except Exception:
        pass
    return path.stem


def _extract_date_prefix(filename: str) -> str | None:
    """파일명에서 YYYY-MM-DD 접두어를 추출. 없으면 None."""
    m = DATE_PREFIX_RE.match(filename)
    return m.group(1) if m else None


def _is_effectively_empty(path: Path, *, file_size: int) -> bool:
    """
    파일이 "사실상 빈 문서"인지 판정합니다.
    - 0 bytes는 무조건 empty
    - 소형 파일은(기본 16KB 이하) 텍스트를 읽어 공백/개행만 있는지 확인
    """
    try:
        size = int(file_size)
    except Exception:
        size = 0

    if size <= 0:
        return True
    if size > 16 * 1024:
        return False

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return (text or "").strip() == ""

# ─── 스캐너 메인 클래스 ──────────────────────────────────────────────────────

class FileScanner:
    """
    지정된 프로젝트 폴더를 스캔하여 메타데이터를 SQLite에 저장.

    사용 예시:
        scanner = FileScanner(db_path=DB_PATH)
        result = scanner.scan_project("MRA", Path("d:/MRA"))
        print(result)
    """

    def __init__(self, db_path: Path = DB_PATH, *, settings: dict | None = None):
        self.db_path = db_path
        self.settings = settings or self._safe_load_settings()
        self._apply_scanner_settings()
        self._apply_project_policies()
        self.con = sqlite3.connect(str(db_path), timeout=60.0)
        self.con.execute("PRAGMA foreign_keys = ON;")
        self.con.execute("PRAGMA journal_mode = WAL;")
        self.con.execute("PRAGMA busy_timeout = 60000;")

    def _safe_load_settings(self) -> dict:
        """설정 파일 로드. 실패 시 기본값으로 동작."""
        try:
            return load_settings()
        except Exception as e:
            logger.warning(f"settings.json 로드 실패(기본값 사용): {e}")
            return {}

    def _apply_scanner_settings(self) -> None:
        cfg = self.settings.get("scanner", {}) if isinstance(self.settings, dict) else {}

        exts = cfg.get("supported_extensions", []) if isinstance(cfg, dict) else []
        if not exts:
            exts = list(DEFAULT_SUPPORTED_EXTENSIONS)
        self.supported_extensions = {str(x).lower() for x in exts if str(x).strip()}

        skip_names = cfg.get("skip_dir_names", []) if isinstance(cfg, dict) else []
        if not skip_names:
            skip_names = list(DEFAULT_SKIP_DIRS)
        self.skip_dir_names = {str(x).lower() for x in skip_names if str(x).strip()}

        skip_prefixes = cfg.get("skip_dir_prefixes", []) if isinstance(cfg, dict) else []
        if not skip_prefixes:
            skip_prefixes = list(DEFAULT_SKIP_DIR_PREFIXES)
        self.skip_dir_prefixes = tuple(str(x).lower() for x in skip_prefixes if str(x).strip())

        max_bytes = cfg.get("max_file_size_bytes") if isinstance(cfg, dict) else None
        try:
            max_bytes_int = int(max_bytes)
        except Exception:
            max_bytes_int = 0
        self.max_file_size_bytes = max_bytes_int if max_bytes_int > 0 else DEFAULT_MAX_FILE_SIZE_BYTES

    def _apply_project_policies(self) -> None:
        projects = self.settings.get("projects", {}) if isinstance(self.settings, dict) else {}
        self.project_policies: dict[str, dict] = {}
        if not isinstance(projects, dict):
            return

        for name, cfg in projects.items():
            if not isinstance(cfg, dict):
                continue
            include = cfg.get("include_rel_path_prefixes", [])
            skip = cfg.get("skip_rel_path_prefixes", [])
            self.project_policies[str(name)] = {
                "include_rel_path_prefixes": list(include) if isinstance(include, list) else [],
                "skip_rel_path_prefixes": list(skip) if isinstance(skip, list) else [],
            }

    def _normalize_rel(self, rel: str) -> str:
        return (rel or "").replace("\\", "/").strip("/").lower()

    def _rel_starts_with_any(self, rel_posix: str, prefixes: list[str]) -> bool:
        rel = self._normalize_rel(rel_posix)
        for p in prefixes:
            pref = self._normalize_rel(str(p))
            if not pref:
                continue
            if rel == pref or rel.startswith(pref + "/"):
                return True
        return False

    def _rel_is_included(self, rel_posix: str, include_prefixes: list[str], *, is_dir: bool) -> bool:
        """
        include_rel_path_prefixes(화이트리스트) 처리.

        정책:
        - include_prefixes가 비어있으면 전체 포함
        - 파일은 "include prefix 하위"만 포함
        - 디렉터리는 "include prefix 하위" + "include prefix로 내려가기 위한 상위 경로"만 포함

        예:
          include=["data/archive"]일 때
            - "data" 디렉터리는 포함(하위로 내려가기 위해 필요)
            - "data/chroma_db"는 제외
        """
        if not include_prefixes:
            return True

        rel = self._normalize_rel(rel_posix)
        if not rel:
            return True

        # (1) include prefix 자체이거나, 그 하위면 포함
        if self._rel_starts_with_any(rel, include_prefixes):
            return True

        # (2) 디렉터리인 경우: include prefix로 내려가기 위한 상위 경로면 포함
        if is_dir:
            for p in include_prefixes:
                pref = self._normalize_rel(str(p))
                if pref and (pref == rel or pref.startswith(rel + "/")):
                    return True

        return False

    def _iter_files(self, root: Path, *, project_root: Path, policy: dict) -> Iterator[Path]:
        """
        project_root 하위 파일을 재귀 탐색.
        - settings 기반 스킵 규칙 적용
        - 프로젝트 include/skip rel_path_prefixes 적용
        """
        include_prefixes = policy.get("include_rel_path_prefixes", []) if isinstance(policy, dict) else []
        skip_prefixes = policy.get("skip_rel_path_prefixes", []) if isinstance(policy, dict) else []

        try:
            entries = list(root.iterdir())
        except PermissionError as e:
            logger.warning(f"접근 불가 폴더: {root} — {e}")
            return

        for entry in entries:
            if entry.is_dir():
                name_lower = entry.name.lower()
                if name_lower in self.skip_dir_names:
                    logger.debug(f"건너뜀(폴더): {entry}")
                    continue
                if any(name_lower.startswith(prefix) for prefix in self.skip_dir_prefixes):
                    logger.debug(f"건너뜀(폴더): {entry}")
                    continue

                try:
                    rel_posix = entry.relative_to(project_root).as_posix().lower()
                except Exception:
                    rel_posix = ""

                if skip_prefixes and self._rel_starts_with_any(rel_posix, skip_prefixes):
                    logger.debug(f"건너뜀(경로 prefix): {entry}")
                    continue
                if include_prefixes and not self._rel_is_included(rel_posix, include_prefixes, is_dir=True):
                    logger.debug(f"건너뜀(미포함 경로): {entry}")
                    continue

                yield from self._iter_files(entry, project_root=project_root, policy=policy)

            elif entry.is_file():
                if entry.suffix.lower() not in self.supported_extensions:
                    continue

                try:
                    rel_posix = entry.relative_to(project_root).as_posix().lower()
                except Exception:
                    rel_posix = ""

                if skip_prefixes and self._rel_starts_with_any(rel_posix, skip_prefixes):
                    continue
                if include_prefixes and not self._rel_is_included(rel_posix, include_prefixes, is_dir=False):
                    continue

                yield entry

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── 내부 메서드 ────────────────────────────────────────────────────────

    def _quarantine(self, abs_path: str, reason: str, detail: str = "") -> None:
        """파서 실패 또는 접근 불가 파일을 quarantine_log에만 기록. 파일은 건드리지 않음."""
        self.con.execute(
            """INSERT INTO quarantine_log(abs_path, reason, error_detail, logged_at)
               VALUES (?, ?, ?, ?)""",
            (abs_path, reason, detail, time.time()),
        )
        logger.warning(f"격리 기록: {abs_path} — {reason}")

    def _existing_record(self, doc_id: str) -> dict | None:
        """(호환) doc_id로 기존 레코드를 조회."""
        cur = self.con.execute(
            "SELECT doc_id, sha256, abs_path FROM documents WHERE doc_id = ?",
            (doc_id,),
        )
        row = cur.fetchone()
        if row:
            return {"doc_id": row[0], "sha256": row[1], "abs_path": row[2]}
        return None

    def _existing_record_by_path(self, abs_path: str) -> dict | None:
        """abs_path로 기존 레코드를 조회."""
        cur = self.con.execute(
            "SELECT doc_id, sha256, file_size, mtime, abs_path, status FROM documents WHERE abs_path = ? AND status IN ('active','empty') LIMIT 1",
            (abs_path,),
        )
        row = cur.fetchone()
        if row:
            return {
                "doc_id": row[0],
                "sha256": row[1],
                "file_size": row[2],
                "mtime": row[3],
                "abs_path": row[4],
                "status": row[5],
            }
        return None

    def _find_by_hash(self, sha256: str, project_name: str) -> dict | None:
        """파일 해시로 기존 레코드를 조회 (rename/move 감지용). 프로젝트 범위 내에서만 조회."""
        cur = self.con.execute(
            "SELECT doc_id, abs_path FROM documents WHERE sha256 = ? AND status IN ('active','empty') AND project = ? LIMIT 1",
            (sha256, project_name),
        )
        row = cur.fetchone()
        if row:
            return {"doc_id": row[0], "abs_path": row[1]}
        return None

    def _upsert_document(self, data: dict) -> str:
        """documents 테이블에 UPSERT(DELETE 없는 UPDATE)로 저장."""
        self.con.execute(
            """INSERT INTO documents
               (doc_id, abs_path, rel_path, filename, project, ext,
                file_size, mtime, ctime, sha256, title, date_prefix,
                status, schema_version, indexer_version, embed_model_id, indexed_at)
               VALUES
               (:doc_id, :abs_path, :rel_path, :filename, :project, :ext,
                :file_size, :mtime, :ctime, :sha256, :title, :date_prefix,
                :status, '1.0', :indexer_version, :embed_model_id, :indexed_at)
               ON CONFLICT(abs_path) DO UPDATE SET
                 doc_id=excluded.doc_id,
                 rel_path=excluded.rel_path,
                 filename=excluded.filename,
                 project=excluded.project,
                 ext=excluded.ext,
                 file_size=excluded.file_size,
                 mtime=excluded.mtime,
                 ctime=excluded.ctime,
                 sha256=excluded.sha256,
                 title=excluded.title,
                 date_prefix=excluded.date_prefix,
                 status=excluded.status,
                 schema_version='1.0',
                 indexer_version=excluded.indexer_version,
                 embed_model_id=excluded.embed_model_id,
                 indexed_at=excluded.indexed_at
            """,
            data,
        )
        return data["doc_id"]

    def _upsert_fts_for_file(self, *, doc_id: str, title: str, file_path: Path) -> None:
        """
        FTS 인덱스를 갱신합니다.
        - 메타 인덱싱 실패는 전체 스캔을 멈추지 않도록, 예외를 삼킵니다.
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = ""
        try:
            upsert_fts(self.con, doc_id=doc_id, title=title or "", content=content)
        except Exception as e:
            logger.warning(f"FTS 인덱싱 실패(무시): {file_path} — {e}")

    # ── 공개 메서드 ────────────────────────────────────────────────────────

    def scan_project(
        self,
        project_name: str,
        root_path: Path,
        *,
        verbose: bool = True,
    ) -> dict:
        """
        단일 프로젝트 폴더를 스캔하여 메타데이터를 DB에 저장.

        반환: {new, updated, skipped, quarantined, total_found}
        """
        stats = {"new": 0, "updated": 0, "skipped": 0, "quarantined": 0, "total_found": 0}

        root_path = root_path.resolve()
        if not root_path.exists():
            logger.error(f"경로 없음: {root_path}")
            return stats

        if verbose:
            print(f"\n[스캔 시작] 프로젝트: {project_name}  폴더: {root_path}")

        policy = self.project_policies.get(project_name, {})

        try:
            self.con.execute("BEGIN")

            for file_path in self._iter_files(root_path, project_root=root_path, policy=policy):
                stats["total_found"] += 1

                # ── 크기 체크 ──────────────────────────────────────────────────
                try:
                    stat = file_path.stat()
                except OSError as e:
                    self._quarantine(str(file_path), "stat 실패", str(e))
                    stats["quarantined"] += 1
                    continue

                if stat.st_size > self.max_file_size_bytes:
                    logger.debug(f"건너뜀(크기 초과 {stat.st_size}B): {file_path}")
                    stats["skipped"] += 1
                    continue

                abs_str = str(file_path)

                # ── doc_id (SSOT §3.0.1) ───────────────────────────────────────
                doc_id = make_doc_id(file_path, stat.st_size, stat.st_mtime)

                computed_status = "empty" if _is_effectively_empty(file_path, file_size=stat.st_size) else "active"

                # ── 기존 레코드 확인 (증분) ────────────────────────────────────
                existing = self._existing_record_by_path(abs_str)
                if existing:
                    same_doc_id = str(existing.get("doc_id") or "") == doc_id
                    same_size = int(existing.get("file_size") or 0) == int(stat.st_size)
                    try:
                        same_mtime = abs(float(existing.get("mtime") or 0.0) - float(stat.st_mtime)) < 1e-6
                    except Exception:
                        same_mtime = False
                    same_status = str(existing.get("status") or "") == computed_status
                    if same_doc_id and same_size and same_mtime and same_status:
                        stats["skipped"] += 1
                        continue

                # ── 파일 해시 계산 (변경 감지 + rename/move 감지) ──────────────
                try:
                    sha256 = compute_file_hash(file_path)
                except OSError as e:
                    self._quarantine(abs_str, "파일 읽기 실패", str(e))
                    stats["quarantined"] += 1
                    continue

                # rename/move 감지(안전): 동일 프로젝트 내에서
                #   - 해시 동일 + 경로 다름 + (기존 경로가 실제로는 없어짐)일 때만 "이동"으로 간주
                #   - 내용이 같은 파일이 여러 개 있는 경우(dup)는 이동으로 오판하지 않음
                moved = self._find_by_hash(sha256, project_name)
                moved_doc_id: str | None = None
                if moved and moved["abs_path"] != abs_str and not Path(moved["abs_path"]).exists():
                    moved_doc_id = str(moved["doc_id"])
                    logger.info(f"이동 감지: {moved['abs_path']} → {abs_str}")

                    # 기존 인덱스(FTS/청크)는 doc_id 변경이 생기므로 먼저 정리
                    try:
                        delete_fts(self.con, doc_id=moved_doc_id)
                    except Exception:
                        pass
                    try:
                        self.con.execute(
                            "DELETE FROM chunks WHERE doc_id = ?",
                            (moved_doc_id,),
                        )
                    except Exception:
                        pass

                    # 레코드의 abs_path를 새 경로로 갱신(정체성 유지)
                    try:
                        rel_path_for_move = str(file_path.relative_to(root_path))
                    except ValueError:
                        rel_path_for_move = abs_str
                    self.con.execute(
                        """UPDATE documents
                           SET abs_path=?, rel_path=?, filename=?, indexed_at=?
                           WHERE doc_id=?""",
                        (
                            abs_str,
                            rel_path_for_move,
                            file_path.name,
                            time.time(),
                            moved_doc_id,
                        ),
                    )
                    existing = self._existing_record_by_path(abs_str) or existing

                # ── 제목 / 날짜 추출 ─────────────────────────────────────────
                try:
                    title = _extract_title(file_path)
                except Exception as e:
                    self._quarantine(abs_str, "제목 추출 실패", str(e))
                    title = file_path.stem

                date_prefix = _extract_date_prefix(file_path.name)
                status = computed_status

                try:
                    rel_path = str(file_path.relative_to(root_path))
                except ValueError:
                    rel_path = abs_str

                # ── DB 저장 ──────────────────────────────────────────────────
                data = {
                    "doc_id":          doc_id,
                    "abs_path":        abs_str,
                    "rel_path":        rel_path,
                    "filename":        file_path.name,
                    "project":         project_name,
                    "ext":             file_path.suffix.lower(),
                    "file_size":       stat.st_size,
                    "mtime":           stat.st_mtime,
                    "ctime":           stat.st_ctime,
                    "sha256":          sha256,
                    "title":           title,
                    "date_prefix":     date_prefix,
                    "status":          status,
                    "indexer_version": INDEXER_VERSION,
                    "embed_model_id":  EMBED_MODEL_ID,
                    "indexed_at":      time.time(),
                }

                # doc_id가 바뀌는 경우(예: 구형 DB → 신형 규칙, 이동 감지 등) 기존 FTS를 먼저 제거
                if existing and str(existing.get("doc_id") or "") and str(existing.get("doc_id") or "") != doc_id:
                    try:
                        delete_fts(self.con, doc_id=str(existing.get("doc_id")))
                    except Exception:
                        pass

                self._upsert_document(data)
                self._upsert_fts_for_file(doc_id=doc_id, title=title, file_path=file_path)
                if existing or moved_doc_id:
                    stats["updated"] += 1
                else:
                    stats["new"] += 1

                if verbose and stats["new"] and stats["new"] % 50 == 0:
                    print(f"  ... {stats['new']}건 처리 중")

            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

        if verbose:
            print(
                f"[완료] {project_name}: "
                f"신규={stats['new']}, 변경={stats['updated']}, "
                f"스킵={stats['skipped']}, 격리={stats['quarantined']}, "
                f"발견={stats['total_found']}"
            )
        return stats

    def scan_multiple(self, projects: dict[str, Path], *, verbose: bool = True) -> dict:
        """여러 프로젝트를 순서대로 스캔. projects = {'이름': Path, ...}"""
        total = {"new": 0, "updated": 0, "skipped": 0, "quarantined": 0, "total_found": 0}
        for name, path in projects.items():
            result = self.scan_project(name, Path(path), verbose=verbose)
            for k in total:
                total[k] += result[k]
        return total

    def mark_deleted(self) -> int:
        """
        DB에 등록된 문서 중 원본 파일이 실제로 없는 것을 soft_deleted로 마킹.
        반환: soft_deleted 처리된 수
        """
        cur = self.con.execute(
            "SELECT doc_id, abs_path FROM documents WHERE status IN ('active','empty')"
        )
        rows = cur.fetchall()
        deleted = 0
        for doc_id, abs_path in rows:
            if not Path(abs_path).exists():
                self.con.execute(
                    "UPDATE documents SET status='soft_deleted' WHERE doc_id=?",
                    (doc_id,),
                )
                try:
                    delete_fts(self.con, doc_id=doc_id)
                except Exception:
                    pass
                deleted += 1
                logger.info(f"soft_delete: {abs_path}")
        if deleted:
            self.con.commit()
        return deleted
