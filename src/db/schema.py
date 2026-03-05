"""
src/db/schema.py
Workspace Brain — SQLite 스키마 정의 + SSOT ID 생성 유틸

§3.0 데이터 계약(SSOT) 구현:
  - doc_id  = sha256(정규화 경로)  # 파일 1개를 대표하는 안정 ID
  - chunk_id = sha256(doc_id + chunk_index + chunk_text_hash)
  - edge_id  = sha256(src_doc_id + tgt_doc_id + edge_type)
  - chain_id = sha256(정규화 주제명)

모든 레코드에 schema_version, indexer_version, embed_model_id 포함.
"""

import hashlib
import os
import re
from pathlib import Path

# ─── 버전 상수 ──────────────────────────────────────────────────────────────
SCHEMA_VERSION  = "1.0"
INDEXER_VERSION = "0.1.0"
EMBED_MODEL_ID  = "paraphrase-multilingual-MiniLM-L12-v2"   # 기본 임베딩 모델(다국어)

# ─── DDL (CREATE TABLE) ─────────────────────────────────────────────────────
DDL_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,       -- sha256 기반 결정적 ID
    abs_path        TEXT NOT NULL UNIQUE,   -- 정규화된 절대 경로
    rel_path        TEXT,                   -- 루트 기준 상대 경로
    filename        TEXT NOT NULL,          -- 파일명
    project         TEXT,                   -- 부모 프로젝트명 (예: MRA)
    ext             TEXT,                   -- 파일 확장자
    file_size       INTEGER,                -- 바이트
    mtime           REAL,                   -- 수정 시각 (Unix timestamp)
    ctime           REAL,                   -- 생성 시각 (Unix timestamp)
    sha256          TEXT NOT NULL,          -- 파일 내용 해시 (무결성 검증용)
    title           TEXT,                   -- H1 heading 또는 파일명
    date_prefix     TEXT,                   -- 파일명의 YYYY-MM-DD 접두어
    status          TEXT DEFAULT 'active',  -- active | empty | soft_deleted | quarantined
    schema_version  TEXT DEFAULT '1.0',
    indexer_version TEXT,
    embed_model_id  TEXT,
    indexed_at      REAL,                   -- 인덱싱 시각
    created_at      REAL DEFAULT (unixepoch())
);
"""

DDL_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,       -- sha256 기반 결정적 ID
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
    chunk_index     INTEGER NOT NULL,       -- 문서 내 순서 (0-index)
    text_hash       TEXT NOT NULL,          -- chunk 텍스트의 sha256
    char_start      INTEGER,               -- 원본 문서 내 문자 시작 위치
    char_end        INTEGER,               -- 원본 문서 내 문자 끝 위치
    token_count     INTEGER,               -- 대략적 토큰 수
    status          TEXT DEFAULT 'active',  -- active | soft_deleted
    schema_version  TEXT DEFAULT '1.0',
    embed_model_id  TEXT,
    indexed_at      REAL,
    created_at      REAL DEFAULT (unixepoch())
);
"""

DDL_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    edge_id         TEXT PRIMARY KEY,       -- sha256 기반 결정적 ID
    src_doc_id      TEXT NOT NULL REFERENCES documents(doc_id),
    tgt_doc_id      TEXT NOT NULL REFERENCES documents(doc_id),
    edge_type       TEXT NOT NULL,          -- explicit | implicit | version_chain | same_project
    weight          REAL DEFAULT 0.5,       -- 가중치 (0.0 ~ 1.0)
    similarity      REAL,                   -- 코사인 유사도 (implicit 엣지에서 사용)
    created_at      REAL DEFAULT (unixepoch())
);
"""

DDL_VERSION_CHAINS = """
CREATE TABLE IF NOT EXISTS version_chains (
    chain_id        TEXT NOT NULL,          -- sha256(정규화 주제명)
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
    version_order   INTEGER,                -- 체인 내 순서 (1, 2, 3...)
    created_at      REAL DEFAULT (unixepoch()),
    PRIMARY KEY (chain_id, doc_id)
);
"""

DDL_VERSION_CHAIN_OVERRIDES = """
CREATE TABLE IF NOT EXISTS version_chain_overrides (
    doc_id               TEXT PRIMARY KEY REFERENCES documents(doc_id),
    manual_chain_key     TEXT,                   -- 수동 체인 키(같으면 같은 체인으로 강제 묶음)
    exclude_from_chains  INTEGER DEFAULT 0,      -- 1이면 자동/수동 체인 모두에서 제외
    note                 TEXT,
    updated_at           REAL DEFAULT (unixepoch()),
    created_at           REAL DEFAULT (unixepoch())
);
"""

DDL_DOC_TAGS = """
CREATE TABLE IF NOT EXISTS doc_tags (
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id),
    tag         TEXT NOT NULL,
    created_at  REAL DEFAULT (unixepoch()),
    PRIMARY KEY (doc_id, tag)
);
"""


DDL_QUARANTINE_LOG = """
CREATE TABLE IF NOT EXISTS quarantine_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    abs_path        TEXT NOT NULL,
    reason          TEXT NOT NULL,          -- 실패 사유
    error_detail    TEXT,                   -- 상세 에러 메시지
    retry_count     INTEGER DEFAULT 0,
    logged_at       REAL DEFAULT (unixepoch())
);
"""

DDL_API_USAGE_LOG = """
CREATE TABLE IF NOT EXISTS api_usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_date     TEXT NOT NULL,          -- YYYY-MM-DD
    provider        TEXT NOT NULL,          -- openai | google | anthropic | ollama
    model           TEXT NOT NULL,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,       -- 계산된 비용 ($)
    prompt_summary  TEXT,                   -- 질의 요약 (내용 아님)
    created_at      REAL DEFAULT (unixepoch())
);
"""

DDL_RECONCILIATION_LOG = """
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          REAL DEFAULT (unixepoch()),
    total_files     INTEGER,
    matched         INTEGER,
    fixed           INTEGER,
    soft_deleted    INTEGER,
    reindexed       INTEGER,
    errors          INTEGER,
    log_path        TEXT
);
"""

# FTS5 가상 테이블 (한국어 포함 키워드 검색용)
DDL_FTS5 = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
USING fts5(
    doc_id UNINDEXED,
    title,
    content,
    tokenize='unicode61'
);
"""

# 인덱스
DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_doc_abs_path  ON documents(abs_path);",
    "CREATE INDEX IF NOT EXISTS idx_doc_project   ON documents(project);",
    "CREATE INDEX IF NOT EXISTS idx_doc_status    ON documents(status);",
    "CREATE INDEX IF NOT EXISTS idx_doc_date      ON documents(date_prefix);",
    "CREATE INDEX IF NOT EXISTS idx_chunk_doc     ON chunks(doc_id);",
    "CREATE INDEX IF NOT EXISTS idx_doc_tags_doc  ON doc_tags(doc_id);",
    "CREATE INDEX IF NOT EXISTS idx_doc_tags_tag  ON doc_tags(tag);",
    "CREATE INDEX IF NOT EXISTS idx_edge_src      ON edges(src_doc_id);",
    "CREATE INDEX IF NOT EXISTS idx_edge_tgt      ON edges(tgt_doc_id);",
    "CREATE INDEX IF NOT EXISTS idx_chain_id      ON version_chains(chain_id);",
    "CREATE INDEX IF NOT EXISTS idx_vco_chain_key ON version_chain_overrides(manual_chain_key);",
    "CREATE INDEX IF NOT EXISTS idx_vco_exclude   ON version_chain_overrides(exclude_from_chains);",
    "CREATE INDEX IF NOT EXISTS idx_api_date      ON api_usage_log(logged_date);",
]

ALL_DDL = [
    DDL_DOCUMENTS,
    DDL_CHUNKS,
    DDL_EDGES,
    DDL_VERSION_CHAINS,
    DDL_VERSION_CHAIN_OVERRIDES,
    DDL_DOC_TAGS,
    DDL_QUARANTINE_LOG,
    DDL_API_USAGE_LOG,
    DDL_RECONCILIATION_LOG,
    DDL_FTS5,
    *DDL_INDEXES,
]


# ─── SSOT ID 생성 함수 (§3.0.1) ─────────────────────────────────────────────

def _sha256(*parts: str) -> str:
    """여러 문자열을 NUL 구분자로 이어 SHA-256 해시를 반환."""
    combined = "\x00".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def normalize_path(path: str | os.PathLike) -> str:
    """
    절대 경로를 정규화:
      - 소문자 변환 (Windows 파일시스템 대소문자 무관)
      - 구분자 '/' 통일
      - trailing slash 제거
    """
    p = Path(path).resolve()
    return p.as_posix().lower().rstrip("/")


def make_doc_id(abs_path: str | os.PathLike, file_size: int, mtime: float) -> str:
    """
    § 3.0.1 문서 ID 생성
    doc_id = sha256(정규화 경로)

    주의:
    - documents 테이블은 abs_path가 UNIQUE이므로, doc_id는 "파일 1개"를 대표하는 안정 ID로 둡니다.
    - 변경 감지는 file_size/mtime/sha256 컬럼으로 수행합니다.
    """
    norm = normalize_path(abs_path)
    return _sha256(norm)


def make_chunk_id(doc_id: str, chunk_index: int, chunk_text: str) -> str:
    """
    § 3.0.1 청크 ID 생성
    chunk_id = sha256(doc_id + chunk_index + chunk_text_hash)
    """
    text_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
    return _sha256(doc_id, str(chunk_index), text_hash)


def make_edge_id(src_doc_id: str, tgt_doc_id: str, edge_type: str) -> str:
    """
    § 3.0.1 엣지 ID 생성
    edge_id = sha256(src_doc_id + tgt_doc_id + edge_type)
    """
    return _sha256(src_doc_id, tgt_doc_id, edge_type)


def make_chain_id(filename_without_prefix: str) -> str:
    """
    § 3.0.1 버전 체인 ID 생성
    chain_id = sha256(정규화된 주제명)
    주제명: 파일명에서 날짜 접두어(YYYY-MM-DD_) 제거 후 소문자·공백 정규화
    """
    # 날짜 접두어 제거 (예: 2026-03-03_설계.md → 설계.md)
    name = re.sub(r"^\d{4}-\d{2}-\d{2}_?", "", filename_without_prefix)
    # 확장자 제거
    name = Path(name).stem
    # 소문자 + 연속 공백/특수문자 정규화
    name = re.sub(r"[\s_\-]+", "_", name.lower()).strip("_")
    return _sha256(name)


def compute_file_hash(abs_path: str | os.PathLike) -> str:
    """파일 내용의 SHA-256 해시 계산 (청크 단위 읽기)."""
    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()
