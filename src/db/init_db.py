"""
src/db/init_db.py
Workspace Brain — SQLite DB 초기화 실행기

실행: py -3 src/db/init_db.py
  → data/metadata.db 생성 및 전체 테이블/인덱스 초기화
"""

import sqlite3
import sys
import time
from pathlib import Path

def _runtime_root() -> Path:
    if bool(getattr(sys, "frozen", False)):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


# 프로젝트 루트를 sys.path에 추가
ROOT = _runtime_root()
if not bool(getattr(sys, "frozen", False)):
    sys.path.insert(0, str(ROOT))

from src.db.schema import ALL_DDL, SCHEMA_VERSION, INDEXER_VERSION, EMBED_MODEL_ID
from src.utils.runtime import storage_root

STORE_ROOT = storage_root()
DB_PATH = STORE_ROOT / "data" / "metadata.db"


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """
    DB 파일을 생성하고 전체 스키마(테이블 + 인덱스)를 초기화합니다.
    이미 존재하는 테이블은 CREATE IF NOT EXISTS이므로 안전하게 재실행 가능.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode = WAL;")   # 쓰기 성능 향상
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA synchronous = NORMAL;")

    cur = con.cursor()
    for ddl in ALL_DDL:
        cur.executescript(ddl)

    # 메타 정보 테이블 (스키마 버전 추적용)
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS _meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    cur.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    cur.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        ("indexer_version", INDEXER_VERSION),
    )
    cur.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        ("embed_model_id", EMBED_MODEL_ID),
    )
    cur.execute(
        "INSERT OR REPLACE INTO _meta(key, value) VALUES (?, ?)",
        ("db_initialized_at", str(time.time())),
    )

    con.commit()
    return con


def verify_db(con: sqlite3.Connection) -> None:
    """생성된 테이블 목록을 출력하여 초기화 결과를 검증합니다."""
    cur = con.cursor()

    # 일반 테이블
    cur.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','index') ORDER BY type, name;"
    )
    rows = cur.fetchall()

    tables = [r for r in rows if r[1] == "table"]
    indexes = [r for r in rows if r[1] == "index"]

    print(f"\n{'='*55}")
    # cp949 콘솔(기본 Windows)에서도 깨지지 않도록 ASCII만 사용
    print("  Workspace Brain - DB 초기화 완료")
    print(f"  DB 경로: {DB_PATH}")
    print(f"{'='*55}")
    print(f"\n  [테이블 {len(tables)}개]")
    for name, _ in tables:
        print(f"    - {name}")

    print(f"\n  [인덱스 {len(indexes)}개]")
    for name, _ in indexes:
        print(f"    - {name}")

    # _meta 확인
    cur.execute("SELECT key, value FROM _meta ORDER BY key;")
    meta = cur.fetchall()
    print(f"\n  [메타 정보]")
    for k, v in meta:
        print(f"    {k}: {v}")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    print("Workspace Brain DB 초기화를 시작합니다...")
    con = init_db()
    verify_db(con)
    con.close()
    print("완료.")
