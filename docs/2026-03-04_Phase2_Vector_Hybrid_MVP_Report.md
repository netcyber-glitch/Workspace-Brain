# Workspace Brain Phase 2(MVP) — 벡터(Chroma) + 하이브리드(RRF) 검색 적용 보고서

작성: codex 5.2 (2026-03-04 KST)
추가 업데이트: codex 5.2 (2026-03-05 KST) — empty 마킹/다국어 임베딩/스냅샷+헬스체크 고정/대형 텍스트 인덱싱 옵션

## 목표 / 성공 기준
- SQLite FTS5(키워드) 외에 **벡터 검색(ChromaDB)** 을 추가한다.
- CLI에서 **FTS / Vector / Hybrid(RRF)** 3가지 모드를 선택해 검색할 수 있다.
- `validate_index.py`로 SQLite chunks ↔ Chroma 벡터 정합성을 샘플 기반으로 점검할 수 있다.
- (안정성) 벡터 인덱싱 실행 전/후에 **Chroma 스냅샷/health-check(count)** 로 손상 여부를 빠르게 감지·복구할 수 있다.

## 결정 사항(중요)
- Windows 환경에서 `sentence-transformers` 설치가 사용자 site-packages 경로에서 **경로 길이 제한(MAX_PATH)** 으로 실패할 수 있어,
  `D:\Workspace_Brain\.venv\` 가상환경을 생성해 그 안에 설치하는 방식으로 고정했습니다.

## 변경/추가 파일
- 신규
  - `D:\Workspace_Brain\src\indexer\chunking.py` (표준 라이브러리 기반 청킹)
  - `D:\Workspace_Brain\src\indexer\vector_indexer.py` (청킹+임베딩+Chroma 업서트)
  - `D:\Workspace_Brain\index_vectors.py` (벡터 인덱싱 CLI)
  - `D:\Workspace_Brain\src\search\vector_search.py` (벡터 검색 → 문서 단위 집계)
  - `D:\Workspace_Brain\src\search\hybrid_search.py` (RRF 하이브리드 검색)
  - `D:\Workspace_Brain\validate_index.py` (정합성 점검 CLI)
  - `D:\Workspace_Brain\chroma_health_check.py` (Chroma count health-check, 별도 프로세스)
- 수정
  - `D:\Workspace_Brain\search_cli.py` (`--mode fts|vector|hybrid` 지원)
  - `D:\Workspace_Brain\scan_all.py` (`--index-vectors` 옵션 추가)
  - `D:\Workspace_Brain\src\scanner\scanner.py` (빈 파일 empty 마킹)
  - `D:\Workspace_Brain\src\db\schema.py` (EMBED_MODEL_ID 다국어 모델로 전환)
  - `D:\Workspace_Brain\index_vectors.py` (스냅샷/health-check/서브프로세스 안전 실행)

## 실행 방법(권장 명령)
### 1) 가상환경(1회)
- 생성: `python -m venv D:\Workspace_Brain\.venv`
- 설치: `D:\Workspace_Brain\.venv\Scripts\python.exe -m pip install chromadb sentence-transformers`

### 2) 벡터 인덱싱
- (예: Workspace_Brain 문서만)  
  `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\index_vectors.py --project Workspace_Brain --exts .md --limit-docs 20 --verbose`
- (예: MRA 문서 일부만 스모크)  
  `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\index_vectors.py --project MRA --exts .md --limit-docs 60 --verbose`
- (전체 실행) `--limit-docs`를 제거하거나 0으로 둡니다.
- (대형 텍스트 포함) 아래 중 하나를 사용합니다.
  - `--include-large-text` (권장, = `--max-file-chars 0`)
  - `--max-file-chars 0`

### 3) 검색 CLI
- FTS: `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\search_cli.py "USI 대안" --mode fts --project MRA --limit 10`
- Vector: `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\search_cli.py "인덱스 리셋" --mode vector --project Workspace_Brain --limit 5`
- Hybrid(RRF): `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\search_cli.py "인덱스 리셋" --mode hybrid --project Workspace_Brain --limit 5`

### 4) 정합성 점검
- `D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\validate_index.py --project Workspace_Brain --exts .md --sample 100`

## 실행 결과(이번 세션)
- (업데이트) 임베딩 모델: `paraphrase-multilingual-MiniLM-L12-v2` (다국어)
- (업데이트) Workspace_Brain(문서 10개) 벡터 인덱싱: 청크 58개 생성, `validate_index.py` 샘플 검사에서 누락 0건 확인
- (업데이트) MRA(전체 .md/.txt) 벡터 인덱싱: eligible 2,125문서 / indexed 2,125, 청크 21,715개 생성/업서트
  - empty 마킹으로 “내용이 비었거나(0 bytes) 사실상 공백” 문서 4개는 `documents.status=empty`로 분리되어 분모(eligible)에서 제외됨
- (업데이트) Chroma count(컬렉션): `workspace_brain_chunks` = 21,773
- (업데이트) 벡터 인덱싱은 `index_vectors.py`에서 기본으로 아래를 고정 실행:
  - (1) Chroma 스냅샷(백업) (2) health-check(before) (3) 별도 프로세스에서 인덱싱(크래시 격리) (4) health-check(after)
  - health-check(after) 실패 시: 가능하면 스냅샷으로 자동 복구 시도

## 실행 팁(Windows 인코딩)
- `.venv` 파이썬에서 콘솔 출력이 깨지거나(문자 깨짐) `UnicodeEncodeError`가 나면, 아래처럼 `PYTHONUTF8=1`을 켜고 실행하면 안정적입니다.
  - PowerShell 예: `$env:PYTHONUTF8=1; D:\Workspace_Brain\.venv\Scripts\python.exe D:\Workspace_Brain\validate_index.py --project MRA --exts .md,.txt --sample 200`

## 남은 작업(Phase 2 확장)
- MRA 전체(또는 `docs` 중심) 벡터 인덱싱을 실제로 돌려 “하이브리드 품질”을 정량 확인
- 청크 정책(길이/겹침/마크다운 헤더 기반 분할) 고도화
- 모델 로딩/로그 출력(현재 SentenceTransformer 로딩 로그가 다소 큼) 정리
  - (운영) 스냅샷 폴더 보관 정책(개수/기간) 확정 또는 정리 스크립트 추가
