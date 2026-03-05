# Workspace-Brain 사용자 설명서

이 문서는 “처음 실행 → 프로젝트 추가 → 인덱싱 → 검색/연관문서 활용”까지의 사용 흐름을 정리합니다.

## 1) 설치/실행(Windows)
1. Python 설치(권장: 3.10+)
2. 가상환경 생성
   - `python -m venv .venv`
3. 의존성 설치
   - lite: `.venv\Scripts\pip install -r requirements-lite.txt`
   - full: `.venv\Scripts\pip install -r requirements-full.txt`
4. 실행
   - `.venv\Scripts\python workspace_brain_gui.py`

## 2) 설정 파일(중요)
Workspace-Brain은 설정 파일로 “스캔할 폴더(프로젝트)”와 “저장소 경로(DB/Chroma)”를 관리합니다.

- 기본 템플릿: `config/settings.json` (레포에 포함)
- 로컬 오버라이드: `config/settings.local.json` (있으면 우선 사용, 커밋 제외)
  - UI에서 경로를 바꾸거나 프로젝트를 추가하면 보통 이 파일에 저장하는 흐름을 권장합니다.

### 2.1) 레포 밖에 data/config 저장(권장)
인덱스(`data/`)와 설정(`config/`)이 레포에 쌓이지 않게, 저장소 루트를 외부 폴더로 분리할 수 있습니다.

- 환경변수: `WORKSPACE_BRAIN_ROOT=D:\WB_Data` 또는 `WB_ROOT=D:\WB_Data`
- 인자: `--root D:\WB_Data`

이 옵션을 켜면 기본 경로가 아래처럼 바뀝니다.
- 설정: `D:\WB_Data\config\settings.local.json`(우선) → `D:\WB_Data\config\settings.json`
- DB: `D:\WB_Data\data\metadata.db`
- Chroma: `D:\WB_Data\data\chroma_db`

처음 1회는 `D:\WB_Data\config\` 아래에 `settings.json`이 필요합니다(없으면 레포의 `config/settings.json`을 복사해 시작하면 됩니다).

가장 쉬운 방법(개발/테스트용):
- `run_wb_full_dev.bat gui`
- `run_wb_full_dev.bat pipeline` (리셋→스캔→FTS→벡터→체인)

### settings.json 구조(요약)
- `projects`: 스캔 대상 폴더 목록
- `scanner`: 확장자/스킵 폴더/최대 파일 크기 등 스캐너 정책
- `storage`: 인덱스 저장 위치

## 3) UI 기본 동선(3패널)
- 상단: 검색어 / 모드(FTS·Vector·Hybrid) / 프로젝트 / Limit
- 좌측: 태그 필터(수동 태그)
- 중앙: 결과 테이블(`date | path | tags | score`)
- 우측 탭
  - `미리보기`: 선택 문서 내용
  - `연관문서`: 링크/버전/작업흐름/유사 문서
  - `메타/태그`: 수동 태그 편집(다건 선택 가능)

## 4) 프로젝트(스캔 대상 폴더) 추가/변경
메뉴 `설정 → 환경설정…`에서 관리합니다.

- `프로젝트/폴더` 탭
  - 프로젝트 추가/삭제
  - Enabled 토글
  - 포함/제외 prefix(상대경로) 편집
- `경로` 탭
  - `db_path`: SQLite 메타DB 경로
  - `chroma_dir`: Chroma 영속 폴더
  - `snapshot_root`: Chroma 스냅샷 저장 폴더

## 5) 인덱싱(스캔/FTS/Vector/버전 체인)
메뉴 `설정 → 환경설정… → 인덱싱`에서 한 번에 실행합니다.

권장 순서(처음 1회):
1. `FTS 재구축`(키워드 검색 준비)
2. (선택) `벡터 인덱싱`(자연어 유사 검색)
3. `버전 체인 재구축`(초안/최종 같은 흐름 묶기)

옵션 메모:
- `대형 텍스트 포함`: 큰 파일도 벡터 인덱싱(시간/용량 증가 가능)
- `벡터 강제`: 기존 벡터를 재생성/재업서트(느리지만 정합성 확보)

## 6) 검색 모드 설명
- FTS: SQLite FTS5 기반 키워드 검색(가볍고 빠름)
- Vector: 임베딩 기반 유사 검색(Chroma 필요)
- Hybrid: FTS + Vector를 함께 사용해 결과를 섞어 보여줌(보통 체감 품질이 좋음)

## 7) 연관문서(왜 이 문서가 뜨나)
연관문서는 우측 `연관문서` 탭에서 섹션별로 표시됩니다.

- 문서 내 링크: 본문 링크/위키링크를 따라 같은 인덱스 내 문서로 연결
- 버전/시리즈: `version_chains`가 있으면 그 결과를 우선 사용
  - 없으면 파일명/날짜 기반으로 “추정” 폴백
- 작업 흐름: 날짜 근접(기본 ±7일) + 같은 폴더/태그 교집합으로 랭킹
- 유사 문서: 제목 기반 하이브리드 검색 Top-N

### 버전 체인 오버라이드(UI)
연관문서 탭 하단 버튼으로 체인 품질을 수동 보정할 수 있습니다.
- `체인 Pin…`: 같은 `manual_chain_key`는 같은 체인으로 강제 묶기
- `체인 제외`: 해당 문서를 체인 빌드에서 제외
- `제외 해제`: 제외 플래그 해제
- `오버라이드 삭제`: 수동 보정 레코드 삭제
- 주의: 오버라이드는 즉시 DB에 저장되지만, **체인 결과 반영은 “버전 체인 재구축”을 다시 실행해야 합니다.**

## 8) 유지보수/점검
- 인덱스 정합성 점검(요약):
  - `python validate_index.py`
- 버전 체인 E2E 점검(오버라이드 포함, 기본은 DB 복제본에서만 실행):
  - `python validate_version_chains_e2e.py`
  - `python validate_version_chains_e2e.py --in-place` (주의: 실 DB 수정)

## 9) 문제 해결(자주 겪는 것)
- Chroma가 크래시/손상 의심: 스냅샷/백업 확인 후 `--reset-index` 재인덱싱을 고려
- 검색이 느림: `Vector/Hybrid` 대신 `FTS`로 먼저 확인, 필요 시 벡터 인덱싱 범위를 줄이기
- 파일 잠김/권한 문제: 해당 파일은 quarantine 로그로 빠질 수 있음(원본은 수정하지 않음)
