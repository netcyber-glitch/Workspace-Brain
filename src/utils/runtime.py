"""
src/utils/runtime.py
Workspace Brain — 실행 환경(frozen/소스)별 런타임 경로 유틸
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """
    PyInstaller 등으로 패키징된(frozen) 실행 파일인지 여부.
    """
    return bool(getattr(sys, "frozen", False))


def runtime_root() -> Path:
    """
    코드/실행 파일 기준 루트.

    - 소스 실행: 레포 루트 (이 파일 기준 3단계 상위)
    - frozen 실행: 실행 파일(.exe)이 있는 폴더
    """
    if is_frozen():
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def storage_root() -> Path:
    """
    설정/데이터(config,data) 저장/로드 기준 루트.

    - 기본: runtime_root()
    - 오버라이드(선택): 환경변수로 강제
        - WORKSPACE_BRAIN_ROOT
        - WB_ROOT
    """
    raw = (os.environ.get("WORKSPACE_BRAIN_ROOT") or os.environ.get("WB_ROOT") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        try:
            return p.resolve()
        except Exception:
            return p
    return runtime_root()


def tool_cmd(*, root: Path, stem: str, script_name: str) -> list[str]:
    """
    동일 프로젝트 내 다른 도구를 서브프로세스로 실행하기 위한 command를 만듭니다.

    - 소스 실행: [sys.executable, <root>/<script_name>]
    - frozen 실행: [<runtime_root>/<stem>.exe]
    """
    if not stem or not script_name:
        raise ValueError("stem/script_name이 비어 있습니다.")

    if is_frozen():
        exe = root / f"{stem}.exe"
        return [str(exe)]

    script = (root / script_name).resolve()
    return [sys.executable, str(script)]
