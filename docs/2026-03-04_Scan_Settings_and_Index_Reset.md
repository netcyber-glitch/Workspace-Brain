# Workspace Brain 스캔 설정(settings.json) & 인덱스 리셋 가이드

작성: codex 5.2 (2026-03-04 KST)

## 목적
- 불필요한 산출물/아티팩트(예: `benchmarks/`, `test-results/`, `.agent/` 등)가 인덱스에 섞여 들어가는 문제를 방지합니다.
- 프로젝트별로 “포함/제외” 규칙을 고정해, 스캔 품질(분모)을 안정화합니다.
- 인덱스가 오염되면 **영구 삭제 후 재생성**할 수 있게 합니다.

---

## 1) 설정 파일 위치
- 기본 설정 파일: `D:\Workspace_Brain\config\settings.json`

이 파일을 수정하면, `scan_all.py` 실행 시 스캔 대상/제외 규칙이 즉시 반영됩니다.

---

## 2) 인덱스 영구 삭제 + 재생성(권장)
아래 명령은 기존 인덱스를 **영구 삭제**하고(`metadata.db`, `chroma_db/`), DB 스키마를 재초기화한 뒤 재스캔합니다.

- `python D:\Workspace_Brain\scan_all.py --reset-index`

또는 설정 파일을 명시할 수도 있습니다.

- `python D:\Workspace_Brain\scan_all.py --settings D:\Workspace_Brain\config\settings.json --reset-index`

---

## 2.1) 키워드(FTS5) 인덱스 재구축
키워드 검색을 위해 SQLite FTS5 인덱스를 재구축합니다.

- `python D:\Workspace_Brain\scan_all.py --rebuild-fts`

참고:
- 과거 스키마(contentless)로 생성된 DB라도, 실행 시 `documents_fts`만 자동 재생성(저장형) 후 재구축합니다.

---

## 2.2) 키워드 검색 CLI
FTS5 키워드 검색을 CLI로 실행합니다.

- `python D:\Workspace_Brain\search_cli.py "USI 대안" --project MRA --limit 10`

---

## 3) 프로젝트별 스캔 범위(분모) 통제
`settings.json`의 `projects` 아래에 프로젝트 단위 설정이 있습니다.

필드 의미:
- `root`: 스캔 루트 폴더(절대 경로 권장)
- `enabled`: `false`면 해당 프로젝트 스캔 제외
- `include_rel_path_prefixes`: 이 리스트가 비어있지 않으면, 해당 prefix 하위만 스캔(= 화이트리스트)
- `skip_rel_path_prefixes`: 해당 prefix 하위는 스캔 제외(= 블랙리스트)

예시(현재 구성 아이디어):
- MRA: `docs/src/scripts/tests`만 포함 + `public` 제외 → 생성물·정적 리소스가 인덱스에 섞이는 문제를 줄임
- 다른 프로젝트: 필요 시 `include_rel_path_prefixes`를 점진적으로 좁혀가며 튜닝

---

## 4) 전역 스캐너 제외 규칙(아티팩트 방지)
`settings.json`의 `scanner`는 모든 프로젝트에 공통 적용됩니다.

필드 의미:
- `supported_extensions`: 인덱싱할 확장자 목록
- `max_file_size_bytes`: 이 크기(바이트) 초과 파일은 스킵(기본 10MB)
- `skip_dir_names`: 디렉터리 이름이 일치하면 하위 전체 스킵
- `skip_dir_prefixes`: 디렉터리 이름이 해당 접두어로 시작하면 하위 전체 스킵(예: `dist-*`, `.tmp.*`)

권장 운영:
- 처음엔 `skip_dir_names`를 넓게 잡고(생성물 방지), 필요한 폴더만 `include_rel_path_prefixes`로 점진 확대
- “인덱스가 갑자기 커짐/검색 품질이 나빠짐”이 느껴지면 `--reset-index`로 리셋 후 규칙을 조정

---

## 5) 현재 상태(참고)
- 현재 인덱스 저장 위치
  - SQLite: `D:\Workspace_Brain\data\metadata.db`
  - Chroma 디렉터리: `D:\Workspace_Brain\data\chroma_db\` (Phase 2에서 실제 벡터 인덱싱이 채워질 예정)

---

## 6) ChatGPT export -> Markdown 변환(아카이브)
ChatGPT Data Export ZIP(또는 `conversations.json`)을 `.md`로 변환해 `data/archive`에 물리 저장하는 도구입니다.

- `python D:\Workspace_Brain\import_chatgpt_export.py --input D:\path\to\export.zip`

기본 출력 위치:
- `D:\Workspace_Brain\data\archive\chatgpt\YYYY-MM\YYYY-MM-DD_ChatGPT_제목_id8.md`

