# Workspace Brain — PySide6 데스크톱 UI(MVP) 적용 보고서

작성: codex 5.2 (2026-03-05 KST)

## 목표 / 성공 기준
- Streamlit이 아닌 **데스크톱 UI(PySide6)** 로 검색/열람을 빠르게 수행한다.
- 결과 테이블은 고정 컬럼 `date | path | tags | score` 기반으로 표시한다.
- **수동 태그**를 DB에 저장하고(다건 선택), 태그로 필터링할 수 있다.
- 문서 선택 시 **미리보기 + 연관문서**를 우측 탭에서 즉시 확인할 수 있다.

## 변경 사항(파일)
- 신규
  - `D:\Workspace_Brain\workspace_brain_gui.py` (UI 실행 엔트리)
  - `D:\Workspace_Brain\build_version_chains.py` (`version_chains` 구축 CLI)
  - `D:\Workspace_Brain\version_chain_overrides.py` (`version_chain_overrides` 관리 CLI)
  - `D:\Workspace_Brain\src\ui\main_window.py` (메인 윈도우)
  - `D:\Workspace_Brain\src\ui\backend.py` (검색/미리보기/연관문서 백엔드)
  - `D:\Workspace_Brain\src\ui\result_model.py` (가상화 테이블 모델)
  - `D:\Workspace_Brain\src\ui\filter_proxy.py` (태그 필터 프록시)
  - `D:\Workspace_Brain\src\db\tags.py` (수동 태그 DAO)
- 수정
  - `D:\Workspace_Brain\requirements.txt` (`PySide6` 추가)
  - `D:\Workspace_Brain\src\db\schema.py` (`doc_tags`, `version_chain_overrides` 테이블/인덱스 추가)
  - `D:\Workspace_Brain\src\search\fts_search.py` (date_prefix 포함)
  - `D:\Workspace_Brain\src\search\vector_search.py` (date_prefix 포함)
  - `D:\Workspace_Brain\src\search\hybrid_search.py` (date_prefix 포함)

## UI 구성(요약)
- 3패널 + 우측 탭
  - 좌측: 수동 태그 기반 필터(체크박스)
  - 중앙: 결과 테이블(`date | path | tags | score`)
  - 우측 탭: `미리보기 | 연관문서 | 메타/태그`

- 환경설정(앱 내 관리)
  - 메뉴: `설정 → 환경설정…`
  - 프로젝트(구조화 대상 폴더) 추가/수정, DB/Chroma 경로 변경, 인덱싱 실행까지 앱에서 처리합니다.
  - 상세: `D:\Workspace_Brain\docs\2026-03-05_Settings_UI_Report.md`

## 연관문서(현재 MVP)
- 문서 내 링크: Markdown 링크/위키 링크(`[[...]]`)를 파싱해 인덱스된 문서로 연결
- 버전/시리즈: `version_chains`가 구축돼 있으면 그 결과를 사용(우선). 비어 있으면 **파일명 기반 추정**으로 폴백
- 작업 흐름: 기본 ±7일 범위에서 같은 프로젝트 문서 중 “같은 폴더/태그 교집합/날짜 근접”으로 랭킹
- 유사 문서: 선택 문서의 제목으로 하이브리드 검색을 수행해 Top-N 노출(의존성/Chroma 오류 시 자동 스킵)
- 버전 체인 오버라이드(UI): 연관문서 탭에서 `pin/exclude/include/clear`로 체인 품질을 수동 보정 (상세: `D:\Workspace_Brain\docs\2026-03-05_Version_Chain_Override_UI_Report.md`)

## 수동 태그(저장/필터/다건 적용)
- DB 테이블: `doc_tags(doc_id, tag)`
- 결과 리스트에서 다중 선택 후, 우측 `메타/태그` 탭 또는 단축키로 일괄 적용
  - 추가: 입력 후 `태그 추가` 또는 `T`
  - 삭제: 입력 후 `태그 삭제`

## 설치 / 실행 방법
### 1) 의존성 설치(1회)
- `D:\Workspace_Brain\.venv\Scripts\python.exe -m pip install -r D:\Workspace_Brain\requirements.txt`

### 2) 실행
- `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\workspace_brain_gui.py`

## 단축키(현재)
- `Ctrl+K`: 검색창 포커스
- (결과 테이블 포커스에서) `Enter`: 파일 열기
- (결과 테이블 포커스에서) `Ctrl+Enter`: 폴더 열기
- (결과 테이블 포커스에서) `T`: 수동 태그 추가(선택 문서 다건 적용)

## 제한/리스크(현 시점)
- “취소”는 실행 중인 스레드를 강제 종료하지 않고, **새 검색이 오면 이전 결과를 폐기**하는 방식(세대 번호)으로 처리합니다.
- 유사 문서(하이브리드)는 내부에서 임베딩 모델을 로드하므로, 첫 실행/저사양 환경에서는 느릴 수 있습니다.
- 버전/시리즈는 `version_chains`가 없을 때만 “파일명 기반 추정”으로 동작합니다. 실제 체인을 쓰려면 아래 CLI로 구축해야 합니다.

## version_chains 구축(권장)
- 스캔/인덱싱 후 1회 실행(프로젝트별 가능)
  - UI: `설정 → 환경설정… → 인덱싱 → 버전 체인 재구축 → 실행`
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\build_version_chains.py --project MRA --verbose`
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\build_version_chains.py --project Workspace_Brain --verbose`
- 전체 재구축(주의: version_chains 전체 삭제 후 재삽입)
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\build_version_chains.py --full --verbose`

- 참고: `build_version_chains.py`는 파일명/날짜 조건 외에 **내용 유사도(코사인)** 로 체인 품질을 높입니다. (자세한 내용/수동 오버라이드는 `D:\Workspace_Brain\docs\2026-03-05_Version_Chain_Quality_Upgrade_Report.md` 참고)
