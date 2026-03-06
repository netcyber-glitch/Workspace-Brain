"""
src/search/vector_search.py
Workspace Brain — ChromaDB 벡터 검색(문서 단위로 집계)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from src.db.schema import EMBED_MODEL_ID
from src.indexer.vector_indexer import DEFAULT_COLLECTION_NAME


@dataclass(frozen=True)
class VectorHit:
    doc_id: str
    score: float  # cosine similarity(근사)
    project: str
    title: str
    date_prefix: str
    rel_path: str
    abs_path: str
    best_chunk_id: str
    best_chunk_index: int


_MODEL_LOCK = Lock()
_ENCODE_LOCK = Lock()
_MODEL = None
_MODEL_ID = ""

_CHROMA_LOCK = Lock()
_CHROMA_CLIENTS: dict[str, object] = {}
_CHROMA_COLLECTIONS: dict[tuple[str, str], object] = {}


def _require_deps():
    try:
        import chromadb  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "필수 패키지가 없습니다. `.venv`에서 실행하거나, "
            "`pip install chromadb sentence-transformers`를 먼저 실행하세요."
        ) from e


def _get_embed_model():
    _require_deps()
    from sentence_transformers import SentenceTransformer

    global _MODEL, _MODEL_ID
    with _MODEL_LOCK:
        if _MODEL is None or str(_MODEL_ID) != str(EMBED_MODEL_ID):
            _MODEL = SentenceTransformer(EMBED_MODEL_ID)
            _MODEL_ID = str(EMBED_MODEL_ID)
        return _MODEL


def shutdown_vector_search_resources() -> None:
    """
    Chroma 클라이언트/컬렉션 캐시를 정리합니다.
    - 인덱싱(벡터 재구축) 같은 외부 작업 전에 호출하면 파일 잠김을 줄일 수 있습니다.
    """
    with _CHROMA_LOCK:
        for client in list(_CHROMA_CLIENTS.values()):
            try:
                client._system.stop()
            except Exception:
                pass
        _CHROMA_COLLECTIONS.clear()
        _CHROMA_CLIENTS.clear()


def _get_chroma_collection(*, chroma_dir: Path, collection_name: str):
    _require_deps()
    import chromadb
    from chromadb.config import Settings

    dkey = str(chroma_dir)
    ckey = (dkey, str(collection_name))

    with _CHROMA_LOCK:
        client = _CHROMA_CLIENTS.get(dkey)
        if client is None:
            client = chromadb.PersistentClient(
                path=dkey,
                settings=Settings(anonymized_telemetry=False),
            )
            _CHROMA_CLIENTS[dkey] = client

        collection = _CHROMA_COLLECTIONS.get(ckey)
        if collection is None:
            try:
                collection = client.get_collection(name=str(collection_name))
            except Exception as e:
                raise ValueError(f"Chroma collection을 찾을 수 없습니다: {collection_name} ({e})") from e
            _CHROMA_COLLECTIONS[ckey] = collection

        return collection


def search_vector(
    *,
    db_path: Path,
    chroma_dir: Path,
    query: str,
    project: str | None = None,
    limit: int = 10,
    chunk_topk: int = 60,
    collection_name: str = DEFAULT_COLLECTION_NAME,
) -> list[VectorHit]:
    if not query or not str(query).strip():
        return []

    collection = _get_chroma_collection(chroma_dir=chroma_dir, collection_name=str(collection_name))

    model = _get_embed_model()
    with _ENCODE_LOCK:
        q_emb = model.encode([query], normalize_embeddings=True, show_progress_bar=False).tolist()

    res = collection.query(
        query_embeddings=q_emb,
        n_results=max(1, int(chunk_topk)),
        include=["distances", "metadatas"],
    )

    ids_list = (res.get("ids") or [[]])[0]
    dist_list = (res.get("distances") or [[]])[0]
    meta_list = (res.get("metadatas") or [[]])[0]

    # chunk → doc 집계 (최고 유사도 기준)
    best_by_doc: dict[str, tuple[float, str, int]] = {}
    for cid, dist, meta in zip(ids_list, dist_list, meta_list):
        if not meta:
            continue
        doc_id = str(meta.get("doc_id") or "")
        if not doc_id:
            continue
        proj = str(meta.get("project") or "")
        if project and proj != project:
            continue

        try:
            d = float(dist)
        except Exception:
            d = 1.0

        # cosine distance로 가정: similarity = 1 - distance
        sim = 1.0 - d
        chunk_index = int(meta.get("chunk_index") or 0)

        prev = best_by_doc.get(doc_id)
        if prev is None or sim > prev[0]:
            best_by_doc[doc_id] = (sim, str(cid), chunk_index)

    if not best_by_doc:
        return []

    ranked = sorted(best_by_doc.items(), key=lambda x: x[1][0], reverse=True)[: max(1, int(limit))]
    doc_ids = [doc_id for doc_id, _ in ranked]

    con = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join(["?"] * len(doc_ids))
        sql = f"""
        SELECT doc_id, project, COALESCE(title,''), COALESCE(date_prefix,''), COALESCE(rel_path,''), COALESCE(abs_path,'')
        FROM documents
        WHERE status='active'
          AND doc_id IN ({placeholders})
        """
        params: list[object] = list(doc_ids)
        if project:
            sql += " AND project = ?"
            params.append(project)
        rows = con.execute(sql, params).fetchall()
        meta_map = {str(r[0]): (str(r[1] or ""), str(r[2] or ""), str(r[3] or ""), str(r[4] or ""), str(r[5] or "")) for r in rows}

        hits: list[VectorHit] = []
        for doc_id in doc_ids:
            if doc_id not in meta_map:
                continue
            proj, title, date_prefix, rel_path, abs_path = meta_map[doc_id]
            sim, best_cid, best_idx = best_by_doc[doc_id]
            hits.append(
                VectorHit(
                    doc_id=doc_id,
                    score=float(sim),
                    project=proj,
                    title=title,
                    date_prefix=str(date_prefix or ""),
                    rel_path=rel_path,
                    abs_path=abs_path,
                    best_chunk_id=best_cid,
                    best_chunk_index=int(best_idx),
                )
            )
        return hits
    finally:
        con.close()
