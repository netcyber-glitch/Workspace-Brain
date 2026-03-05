"""
src/indexer/vector_indexer.py
Workspace Brain — 청킹 + 임베딩 + ChromaDB(영속) 벡터 인덱서

설계 원칙(MVP):
- 문서 메타: SQLite documents
- 청크 메타: SQLite chunks
- 벡터/본문: ChromaDB (data/chroma_db)

주의:
- 이 모듈은 `chromadb`, `sentence-transformers` 의존성이 있습니다.
  가상환경(.venv)에서 실행하는 것을 권장합니다.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from src.db.schema import EMBED_MODEL_ID, make_chunk_id
from src.indexer.chunking import TextChunk, chunk_text


DEFAULT_COLLECTION_NAME = "workspace_brain_chunks"

# Chroma(rust backend) 업서트에서 ASCII 제어문자(예: form feed \f)가 포함된 문서를 처리할 때
# Windows 환경에서 access violation이 발생한 사례가 있어, 인덱싱용 텍스트는 제어문자를 정규화합니다.
# - 허용: \n(10), \r(13), \t(9)
# - form feed(\f=12)는 페이지 구분자 성격이므로 \n으로 변환
# - 그 외 제어문자는 공백으로 치환
_CONTROL_CHAR_TRANSLATION = {i: " " for i in range(32) if i not in (9, 10, 13)}
_CONTROL_CHAR_TRANSLATION[12] = "\n"


def _sanitize_index_text(text: str) -> str:
    if not text:
        return ""
    return text.translate(_CONTROL_CHAR_TRANSLATION)


@dataclass(frozen=True)
class VectorIndexStats:
    docs_seen: int
    docs_indexed: int
    docs_skipped: int
    docs_failed: int
    chunks_added: int
    chunks_removed: int


def _require_deps():
    try:
        import chromadb  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "필수 패키지가 없습니다. `.venv`에서 실행하거나, "
            "`pip install chromadb sentence-transformers`를 먼저 실행하세요."
        ) from e


def _normalize_ext(ext: str) -> str:
    e = (ext or "").strip().lower()
    if not e:
        return ""
    return e if e.startswith(".") else f".{e}"


def _iter_documents(
    con: sqlite3.Connection,
    *,
    project: str | None,
) -> list[tuple[str, str, str, str, str, str]]:
    """
    반환: (doc_id, project, title, rel_path, abs_path, ext)
    """
    sql = """
    SELECT
      doc_id,
      COALESCE(project, '') AS project,
      COALESCE(title, '') AS title,
      COALESCE(rel_path, '') AS rel_path,
      COALESCE(abs_path, '') AS abs_path,
      COALESCE(ext, '') AS ext
    FROM documents
    WHERE status='active'
    """
    params: list[object] = []
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY abs_path"
    return [(str(a), str(b), str(c), str(d), str(e), str(f)) for a, b, c, d, e, f in con.execute(sql, params)]


def _get_existing_active_chunk_ids(con: sqlite3.Connection, doc_id: str) -> set[str]:
    rows = con.execute(
        "SELECT chunk_id FROM chunks WHERE doc_id = ? AND status='active'",
        (doc_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def _mark_chunks_soft_deleted(con: sqlite3.Connection, chunk_ids: list[str]) -> int:
    if not chunk_ids:
        return 0
    now = time.time()
    con.executemany(
        "UPDATE chunks SET status='soft_deleted', indexed_at=? WHERE chunk_id=?",
        [(now, cid) for cid in chunk_ids],
    )
    return len(chunk_ids)


def _upsert_chunks(con: sqlite3.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    sql = """
    INSERT INTO chunks
      (chunk_id, doc_id, chunk_index, text_hash, char_start, char_end, token_count,
       status, schema_version, embed_model_id, indexed_at)
    VALUES
      (:chunk_id, :doc_id, :chunk_index, :text_hash, :char_start, :char_end, :token_count,
       'active', '1.0', :embed_model_id, :indexed_at)
    ON CONFLICT(chunk_id) DO UPDATE SET
      doc_id=excluded.doc_id,
      chunk_index=excluded.chunk_index,
      text_hash=excluded.text_hash,
      char_start=excluded.char_start,
      char_end=excluded.char_end,
      token_count=excluded.token_count,
      status='active',
      schema_version='1.0',
      embed_model_id=excluded.embed_model_id,
      indexed_at=excluded.indexed_at
    """
    con.executemany(sql, rows)


def _chunks_for_doc(text: str, ext: str, *, max_chars: int, overlap: int) -> list[TextChunk]:
    prefer_code = _normalize_ext(ext) in {
        ".py",
        ".ts",
        ".js",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".css",
        ".html",
    }
    return chunk_text(
        text,
        max_chars=max_chars,
        overlap=overlap,
        min_chars=max(80, int(max_chars * 0.25)),
        prefer_code=prefer_code,
    )


def index_vectors(
    *,
    db_path: Path,
    chroma_dir: Path,
    project: str | None = None,
    include_exts: set[str] | None = None,
    limit_docs: int | None = None,
    chunk_max_chars: int = 1400,
    chunk_overlap: int = 200,
    batch_size: int = 64,
    force: bool = False,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    max_file_chars: int = 200_000,
    verbose: bool = True,
) -> VectorIndexStats:
    """
    SQLite documents → 청킹 → (SQLite chunks + ChromaDB) 인덱싱

    - include_exts: 예) {".md",".txt"} (None이면 전체 확장자 허용)
    - force: True면 기존 chunk_id가 있어도 재임베딩/재업서트
    """
    _require_deps()
    from sentence_transformers import SentenceTransformer
    import chromadb
    from chromadb.config import Settings

    include_exts_norm = {_normalize_ext(x) for x in (include_exts or set()) if _normalize_ext(x)}

    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(chroma_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=str(collection_name),
        metadata={"hnsw:space": "cosine"},
    )

    model = SentenceTransformer(EMBED_MODEL_ID)

    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA busy_timeout = 60000;")

    docs_seen = 0
    docs_indexed = 0
    docs_skipped = 0
    docs_failed = 0
    chunks_added = 0
    chunks_removed = 0

    pending_ids: list[str] = []
    pending_texts: list[str] = []
    pending_metas: list[dict] = []
    pending_rows: list[dict] = []

    def flush_pending() -> None:
        nonlocal chunks_added
        if not pending_ids:
            return
        # 임베딩
        embeddings = model.encode(
            pending_texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        emb_list = embeddings.tolist()

        # Chroma upsert
        collection.upsert(
            ids=pending_ids,
            documents=pending_texts,
            embeddings=emb_list,
            metadatas=pending_metas,
        )

        # SQLite upsert
        con.execute("BEGIN;")
        try:
            _upsert_chunks(con, pending_rows)
            con.commit()
        except Exception:
            con.rollback()
            raise

        chunks_added += len(pending_ids)
        pending_ids.clear()
        pending_texts.clear()
        pending_metas.clear()
        pending_rows.clear()

    try:
        docs = _iter_documents(con, project=project)
        if limit_docs is not None:
            docs = docs[: max(0, int(limit_docs))]

        for doc_id, proj, title, rel_path, abs_path, ext in docs:
            docs_seen += 1

            ext_norm = _normalize_ext(ext)
            if include_exts_norm and ext_norm not in include_exts_norm:
                continue

            path = Path(abs_path)
            if not path.exists():
                docs_failed += 1
                continue

            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                docs_failed += 1
                continue

            if max_file_chars and len(text) > int(max_file_chars):
                # 너무 큰 파일은 MVP에서 스킵(추후 배치/스트리밍 청킹으로 개선)
                docs_failed += 1
                continue

            chunks = _chunks_for_doc(text, ext_norm, max_chars=int(chunk_max_chars), overlap=int(chunk_overlap))
            new_ids: list[str] = []
            new_text_hash: dict[str, str] = {}
            chunk_by_id: dict[str, TextChunk] = {}
            for ch in chunks:
                cid = make_chunk_id(doc_id, ch.chunk_index, ch.text)
                new_ids.append(cid)
                # make_chunk_id 내부에서 text_hash를 계산하지만, DB에는 text_hash도 저장해야 함
                # 여기서는 동일 해시를 다시 계산하지 않고, chunks 테이블에는 chunk_id만으로도 충분하나
                # 추후 디버깅을 위해 text_hash를 저장한다(계산 비용은 크지 않음).
                import hashlib

                th = hashlib.sha256(ch.text.encode("utf-8")).hexdigest()
                new_text_hash[cid] = th
                chunk_by_id[cid] = ch

            existing_active = _get_existing_active_chunk_ids(con, doc_id)
            new_set = set(new_ids)

            if not force and existing_active and existing_active == new_set:
                docs_skipped += 1
                continue

            # 제거된 청크 처리
            removed = sorted(existing_active - new_set)
            if removed:
                con.execute("BEGIN;")
                try:
                    chunks_removed += _mark_chunks_soft_deleted(con, removed)
                    con.commit()
                except Exception:
                    con.rollback()
                    raise
                # Chroma delete (배치)
                for i in range(0, len(removed), 1000):
                    collection.delete(ids=removed[i : i + 1000])

            # 추가/갱신 대상만 버퍼에 쌓기
            for cid in new_ids:
                if (not force) and (cid in existing_active):
                    continue

                ch = chunk_by_id[cid]
                meta = {
                    "doc_id": doc_id,
                    "project": proj,
                    "abs_path": abs_path,
                    "rel_path": rel_path,
                    "title": title,
                    "ext": ext_norm,
                    "chunk_index": int(ch.chunk_index),
                    "char_start": int(ch.char_start),
                    "char_end": int(ch.char_end),
                }
                pending_ids.append(cid)
                pending_texts.append(_sanitize_index_text(ch.text))
                pending_metas.append(meta)
                pending_rows.append(
                    {
                        "chunk_id": cid,
                        "doc_id": doc_id,
                        "chunk_index": int(ch.chunk_index),
                        "text_hash": new_text_hash[cid],
                        "char_start": int(ch.char_start),
                        "char_end": int(ch.char_end),
                        "token_count": int(ch.token_count),
                        "embed_model_id": EMBED_MODEL_ID,
                        "indexed_at": time.time(),
                    }
                )

                if len(pending_ids) >= int(batch_size):
                    flush_pending()

            docs_indexed += 1
            if verbose and (docs_seen % 50 == 0):
                print(f"  ... docs {docs_seen} 처리 중 (indexed={docs_indexed}, skipped={docs_skipped})")

        flush_pending()

        if verbose:
            print("\n[벡터 인덱싱 요약]")
            print(f"  docs_seen:    {docs_seen}")
            print(f"  docs_indexed: {docs_indexed}")
            print(f"  docs_skipped: {docs_skipped}")
            print(f"  docs_failed:  {docs_failed}")
            print(f"  chunks_added: {chunks_added}")
            print(f"  chunks_removed: {chunks_removed}")

        return VectorIndexStats(
            docs_seen=docs_seen,
            docs_indexed=docs_indexed,
            docs_skipped=docs_skipped,
            docs_failed=docs_failed,
            chunks_added=chunks_added,
            chunks_removed=chunks_removed,
        )
    finally:
        con.close()
        try:
            client._system.stop()
        except Exception:
            pass
