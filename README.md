# Workspace-Brain
여러 프로젝트에 산재한 개발 문서/기획서/태스크 노트 등을 한 곳에서 수집·인덱싱하여 **자연어 검색(FTS/Vector/Hybrid)** 과 **문서 간 연관 관계(버전 체인/링크/유사도)** 를 추적하는 로컬 독립형 “개인 지식 베이스”입니다.

## 문서
- 사용자 설명서: `docs/USER_GUIDE.md`
- EXE/포터블/배포 메모: `docs/PACKAGING.md`

## 빠른 시작(Windows 기준)
1) 가상환경 생성
   - `python -m venv .venv`
2) 의존성 설치
   - `.venv\\Scripts\\pip install -r requirements.txt`
3) 실행
   - `.venv\\Scripts\\python workspace_brain_gui.py`

## 설정 파일
- 기본: `config/settings.json` (레포 포함)
- 로컬 오버라이드: `config/settings.local.json` (있으면 우선 사용, `.gitignore` 처리)
- 앱 내에서: `설정 → 환경설정…`에서 프로젝트(폴더) 추가/경로 변경/인덱싱 실행

## 인덱스/아티팩트
- SQLite/Chroma 인덱스는 기본적으로 `data/` 아래에 생성됩니다.
- `data/`, `tmp/`, `logs/`, `.venv/`는 커밋 대상이 아니며 `.gitignore`로 제외되어 있습니다.

## 라이선스
- GPL-3.0 (`LICENSE`)
