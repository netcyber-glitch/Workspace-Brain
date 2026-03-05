"""
build_version_chains.py
Workspace Brain — version_chains(버전 체인) 구축 CLI

목표:
  documents 테이블을 기준으로 "같은 작업의 버전 묶음"을 만들어
  version_chains(chain_id, doc_id, version_order)를 채웁니다.

예:
  D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\build_version_chains.py --project MRA
  D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\build_version_chains.py --full
  D:\\Workspace_Brain\\.venv\\Scripts\\python.exe D:\\Workspace_Brain\\build_version_chains.py --project Workspace_Brain --dry-run
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from src.db.schema import EMBED_MODEL_ID
from src.db.init_db import init_db


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "data" / "metadata.db"

# Hugging Face / transformers 로그/프로그레스 바 최소화(콘솔 스팸 완화)
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_?")
_NORM_RE = re.compile(r"[\s_\-]+")


def _sha256(*parts: str) -> str:
    combined = "\x00".join([str(p) for p in parts])
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _normalize_dir(rel_path: str) -> str:
    try:
        p = Path(str(rel_path or "").replace("\\", "/"))
        parent = p.parent.as_posix().lower().strip("/")
        return parent
    except Exception:
        return ""


def _base_topic(filename: str) -> str:
    """
    파일명에서 날짜 접두어를 제거한 뒤, 확장자를 떼고 주제 토큰을 만듭니다.
    """
    name = str(filename or "").strip()
    if not name:
        return ""
    name = _DATE_PREFIX_RE.sub("", name)
    stem = Path(name).stem
    stem = _NORM_RE.sub("_", stem.lower()).strip("_")
    return stem


def _parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None


def _effective_date(date_prefix: str, mtime: float) -> date | None:
    """
    버전 체인 기준 날짜:
      1) 파일명 date_prefix(YYYY-MM-DD)
      2) mtime(Unix timestamp)
    """
    dt = _parse_iso(date_prefix or "")
    if dt is not None:
        return dt
    try:
        mt = float(mtime or 0.0)
    except Exception:
        mt = 0.0
    if mt <= 0:
        return None
    try:
        return datetime.fromtimestamp(mt).date()
    except Exception:
        return None


def _days_between(a: date | None, b: date | None) -> int | None:
    if a is None or b is None:
        return None
    try:
        return abs((b - a).days)
    except Exception:
        return None


def _filename_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    try:
        return float(difflib.SequenceMatcher(a=a, b=b).ratio())
    except Exception:
        return 0.0


@dataclass(frozen=True)
class _Doc:
    doc_id: str
    project: str
    rel_path: str
    filename: str
    date_prefix: str
    mtime: float
    abs_path: str
    base_topic: str
    rel_dir: str
    eff_date: date | None


def _load_docs(*, con: sqlite3.Connection, project: str | None) -> list[_Doc]:
    sql = """
    SELECT
      doc_id,
      COALESCE(project,''),
      COALESCE(rel_path,''),
      COALESCE(filename,''),
      COALESCE(date_prefix,''),
      COALESCE(mtime, 0.0),
      COALESCE(abs_path,'')
    FROM documents
    WHERE status='active'
    """
    params: list[object] = []
    if project:
        sql += " AND project=?"
        params.append(str(project))
    rows = con.execute(sql, params).fetchall()

    out: list[_Doc] = []
    for doc_id, proj, rel_path, filename, date_prefix, mtime, abs_path in rows:
        proj_s = str(proj or "").strip()
        if not proj_s:
            continue

        base = _base_topic(str(filename or ""))
        if not base:
            continue

        rel_dir = _normalize_dir(str(rel_path or ""))
        eff = _effective_date(str(date_prefix or ""), float(mtime or 0.0))
        out.append(
            _Doc(
                doc_id=str(doc_id),
                project=proj_s,
                rel_path=str(rel_path or ""),
                filename=str(filename or ""),
                date_prefix=str(date_prefix or ""),
                mtime=float(mtime or 0.0),
                abs_path=str(abs_path or ""),
                base_topic=str(base),
                rel_dir=str(rel_dir),
                eff_date=eff,
            )
        )
    return out


@dataclass(frozen=True)
class _Override:
    manual_chain_key: str
    exclude_from_chains: bool


def _load_overrides(*, con: sqlite3.Connection, project: str | None) -> dict[str, _Override]:
    sql = """
    SELECT
      o.doc_id,
      COALESCE(o.manual_chain_key,''),
      COALESCE(o.exclude_from_chains, 0)
    FROM version_chain_overrides o
    JOIN documents d ON d.doc_id = o.doc_id
    WHERE d.status='active'
    """
    params: list[object] = []
    if project:
        sql += " AND d.project=?"
        params.append(str(project))

    out: dict[str, _Override] = {}
    for doc_id, manual_key, ex in con.execute(sql, params).fetchall():
        out[str(doc_id)] = _Override(
            manual_chain_key=str(manual_key or "").strip(),
            exclude_from_chains=bool(int(ex or 0) == 1),
        )
    return out


def _sort_key(d: _Doc) -> tuple[int, str, float, str]:
    if d.eff_date is None:
        # 날짜 없는 문서는 뒤로 보냄
        return (1, "9999-99-99", float(d.mtime), d.doc_id)
    return (0, d.eff_date.isoformat(), float(d.mtime), d.doc_id)


def _read_text_prefix(*, abs_path: str, max_chars: int) -> str:
    if not abs_path:
        return ""
    try:
        p = Path(abs_path)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text:
            return ""
        return text[: max(0, int(max_chars))] if int(max_chars) > 0 else text
    except Exception:
        return ""


def _load_embedder(*, enable: bool):
    if not bool(enable):
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None
    try:
        return SentenceTransformer(EMBED_MODEL_ID)
    except Exception:
        return None


def _cosine_sim_norm(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b:
        return None
    if len(a) != len(b):
        return None
    try:
        return float(sum(x * y for x, y in zip(a, b)))
    except Exception:
        return None


def _embed_docs(
    *,
    embedder,
    docs: list[_Doc],
    max_embed_chars: int,
) -> dict[str, list[float]]:
    if embedder is None:
        return {}
    texts: list[str] = []
    ids: list[str] = []
    for d in docs:
        t = _read_text_prefix(abs_path=d.abs_path, max_chars=max_embed_chars).strip()
        # 너무 짧으면 유사도 판단이 불안정해서 제외
        if len(t) < 200:
            continue
        ids.append(d.doc_id)
        texts.append(t)

    if not texts:
        return {}

    emb = embedder.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    try:
        emb_list = emb.tolist()
    except Exception:
        emb_list = [list(map(float, row)) for row in emb]

    return {doc_id: vec for doc_id, vec in zip(ids, emb_list)}


def _build_auto_chains_for_group(
    *,
    docs: list[_Doc],
    min_chain_size: int,
    max_day_gap: int,
    filename_sim_threshold: float,
    content_sim_threshold: float,
    enable_content_sim: bool,
    require_content_sim: bool,
    embedder,
    max_embed_chars: int,
    verbose: bool,
    debug_edge_filter: bool,
    edge_filter_stats: dict[str, int] | None,
) -> list[list[_Doc]]:
    if len(docs) < int(min_chain_size):
        return []

    docs_sorted = sorted(docs, key=_sort_key)

    # (1) 후보 엣지: 날짜 gap <= N AND 파일명 유사도 >= threshold
    candidate_pairs: list[tuple[str, str]] = []
    for i in range(len(docs_sorted)):
        di = docs_sorted[i]
        if di.eff_date is None:
            continue
        for j in range(i - 1, -1, -1):
            dj = docs_sorted[j]
            if dj.eff_date is None:
                continue
            gap = _days_between(dj.eff_date, di.eff_date)
            if gap is None:
                continue
            if gap > int(max_day_gap):
                break
            name_sim = _filename_similarity(dj.base_topic, di.base_topic)
            if name_sim >= float(filename_sim_threshold):
                candidate_pairs.append((dj.doc_id, di.doc_id))

    # 후보가 없으면 체인도 없음
    if not candidate_pairs:
        return []

    need_embed_ids = {a for a, b in candidate_pairs} | {b for a, b in candidate_pairs}
    docs_for_embed = [d for d in docs_sorted if d.doc_id in need_embed_ids]

    emb_map: dict[str, list[float]] = {}
    if bool(enable_content_sim) and embedder is not None:
        emb_map = _embed_docs(embedder=embedder, docs=docs_for_embed, max_embed_chars=int(max_embed_chars))

    # (2) 엣지 필터: content similarity >= threshold (가능한 경우)
    edges: list[tuple[str, str]] = []
    dropped_no_embed = 0
    dropped_low_sim = 0
    for a, b in candidate_pairs:
        if bool(enable_content_sim):
            sim = _cosine_sim_norm(emb_map.get(a), emb_map.get(b))
            if sim is None:
                if bool(require_content_sim):
                    dropped_no_embed += 1
                    continue
            else:
                if float(sim) < float(content_sim_threshold):
                    dropped_low_sim += 1
                    continue
        edges.append((a, b))

    if edge_filter_stats is not None:
        edge_filter_stats["candidate_pairs"] = int(edge_filter_stats.get("candidate_pairs", 0)) + int(len(candidate_pairs))
        edge_filter_stats["edges_kept"] = int(edge_filter_stats.get("edges_kept", 0)) + int(len(edges))
        edge_filter_stats["dropped_no_embed"] = int(edge_filter_stats.get("dropped_no_embed", 0)) + int(dropped_no_embed)
        edge_filter_stats["dropped_low_sim"] = int(edge_filter_stats.get("dropped_low_sim", 0)) + int(dropped_low_sim)

    if bool(debug_edge_filter) and verbose and (dropped_no_embed or dropped_low_sim):
        print(
            f"  [auto] edge_filter: dropped_no_embed={dropped_no_embed}, dropped_low_sim={dropped_low_sim}"
        )

    if not edges:
        return []

    # (3) union-find로 컴포넌트(묶음) 생성
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        px = parent.get(x, x)
        if px != x:
            parent[x] = find(px)
        return parent.get(x, x)

    def union(a: str, b: str) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        parent[rb] = ra

    for a, b in edges:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)

    by_root: dict[str, list[_Doc]] = {}
    doc_by_id = {d.doc_id: d for d in docs_sorted}
    for doc_id in parent.keys():
        r = find(doc_id)
        d = doc_by_id.get(doc_id)
        if d is None:
            continue
        by_root.setdefault(r, []).append(d)

    # (4) 컴포넌트 내부 순차 분할(정책):
    #   - 날짜 gap > N -> split
    #   - 파일명 유사도 < threshold -> split
    #   - content sim < threshold(가능한 경우) -> split
    chains: list[list[_Doc]] = []
    for _, comp_docs in by_root.items():
        if len(comp_docs) < int(min_chain_size):
            continue
        comp_sorted = sorted(comp_docs, key=_sort_key)
        cur: list[_Doc] = []
        prev: _Doc | None = None
        for d in comp_sorted:
            if not cur:
                cur = [d]
                prev = d
                continue

            gap = _days_between(prev.eff_date, d.eff_date) if prev else None
            name_sim = _filename_similarity(prev.base_topic, d.base_topic) if prev else 0.0
            split = False
            if gap is None or gap > int(max_day_gap):
                split = True
            if float(name_sim) < float(filename_sim_threshold):
                split = True
            if bool(enable_content_sim):
                sim = _cosine_sim_norm(emb_map.get(prev.doc_id if prev else ""), emb_map.get(d.doc_id))
                if sim is None:
                    if bool(require_content_sim):
                        split = True
                else:
                    if float(sim) < float(content_sim_threshold):
                        split = True

            if split:
                if len(cur) >= int(min_chain_size):
                    chains.append(cur)
                cur = [d]
            else:
                cur.append(d)
            prev = d

        if cur and len(cur) >= int(min_chain_size):
            chains.append(cur)

    return chains


def build_version_chains(
    *,
    db_path: Path,
    project: str | None,
    min_chain_size: int,
    dry_run: bool,
    verbose: bool,
    max_day_gap: int,
    filename_sim_threshold: float,
    content_sim_threshold: float,
    no_content_sim: bool,
    require_content_sim: bool,
    max_embed_chars: int,
    debug_edge_filter: bool,
) -> int:
    con = init_db(db_path=db_path)
    try:
        docs = _load_docs(con=con, project=project)
        overrides = _load_overrides(con=con, project=project)

        excluded = 0
        manual_docs = 0
        manual_groups: dict[tuple[str, str], list[_Doc]] = {}
        auto_groups: dict[tuple[str, str], list[_Doc]] = {}

        for d in docs:
            ov = overrides.get(d.doc_id)
            if ov and bool(ov.exclude_from_chains):
                excluded += 1
                continue
            if ov and str(ov.manual_chain_key or "").strip():
                manual_docs += 1
                manual_groups.setdefault((d.project, ov.manual_chain_key.strip()), []).append(d)
            else:
                auto_groups.setdefault((d.project, d.rel_dir), []).append(d)

        enable_content_sim = not bool(no_content_sim)
        embedder = _load_embedder(enable=enable_content_sim)
        if enable_content_sim and embedder is None and verbose:
            print("  [warn] 내용 유사도(content similarity) 계산 비활성화: sentence-transformers 로드 실패")
            enable_content_sim = False

        chains: list[tuple[str, list[_Doc]]] = []

        # (1) 수동 체인(오버라이드)
        for (proj, manual_key), items in manual_groups.items():
            if len(items) < int(min_chain_size):
                continue
            items_sorted = sorted(items, key=_sort_key)
            cid = _sha256("manual", str(proj), str(manual_key))
            chains.append((cid, items_sorted))

        # (2) 자동 체인(정책 기반)
        edge_filter_stats: dict[str, int] = {}
        for (proj, rel_dir), items in auto_groups.items():
            auto_chains = _build_auto_chains_for_group(
                docs=items,
                min_chain_size=int(min_chain_size),
                max_day_gap=int(max_day_gap),
                filename_sim_threshold=float(filename_sim_threshold),
                content_sim_threshold=float(content_sim_threshold),
                enable_content_sim=bool(enable_content_sim),
                require_content_sim=bool(require_content_sim),
                embedder=embedder,
                max_embed_chars=int(max_embed_chars),
                verbose=bool(verbose),
                debug_edge_filter=bool(debug_edge_filter),
                edge_filter_stats=edge_filter_stats,
            )
            for ch in auto_chains:
                canonical = ch[0].base_topic if ch else ""
                cid = _sha256(str(proj), str(rel_dir), str(canonical))
                chains.append((cid, ch))

        # 통계
        total_docs = len(docs)
        chain_docs = sum(len(items) for _, items in chains)
        chain_count = len(chains)

        if verbose:
            scope = project or "(전체)"
            print("\n[version_chains 구축]")
            print(f"  scope: {scope}")
            print(f"  active_docs_loaded: {total_docs}")
            print(f"  overrides: excluded={excluded}, manual={manual_docs}")
            print(f"  chains: {chain_count}")
            print(f"  docs_in_chains: {chain_docs}")
            print(f"  min_chain_size: {int(min_chain_size)}")
            print(f"  max_day_gap: {int(max_day_gap)}")
            print(f"  filename_sim_threshold: {float(filename_sim_threshold):.3f}")
            print(f"  content_sim_threshold: {float(content_sim_threshold):.3f}")
            print(f"  content_sim_enabled: {bool(enable_content_sim)}")
            print(f"  require_content_sim: {bool(require_content_sim)}")
            print(f"  max_embed_chars: {int(max_embed_chars)}")
            if enable_content_sim:
                print(
                    "  edge_filter_summary: "
                    f"pairs={int(edge_filter_stats.get('candidate_pairs', 0))}, "
                    f"kept={int(edge_filter_stats.get('edges_kept', 0))}, "
                    f"dropped_low_sim={int(edge_filter_stats.get('dropped_low_sim', 0))}, "
                    f"dropped_no_embed={int(edge_filter_stats.get('dropped_no_embed', 0))}"
                )
            print(f"  dry_run: {bool(dry_run)}")

        if dry_run:
            return 0

        con.execute("BEGIN;")
        try:
            # 스코프 내 기존 레코드 삭제
            if project:
                con.execute(
                    """
                    DELETE FROM version_chains
                    WHERE doc_id IN (
                      SELECT doc_id FROM documents WHERE status='active' AND project=?
                    )
                    """,
                    (str(project),),
                )
            else:
                con.execute("DELETE FROM version_chains;")

            # 재삽입
            rows_to_insert: list[tuple[str, str, int]] = []
            for chain_id, items in chains:
                for i, d in enumerate(items, start=1):
                    rows_to_insert.append((str(chain_id), str(d.doc_id), int(i)))

            con.executemany(
                "INSERT OR REPLACE INTO version_chains(chain_id, doc_id, version_order) VALUES (?, ?, ?)",
                rows_to_insert,
            )
            con.commit()
        except Exception:
            con.rollback()
            raise

        if verbose:
            cnt = con.execute("SELECT COUNT(*) FROM version_chains").fetchone()[0]
            print(f"  version_chains_total_rows: {int(cnt)}")

        return 0
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace Brain version_chains 구축")
    p.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="metadata.db 경로")
    p.add_argument("--project", type=str, default="", help="프로젝트 필터(예: MRA). 비우면 전체")
    p.add_argument("--full", action="store_true", help="전체 스코프로 재구축(= --project 무시)")
    p.add_argument("--min-chain-size", type=int, default=2, help="체인으로 인정할 최소 문서 수")
    p.add_argument("--max-day-gap", type=int, default=14, help="버전 후보로 묶을 최대 날짜 차이(일)")
    p.add_argument("--filename-sim-threshold", type=float, default=0.70, help="파일명(접두어 제거 후) 유사도 임계값(0~1)")
    p.add_argument("--content-sim-threshold", type=float, default=0.75, help="내용 코사인 유사도 임계값(0~1)")
    p.add_argument("--no-content-sim", action="store_true", help="내용 유사도 계산을 비활성화(파일명/날짜만 사용)")
    p.add_argument("--require-content-sim", action="store_true", help="내용 유사도를 계산할 수 없는 경우 후보를 제외")
    p.add_argument("--max-embed-chars", type=int, default=12000, help="내용 유사도 계산용 최대 텍스트 길이(문자)")
    p.add_argument("--debug-edge-filter", action="store_true", help="그룹별 edge 필터 로그 출력(많아질 수 있음)")
    p.add_argument("--dry-run", action="store_true", help="DB 반영 없이 통계만 출력")
    p.add_argument("--verbose", action="store_true", help="상세 로그")
    args = p.parse_args()

    db_path = Path(args.db)
    project = None if bool(args.full) else (str(args.project or "").strip() or None)
    min_chain_size = int(args.min_chain_size)

    return int(
        build_version_chains(
            db_path=db_path,
            project=project,
            min_chain_size=min_chain_size,
            dry_run=bool(args.dry_run),
            verbose=bool(args.verbose),
            max_day_gap=int(args.max_day_gap),
            filename_sim_threshold=float(args.filename_sim_threshold),
            content_sim_threshold=float(args.content_sim_threshold),
            no_content_sim=bool(args.no_content_sim),
            require_content_sim=bool(args.require_content_sim),
            max_embed_chars=int(args.max_embed_chars),
            debug_edge_filter=bool(args.debug_edge_filter),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
