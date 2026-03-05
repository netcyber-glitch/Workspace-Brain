"""
src/ui/main_window.py
Workspace Brain — PySide6 데스크톱 UI(MVP)

구성:
  - 상단: 검색어/모드/프로젝트/limit
  - 좌측: 태그 필터(수동 태그)
  - 중앙: 결과 테이블(date/path/tags/score)
  - 우측: 탭(미리보기/연관문서/메타·태그)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.db.tags import TagOpResult, add_manual_tags, get_distinct_manual_tags, get_manual_tags_for_docs, remove_manual_tags
from src.ui.backend import (
    DocRecord,
    RelatedSection,
    SearchRow,
    build_related_sections,
    clear_version_chain_override,
    ensure_db,
    exclude_from_version_chains,
    get_doc_record,
    get_version_chain_override,
    get_version_chain_overrides,
    include_in_version_chains,
    list_projects,
    load_text_preview,
    parse_manual_tags_input,
    pin_version_chain_doc,
    search_rows,
)
from src.ui.filter_proxy import SearchFilterProxyModel
from src.ui.result_model import Roles, SearchResultsModel
from src.ui.settings_dialog import SettingsDialog, StoragePaths


@dataclass(frozen=True)
class AppPaths:
    settings_path: Path
    db_path: Path
    chroma_dir: Path
    snapshot_root: Path


class _FnWorker(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, *, gen: int, fn, kwargs: dict):
        super().__init__()
        self._gen = int(gen)
        self._fn = fn
        self._kwargs = dict(kwargs or {})

    @Slot()
    def run(self) -> None:
        try:
            res = self._fn(**self._kwargs)
            self.finished.emit(self._gen, res)
        except Exception as e:
            self.failed.emit(self._gen, f"{type(e).__name__}: {e}")


class MainWindow(QMainWindow):
    def __init__(self, *, paths: AppPaths):
        super().__init__()
        self.paths = paths
        ensure_db(self.paths.db_path)

        self._search_gen = 0
        self._preview_gen = 0
        self._related_gen = 0
        self._override_gen = 0
        self._threads: list[QThread] = []
        self._last_preview_doc_id: str | None = None
        self._last_preview_abs_path: str | None = None
        self._last_preview_text: str = ""

        self.setWindowTitle("Workspace Brain")
        self.resize(1280, 820)

        self._build_menu()

        # ── 상단 바 ─────────────────────────────────────────────────────
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("검색어(FTS/Vector/Hybrid) — 입력 후 잠시 멈추면 자동 검색")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("FTS", "fts")
        self.mode_combo.addItem("Vector", "vector")
        self.mode_combo.addItem("Hybrid", "hybrid")

        self.project_combo = QComboBox()
        self.project_combo.addItem("(전체)", "")
        for p in list_projects(db_path=self.paths.db_path):
            self.project_combo.addItem(p, p)

        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(10, 2000)
        self.limit_spin.setValue(200)
        self.limit_spin.setSingleStep(50)

        top = QWidget()
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.addWidget(QLabel("Query:"))
        top_l.addWidget(self.query_edit, 1)
        top_l.addWidget(QLabel("Mode:"))
        top_l.addWidget(self.mode_combo)
        top_l.addWidget(QLabel("Project:"))
        top_l.addWidget(self.project_combo)
        top_l.addWidget(QLabel("Limit:"))
        top_l.addWidget(self.limit_spin)

        # ── 좌측: 태그 필터 ────────────────────────────────────────────
        self.tag_search_edit = QLineEdit()
        self.tag_search_edit.setPlaceholderText("태그 검색")
        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QListWidget.NoSelection)

        self.tag_clear_btn = QPushButton("선택 초기화")
        self.tag_refresh_btn = QPushButton("태그 새로고침")

        left_box = QGroupBox("태그 필터(수동 태그 포함)")
        left_l = QVBoxLayout(left_box)
        left_l.addWidget(self.tag_search_edit)
        left_l.addWidget(self.tag_list, 1)
        left_btns = QHBoxLayout()
        left_btns.addWidget(self.tag_clear_btn)
        left_btns.addWidget(self.tag_refresh_btn)
        left_l.addLayout(left_btns)

        # ── 중앙: 결과 테이블 ──────────────────────────────────────────
        self.results_model = SearchResultsModel([])
        self.proxy = SearchFilterProxyModel(self)
        self.proxy.setSourceModel(self.results_model)
        self.proxy.setDynamicSortFilter(True)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)

        # ── 우측: 탭(미리보기/연관/메타·태그) ──────────────────────────
        self.tabs = QTabWidget()

        # preview tab
        self.preview_note = QLabel("")
        self.preview_note.setWordWrap(True)
        self.preview_open_btn = QPushButton("파일 열기")
        self.preview_open_folder_btn = QPushButton("폴더 열기")
        self.preview_browser = QTextBrowser()
        self.preview_browser.setOpenExternalLinks(False)

        pv = QWidget()
        pv_l = QVBoxLayout(pv)
        pv_btns = QHBoxLayout()
        pv_btns.addWidget(self.preview_open_btn)
        pv_btns.addWidget(self.preview_open_folder_btn)
        pv_btns.addStretch(1)
        pv_l.addLayout(pv_btns)
        pv_l.addWidget(self.preview_note)
        pv_l.addWidget(self.preview_browser, 1)
        self.tabs.addTab(pv, "미리보기")

        # related tab
        self.related_tree = QTreeWidget()
        self.related_tree.setHeaderLabels(["문서", "근거/점수"])
        self.related_tree.setColumnWidth(0, 520)
        self.related_tree.setSelectionMode(QTreeWidget.SingleSelection)

        self.related_pin_btn = QPushButton("체인 Pin…")
        self.related_exclude_btn = QPushButton("체인 제외")
        self.related_include_btn = QPushButton("제외 해제")
        self.related_clear_override_btn = QPushButton("오버라이드 삭제")
        self.related_open_indexing_btn = QPushButton("인덱싱…")

        for b in (
            self.related_pin_btn,
            self.related_exclude_btn,
            self.related_include_btn,
            self.related_clear_override_btn,
        ):
            b.setEnabled(False)
        rl = QWidget()
        rl_l = QVBoxLayout(rl)
        rl_l.addWidget(self.related_tree, 1)
        rb = QHBoxLayout()
        rb.addWidget(self.related_pin_btn)
        rb.addWidget(self.related_exclude_btn)
        rb.addWidget(self.related_include_btn)
        rb.addWidget(self.related_clear_override_btn)
        rb.addStretch(1)
        rb.addWidget(self.related_open_indexing_btn)
        rl_l.addLayout(rb)
        self.tabs.addTab(rl, "연관문서")

        # meta/tags tab
        self.meta_label = QLabel("선택 없음")
        self.meta_label.setWordWrap(True)
        self.manual_tags_label = QLabel("")
        self.manual_tags_label.setWordWrap(True)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("태그 입력(쉼표/공백 구분). 예: usi 튜닝 benchmark")
        self.tags_add_btn = QPushButton("태그 추가(선택 N건)")
        self.tags_remove_btn = QPushButton("태그 삭제(선택 N건)")

        mt = QWidget()
        mt_l = QVBoxLayout(mt)
        mt_l.addWidget(self.meta_label)
        mt_l.addWidget(QLabel("수동 태그(공통):"))
        mt_l.addWidget(self.manual_tags_label)
        mt_l.addWidget(self.tags_input)
        mt_btns = QHBoxLayout()
        mt_btns.addWidget(self.tags_add_btn)
        mt_btns.addWidget(self.tags_remove_btn)
        mt_l.addLayout(mt_btns)
        mt_l.addStretch(1)
        self.tabs.addTab(mt, "메타/태그")

        # ── 레이아웃 조합 ──────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_box)
        splitter.addWidget(self.table)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([260, 700, 420])

        root = QWidget()
        root_l = QVBoxLayout(root)
        root_l.addWidget(top)
        root_l.addWidget(splitter, 1)

        self.setCentralWidget(root)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # ── 이벤트 연결 ────────────────────────────────────────────────
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)

        self.query_edit.textChanged.connect(self._on_query_changed)
        self._debounce.timeout.connect(self._run_search)
        self.mode_combo.currentIndexChanged.connect(lambda _: self._run_search())
        self.project_combo.currentIndexChanged.connect(lambda _: self._run_search())
        self.limit_spin.valueChanged.connect(lambda _: self._run_search())

        self.tag_search_edit.textChanged.connect(self._apply_tag_search_filter)
        self.tag_clear_btn.clicked.connect(self._clear_tag_selection)
        self.tag_refresh_btn.clicked.connect(self._reload_tag_list)
        self.tag_list.itemChanged.connect(self._on_tag_check_changed)

        sel_model = self.table.selectionModel()
        sel_model.selectionChanged.connect(self._on_selection_changed)
        self.table.doubleClicked.connect(self._open_current_file)

        self.preview_open_btn.clicked.connect(self._open_current_file)
        self.preview_open_folder_btn.clicked.connect(self._open_current_folder)
        self.related_tree.itemDoubleClicked.connect(self._on_related_double_clicked)
        self.related_tree.itemSelectionChanged.connect(self._on_related_selection_changed)

        self.related_pin_btn.clicked.connect(self._on_related_pin_clicked)
        self.related_exclude_btn.clicked.connect(self._on_related_exclude_clicked)
        self.related_include_btn.clicked.connect(self._on_related_include_clicked)
        self.related_clear_override_btn.clicked.connect(self._on_related_clear_override_clicked)
        self.related_open_indexing_btn.clicked.connect(self._on_related_open_indexing_clicked)

        self.tags_add_btn.clicked.connect(self._add_tags_to_selection)
        self.tags_remove_btn.clicked.connect(self._remove_tags_from_selection)

        # 단축키(Everything 감성)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: self.query_edit.setFocus())
        QShortcut(QKeySequence("Return"), self.table, activated=self._open_current_file)
        QShortcut(QKeySequence("Ctrl+Return"), self.table, activated=self._open_current_folder)
        QShortcut(QKeySequence("T"), self.table, activated=self._add_tags_to_selection)

        # 초기 로드
        self._reload_tag_list()
        self._run_search()

    def _build_menu(self) -> None:
        m_settings = self.menuBar().addMenu("설정")
        act_prefs = QAction("환경설정…", self)
        act_prefs.triggered.connect(self._open_settings_dialog)
        m_settings.addAction(act_prefs)

    def _open_settings_dialog(self) -> None:
        dlg = SettingsDialog(
            settings_path=self.paths.settings_path,
            storage_paths=StoragePaths(
                db_path=self.paths.db_path,
                chroma_dir=self.paths.chroma_dir,
                snapshot_root=self.paths.snapshot_root,
            ),
            parent=self,
        )
        dlg.settings_applied.connect(self._on_settings_applied)
        dlg.exec()

    def _reload_project_combo(self) -> None:
        cur = str(self.project_combo.currentData() or "")
        self.project_combo.blockSignals(True)
        try:
            self.project_combo.clear()
            self.project_combo.addItem("(전체)", "")
            for p in list_projects(db_path=self.paths.db_path):
                self.project_combo.addItem(p, p)
        finally:
            self.project_combo.blockSignals(False)

        if cur:
            idx = self.project_combo.findData(cur)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)

    @Slot(object, object)
    def _on_settings_applied(self, settings: object, storage_paths: object) -> None:
        # settings는 dict, storage_paths는 StoragePaths로 들어옴(시그니처 유연 처리)
        try:
            sp = storage_paths
            db_path = Path(getattr(sp, "db_path"))
            chroma_dir = Path(getattr(sp, "chroma_dir"))
            snapshot_root = Path(getattr(sp, "snapshot_root"))
        except Exception:
            return

        paths_changed = (db_path != self.paths.db_path) or (chroma_dir != self.paths.chroma_dir) or (snapshot_root != self.paths.snapshot_root)

        # 실행 중 작업 무효화
        self._search_gen += 1
        self._preview_gen += 1
        self._related_gen += 1

        self.paths = AppPaths(
            settings_path=self.paths.settings_path,
            db_path=db_path,
            chroma_dir=chroma_dir,
            snapshot_root=snapshot_root,
        )

        if paths_changed:
            try:
                ensure_db(self.paths.db_path)
            except Exception as e:
                QMessageBox.critical(self, "오류", f"DB 초기화에 실패했습니다.\n\n{type(e).__name__}: {e}")

        self._reload_project_combo()
        self._reload_tag_list()
        self._run_search()

    # ── 태그 리스트 ───────────────────────────────────────────────────
    def _reload_tag_list(self) -> None:
        self.tag_list.blockSignals(True)
        try:
            project = str(self.project_combo.currentData() or "").strip() or None
            tags = get_distinct_manual_tags(db_path=self.paths.db_path, project=project, limit=5000)
            checked = self._checked_tags()

            self.tag_list.clear()
            for t in tags:
                it = QListWidgetItem(t)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if t in checked else Qt.Unchecked)
                self.tag_list.addItem(it)
        finally:
            self.tag_list.blockSignals(False)
        self._apply_tag_search_filter()
        self._apply_required_tags()

    def _apply_tag_search_filter(self) -> None:
        q = (self.tag_search_edit.text() or "").strip().lower()
        for i in range(self.tag_list.count()):
            it = self.tag_list.item(i)
            if not q:
                it.setHidden(False)
            else:
                it.setHidden(q not in (it.text() or "").lower())

    def _checked_tags(self) -> set[str]:
        out: set[str] = set()
        for i in range(self.tag_list.count()):
            it = self.tag_list.item(i)
            if it.checkState() == Qt.Checked:
                out.add(it.text())
        return out

    def _clear_tag_selection(self) -> None:
        self.tag_list.blockSignals(True)
        try:
            for i in range(self.tag_list.count()):
                it = self.tag_list.item(i)
                it.setCheckState(Qt.Unchecked)
        finally:
            self.tag_list.blockSignals(False)
        self._apply_required_tags()

    def _on_tag_check_changed(self, _: QListWidgetItem) -> None:
        self._apply_required_tags()

    def _apply_required_tags(self) -> None:
        self.proxy.set_required_tags(self._checked_tags())
        self.status.showMessage(f"태그 필터: {', '.join(sorted(self._checked_tags()))}" if self._checked_tags() else "태그 필터 없음", 2500)

    # ── 검색 ───────────────────────────────────────────────────────────
    def _on_query_changed(self, _: str) -> None:
        self._debounce.start()

    def _run_search(self) -> None:
        self._search_gen += 1
        gen = int(self._search_gen)

        q = self.query_edit.text() or ""
        mode = str(self.mode_combo.currentData() or "fts")
        project = str(self.project_combo.currentData() or "").strip() or None
        limit = int(self.limit_spin.value())

        self.status.showMessage("검색 중...", 2000)
        self._start_thread_task(
            gen=gen,
            fn=search_rows,
            kwargs={
                "db_path": self.paths.db_path,
                "chroma_dir": self.paths.chroma_dir,
                "mode": mode,
                "query": q,
                "project": project,
                "limit": limit,
            },
            on_ok=self._on_search_done,
            on_fail=self._on_search_failed,
        )

    def _on_search_done(self, gen: int, res: object) -> None:
        if int(gen) != int(self._search_gen):
            return
        rows: list[SearchRow] = list(res or [])

        # 선택 유지(가능하면)
        prev_doc_id = self._current_doc_id()

        self.results_model.set_rows(rows)
        self.table.resizeColumnsToContents()
        self._apply_required_tags()

        self.status.showMessage(f"검색 완료: {len(rows)}건", 2500)

        if prev_doc_id:
            self._select_doc_id(prev_doc_id)
        if self.table.selectionModel().selectedRows():
            return
        if self.proxy.rowCount() > 0:
            self.table.selectRow(0)

    def _on_search_failed(self, gen: int, msg: str) -> None:
        if int(gen) != int(self._search_gen):
            return
        self.status.showMessage(f"검색 실패: {msg}", 5000)
        QMessageBox.warning(self, "검색 실패", msg)

    # ── 선택/미리보기/연관문서 ─────────────────────────────────────────
    def _on_selection_changed(self, _: QItemSelection, __: QItemSelection) -> None:
        doc_ids = self._selected_doc_ids()
        if not doc_ids:
            self.meta_label.setText("선택 없음")
            self.manual_tags_label.setText("")
            return

        current = self._current_row()
        title = (current.title or "").strip() if current else ""
        self.meta_label.setText(f"선택 {len(doc_ids)}건\n{title}" if title else f"선택 {len(doc_ids)}건")

        # 공통 태그 표시
        tags_by = get_manual_tags_for_docs(db_path=self.paths.db_path, doc_ids=doc_ids)
        tag_sets = [set(tags_by.get(d, []) or []) for d in doc_ids]
        common = set.intersection(*tag_sets) if tag_sets else set()
        self.manual_tags_label.setText(" ".join(sorted(common)) if common else "(없음)")

        # preview는 현재 포커스 1건 기준
        if current:
            self._load_preview_for(current.doc_id, current.abs_path)

        self.tags_add_btn.setText(f"태그 추가(선택 {len(doc_ids)}건)")
        self.tags_remove_btn.setText(f"태그 삭제(선택 {len(doc_ids)}건)")

    def _load_preview_for(self, doc_id: str, abs_path: str) -> None:
        self._preview_gen += 1
        gen = int(self._preview_gen)
        self.preview_note.setText("로딩 중...")
        self.preview_browser.setPlainText("")
        self.related_tree.clear()
        self._set_related_override_buttons_enabled(False)

        self._start_thread_task(
            gen=gen,
            fn=load_text_preview,
            kwargs={"abs_path": abs_path, "max_chars": 200_000},
            on_ok=lambda g, res: self._on_preview_done(g, res, doc_id, abs_path),
            on_fail=self._on_preview_failed,
        )

    def _on_preview_done(self, gen: int, res: object, doc_id: str, abs_path: str) -> None:
        if int(gen) != int(self._preview_gen):
            return
        try:
            text, note = res  # type: ignore[misc]
        except Exception:
            text, note = ("", "미리보기 파싱 실패")

        ext = Path(abs_path).suffix.lower()
        if ext == ".md":
            self.preview_browser.setMarkdown(text or "")
        else:
            self.preview_browser.setPlainText(text or "")
        self.preview_note.setText(note or "")

        self._last_preview_doc_id = str(doc_id)
        self._last_preview_abs_path = str(abs_path)
        self._last_preview_text = str(text or "")

        # 연관문서
        self._related_gen += 1
        rgen = int(self._related_gen)
        self._start_thread_task(
            gen=rgen,
            fn=self._build_related,
            kwargs={"doc_id": doc_id, "preview_text": text or ""},
            on_ok=self._on_related_done,
            on_fail=self._on_related_failed,
        )

    def _on_preview_failed(self, gen: int, msg: str) -> None:
        if int(gen) != int(self._preview_gen):
            return
        self.preview_note.setText(f"미리보기 실패: {msg}")
        self.preview_browser.setPlainText("")

    def _build_related(self, *, doc_id: str, preview_text: str) -> list[RelatedSection]:
        doc = get_doc_record(db_path=self.paths.db_path, doc_id=doc_id)
        if not doc:
            return []
        return build_related_sections(
            db_path=self.paths.db_path,
            chroma_dir=self.paths.chroma_dir,
            doc=doc,
            preview_text=preview_text,
            days_stream=7,
            limit_each=15,
        )

    def _on_related_done(self, gen: int, res: object) -> None:
        if int(gen) != int(self._related_gen):
            return
        sections: list[RelatedSection] = list(res or [])
        self.related_tree.clear()

        related_doc_ids: list[str] = []
        for sec in sections:
            for it in sec.items:
                if it.doc_id:
                    related_doc_ids.append(str(it.doc_id))
        overrides_by = get_version_chain_overrides(db_path=self.paths.db_path, doc_ids=related_doc_ids)

        for sec in sections:
            top = QTreeWidgetItem([f"{sec.title} ({len(sec.items)})", ""])
            top.setFirstColumnSpanned(False)
            self.related_tree.addTopLevelItem(top)
            for it in sec.items:
                score = f"{float(it.score):.4f}" if it.score is not None else ""
                label = f"{it.date_prefix} {it.rel_path or Path(it.abs_path).name}".strip()
                why = f"{it.why} {score}".strip()
                ov = overrides_by.get(str(it.doc_id))
                badge = self._format_chain_override_badge(ov)
                if badge:
                    why = f"{why} {badge}".strip()
                child = QTreeWidgetItem([label, why])
                child.setData(0, Roles.DOC_ID, it.doc_id)
                child.setData(0, Roles.ABS_PATH, it.abs_path)
                top.addChild(child)
            top.setExpanded(True)

        if not sections:
            self.related_tree.addTopLevelItem(QTreeWidgetItem(["(연관문서 없음)", ""]))
        self._on_related_selection_changed()

    def _on_related_failed(self, gen: int, msg: str) -> None:
        if int(gen) != int(self._related_gen):
            return
        self.related_tree.clear()
        self.related_tree.addTopLevelItem(QTreeWidgetItem([f"(연관문서 실패: {msg})", ""]))
        self._set_related_override_buttons_enabled(False)

    def _on_related_double_clicked(self, item: QTreeWidgetItem, _: int) -> None:
        doc_id = str(item.data(0, Roles.DOC_ID) or "").strip()
        abs_path = str(item.data(0, Roles.ABS_PATH) or "").strip()
        if doc_id:
            self._select_doc_id(doc_id)
            self._load_preview_for(doc_id, abs_path or "")
            self.tabs.setCurrentIndex(0)
            return
        if abs_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path))

    def _set_related_override_buttons_enabled(self, enabled: bool) -> None:
        self.related_pin_btn.setEnabled(bool(enabled))
        self.related_exclude_btn.setEnabled(bool(enabled))
        self.related_include_btn.setEnabled(False)
        self.related_clear_override_btn.setEnabled(False)

    def _format_chain_override_badge(self, ov: object) -> str:
        try:
            if ov is None:
                return ""
            ex = bool(getattr(ov, "exclude_from_chains"))
            key = str(getattr(ov, "manual_chain_key") or "").strip()
        except Exception:
            return ""
        if ex:
            return "[EXCLUDE]"
        if key:
            key_s = key if len(key) <= 24 else (key[:21] + "...")
            return f"[PIN:{key_s}]"
        return ""

    def _related_selected_doc_id(self) -> str | None:
        it = self.related_tree.currentItem()
        if not it:
            return None
        did = str(it.data(0, Roles.DOC_ID) or "").strip()
        return did or None

    def _on_related_selection_changed(self) -> None:
        did = self._related_selected_doc_id()
        if not did:
            self._set_related_override_buttons_enabled(False)
            return

        self.related_pin_btn.setEnabled(True)
        self.related_exclude_btn.setEnabled(True)

        ov = get_version_chain_override(db_path=self.paths.db_path, doc_id=did)
        if ov is None:
            self.related_include_btn.setEnabled(False)
            self.related_clear_override_btn.setEnabled(False)
            self.status.showMessage("버전 체인 오버라이드: 없음", 2500)
            return

        self.related_include_btn.setEnabled(bool(ov.exclude_from_chains))
        self.related_clear_override_btn.setEnabled(True)
        if ov.exclude_from_chains:
            self.related_exclude_btn.setEnabled(False)
            self.status.showMessage("버전 체인 오버라이드: EXCLUDE", 3500)
            return

        key = str(ov.manual_chain_key or "").strip()
        if key:
            self.status.showMessage(f"버전 체인 오버라이드: PIN key={key}", 3500)
        else:
            self.status.showMessage("버전 체인 오버라이드: (기록만 있음)", 3500)

    def _suggest_manual_chain_key(self, filename: str) -> str:
        import re

        name = (filename or "").strip()
        if not name:
            return ""
        name = re.sub(r"^\\d{4}-\\d{2}-\\d{2}_?", "", name)
        stem = Path(name).stem
        stem = re.sub(r"[\\s_\\-]+", "_", stem.lower()).strip("_")
        return stem

    def _refresh_related(self) -> None:
        if not self._last_preview_doc_id:
            return
        self._related_gen += 1
        rgen = int(self._related_gen)
        self._start_thread_task(
            gen=rgen,
            fn=self._build_related,
            kwargs={"doc_id": str(self._last_preview_doc_id), "preview_text": str(self._last_preview_text or "")},
            on_ok=self._on_related_done,
            on_fail=self._on_related_failed,
        )

    def _run_override_task(self, *, action: str, fn, kwargs: dict) -> None:
        self._override_gen += 1
        gen = int(self._override_gen)
        self.status.showMessage(f"버전 체인 오버라이드 적용 중... ({action})", 2000)
        self._start_thread_task(
            gen=gen,
            fn=fn,
            kwargs=kwargs,
            on_ok=lambda g, res: self._on_override_done(g, res, action),
            on_fail=lambda g, msg: self._on_override_failed(g, msg, action),
        )

    def _on_override_done(self, gen: int, _res: object, action: str) -> None:
        if int(gen) != int(self._override_gen):
            return
        self.status.showMessage(
            f"오버라이드 저장 완료: {action} (버전 체인 반영은 '인덱싱→버전 체인 재구축' 필요)",
            5000,
        )
        self._refresh_related()

    def _on_override_failed(self, gen: int, msg: str, action: str) -> None:
        if int(gen) != int(self._override_gen):
            return
        QMessageBox.warning(self, "오버라이드 실패", f"{action} 실패\n\n{msg}")

    def _on_related_pin_clicked(self) -> None:
        did = self._related_selected_doc_id()
        if not did:
            return
        doc = get_doc_record(db_path=self.paths.db_path, doc_id=did)
        if not doc:
            QMessageBox.warning(self, "오류", "문서를 찾을 수 없습니다.")
            return
        default_key = self._suggest_manual_chain_key(doc.filename or Path(doc.abs_path).name)
        key, ok = QInputDialog.getText(
            self,
            "체인 Pin",
            "manual_chain_key(같은 키면 같은 체인으로 강제 묶기):",
            text=default_key,
        )
        if not ok:
            return
        key = str(key or "").strip()
        if not key:
            return
        self._run_override_task(
            action=f"PIN key={key}",
            fn=pin_version_chain_doc,
            kwargs={"db_path": self.paths.db_path, "doc_id": did, "manual_chain_key": key},
        )

    def _on_related_exclude_clicked(self) -> None:
        did = self._related_selected_doc_id()
        if not did:
            return
        self._run_override_task(
            action="EXCLUDE",
            fn=exclude_from_version_chains,
            kwargs={"db_path": self.paths.db_path, "doc_id": did},
        )

    def _on_related_include_clicked(self) -> None:
        did = self._related_selected_doc_id()
        if not did:
            return
        self._run_override_task(
            action="INCLUDE",
            fn=include_in_version_chains,
            kwargs={"db_path": self.paths.db_path, "doc_id": did},
        )

    def _on_related_clear_override_clicked(self) -> None:
        did = self._related_selected_doc_id()
        if not did:
            return
        self._run_override_task(
            action="CLEAR",
            fn=clear_version_chain_override,
            kwargs={"db_path": self.paths.db_path, "doc_id": did},
        )

    def _on_related_open_indexing_clicked(self) -> None:
        did = self._related_selected_doc_id()
        doc = get_doc_record(db_path=self.paths.db_path, doc_id=did) if did else None
        project = str(doc.project or "").strip() if doc else ""

        dlg = SettingsDialog(
            settings_path=self.paths.settings_path,
            storage_paths=StoragePaths(
                db_path=self.paths.db_path,
                chroma_dir=self.paths.chroma_dir,
                snapshot_root=self.paths.snapshot_root,
            ),
            parent=self,
        )
        dlg.settings_applied.connect(self._on_settings_applied)
        try:
            dlg.tabs.setCurrentIndex(2)  # 인덱싱 탭
            if project:
                idx = dlg.index_project_combo.findData(project)
                if idx >= 0:
                    dlg.index_project_combo.setCurrentIndex(idx)
        except Exception:
            pass
        dlg.exec()

    # ── 태그 편집 ─────────────────────────────────────────────────────
    def _selected_doc_ids(self) -> list[str]:
        ids: list[str] = []
        sel = self.table.selectionModel().selectedRows()
        for proxy_idx in sel:
            src_idx = self.proxy.mapToSource(proxy_idx)
            did = self.results_model.data(src_idx, Roles.DOC_ID)
            if did:
                ids.append(str(did))
        # 순서 보존 dedup
        seen: set[str] = set()
        out: list[str] = []
        for d in ids:
            if d in seen:
                continue
            seen.add(d)
            out.append(d)
        return out

    def _add_tags_to_selection(self) -> None:
        doc_ids = self._selected_doc_ids()
        tags = parse_manual_tags_input(self.tags_input.text())
        if not doc_ids or not tags:
            self.status.showMessage("태그 추가: 선택/입력 없음", 2500)
            return
        r: TagOpResult = add_manual_tags(db_path=self.paths.db_path, doc_ids=doc_ids, tags=tags)
        self.status.showMessage(f"태그 추가 완료: inserted={r.inserted}", 3000)
        self._refresh_tags_after_edit(doc_ids)

    def _remove_tags_from_selection(self) -> None:
        doc_ids = self._selected_doc_ids()
        tags = parse_manual_tags_input(self.tags_input.text())
        if not doc_ids or not tags:
            self.status.showMessage("태그 삭제: 선택/입력 없음", 2500)
            return
        r: TagOpResult = remove_manual_tags(db_path=self.paths.db_path, doc_ids=doc_ids, tags=tags)
        self.status.showMessage(f"태그 삭제 완료: deleted={r.deleted}", 3000)
        self._refresh_tags_after_edit(doc_ids)

    def _refresh_tags_after_edit(self, doc_ids: list[str]) -> None:
        tags_by = get_manual_tags_for_docs(db_path=self.paths.db_path, doc_ids=doc_ids)
        self.results_model.update_tags(tags_by)
        self._reload_tag_list()
        self._on_selection_changed(QItemSelection(), QItemSelection())

    # ── 파일 열기 ─────────────────────────────────────────────────────
    def _current_row(self) -> SearchRow | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        src = self.proxy.mapToSource(idx)
        return self.results_model.get_row(src.row())

    def _current_doc_id(self) -> str | None:
        row = self._current_row()
        return str(row.doc_id) if row else None

    def _open_current_file(self) -> None:
        row = self._current_row()
        if not row:
            return
        if row.abs_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(row.abs_path))

    def _open_current_folder(self) -> None:
        row = self._current_row()
        if not row or not row.abs_path:
            return
        try:
            folder = str(Path(row.abs_path).resolve().parent)
        except Exception:
            folder = str(Path(row.abs_path).parent)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _select_doc_id(self, doc_id: str) -> None:
        if not doc_id:
            return
        # proxy 기준으로 선형 탐색(상한: limit)
        for prow in range(self.proxy.rowCount()):
            pidx = self.proxy.index(prow, 0)
            sidx = self.proxy.mapToSource(pidx)
            did = str(self.results_model.data(sidx, Roles.DOC_ID) or "")
            if did == doc_id:
                self.table.setCurrentIndex(pidx)
                self.table.selectionModel().select(
                    pidx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
                )
                self.table.scrollTo(pidx)
                return

    # ── 스레드 실행 유틸 ───────────────────────────────────────────────
    def _start_thread_task(self, *, gen: int, fn, kwargs: dict, on_ok, on_fail) -> None:
        worker = _FnWorker(gen=gen, fn=fn, kwargs=kwargs)
        thread = QThread(self)
        worker.moveToThread(thread)

        worker.finished.connect(lambda g, res: self._finish_thread(thread, on_ok, g, res))
        worker.failed.connect(lambda g, msg: self._finish_thread(thread, on_fail, g, msg))

        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)
        thread.start()

    def _finish_thread(self, thread: QThread, cb, gen: int, payload) -> None:
        try:
            cb(int(gen), payload)
        finally:
            thread.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        # 종료 시점에 백그라운드 스레드가 남아있으면 종료 코드가 불안정해질 수 있어,
        # 가능한 범위에서 정리(wait)합니다.
        try:
            self._search_gen += 1
            self._preview_gen += 1
            self._related_gen += 1

            for t in list(self._threads):
                try:
                    t.quit()
                except Exception:
                    pass
            for t in list(self._threads):
                try:
                    t.wait(1200)
                except Exception:
                    pass
        finally:
            super().closeEvent(event)


def run_gui(*, settings_path: Path, db_path: Path, chroma_dir: Path, snapshot_root: Path) -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    app = QApplication.instance() or QApplication([])
    w = MainWindow(
        paths=AppPaths(
            settings_path=settings_path,
            db_path=db_path,
            chroma_dir=chroma_dir,
            snapshot_root=snapshot_root,
        )
    )
    w.show()
    return app.exec()
