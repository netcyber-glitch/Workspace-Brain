"""
src/ui/filter_proxy.py
Workspace Brain — 결과 테이블 필터(태그 포함)
"""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, QModelIndex

from src.ui.result_model import Roles, SearchResultsModel


class SearchFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._required_tags: set[str] = set()

    def set_required_tags(self, tags: set[str]) -> None:
        self._required_tags = set(tags or set())
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._required_tags:
            return True
        model = self.sourceModel()
        if not isinstance(model, SearchResultsModel):
            return True
        idx = model.index(source_row, SearchResultsModel.COL_TAGS, source_parent)
        tags = model.data(idx, Roles.TAGS) or []
        tag_set = set([str(t) for t in tags])
        return self._required_tags.issubset(tag_set)

