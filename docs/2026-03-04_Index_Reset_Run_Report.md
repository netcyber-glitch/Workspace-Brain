# Workspace Brain 인덱스 영구삭제 후 재생성 실행 보고서

작성: codex 5.2 (2026-03-04 KST)

## 목표 / 성공 기준
- 불필요 파일이 과다 포함된 인덱스를 **영구삭제**하고, 현재 `settings.json` 기준으로 **처음부터 재색인**한다.
- FTS5(키워드 검색) 인덱스까지 재구축하고, CLI 검색이 정상 동작함을 확인한다.

## 수행 내용(요약)
1) `D:\Workspace_Brain\docs\2026-03-03_Workspace_Brain_Master_Plan.md` 및 관련 문서 확인
2) 스캐너의 include(화이트리스트) 의미를 문서 정의에 맞게 정리
3) `metadata.db*` 영구삭제 → DB 스키마 재초기화 → 전체 스캔 → FTS 재구축
4) `search_cli.py`로 샘플 질의 동작 확인

## 변경 파일
- `D:\Workspace_Brain\src\scanner\scanner.py`
  - `include_rel_path_prefixes`를 “디렉터리/파일 모두”에 대해 일관된 화이트리스트로 적용하도록 수정
  - `data/archive` 같은 하위 포함 경로를 위해 상위 디렉터리(예: `data/`)는 탐색 가능하도록 처리

## 실행 명령 / 결과
- DB 영구삭제(파이썬으로 삭제)
  - `D:\Workspace_Brain\data\metadata.db`
  - `D:\Workspace_Brain\data\metadata.db-wal`
  - `D:\Workspace_Brain\data\metadata.db-shm`
- DB 초기화
  - `python D:\Workspace_Brain\src\db\init_db.py`
- 재색인 + FTS 재구축
  - `python D:\Workspace_Brain\scan_all.py --settings D:\Workspace_Brain\config\settings.json --rebuild-fts`
  - 결과(요약):
    - 활성 문서: 4,916건
    - 프로젝트별: MRA 4,724 / Where_AI 111 / AI_Music_Hub 50 / Rental 23 / Workspace_Brain 8
    - 격리 기록: 0건
    - FTS indexed: 4,916건, missing_files: 0건
- 샘플 검색 확인
  - `python D:\Workspace_Brain\search_cli.py "USI 대안" --project MRA --limit 10` (정상 출력 확인)

## 참고 / 남은 이슈
- 이번 변경으로 `include_rel_path_prefixes`가 “엄격한 화이트리스트”로 동작합니다.
  - 포함 목록에 없는 **루트 파일(예: `package.json`, `AGENTS.md`)은 기본 제외**됩니다.
  - 루트 파일도 필요하면 `include_rel_path_prefixes`에 파일명을 추가하는 방식으로 포함시킬 수 있습니다.

