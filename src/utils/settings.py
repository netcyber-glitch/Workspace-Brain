"""
src/utils/settings.py
Workspace Brain — 설정 로더

- 기본 설정 파일: config/settings.json
- 목적: 감시 경로(프로젝트 루트), 스캐너 포함/제외 규칙을 코드에서 분리
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.utils.runtime import storage_root


ROOT = storage_root()
DEFAULT_SETTINGS_PATH = ROOT / "config" / "settings.json"
DEFAULT_DB_PATH = ROOT / "data" / "metadata.db"
DEFAULT_CHROMA_DIR = ROOT / "data" / "chroma_db"
DEFAULT_SNAPSHOT_ROOT = ROOT / "data" / "backups" / "chroma_snapshots"
DEFAULT_SETTINGS_LOCAL_PATH = ROOT / "config" / "settings.local.json"


def default_settings_path() -> Path:
    """
    기본 settings 경로를 반환합니다.
    - 로컬 환경 전용 설정(config/settings.local.json)이 있으면 그 파일을 우선 사용합니다.
    - 없으면 기본 설정(config/settings.json)을 사용합니다.
    """
    try:
        if DEFAULT_SETTINGS_LOCAL_PATH.exists():
            return DEFAULT_SETTINGS_LOCAL_PATH
    except Exception:
        pass
    return DEFAULT_SETTINGS_PATH


def _normalize_rel_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/").strip("/")
    return p.lower()


def _normalize_ext(ext: str) -> str:
    e = (ext or "").strip().lower()
    if not e:
        return ""
    return e if e.startswith(".") else f".{e}"


def load_settings(settings_path: Path | None = None) -> dict[str, Any]:
    """
    settings.json을 로드합니다.
    - settings_path가 None이면 기본 경로(config/settings.json)를 사용합니다.
    - 로드된 값은 최소 정규화(경로 접두어/확장자/리스트 타입)를 수행합니다.
    """
    path = settings_path or default_settings_path()
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    settings: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}

    # projects 정규화: {name: {root, enabled, ...}} 형태를 보장
    projects_raw = settings.get("projects", {})
    projects: dict[str, Any] = {}
    if isinstance(projects_raw, dict):
        for name, cfg in projects_raw.items():
            if isinstance(cfg, str):
                projects[name] = {"root": cfg, "enabled": True}
            elif isinstance(cfg, dict):
                projects[name] = dict(cfg)
            else:
                continue
    settings["projects"] = projects

    # scanner 정규화
    scanner = settings.get("scanner", {})
    if not isinstance(scanner, dict):
        scanner = {}

    # supported_extensions
    exts_raw = scanner.get("supported_extensions", [])
    if not isinstance(exts_raw, list):
        exts_raw = []
    scanner["supported_extensions"] = [e for e in (_normalize_ext(x) for x in exts_raw) if e]

    # skip_dir_names / skip_dir_prefixes
    for key in ("skip_dir_names", "skip_dir_prefixes"):
        v = scanner.get(key, [])
        if not isinstance(v, list):
            v = []
        scanner[key] = [str(x).strip() for x in v if str(x).strip()]

    # max_file_size_bytes
    try:
        scanner["max_file_size_bytes"] = int(scanner.get("max_file_size_bytes", 0))
    except Exception:
        scanner["max_file_size_bytes"] = 0

    settings["scanner"] = scanner

    # project 별 include/skip rel prefixes 정규화
    for name, cfg in projects.items():
        if not isinstance(cfg, dict):
            continue

        include_raw = cfg.get("include_rel_path_prefixes")
        if include_raw is None:
            include: list[str] = []
        elif isinstance(include_raw, list):
            include = [p for p in (_normalize_rel_prefix(x) for x in include_raw) if p]
        else:
            include = []

        skip_raw = cfg.get("skip_rel_path_prefixes")
        if skip_raw is None:
            skip: list[str] = []
        elif isinstance(skip_raw, list):
            skip = [p for p in (_normalize_rel_prefix(x) for x in skip_raw) if p]
        else:
            skip = []

        cfg["include_rel_path_prefixes"] = include
        cfg["skip_rel_path_prefixes"] = skip

    # storage 경로 정규화:
    # - settings.json에 상대 경로("data/metadata.db")가 들어있는 경우가 많아,
    #   storage_root()를 기준으로 절대 경로로 보정합니다.
    storage = settings.get("storage")
    if not isinstance(storage, dict):
        storage = {}
    for k in ("db_path", "chroma_dir", "snapshot_root"):
        v = storage.get(k)
        if not isinstance(v, str):
            continue
        vv = v.strip().replace("\\", "/")
        if not vv:
            continue
        try:
            p = Path(vv)
            if not p.is_absolute():
                storage[k] = str((ROOT / p).resolve())
            else:
                storage[k] = str(p)
        except Exception:
            # 파싱 실패는 원본 유지
            pass
    settings["storage"] = storage

    # pipeline 프리셋 기본값
    pipeline = settings.get("pipeline")
    if not isinstance(pipeline, dict):
        pipeline = {}

    pipeline_defaults = default_pipeline_settings()
    pipeline.setdefault("confirm_before_run", pipeline_defaults["confirm_before_run"])
    pipeline.setdefault("default_preset", pipeline_defaults["default_preset"])

    presets = pipeline.get("presets")
    if not isinstance(presets, dict):
        presets = {}

    for preset_name, preset_defaults in pipeline_defaults["presets"].items():
        cur = presets.get(preset_name)
        if not isinstance(cur, dict):
            cur = {}
        for k, v in preset_defaults.items():
            cur.setdefault(k, v)
        presets[preset_name] = cur

    pipeline["presets"] = presets
    settings["pipeline"] = pipeline

    return settings


def resolve_enabled_projects(settings: dict[str, Any]) -> dict[str, Path]:
    """
    settings에서 enabled 프로젝트만 뽑아 {프로젝트명: Path(root)}로 반환합니다.
    """
    projects = settings.get("projects", {})
    out: dict[str, Path] = {}
    if not isinstance(projects, dict):
        return out

    for name, cfg in projects.items():
        if isinstance(cfg, dict):
            if cfg.get("enabled", True) is False:
                continue
            root = cfg.get("root")
        else:
            root = None
        if not root:
            continue
        out[str(name)] = Path(str(root))
    return out


def default_storage_settings() -> dict[str, str]:
    return {
        "db_path": str(DEFAULT_DB_PATH.as_posix()),
        "chroma_dir": str(DEFAULT_CHROMA_DIR.as_posix()),
        "snapshot_root": str(DEFAULT_SNAPSHOT_ROOT.as_posix()),
    }


def default_pipeline_settings() -> dict[str, Any]:
    """
    scan_all.py에서 사용할 파이프라인 프리셋 기본값입니다.
    - incremental: 리셋 없이(기존 인덱스 유지) 재인덱싱 위주
    - full: 리셋(영구 삭제) 후 전체 재구축
    """
    return {
        "confirm_before_run": True,
        "default_preset": "incremental",
        "presets": {
            "incremental": {
                "reset_index": False,
                "rebuild_fts": True,
                "index_vectors": True,
                "vector_include_large_text": True,
                "build_version_chains": True,
            },
            "full": {
                "reset_index": True,
                "rebuild_fts": True,
                "index_vectors": True,
                "vector_include_large_text": True,
                "build_version_chains": True,
            },
        },
    }


def save_settings(
    settings: dict[str, Any],
    settings_path: Path | None = None,
    *,
    make_backup: bool = True,
) -> Path:
    """
    settings.json을 저장합니다.
    - 기존 파일이 있으면 .bak_YYYY-MM-DD_HHMMSS 백업을 남깁니다(기본 ON).
    - 임시 파일에 쓴 뒤 replace로 원자적 교체를 시도합니다.
    """
    path = settings_path or DEFAULT_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    if make_backup and path.exists():
        ts = time.strftime("%Y-%m-%d_%H%M%S")
        backup = path.with_name(f"{path.stem}.bak_{ts}{path.suffix}")
        try:
            backup.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        except Exception:
            # 백업 실패는 저장을 막지 않되, 상위에서 처리할 수 있도록 예외는 삼키지 않습니다.
            raise

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
