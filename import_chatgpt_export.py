"""
import_chatgpt_export.py
Workspace Brain — ChatGPT Data Export(conversations.json / export zip) -> .md 변환 + 인덱싱

권장 흐름:
1) ChatGPT Data Export ZIP 또는 conversations.json을 준비
2) 아래 커맨드로 변환
   python D:\\Workspace_Brain\\import_chatgpt_export.py --input D:\\path\\to\\export.zip
3) 생성된 .md는 data/archive 아래에 영구 보관되고, 즉시 DB/FTS 인덱싱됩니다.
"""

from __future__ import annotations

import argparse
import re
import zipfile
from datetime import datetime
from pathlib import Path

from src.archive.chatgpt_export import parse_conversations, to_iso
from src.scanner.scanner import FileScanner
from src.utils.settings import load_settings


ROOT = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = ROOT / "data" / "archive" / "chatgpt"


def _safe_filename(name: str, *, max_len: int = 80) -> str:
    s = (name or "").strip()
    if not s:
        return "untitled"
    s = re.sub(r"[\\/:*?\"<>|]", "_", s)
    s = re.sub(r"\\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def _resolve_conversations_json(input_path: Path, work_dir: Path) -> Path:
    if input_path.suffix.lower() == ".json":
        return input_path

    if input_path.suffix.lower() != ".zip":
        raise ValueError("입력은 .zip 또는 conversations.json(.json)만 지원합니다.")

    with zipfile.ZipFile(str(input_path), "r") as zf:
        # zip 내부에서 conversations.json 찾기
        cand = None
        for name in zf.namelist():
            if name.lower().endswith("conversations.json"):
                cand = name
                break
        if not cand:
            raise ValueError("ZIP 안에서 conversations.json을 찾지 못했습니다.")

        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "conversations.json"
        with zf.open(cand, "r") as src, out.open("wb") as dst:
            dst.write(src.read())
        return out


def _conv_to_md(conv) -> str:
    title = conv.title or "Untitled"
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    if conv.conv_id:
        lines.append(f"- conversation_id: {conv.conv_id}")
    if conv.create_time is not None:
        lines.append(f"- created_at: {to_iso(conv.create_time)}")
    if conv.update_time is not None:
        lines.append(f"- updated_at: {to_iso(conv.update_time)}")
    lines.append("- source: ChatGPT Data Export")
    lines.append("")

    for msg in conv.messages:
        role = (msg.role or "unknown").upper()
        ts = to_iso(msg.create_time)
        header = f"## {role}" + (f" ({ts})" if ts else "")
        lines.append(header)
        lines.append("")
        lines.append((msg.content or "").rstrip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="ChatGPT export -> Markdown 변환 + 인덱싱")
    p.add_argument("--input", required=True, type=str, help="export.zip 또는 conversations.json 경로")
    p.add_argument("--out", type=str, default=str(DEFAULT_OUT_DIR), help="출력 루트(기본: data/archive/chatgpt)")
    p.add_argument("--min-chars", type=int, default=800, help="대화 본문 최소 글자 수(필터)")
    p.add_argument(
        "--keywords",
        type=str,
        default="",
        help="쉼표로 구분한 키워드(하나라도 포함 시 통과). 비우면 키워드 필터 없음",
    )
    p.add_argument("--limit", type=int, default=0, help="최대 변환 개수(0=전체)")
    p.add_argument("--no-index", action="store_true", help="변환 후 DB 인덱싱을 수행하지 않음")
    args = p.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"입력 파일이 없습니다: {input_path}")
        return 2

    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    work_dir = ROOT / "data" / "imports" / "_tmp_chatgpt_export"
    conversations_json = _resolve_conversations_json(input_path, work_dir)

    conversations = parse_conversations(conversations_json)
    if not conversations:
        print("파싱 결과가 비었습니다. conversations.json 구조를 확인해 주세요.")
        return 2

    keywords = [k.strip().lower() for k in (args.keywords or "").split(",") if k.strip()]
    min_chars = max(0, int(args.min_chars))
    limit = max(0, int(args.limit))

    written = 0
    skipped = 0

    for conv in conversations:
        if limit and written >= limit:
            break

        body_len = sum(len(m.content or "") for m in conv.messages)
        if body_len < min_chars:
            skipped += 1
            continue

        if keywords:
            blob = (conv.title + "\n" + "\n".join(m.content for m in conv.messages)).lower()
            if not any(k in blob for k in keywords):
                skipped += 1
                continue

        # 날짜 기반 폴더/파일명
        ts = conv.create_time or conv.update_time
        dt = datetime.fromtimestamp(ts) if ts else datetime.now()
        ym = dt.strftime("%Y-%m")
        ymd = dt.strftime("%Y-%m-%d")
        id8 = (conv.conv_id or "noid").replace("-", "")[:8]
        slug = _safe_filename(conv.title)

        out_dir = out_root / ym
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ymd}_ChatGPT_{slug}_{id8}.md"

        if out_path.exists():
            skipped += 1
            continue

        out_path.write_text(_conv_to_md(conv), encoding="utf-8")
        written += 1

    print("\n[변환 완료]")
    print(f"  written: {written}건")
    print(f"  skipped: {skipped}건")
    print(f"  out_root: {out_root}")

    if args.no_index:
        return 0

    # 변환된 결과를 즉시 인덱싱 (설정 기반)
    settings = load_settings()
    with FileScanner(settings=settings) as scanner:
        _ = scanner.scan_project("Workspace_Brain", ROOT, verbose=True)
        _ = scanner.mark_deleted()

    print("\n[인덱싱 완료]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

