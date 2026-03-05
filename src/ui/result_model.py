"""
src/ui/result_model.py
Workspace Brain — 검색 결과 테이블 모델(QAbstractTableModel)
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from src.ui.backend import SearchRow


class Roles:
    DOC_ID = Qt.UserRole + 1
    ABS_PATH = Qt.UserRole + 2
    TAGS = Qt.UserRole + 3
    SCORE = Qt.UserRole + 4
    WHY = Qt.UserRole + 5
    MODE = Qt.UserRole + 6


class SearchResultsModel(QAbstractTableModel):
    COL_DATE = 0
    COL_PATH = 1
    COL_TAGS = 2
    COL_SCORE = 3

    def __init__(self, rows: list[SearchRow] | None = None):
        super().__init__()
        self._rows: list[SearchRow] = list(rows or [])
        self._headers = ["date", "path", "tags", "score"]

    def set_rows(self, rows: list[SearchRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows or [])
        self.endResetModel()

    def rows(self) -> list[SearchRow]:
        return list(self._rows)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self._headers):
            return self._headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        if not index.isValid():
            return None
        r = index.row()
        c = index.column()
        if r < 0 or r >= len(self._rows):
            return None

        row = self._rows[r]

        if role == Roles.DOC_ID:
            return row.doc_id
        if role == Roles.ABS_PATH:
            return row.abs_path
        if role == Roles.TAGS:
            return list(row.tags or [])
        if role == Roles.SCORE:
            return float(row.score or 0.0)
        if role == Roles.WHY:
            return row.why
        if role == Roles.MODE:
            return row.mode

        if role == Qt.ToolTipRole:
            title = (row.title or "").strip() or "(제목 없음)"
            tags = " ".join(row.tags or [])
            return "\n".join(
                [
                    f"{title}",
                    f"{row.abs_path}",
                    f"mode={row.mode}",
                    f"score={float(row.score or 0.0):.4f}",
                    f"tags={tags}" if tags else "tags=(none)",
                    f"why={row.why}" if row.why else "",
                ]
            ).strip()

        if role != Qt.DisplayRole:
            return None

        if c == self.COL_DATE:
            return row.date_prefix or ""
        if c == self.COL_PATH:
            return row.rel_path or row.abs_path or ""
        if c == self.COL_TAGS:
            return " ".join(row.tags or [])
        if c == self.COL_SCORE:
            prefix = {"fts": "F", "vector": "V", "hybrid": "H", "recent": "R"}.get(row.mode, row.mode[:1].upper())
            return f"{prefix} {float(row.score or 0.0):.4f}"
        return None

    def get_row(self, row_index: int) -> SearchRow | None:
        if row_index < 0 or row_index >= len(self._rows):
            return None
        return self._rows[row_index]

    def update_tags(self, tags_by_doc: dict[str, list[str]]) -> None:
        """
        doc_id -> tags 리스트로 모델 내부 태그를 갱신합니다.
        """
        if not tags_by_doc:
            return
        changed_rows: list[int] = []
        new_rows: list[SearchRow] = []
        for i, r in enumerate(self._rows):
            if r.doc_id not in tags_by_doc:
                new_rows.append(r)
                continue
            new_tags = tags_by_doc.get(r.doc_id, []) or []
            if list(r.tags or []) == list(new_tags):
                new_rows.append(r)
                continue
            new_rows.append(
                SearchRow(
                    doc_id=r.doc_id,
                    mode=r.mode,
                    score=r.score,
                    project=r.project,
                    title=r.title,
                    date_prefix=r.date_prefix,
                    rel_path=r.rel_path,
                    abs_path=r.abs_path,
                    tags=list(new_tags),
                    why=r.why,
                )
            )
            changed_rows.append(i)

        if not changed_rows:
            return

        self._rows = new_rows
        for i in changed_rows:
            top_left = self.index(i, self.COL_TAGS)
            bottom_right = self.index(i, self.COL_TAGS)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole, Qt.ToolTipRole])

