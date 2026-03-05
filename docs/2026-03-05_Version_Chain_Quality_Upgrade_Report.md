# Workspace Brain — 버전 체인(Version chain) 품질 고도화 보고서

작성: codex 5.2 (2026-03-05 KST)

## 목표 / 성공 기준
- `version_chains` 구축 로직을 “파일명만”이 아니라 **파일명 유사도 + 날짜 근접 + 내용 유사도(코사인)** 기준으로 고도화한다.
- 체인이 잘못 묶이거나 끊긴 경우를 위해 **수동 오버라이드(강제 묶기/제외)** 를 DB에 저장할 수 있게 한다.
- `scan_all.py`에서 스캔 이후 **자동으로 version_chains를 재구축**할 수 있는 옵션을 제공한다.

## 변경 사항(파일)
- 수정
  - `D:\Workspace_Brain\src\db\schema.py`
    - `version_chain_overrides` 테이블 + 인덱스 추가
  - `D:\Workspace_Brain\build_version_chains.py`
    - 자동 체인 로직 고도화(파일명 유사도/날짜/내용 유사도)
    - 체인 분할 정책 추가(연속 버전 간 조건 불충족 시 split)
    - 수동 오버라이드 반영(`version_chain_overrides`)
    - CLI 옵션 추가(임계값/내용 유사도 on/off 등)
  - `D:\Workspace_Brain\scan_all.py`
    - 스캔 후 `build_version_chains.py`를 실행하는 옵션 추가
- 신규
  - `D:\Workspace_Brain\version_chain_overrides.py`
    - 수동 오버라이드 관리 CLI(`pin/exclude/include/clear/list`)

## 스키마(추가)
- `version_chain_overrides`
  - `manual_chain_key`: 같은 키면 **수동 체인으로 강제 묶기**
  - `exclude_from_chains=1`: **체인 빌드에서 제외**

## 자동 체인(정책) 요약
- 후보 조건(기본값)
  - 날짜 차이 ≤ 14일(`--max-day-gap`)
  - 파일명 유사도 ≥ 0.70(`--filename-sim-threshold`)
  - 내용 코사인 유사도 ≥ 0.75(`--content-sim-threshold`, 기본 ON)
- 구현 메모
  - 내용 유사도는 Chroma가 아니라 **파일 본문(앞부분) 임베딩**으로 계산합니다.
  - `--no-content-sim`으로 내용 유사도 계산을 끌 수 있습니다.

## 수동 오버라이드 CLI 사용
- 목록
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\version_chain_overrides.py list --project MRA`
- 강제 묶기(pin)
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\version_chain_overrides.py pin --path <파일경로> --key <manual_chain_key>`
- 제외/해제
  - `... exclude --path <파일경로>`
  - `... include --path <파일경로>`
- 삭제
  - `... clear --path <파일경로>`

## scan_all.py 연동 사용
- 예시(스캔 + FTS 재구축 + 벡터 인덱싱 + 버전 체인 재구축)
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\scan_all.py --rebuild-fts --index-vectors --build-version-chains --version-chain-project MRA`

## 검증(이번 작업에서 실제 실행)
- 문법 검증
  - `python -m py_compile D:\Workspace_Brain\build_version_chains.py D:\Workspace_Brain\scan_all.py D:\Workspace_Brain\version_chain_overrides.py D:\Workspace_Brain\src\db\schema.py`
- 드라이런(시스템 Python: sentence-transformers 미설치 → 내용 유사도 자동 OFF)
  - `python D:\Workspace_Brain\build_version_chains.py --project MRA --dry-run --verbose`
- 드라이런(가상환경 Python: 내용 유사도 ON)
  - `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\build_version_chains.py --project MRA --dry-run --verbose`

## 남은 이슈 / 다음 작업 후보
- `build_version_chains.py`는 기본적으로 edge 필터 통계를 **요약(`edge_filter_summary`)**으로만 출력합니다. 그룹별 상세 로그가 필요하면 `--debug-edge-filter`를 사용합니다.
- (옵션) UI에서 체인 오류를 발견했을 때 `manual_chain_key`를 바로 설정/해제하는 기능(버튼/컨텍스트 메뉴)을 추가할 수 있습니다.
