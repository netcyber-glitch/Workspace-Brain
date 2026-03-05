# Workspace Brain — 환경설정(UI) 추가 보고서

작성: codex 5.2 (2026-03-05 KST)

## 목표 / 성공 기준
- 앱(UI) 안에서 **프로젝트(구조화 대상 폴더)** 를 추가/수정/삭제하고, 포함/제외(prefix) 규칙까지 관리할 수 있다.
- 앱(UI) 안에서 **DB/Chroma 경로**를 바꾸고, 인덱싱(스캔/FTS/벡터/체인)을 실행할 수 있다.
- 설정 저장은 `config/settings.json`에 반영되고, 저장 시 자동 백업이 남는다.

## 변경 사항(파일)
- 신규
  - `D:\Workspace_Brain\src\ui\settings_dialog.py` (환경설정 다이얼로그 + 인덱싱 실행 탭)
  - `D:\Workspace_Brain\docs\2026-03-05_Settings_UI_Report.md` (본 문서)
- 수정
  - `D:\Workspace_Brain\src\utils\settings.py` (`save_settings`, 기본 storage 경로)
  - `D:\Workspace_Brain\src\ui\main_window.py` (메뉴 `설정 → 환경설정…` + 적용 시 경로/프로젝트 리로드)
  - `D:\Workspace_Brain\workspace_brain_gui.py` (`--settings` 추가, settings.json의 storage 경로를 기본값으로 사용)
  - `D:\Workspace_Brain\scan_all.py` (`--db/--chroma-dir/--snapshot-root` 추가 + FileScanner/FTS/Vector/체인에 반영)

## 사용 방법
### 1) 환경설정 열기
- 메인 UI 상단 메뉴: `설정 → 환경설정…`

### 2) 프로젝트/폴더 탭
- **추가**: 프로젝트 이름 입력 → 루트 폴더 선택
- **사용(Enabled)**: 스캔 대상 포함/제외
- **포함(prefix)**: 비우면 전체 포함, 입력 시 해당 상대경로(prefix) 하위만 포함
- **제외(prefix)**: 특정 상대경로(prefix) 하위를 제외

### 3) 경로 탭
- `SQLite DB`, `ChromaDB`, `스냅샷` 경로를 지정하고 `적용`으로 저장
- 저장 시 `config/settings.bak_YYYY-MM-DD_HHMMSS.json` 백업이 자동 생성됩니다.

### 4) 인덱싱 탭
- `실행` 버튼이 내부적으로 `scan_all.py`를 실행합니다.
  - FTS 재구축 / 벡터 인덱싱 / 버전 체인 재구축을 체크로 선택
  - “벡터/체인 대상 프로젝트”는 해당 단계에만 필터로 적용됩니다.
  - 스캔 단계는 **settings.json에서 enabled=true인 프로젝트 전체**가 대상입니다.

## 검증(이번 작업에서 실행)
- 문법 검증
  - `python -m py_compile D:\Workspace_Brain\workspace_brain_gui.py D:\Workspace_Brain\scan_all.py D:\Workspace_Brain\src\ui\main_window.py D:\Workspace_Brain\src\ui\settings_dialog.py D:\Workspace_Brain\src\utils\settings.py`
- 도움말 확인(venv)
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\workspace_brain_gui.py --help`
  - `python D:\Workspace_Brain\scan_all.py --help`

## 메모 / 제한
- 시스템 Python에 `PySide6`가 없으면 `workspace_brain_gui.py` 실행이 실패합니다. (권장: `D:\Workspace_Brain\.venv\Scripts\python.exe`로 실행)

