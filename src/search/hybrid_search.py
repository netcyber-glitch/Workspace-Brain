"""
src/search/hybrid_search.py
Workspace Brain — FTS(키워드) + Vector(Chroma) 하이브리드 검색(RRF)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.search.fts_search import FtsHit, search_fts
from src.search.vector_search import VectorHit, search_vector


@dataclass(frozen=True)
class HybridHit:
    doc_id: str
    score: float  # RRF score
    project: str
    title: str
    date_prefix: str
    rel_path: str
    abs_path: str
    fts_rank: int | None
    vector_rank: int | None


def _rrf(rank: int, k: int) -> float:
    return 1.0 / (float(k) + float(rank))


def hybrid_search(
    *,
    db_path: Path,
    chroma_dir: Path,
    query: str,
    project: str | None = None,
    limit: int = 10,
    fts_limit: int = 30,
    vector_limit: int = 30,
    vector_chunk_topk: int = 80,
    rrf_k: int = 60,
) -> tuple[list[HybridHit], list[FtsHit], list[VectorHit]]:
    fts_hits = search_fts(db_path=db_path, query=query, project=project, limit=max(1, int(fts_limit)))
    vec_hits = search_vector(
        db_path=db_path,
        chroma_dir=chroma_dir,
        query=query,
        project=project,
        limit=max(1, int(vector_limit)),
        chunk_topk=max(1, int(vector_chunk_topk)),
    )

    fts_rank: dict[str, int] = {h.doc_id: i + 1 for i, h in enumerate(fts_hits)}
    vec_rank: dict[str, int] = {h.doc_id: i + 1 for i, h in enumerate(vec_hits)}

    doc_ids = sorted(set(fts_rank.keys()) | set(vec_rank.keys()))
    if not doc_ids:
        return ([], fts_hits, vec_hits)

    fused_scores: dict[str, float] = {}
    for doc_id in doc_ids:
        s = 0.0
        if doc_id in fts_rank:
            s += _rrf(fts_rank[doc_id], int(rrf_k))
        if doc_id in vec_rank:
            s += _rrf(vec_rank[doc_id], int(rrf_k))
        fused_scores[doc_id] = s

    ranked = sorted(doc_ids, key=lambda d: fused_scores.get(d, 0.0), reverse=True)[: max(1, int(limit))]

    con = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join(["?"] * len(ranked))
        rows = con.execute(
            f"""
            SELECT doc_id, project, COALESCE(title,''), COALESCE(date_prefix,''), COALESCE(rel_path,''), COALESCE(abs_path,'')
            FROM documents
            WHERE status='active'
              AND doc_id IN ({placeholders})
            """,
            ranked,
        ).fetchall()
        meta_map = {str(r[0]): (str(r[1] or ""), str(r[2] or ""), str(r[3] or ""), str(r[4] or ""), str(r[5] or "")) for r in rows}

        out: list[HybridHit] = []
        for doc_id in ranked:
            if doc_id not in meta_map:
                continue
            proj, title, date_prefix, rel_path, abs_path = meta_map[doc_id]
            out.append(
                HybridHit(
                    doc_id=doc_id,
                    score=float(fused_scores.get(doc_id, 0.0)),
                    project=proj,
                    title=title,
                    date_prefix=str(date_prefix or ""),
                    rel_path=rel_path,
                    abs_path=abs_path,
                    fts_rank=fts_rank.get(doc_id),
                    vector_rank=vec_rank.get(doc_id),
                )
            )
        return (out, fts_hits, vec_hits)
    finally:
        con.close()
