"""
src/ui/settings_dialog.py
Workspace Brain — 환경설정(프로젝트/경로/인덱싱) 다이얼로그
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.utils.settings import default_storage_settings, load_settings, save_settings
from src.utils.runtime import runtime_root, tool_cmd
from src.utils.optional_deps import has_content_sim_deps, has_vector_deps


@dataclass(frozen=True)
class StoragePaths:
    db_path: Path
    chroma_dir: Path
    snapshot_root: Path


def _norm_prefix(s: str) -> str:
    return (s or "").strip().replace("\\", "/").strip("/").lower()


def _parse_prefix_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in (text or "").replace(",", "\n").splitlines():
        p = _norm_prefix(line)
        if not p:
            continue
        out.append(p)
    # 중복 제거(순서 유지)
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


class SettingsDialog(QDialog):
    settings_applied = Signal(dict, object)  # (settings_dict, StoragePaths)

    def __init__(self, *, settings_path: Path, storage_paths: StoragePaths, parent=None):
        super().__init__(parent)
        self.settings_path = Path(settings_path)
        self._storage_paths = storage_paths
        self._proc: QProcess | None = None

        self.setWindowTitle("환경설정")
        self.resize(980, 700)

        self._settings = load_settings(self.settings_path)
        self._ensure_storage_defaults()

        self.tabs = QTabWidget()

        self._build_projects_tab()
        self._build_paths_tab()
        self._build_indexing_tab()

        self.apply_btn = QPushButton("적용")
        self.close_btn = QPushButton("닫기")

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.close_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.tabs, 1)
        root.addLayout(btns)

        self.apply_btn.clicked.connect(self._apply_settings)
        self.close_btn.clicked.connect(self.reject)

        self._reload_project_list()
        self._refresh_index_project_combo()

    # ── 공통: settings/storage ───────────────────────────────────────────

    def _ensure_storage_defaults(self) -> None:
        storage = self._settings.get("storage")
        if not isinstance(storage, dict):
            storage = {}
        defaults = default_storage_settings()
        for k, v in defaults.items():
            storage.setdefault(k, v)
        self._settings["storage"] = storage

    def _read_storage_paths_from_ui(self) -> StoragePaths:
        db_path = Path(str(self.db_path_edit.text()).strip() or str(self._storage_paths.db_path))
        chroma_dir = Path(str(self.chroma_dir_edit.text()).strip() or str(self._storage_paths.chroma_dir))
        snapshot_root = Path(str(self.snapshot_root_edit.text()).strip() or str(self._storage_paths.snapshot_root))
        return StoragePaths(db_path=db_path, chroma_dir=chroma_dir, snapshot_root=snapshot_root)

    # ── 탭: 프로젝트/폴더 ────────────────────────────────────────────────

    def _build_projects_tab(self) -> None:
        w = QWidget()
        layout = QHBoxLayout(w)

        # 좌측: 목록 + 버튼
        left = QVBoxLayout()
        self.project_list = QListWidget()
        self.add_project_btn = QPushButton("추가")
        self.remove_project_btn = QPushButton("삭제")

        left.addWidget(QLabel("프로젝트(구조화 대상 폴더):"))
        left.addWidget(self.project_list, 1)
        left_btns = QHBoxLayout()
        left_btns.addWidget(self.add_project_btn)
        left_btns.addWidget(self.remove_project_btn)
        left.addLayout(left_btns)

        # 우측: 상세
        right_box = QGroupBox("프로젝트 상세")
        form = QGridLayout(right_box)

        self.project_name_label = QLabel("-")
        self.project_enabled_chk = QCheckBox("사용(Enabled)")

        self.project_root_edit = QLineEdit()
        self.project_root_edit.setReadOnly(True)
        self.project_root_btn = QPushButton("폴더 선택…")

        self.include_prefixes_edit = QPlainTextEdit()
        self.include_prefixes_edit.setPlaceholderText("예: docs\\nsrc\\ntests  (빈칸이면 전체 포함)")
        self.skip_prefixes_edit = QPlainTextEdit()
        self.skip_prefixes_edit.setPlaceholderText("예: public\\nnode_modules")

        form.addWidget(QLabel("이름:"), 0, 0)
        form.addWidget(self.project_name_label, 0, 1, 1, 2)
        form.addWidget(self.project_enabled_chk, 1, 1, 1, 2)
        form.addWidget(QLabel("루트:"), 2, 0)
        form.addWidget(self.project_root_edit, 2, 1)
        form.addWidget(self.project_root_btn, 2, 2)
        form.addWidget(QLabel("포함(prefix):"), 3, 0, Qt.AlignTop)
        form.addWidget(self.include_prefixes_edit, 3, 1, 1, 2)
        form.addWidget(QLabel("제외(prefix):"), 4, 0, Qt.AlignTop)
        form.addWidget(self.skip_prefixes_edit, 4, 1, 1, 2)

        layout.addLayout(left, 0)
        layout.addWidget(right_box, 1)

        self.tabs.addTab(w, "프로젝트/폴더")

        self.project_list.currentItemChanged.connect(self._on_project_selected)
        self.add_project_btn.clicked.connect(self._add_project)
        self.remove_project_btn.clicked.connect(self._remove_project)
        self.project_root_btn.clicked.connect(self._choose_project_root)

    def _projects_dict(self) -> dict:
        projects = self._settings.get("projects")
        if not isinstance(projects, dict):
            projects = {}
            self._settings["projects"] = projects
        return projects

    def _current_project_name(self) -> str | None:
        item = self.project_list.currentItem()
        if not item:
            return None
        name = str(item.data(Qt.UserRole) or "").strip()
        return name or None

    def _commit_project_fields(self) -> None:
        name = self._current_project_name()
        if not name:
            return
        projects = self._projects_dict()
        cfg = projects.get(name, {})
        if not isinstance(cfg, dict):
            cfg = {}

        cfg["enabled"] = bool(self.project_enabled_chk.isChecked())
        cfg["root"] = str(self.project_root_edit.text()).strip()
        cfg["include_rel_path_prefixes"] = _parse_prefix_lines(self.include_prefixes_edit.toPlainText())
        cfg["skip_rel_path_prefixes"] = _parse_prefix_lines(self.skip_prefixes_edit.toPlainText())
        projects[name] = cfg

    def _reload_project_list(self) -> None:
        self.project_list.blockSignals(True)
        try:
            self.project_list.clear()
            projects = self._projects_dict()
            for name in sorted(projects.keys()):
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                self.project_list.addItem(item)
        finally:
            self.project_list.blockSignals(False)

        if self.project_list.count() > 0:
            self.project_list.setCurrentRow(0)
        else:
            self._clear_project_fields()

        self._refresh_index_project_combo()

    def _clear_project_fields(self) -> None:
        self.project_name_label.setText("-")
        self.project_enabled_chk.setChecked(False)
        self.project_root_edit.setText("")
        self.include_prefixes_edit.setPlainText("")
        self.skip_prefixes_edit.setPlainText("")

    def _on_project_selected(self, cur: QListWidgetItem | None, prev: QListWidgetItem | None) -> None:
        if prev is not None:
            self._commit_project_fields()

        if cur is None:
            self._clear_project_fields()
            return

        name = str(cur.data(Qt.UserRole) or "").strip()
        projects = self._projects_dict()
        cfg = projects.get(name, {})
        if not isinstance(cfg, dict):
            cfg = {}

        self.project_name_label.setText(name)
        self.project_enabled_chk.setChecked(bool(cfg.get("enabled", True)))
        self.project_root_edit.setText(str(cfg.get("root", "") or ""))
        self.include_prefixes_edit.setPlainText("\n".join([str(x) for x in cfg.get("include_rel_path_prefixes", []) or []]))
        self.skip_prefixes_edit.setPlainText("\n".join([str(x) for x in cfg.get("skip_rel_path_prefixes", []) or []]))

    def _add_project(self) -> None:
        name, ok = QInputDialog.getText(self, "프로젝트 추가", "프로젝트 이름(예: MRA):")
        if not ok:
            return
        name = str(name or "").strip()
        if not name:
            QMessageBox.warning(self, "오류", "프로젝트 이름이 비어 있습니다.")
            return

        projects = self._projects_dict()
        if name in projects:
            QMessageBox.warning(self, "오류", f"이미 존재하는 프로젝트입니다: {name}")
            return

        root_dir = QFileDialog.getExistingDirectory(self, "프로젝트 루트 폴더 선택")
        if not root_dir:
            return

        projects[name] = {
            "root": str(root_dir).replace("\\", "/"),
            "enabled": True,
            "include_rel_path_prefixes": [],
            "skip_rel_path_prefixes": [],
        }
        self._reload_project_list()

        # 새로 추가된 항목으로 이동
        for i in range(self.project_list.count()):
            it = self.project_list.item(i)
            if str(it.data(Qt.UserRole)) == name:
                self.project_list.setCurrentItem(it)
                break

    def _remove_project(self) -> None:
        name = self._current_project_name()
        if not name:
            return
        r = QMessageBox.question(self, "삭제 확인", f"프로젝트를 삭제할까요?\n\n{name}")
        if r != QMessageBox.Yes:
            return
        projects = self._projects_dict()
        projects.pop(name, None)
        self._reload_project_list()

    def _choose_project_root(self) -> None:
        name = self._current_project_name()
        if not name:
            return
        root_dir = QFileDialog.getExistingDirectory(self, "프로젝트 루트 폴더 선택")
        if not root_dir:
            return
        self.project_root_edit.setText(str(root_dir).replace("\\", "/"))

    # ── 탭: 경로 ─────────────────────────────────────────────────────────

    def _build_paths_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)

        self.settings_path_label = QLabel(str(self.settings_path))

        grid_box = QGroupBox("저장소 경로")
        grid = QGridLayout(grid_box)

        self.db_path_edit = QLineEdit()
        self.db_path_btn = QPushButton("DB 선택…")

        self.chroma_dir_edit = QLineEdit()
        self.chroma_dir_btn = QPushButton("Chroma 폴더 선택…")

        self.snapshot_root_edit = QLineEdit()
        self.snapshot_root_btn = QPushButton("스냅샷 폴더 선택…")

        grid.addWidget(QLabel("설정 파일:"), 0, 0)
        grid.addWidget(self.settings_path_label, 0, 1, 1, 2)
        grid.addWidget(QLabel("SQLite DB:"), 1, 0)
        grid.addWidget(self.db_path_edit, 1, 1)
        grid.addWidget(self.db_path_btn, 1, 2)
        grid.addWidget(QLabel("ChromaDB:"), 2, 0)
        grid.addWidget(self.chroma_dir_edit, 2, 1)
        grid.addWidget(self.chroma_dir_btn, 2, 2)
        grid.addWidget(QLabel("스냅샷:"), 3, 0)
        grid.addWidget(self.snapshot_root_edit, 3, 1)
        grid.addWidget(self.snapshot_root_btn, 3, 2)

        layout.addWidget(grid_box)
        layout.addStretch(1)

        storage = self._settings.get("storage") if isinstance(self._settings.get("storage"), dict) else {}
        self.db_path_edit.setText(str(storage.get("db_path", "") or self._storage_paths.db_path))
        self.chroma_dir_edit.setText(str(storage.get("chroma_dir", "") or self._storage_paths.chroma_dir))
        self.snapshot_root_edit.setText(str(storage.get("snapshot_root", "") or self._storage_paths.snapshot_root))

        self.db_path_btn.clicked.connect(self._choose_db_path)
        self.chroma_dir_btn.clicked.connect(self._choose_chroma_dir)
        self.snapshot_root_btn.clicked.connect(self._choose_snapshot_root)

        self.tabs.addTab(w, "경로")

    def _choose_db_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "SQLite DB 경로 선택", str(self.db_path_edit.text()), "SQLite DB (*.db)")
        if not path:
            return
        self.db_path_edit.setText(str(path).replace("\\", "/"))

    def _choose_chroma_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "ChromaDB 폴더 선택")
        if not path:
            return
        self.chroma_dir_edit.setText(str(path).replace("\\", "/"))

    def _choose_snapshot_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "스냅샷 폴더 선택")
        if not path:
            return
        self.snapshot_root_edit.setText(str(path).replace("\\", "/"))

    # ── 탭: 인덱싱 ───────────────────────────────────────────────────────

    def _build_indexing_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)

        box = QGroupBox("인덱싱 실행(Scan / FTS / Vector / Version chains)")
        g = QGridLayout(box)

        self.index_project_combo = QComboBox()
        self.index_project_combo.addItem("(전체)", "")

        self.rebuild_fts_chk = QCheckBox("FTS 재구축")
        self.rebuild_fts_chk.setChecked(True)

        self.index_vectors_chk = QCheckBox("벡터 인덱싱")
        self.include_large_text_chk = QCheckBox("대형 텍스트 포함")
        self.vector_force_chk = QCheckBox("벡터 강제(재임베딩/재업서트)")

        self.build_chains_chk = QCheckBox("버전 체인 재구축")
        self.build_chains_chk.setChecked(True)

        self.run_index_btn = QPushButton("실행")
        self.stop_index_btn = QPushButton("중단")
        self.stop_index_btn.setEnabled(False)

        g.addWidget(QLabel("벡터/체인 대상 프로젝트:"), 0, 0)
        g.addWidget(self.index_project_combo, 0, 1, 1, 2)
        g.addWidget(self.rebuild_fts_chk, 1, 0, 1, 3)
        g.addWidget(self.index_vectors_chk, 2, 0, 1, 3)
        g.addWidget(self.include_large_text_chk, 3, 1, 1, 2)
        g.addWidget(self.vector_force_chk, 4, 1, 1, 2)
        g.addWidget(self.build_chains_chk, 5, 0, 1, 3)
        g.addWidget(self.run_index_btn, 6, 1)
        g.addWidget(self.stop_index_btn, 6, 2)

        self.include_large_text_chk.setEnabled(False)
        self.vector_force_chk.setEnabled(False)
        if not has_vector_deps():
            msg = "벡터 인덱싱은 full 버전(chromadb, sentence-transformers) 설치가 필요합니다."
            self.index_vectors_chk.setChecked(False)
            self.index_vectors_chk.setEnabled(False)
            self.index_vectors_chk.setToolTip(msg)
            self.include_large_text_chk.setToolTip(msg)
            self.vector_force_chk.setToolTip(msg)
        if not has_content_sim_deps():
            self.build_chains_chk.setToolTip("sentence-transformers가 없으면 내용 유사도 계산은 자동으로 스킵됩니다.")

        self.index_log = QPlainTextEdit()
        self.index_log.setReadOnly(True)
        self.index_log.setMaximumBlockCount(4000)

        layout.addWidget(box)
        layout.addWidget(QLabel("실행 로그:"))
        layout.addWidget(self.index_log, 1)

        self.tabs.addTab(w, "인덱싱")

        self.index_vectors_chk.toggled.connect(self._on_vector_toggled)
        self.run_index_btn.clicked.connect(self._run_indexing)
        self.stop_index_btn.clicked.connect(self._stop_indexing)

    def _on_vector_toggled(self, checked: bool) -> None:
        self.include_large_text_chk.setEnabled(bool(checked))
        self.vector_force_chk.setEnabled(bool(checked))

    def _refresh_index_project_combo(self) -> None:
        cur = str(self.index_project_combo.currentData() or "")
        self.index_project_combo.blockSignals(True)
        try:
            self.index_project_combo.clear()
            self.index_project_combo.addItem("(전체)", "")

            projects = self._projects_dict()
            for name in sorted(projects.keys()):
                cfg = projects.get(name, {})
                if isinstance(cfg, dict) and cfg.get("enabled", True) is False:
                    continue
                self.index_project_combo.addItem(name, name)
        finally:
            self.index_project_combo.blockSignals(False)

        # 가능한 경우 기존 선택 유지
        if cur:
            idx = self.index_project_combo.findData(cur)
            if idx >= 0:
                self.index_project_combo.setCurrentIndex(idx)

    def _append_log(self, text: str) -> None:
        self.index_log.appendPlainText(text.rstrip("\n"))

    def _run_indexing(self) -> None:
        if self._proc is not None:
            QMessageBox.information(self, "실행 중", "이미 인덱싱이 실행 중입니다.")
            return

        # 편집 중인 프로젝트 필드 반영 + 저장 후 실행
        self._commit_project_fields()

        storage_paths = self._read_storage_paths_from_ui()
        storage = self._settings.get("storage")
        if not isinstance(storage, dict):
            storage = {}
        storage["db_path"] = str(storage_paths.db_path).replace("\\", "/")
        storage["chroma_dir"] = str(storage_paths.chroma_dir).replace("\\", "/")
        storage["snapshot_root"] = str(storage_paths.snapshot_root).replace("\\", "/")
        self._settings["storage"] = storage

        try:
            save_settings(self._settings, self.settings_path, make_backup=True)
        except Exception as e:
            QMessageBox.critical(self, "저장 실패", f"settings.json 저장에 실패했습니다.\n\n{type(e).__name__}: {e}")
            return

        self._append_log("=== 인덱싱 시작 ===")
        self._append_log(f"settings: {self.settings_path}")
        self._append_log(f"db: {storage_paths.db_path}")
        self._append_log(f"chroma: {storage_paths.chroma_dir}")

        project = str(self.index_project_combo.currentData() or "").strip()

        root = runtime_root()
        cmd = tool_cmd(root=root, stem="scan_all", script_name="scan_all.py") + [
            "--settings",
            str(self.settings_path),
            "--db",
            str(storage_paths.db_path),
            "--chroma-dir",
            str(storage_paths.chroma_dir),
        ]

        if bool(self.rebuild_fts_chk.isChecked()):
            cmd.append("--rebuild-fts")

        if bool(self.index_vectors_chk.isChecked()):
            cmd.append("--index-vectors")
            if project:
                cmd.extend(["--vector-project", project])
            if bool(self.include_large_text_chk.isChecked()):
                cmd.append("--vector-include-large-text")
            if bool(self.vector_force_chk.isChecked()):
                cmd.append("--vector-force")

        if bool(self.build_chains_chk.isChecked()):
            cmd.append("--build-version-chains")
            if project:
                cmd.extend(["--version-chain-project", project])

        self._proc = QProcess(self)
        self._proc.setProgram(cmd[0])
        self._proc.setArguments(cmd[1:])
        env = self._proc.processEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONFAULTHANDLER", "1")
        self._proc.setProcessEnvironment(env)

        self._proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self._proc.readyReadStandardError.connect(self._on_proc_stderr)
        self._proc.finished.connect(self._on_proc_finished)

        self.run_index_btn.setEnabled(False)
        self.stop_index_btn.setEnabled(True)
        self._proc.start()

    def _stop_indexing(self) -> None:
        if self._proc is None:
            return
        try:
            self._append_log("=== 중단 요청 ===")
            self._proc.kill()
        except Exception:
            pass

    def _on_proc_stdout(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._append_log(data)

    def _on_proc_stderr(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        if data:
            self._append_log(data)

    def _on_proc_finished(self, exit_code: int, exit_status) -> None:
        self._append_log(f"=== 종료: exit_code={int(exit_code)} ===")
        self.run_index_btn.setEnabled(True)
        self.stop_index_btn.setEnabled(False)
        try:
            self._proc.deleteLater()
        except Exception:
            pass
        self._proc = None

    # ── 적용(저장) ───────────────────────────────────────────────────────

    def _apply_settings(self) -> None:
        self._commit_project_fields()
        storage_paths = self._read_storage_paths_from_ui()

        storage = self._settings.get("storage")
        if not isinstance(storage, dict):
            storage = {}
        storage["db_path"] = str(storage_paths.db_path).replace("\\", "/")
        storage["chroma_dir"] = str(storage_paths.chroma_dir).replace("\\", "/")
        storage["snapshot_root"] = str(storage_paths.snapshot_root).replace("\\", "/")
        self._settings["storage"] = storage

        try:
            save_settings(self._settings, self.settings_path, make_backup=True)
        except Exception as e:
            QMessageBox.critical(self, "저장 실패", f"settings.json 저장에 실패했습니다.\n\n{type(e).__name__}: {e}")
            return

        self._storage_paths = storage_paths
        self._refresh_index_project_combo()
        self.settings_applied.emit(self._settings, storage_paths)
        QMessageBox.information(self, "완료", "설정을 저장했습니다.")

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        finally:
            super().closeEvent(event)
