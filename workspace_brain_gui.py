"""
workspace_brain_gui.py
Workspace Brain — PySide6 데스크톱 UI 실행 엔트리

예:
  D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\workspace_brain_gui.py
  D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\workspace_brain_gui.py --project MRA
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

def _preparse_root(argv: list[str]) -> str | None:
    """
    --root는 import 시점(settings/runtime 상수 계산) 전에 필요할 수 있어,
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

def _runtime_root() -> Path:
    if bool(getattr(sys, "frozen", False)):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return Path(sys.executable).parent
    return Path(__file__).resolve().parent


_maybe_root = _preparse_root(list(sys.argv[1:]))
if _maybe_root:
    os.environ["WORKSPACE_BRAIN_ROOT"] = _maybe_root

ROOT = _runtime_root()
if not bool(getattr(sys, "frozen", False)):
    sys.path.insert(0, str(ROOT))

from src.ui.main_window import run_gui  # noqa: E402
from src.utils.settings import default_settings_path, default_storage_settings, load_settings  # noqa: E402


def main() -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    # 설정에서 root="." 같은 상대 경로를 쓰는 경우를 위해,
    # 실행 위치와 무관하게 작업 디렉터리를 프로젝트 루트로 고정합니다.
    os.chdir(str(ROOT))

    p = argparse.ArgumentParser(description="Workspace Brain 데스크톱 UI(PySide6)")
    p.add_argument("--root", type=str, default="", help="config/data 루트 오버라이드(예: D:\\WB_Data)")
    p.add_argument("--settings", type=str, default="", help="설정 파일 경로(비우면 config/settings.local.json 우선)")
    p.add_argument("--db", type=str, default="", help="metadata.db 경로(비우면 settings.json의 storage.db_path)")
    p.add_argument("--chroma-dir", type=str, default="", help="ChromaDB 영속 디렉터리(비우면 settings.json의 storage.chroma_dir)")
    args = p.parse_args()

    if str(args.root or "").strip():
        os.environ["WORKSPACE_BRAIN_ROOT"] = str(args.root).strip()

    settings_arg = str(args.settings or "").strip()
    if settings_arg:
        settings_path = Path(settings_arg)
    else:
        settings_path = default_settings_path()
    settings = load_settings(settings_path) if settings_path.exists() else {}

    defaults = default_storage_settings()
    storage = settings.get("storage") if isinstance(settings.get("storage"), dict) else {}

    db_s = str(args.db or "").strip() or str(storage.get("db_path") or defaults["db_path"])
    chroma_s = str(args.chroma_dir or "").strip() or str(storage.get("chroma_dir") or defaults["chroma_dir"])
    snap_s = str(storage.get("snapshot_root") or defaults["snapshot_root"])

    db_path = Path(db_s)
    chroma_dir = Path(chroma_s)
    snapshot_root = Path(snap_s)

    return int(run_gui(settings_path=settings_path, db_path=db_path, chroma_dir=chroma_dir, snapshot_root=snapshot_root))


if __name__ == "__main__":
    raise SystemExit(main())
