"""
src/ui/backend.py
Workspace Brain — PySide6 UI용 백엔드(검색/미리보기/연관문서/태그)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from src.db.init_db import init_db
from src.db.schema import normalize_path
from src.db.tags import get_manual_tags_for_docs, parse_tags
from src.search.fts_search import search_fts


@dataclass(frozen=True)
class DocRecord:
    doc_id: str
    project: str
    title: str
    date_prefix: str
    rel_path: str
    abs_path: str
    filename: str
    ext: str


@dataclass(frozen=True)
class SearchRow:
    doc_id: str
    mode: str  # fts | vector | hybrid | recent
    score: float
    project: str
    title: str
    date_prefix: str
    rel_path: str
    abs_path: str
    tags: list[str]
    why: str


@dataclass(frozen=True)
class RelatedItem:
    doc_id: str
    title: str
    date_prefix: str
    rel_path: str
    abs_path: str
    tags: list[str]
    score: float | None
    why: str


@dataclass(frozen=True)
class RelatedSection:
    title: str
    items: list[RelatedItem]


@dataclass(frozen=True)
class VersionChainOverride:
    doc_id: str
    manual_chain_key: str
    exclude_from_chains: bool
    note: str
    updated_at: float


def ensure_db(db_path: Path) -> None:
    con = init_db(db_path=db_path)
    con.close()


def list_projects(*, db_path: Path) -> list[str]:
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT DISTINCT project FROM documents WHERE status='active' AND project IS NOT NULL ORDER BY project"
        ).fetchall()
        return [str(r[0]) for r in rows if r and str(r[0] or "").strip()]
    finally:
        con.close()


def get_doc_record(*, db_path: Path, doc_id: str) -> DocRecord | None:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            """
            SELECT
              doc_id,
              COALESCE(project,''),
              COALESCE(title,''),
              COALESCE(date_prefix,''),
              COALESCE(rel_path,''),
              COALESCE(abs_path,''),
              COALESCE(filename,''),
              COALESCE(ext,'')
            FROM documents
            WHERE doc_id = ?
            LIMIT 1
            """,
            (str(doc_id),),
        ).fetchone()
        if not row:
            return None
        return DocRecord(
            doc_id=str(row[0]),
            project=str(row[1] or ""),
            title=str(row[2] or ""),
            date_prefix=str(row[3] or ""),
            rel_path=str(row[4] or ""),
            abs_path=str(row[5] or ""),
            filename=str(row[6] or ""),
            ext=str(row[7] or ""),
        )
    finally:
        con.close()


def get_version_chain_override(*, db_path: Path, doc_id: str) -> VersionChainOverride | None:
    did = str(doc_id or "").strip()
    if not did:
        return None
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            """
            SELECT
              doc_id,
              COALESCE(manual_chain_key,''),
              COALESCE(exclude_from_chains, 0),
              COALESCE(note,''),
              COALESCE(updated_at, 0)
            FROM version_chain_overrides
            WHERE doc_id=?
            LIMIT 1
            """,
            (did,),
        ).fetchone()
        if not row:
            return None
        return VersionChainOverride(
            doc_id=str(row[0]),
            manual_chain_key=str(row[1] or "").strip(),
            exclude_from_chains=bool(int(row[2] or 0) == 1),
            note=str(row[3] or "").strip(),
            updated_at=float(row[4] or 0.0),
        )
    finally:
        con.close()


def get_version_chain_overrides(*, db_path: Path, doc_ids: list[str]) -> dict[str, VersionChainOverride]:
    ids = [str(d) for d in (doc_ids or []) if str(d).strip()]
    if not ids:
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join(["?"] * len(ids))
        rows = con.execute(
            f"""
            SELECT
              doc_id,
              COALESCE(manual_chain_key,''),
              COALESCE(exclude_from_chains, 0),
              COALESCE(note,''),
              COALESCE(updated_at, 0)
            FROM version_chain_overrides
            WHERE doc_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        out: dict[str, VersionChainOverride] = {}
        for did, key, ex, note, updated_at in rows:
            out[str(did)] = VersionChainOverride(
                doc_id=str(did),
                manual_chain_key=str(key or "").strip(),
                exclude_from_chains=bool(int(ex or 0) == 1),
                note=str(note or "").strip(),
                updated_at=float(updated_at or 0.0),
            )
        return out
    finally:
        con.close()


def _ensure_doc_exists(*, con: sqlite3.Connection, doc_id: str) -> None:
    ok = con.execute("SELECT 1 FROM documents WHERE doc_id=? LIMIT 1", (str(doc_id),)).fetchone()
    if not ok:
        raise ValueError(f"문서를 찾을 수 없습니다(doc_id): {doc_id}")


def _upsert_version_chain_override(
    *,
    con: sqlite3.Connection,
    doc_id: str,
    manual_chain_key: str | None,
    exclude_from_chains: bool,
    note: str | None,
) -> None:
    import time

    now = float(time.time())
    con.execute(
        """
        INSERT INTO version_chain_overrides(doc_id, manual_chain_key, exclude_from_chains, note, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(doc_id) DO UPDATE SET
          manual_chain_key=excluded.manual_chain_key,
          exclude_from_chains=excluded.exclude_from_chains,
          note=excluded.note,
          updated_at=excluded.updated_at
        """,
        (
            str(doc_id),
            (str(manual_chain_key).strip() if manual_chain_key is not None and str(manual_chain_key).strip() else None),
            1 if bool(exclude_from_chains) else 0,
            (str(note).strip() if note is not None and str(note).strip() else None),
            now,
        ),
    )


def pin_version_chain_doc(*, db_path: Path, doc_id: str, manual_chain_key: str, note: str | None = None) -> None:
    did = str(doc_id or "").strip()
    key = str(manual_chain_key or "").strip()
    if not did:
        raise ValueError("doc_id가 비어 있습니다.")
    if not key:
        raise ValueError("manual_chain_key가 비어 있습니다.")
    con = sqlite3.connect(str(db_path))
    try:
        _ensure_doc_exists(con=con, doc_id=did)
        _upsert_version_chain_override(con=con, doc_id=did, manual_chain_key=key, exclude_from_chains=False, note=note)
        con.commit()
    finally:
        con.close()


def exclude_from_version_chains(*, db_path: Path, doc_id: str, note: str | None = None) -> None:
    did = str(doc_id or "").strip()
    if not did:
        raise ValueError("doc_id가 비어 있습니다.")
    con = sqlite3.connect(str(db_path))
    try:
        _ensure_doc_exists(con=con, doc_id=did)
        # exclude는 exclude 플래그만 켜고, 기존 manual_chain_key는 유지합니다(토글 UX).
        row = con.execute(
            "SELECT COALESCE(manual_chain_key,''), COALESCE(note,'') FROM version_chain_overrides WHERE doc_id=? LIMIT 1",
            (did,),
        ).fetchone()
        existing_key = str(row[0]) if row else ""
        existing_note = str(row[1]) if row else ""
        _upsert_version_chain_override(
            con=con,
            doc_id=did,
            manual_chain_key=existing_key or None,
            exclude_from_chains=True,
            note=note if note is not None else (existing_note.strip() or None),
        )
        con.commit()
    finally:
        con.close()


def include_in_version_chains(*, db_path: Path, doc_id: str, note: str | None = None) -> None:
    did = str(doc_id or "").strip()
    if not did:
        raise ValueError("doc_id가 비어 있습니다.")
    con = sqlite3.connect(str(db_path))
    try:
        _ensure_doc_exists(con=con, doc_id=did)
        row = con.execute(
            "SELECT COALESCE(manual_chain_key,''), COALESCE(note,'') FROM version_chain_overrides WHERE doc_id=? LIMIT 1",
            (did,),
        ).fetchone()
        existing_key = str(row[0]) if row else ""
        existing_note = str(row[1]) if row else ""
        _upsert_version_chain_override(
            con=con,
            doc_id=did,
            manual_chain_key=existing_key or None,
            exclude_from_chains=False,
            note=note if note is not None else (existing_note.strip() or None),
        )
        con.commit()
    finally:
        con.close()


def clear_version_chain_override(*, db_path: Path, doc_id: str) -> int:
    did = str(doc_id or "").strip()
    if not did:
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        before = con.total_changes
        con.execute("DELETE FROM version_chain_overrides WHERE doc_id=?", (did,))
        con.commit()
        return int(con.total_changes - before)
    finally:
        con.close()


def _to_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None


def list_recent(*, db_path: Path, project: str | None, limit: int) -> list[SearchRow]:
    con = sqlite3.connect(str(db_path))
    try:
        sql = """
        SELECT
          doc_id,
          COALESCE(project,''),
          COALESCE(title,''),
          COALESCE(date_prefix,''),
          COALESCE(rel_path,''),
          COALESCE(abs_path,'')
        FROM documents
        WHERE status='active'
        """
        params: list[object] = []
        if project:
            sql += " AND project=?"
            params.append(project)
        sql += " ORDER BY COALESCE(date_prefix,'') DESC, indexed_at DESC LIMIT ?"
        params.append(int(limit))
        rows = con.execute(sql, params).fetchall()
        doc_ids = [str(r[0]) for r in rows]
        tags_by_doc = get_manual_tags_for_docs(db_path=db_path, doc_ids=doc_ids)
        out: list[SearchRow] = []
        for doc_id, proj, title, date_prefix, rel_path, abs_path in rows:
            did = str(doc_id)
            out.append(
                SearchRow(
                    doc_id=did,
                    mode="recent",
                    score=0.0,
                    project=str(proj or ""),
                    title=str(title or ""),
                    date_prefix=str(date_prefix or ""),
                    rel_path=str(rel_path or ""),
                    abs_path=str(abs_path or ""),
                    tags=tags_by_doc.get(did, []) or [],
                    why="최근 문서",
                )
            )
        return out
    finally:
        con.close()


def search_rows(
    *,
    db_path: Path,
    chroma_dir: Path,
    mode: str,
    query: str,
    project: str | None,
    limit: int,
    vector_chunk_topk: int = 80,
    fts_limit: int = 30,
    vector_limit: int = 30,
    rrf_k: int = 60,
) -> list[SearchRow]:
    q = str(query or "").strip()
    if not q:
        return list_recent(db_path=db_path, project=project, limit=limit)

    m = str(mode or "fts").strip().lower()
    hits: list[object] = []

    if m == "fts":
        hits = search_fts(db_path=db_path, query=q, project=project, limit=limit)
    elif m == "vector":
        from src.search.vector_search import search_vector

        hits = search_vector(
            db_path=db_path,
            chroma_dir=chroma_dir,
            query=q,
            project=project,
            limit=limit,
            chunk_topk=max(1, int(vector_chunk_topk)),
        )
    elif m == "hybrid":
        from src.search.hybrid_search import hybrid_search

        fused, _, _ = hybrid_search(
            db_path=db_path,
            chroma_dir=chroma_dir,
            query=q,
            project=project,
            limit=limit,
            fts_limit=max(1, int(fts_limit)),
            vector_limit=max(1, int(vector_limit)),
            vector_chunk_topk=max(1, int(vector_chunk_topk)),
            rrf_k=max(1, int(rrf_k)),
        )
        hits = fused
    else:
        raise ValueError(f"알 수 없는 검색 모드: {m}")

    doc_ids = [str(getattr(h, "doc_id", "")) for h in hits if str(getattr(h, "doc_id", "")).strip()]
    tags_by_doc = get_manual_tags_for_docs(db_path=db_path, doc_ids=doc_ids)

    out: list[SearchRow] = []
    for h in hits:
        doc_id = str(getattr(h, "doc_id", ""))
        title = str(getattr(h, "title", ""))
        date_prefix = str(getattr(h, "date_prefix", ""))
        rel_path = str(getattr(h, "rel_path", ""))
        abs_path = str(getattr(h, "abs_path", ""))
        proj = str(getattr(h, "project", ""))
        score = float(getattr(h, "score", 0.0) or 0.0)

        why = ""
        if m == "fts":
            why = "FTS(키워드)"
        elif m == "vector":
            bi = getattr(h, "best_chunk_index", None)
            why = f"Vector(best_chunk_index={int(bi) if bi is not None else 0})"
        elif m == "hybrid":
            fr = getattr(h, "fts_rank", None)
            vr = getattr(h, "vector_rank", None)
            why = f"Hybrid(fts_rank={fr}, vector_rank={vr})"
        else:
            why = m

        out.append(
            SearchRow(
                doc_id=doc_id,
                mode=m,
                score=score,
                project=proj,
                title=title,
                date_prefix=date_prefix,
                rel_path=rel_path,
                abs_path=abs_path,
                tags=tags_by_doc.get(doc_id, []) or [],
                why=why,
            )
        )
    return out


def load_text_preview(*, abs_path: str, max_chars: int = 200_000) -> tuple[str, str]:
    """
    반환: (text, note)
    note는 UI에 경고/절단 메시지로 표시하는 용도입니다.
    """
    p = Path(abs_path)
    if not p.exists():
        return ("", "파일이 없습니다.")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ("", f"파일 읽기 실패: {type(e).__name__}")

    if max_chars and len(text) > int(max_chars):
        return (text[: int(max_chars)], f"미리보기는 {int(max_chars):,}자까지만 표시합니다.")
    return (text, "")


_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def extract_link_targets(text: str) -> list[str]:
    out: list[str] = []
    for m in _MD_LINK_RE.finditer(text or ""):
        out.append(str(m.group(1) or ""))
    for m in _WIKI_LINK_RE.finditer(text or ""):
        out.append(str(m.group(1) or ""))
    cleaned: list[str] = []
    for t in out:
        s = (t or "").strip().strip("\"'")
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith(("http://", "https://")):
            continue
        cleaned.append(s)
    # 순서 보존 dedup
    seen: set[str] = set()
    uniq: list[str] = []
    for s in cleaned:
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    return uniq


def resolve_link_paths(*, base_abs_path: str, targets: list[str]) -> list[Path]:
    base = Path(base_abs_path).parent
    out: list[Path] = []
    for t in targets:
        s = str(t or "").strip()
        if not s:
            continue
        # mailto: 등 제외
        if ":" in s and not re.match(r"^[a-zA-Z]:[\\/]", s):
            continue

        p = Path(s)
        if not p.is_absolute():
            p = (base / p).resolve()
        else:
            p = p.resolve()
        out.append(p)
    # 존재하는 파일만
    return [p for p in out if p.exists() and p.is_file()]


def _find_docs_by_abs_paths(*, db_path: Path, abs_paths: list[Path]) -> list[DocRecord]:
    if not abs_paths:
        return []
    norms = [normalize_path(str(p)) for p in abs_paths]
    con = sqlite3.connect(str(db_path))
    try:
        out: list[DocRecord] = []
        for norm in norms:
            row = con.execute(
                """
                SELECT
                  doc_id,
                  COALESCE(project,''),
                  COALESCE(title,''),
                  COALESCE(date_prefix,''),
                  COALESCE(rel_path,''),
                  COALESCE(abs_path,''),
                  COALESCE(filename,''),
                  COALESCE(ext,'')
                FROM documents
                WHERE LOWER(REPLACE(abs_path, '\\', '/')) = ?
                LIMIT 1
                """,
                (str(norm),),
            ).fetchone()
            if not row:
                continue
            out.append(
                DocRecord(
                    doc_id=str(row[0]),
                    project=str(row[1] or ""),
                    title=str(row[2] or ""),
                    date_prefix=str(row[3] or ""),
                    rel_path=str(row[4] or ""),
                    abs_path=str(row[5] or ""),
                    filename=str(row[6] or ""),
                    ext=str(row[7] or ""),
                )
            )
        return out
    finally:
        con.close()


def _strip_date_prefix(filename: str) -> str:
    s = str(filename or "").strip()
    s = re.sub(r"^\d{4}-\d{2}-\d{2}_?", "", s)
    return s.lower()


def _same_dir(a: str, b: str) -> bool:
    try:
        return Path(a).resolve().parent == Path(b).resolve().parent
    except Exception:
        return False


def _list_docs_in_date_range(
    *,
    db_path: Path,
    project: str,
    start: str,
    end: str,
    limit: int = 4000,
) -> list[DocRecord]:
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            """
            SELECT
              doc_id,
              COALESCE(project,''),
              COALESCE(title,''),
              COALESCE(date_prefix,''),
              COALESCE(rel_path,''),
              COALESCE(abs_path,''),
              COALESCE(filename,''),
              COALESCE(ext,'')
            FROM documents
            WHERE status='active'
              AND project=?
              AND date_prefix IS NOT NULL
              AND date_prefix <> ''
              AND date_prefix BETWEEN ? AND ?
            ORDER BY date_prefix ASC, indexed_at ASC
            LIMIT ?
            """,
            (str(project), str(start), str(end), int(limit)),
        ).fetchall()
        out: list[DocRecord] = []
        for r in rows:
            out.append(
                DocRecord(
                    doc_id=str(r[0]),
                    project=str(r[1] or ""),
                    title=str(r[2] or ""),
                    date_prefix=str(r[3] or ""),
                    rel_path=str(r[4] or ""),
                    abs_path=str(r[5] or ""),
                    filename=str(r[6] or ""),
                    ext=str(r[7] or ""),
                )
            )
        return out
    finally:
        con.close()


def _load_version_chain_docs(*, db_path: Path, doc_id: str) -> list[tuple[int, DocRecord]]:
    """
    doc_id가 속한 version_chains를 조회해 (version_order, DocRecord) 목록을 반환합니다.
    체인이 없으면 빈 리스트를 반환합니다.
    """
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            """
            SELECT vc.version_order,
                   d.doc_id,
                   COALESCE(d.project,''),
                   COALESCE(d.title,''),
                   COALESCE(d.date_prefix,''),
                   COALESCE(d.rel_path,''),
                   COALESCE(d.abs_path,''),
                   COALESCE(d.filename,''),
                   COALESCE(d.ext,'')
            FROM version_chains vc
            JOIN documents d ON d.doc_id = vc.doc_id
            WHERE vc.chain_id IN (SELECT chain_id FROM version_chains WHERE doc_id=?)
              AND d.status='active'
            ORDER BY vc.version_order ASC, d.indexed_at ASC
            """,
            (str(doc_id),),
        ).fetchall()
        out: list[tuple[int, DocRecord]] = []
        for ver, did, proj, title, date_prefix, rel_path, abs_path, filename, ext in rows:
            out.append(
                (
                    int(ver or 0),
                    DocRecord(
                        doc_id=str(did),
                        project=str(proj or ""),
                        title=str(title or ""),
                        date_prefix=str(date_prefix or ""),
                        rel_path=str(rel_path or ""),
                        abs_path=str(abs_path or ""),
                        filename=str(filename or ""),
                        ext=str(ext or ""),
                    ),
                )
            )
        return out
    finally:
        con.close()


def build_related_sections(
    *,
    db_path: Path,
    chroma_dir: Path,
    doc: DocRecord,
    preview_text: str,
    days_stream: int = 7,
    limit_each: int = 15,
) -> list[RelatedSection]:
    sections: list[RelatedSection] = []
    seen: set[str] = {str(doc.doc_id)}

    # (1) 문서 내 링크
    targets = extract_link_targets(preview_text or "")
    link_paths = resolve_link_paths(base_abs_path=doc.abs_path, targets=targets)
    link_docs = _find_docs_by_abs_paths(db_path=db_path, abs_paths=link_paths)
    if link_docs:
        doc_ids = [d.doc_id for d in link_docs]
        tags_by = get_manual_tags_for_docs(db_path=db_path, doc_ids=doc_ids)
        items: list[RelatedItem] = []
        for d in link_docs[: max(1, int(limit_each))]:
            if d.doc_id in seen:
                continue
            seen.add(d.doc_id)
            items.append(
                RelatedItem(
                    doc_id=d.doc_id,
                    title=d.title or d.filename,
                    date_prefix=d.date_prefix,
                    rel_path=d.rel_path,
                    abs_path=d.abs_path,
                    tags=tags_by.get(d.doc_id, []) or [],
                    score=None,
                    why="본문 링크",
                )
            )
        if items:
            sections.append(RelatedSection(title="문서 내 링크", items=items))

    # (2) 버전/시리즈(version_chains 우선): 구축되어 있으면 그 결과를 사용
    chain_rows = _load_version_chain_docs(db_path=db_path, doc_id=doc.doc_id)
    chain_docs = [(ver, d) for ver, d in chain_rows if d.doc_id != doc.doc_id]
    if chain_docs:
        doc_ids = [d.doc_id for _, d in chain_docs]
        tags_by = get_manual_tags_for_docs(db_path=db_path, doc_ids=doc_ids)
        items: list[RelatedItem] = []
        for ver, d in chain_docs[: max(1, int(limit_each))]:
            if d.doc_id in seen:
                continue
            seen.add(d.doc_id)
            items.append(
                RelatedItem(
                    doc_id=d.doc_id,
                    title=d.title or d.filename,
                    date_prefix=d.date_prefix,
                    rel_path=d.rel_path,
                    abs_path=d.abs_path,
                    tags=tags_by.get(d.doc_id, []) or [],
                    score=None,
                    why=f"버전 체인(order={int(ver)})",
                )
            )
        if items:
            sections.append(RelatedSection(title="버전/시리즈", items=items))

    # (2b) 폴백(간단 체인): 아직 version_chains가 비어있으면 파일명 기준으로 추정
    if not chain_docs:
        base_topic = _strip_date_prefix(doc.filename)
        base_date = _parse_iso(doc.date_prefix)
        if base_topic and base_date:
            start = _to_iso(base_date - timedelta(days=14))
            end = _to_iso(base_date + timedelta(days=14))
            candidates = _list_docs_in_date_range(
                db_path=db_path, project=doc.project, start=start, end=end, limit=2000
            )
            fb = [c for c in candidates if _strip_date_prefix(c.filename) == base_topic and c.doc_id != doc.doc_id]
            if fb:
                doc_ids = [c.doc_id for c in fb]
                tags_by = get_manual_tags_for_docs(db_path=db_path, doc_ids=doc_ids)
                items: list[RelatedItem] = []
                for c in fb[: max(1, int(limit_each))]:
                    if c.doc_id in seen:
                        continue
                    seen.add(c.doc_id)
                    items.append(
                        RelatedItem(
                            doc_id=c.doc_id,
                            title=c.title or c.filename,
                            date_prefix=c.date_prefix,
                            rel_path=c.rel_path,
                            abs_path=c.abs_path,
                            tags=tags_by.get(c.doc_id, []) or [],
                            score=None,
                            why="버전/시리즈(추정)",
                        )
                    )
                if items:
                    sections.append(RelatedSection(title="버전/시리즈", items=items))

    # (3) 작업 흐름(±N일): 같은 프로젝트 + 날짜 근접 + (같은 폴더/태그 교집합) 가중
    base_date = _parse_iso(doc.date_prefix)
    if base_date and doc.project:
        start = _to_iso(base_date - timedelta(days=max(0, int(days_stream))))
        end = _to_iso(base_date + timedelta(days=max(0, int(days_stream))))
        candidates = _list_docs_in_date_range(
            db_path=db_path, project=doc.project, start=start, end=end, limit=3000
        )
        candidates = [c for c in candidates if c.doc_id != doc.doc_id]
        cand_ids = [c.doc_id for c in candidates]
        tags_by = get_manual_tags_for_docs(db_path=db_path, doc_ids=cand_ids)
        my_tags = set(get_manual_tags_for_docs(db_path=db_path, doc_ids=[doc.doc_id]).get(doc.doc_id, []) or [])

        def score(c: DocRecord) -> float:
            d2 = _parse_iso(c.date_prefix)
            dd = abs((d2 - base_date).days) if d2 else 999
            same_dir_bonus = 5.0 if _same_dir(doc.abs_path, c.abs_path) else 0.0
            overlap = len(my_tags & set(tags_by.get(c.doc_id, []) or []))
            return (same_dir_bonus + float(overlap)) - float(dd) * 0.25

        ranked = sorted(candidates, key=score, reverse=True)[: max(1, int(limit_each))]
        items: list[RelatedItem] = []
        for c in ranked:
            if c.doc_id in seen:
                continue
            seen.add(c.doc_id)
            items.append(
                RelatedItem(
                    doc_id=c.doc_id,
                    title=c.title or c.filename,
                    date_prefix=c.date_prefix,
                    rel_path=c.rel_path,
                    abs_path=c.abs_path,
                    tags=tags_by.get(c.doc_id, []) or [],
                    score=None,
                    why=f"작업 흐름(±{int(days_stream)}일)",
                )
            )
        if items:
            sections.append(RelatedSection(title="작업 흐름", items=items))

    # (4) 유사 문서(하이브리드): 제목 기반 질의로 Top-N
    try:
        from src.search.hybrid_search import hybrid_search

        q = (doc.title or "").strip() or Path(doc.abs_path).stem
        if q:
            fused, _, _ = hybrid_search(
                db_path=db_path,
                chroma_dir=chroma_dir,
                query=q,
                project=doc.project or None,
                limit=max(1, int(limit_each)),
                fts_limit=30,
                vector_limit=30,
                vector_chunk_topk=80,
                rrf_k=60,
            )
            doc_ids = [h.doc_id for h in fused]
            tags_by = get_manual_tags_for_docs(db_path=db_path, doc_ids=doc_ids)
            items: list[RelatedItem] = []
            for h in fused:
                if h.doc_id in seen:
                    continue
                seen.add(h.doc_id)
                items.append(
                    RelatedItem(
                        doc_id=h.doc_id,
                        title=h.title or Path(h.abs_path).name,
                        date_prefix=h.date_prefix,
                        rel_path=h.rel_path,
                        abs_path=h.abs_path,
                        tags=tags_by.get(h.doc_id, []) or [],
                        score=float(h.score),
                        why="유사(하이브리드)",
                    )
                )
            if items:
                sections.append(RelatedSection(title="유사 문서", items=items))
    except Exception:
        # 의존성 누락 또는 Chroma 오류는 조용히 스킵(MVP)
        pass

    return sections


def parse_manual_tags_input(text: str) -> list[str]:
    return parse_tags(text)
