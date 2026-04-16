"""Tkinter desktop shell for the confidential client."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, filedialog, messagebox, ttk

from confidential_client.controller import ConfidentialClientController
from confidential_client.version import CLIENT_NAME, CLIENT_VERSION
from llmwiki_core.contracts import ConfidentialServices


class ConfidentialDesktopApp(tk.Tk):
    """Desktop client shell for local confidential repositories."""

    def __init__(self, controller: ConfidentialClientController | None = None) -> None:
        super().__init__()
        self.title(f"{CLIENT_NAME} {CLIENT_VERSION}")
        self.geometry("1200x780")
        self.controller = controller or ConfidentialClientController()
        self.selected_repo_uuid: str | None = None
        self._busy = False
        self._worker_queue: queue.Queue = queue.Queue()

        self.repo_name_var = tk.StringVar()
        self.repo_slug_var = tk.StringVar()
        self.passphrase_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.services_vars = {
            "llm_api_base": tk.StringVar(),
            "llm_api_key": tk.StringVar(),
            "llm_model": tk.StringVar(),
            "llm_max_tokens": tk.StringVar(value="4000"),
            "embedding_api_base": tk.StringVar(),
            "embedding_api_key": tk.StringVar(),
            "embedding_model": tk.StringVar(),
            "embedding_dimensions": tk.StringVar(value="1024"),
            "qdrant_url": tk.StringVar(),
            "mineru_api_url": tk.StringVar(),
        }
        self.question_var = tk.StringVar()
        self.ingest_path_var = tk.StringVar()
        settings = self.controller.load_client_settings()
        self.update_manifest_var = tk.StringVar(value=settings.get("update_manifest_url", ""))
        self.update_channel_var = tk.StringVar(value=settings.get("update_channel", "stable"))
        self._history_rows: list[dict] = []
        self._last_query_result = None

        self._build_ui()
        self.refresh_repo_list()
        self.after(100, self._poll_worker_queue)

    def _build_ui(self) -> None:
        root = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=BOTH, expand=True)

        left = ttk.Frame(root, padding=12)
        right = ttk.Frame(root, padding=12)
        root.add(left, weight=1)
        root.add(right, weight=3)

        ttk.Label(left, text="Knowledge Bases").pack(anchor="w")
        self.repo_listbox = tk.Listbox(left, height=24)
        self.repo_listbox.pack(fill=BOTH, expand=True, pady=(8, 12))
        self.repo_listbox.bind("<<ListboxSelect>>", lambda _event: self._on_repo_selected())

        list_actions = ttk.Frame(left)
        list_actions.pack(fill="x")
        self.refresh_button = ttk.Button(list_actions, text="Refresh", command=self.refresh_repo_list)
        self.refresh_button.pack(side=LEFT)
        self.import_button = ttk.Button(list_actions, text="Import Bundle", command=self.import_bundle)
        self.import_button.pack(side=LEFT, padx=6)
        self.delete_button = ttk.Button(list_actions, text="Delete", command=self.delete_repo)
        self.delete_button.pack(side=LEFT)

        notebook = ttk.Notebook(right)
        notebook.pack(fill=BOTH, expand=True)

        create_tab = ttk.Frame(notebook, padding=12)
        services_tab = ttk.Frame(notebook, padding=12)
        operations_tab = ttk.Frame(notebook, padding=12)
        history_tab = ttk.Frame(notebook, padding=12)
        updates_tab = ttk.Frame(notebook, padding=12)
        notebook.add(create_tab, text="Create")
        notebook.add(services_tab, text="Services")
        notebook.add(operations_tab, text="Operations")
        notebook.add(history_tab, text="History")
        notebook.add(updates_tab, text="Updates")

        self._build_create_tab(create_tab)
        self._build_services_tab(services_tab)
        self._build_operations_tab(operations_tab)
        self._build_history_tab(history_tab)
        self._build_updates_tab(updates_tab)
        ttk.Label(right, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(8, 0))

    def _build_create_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.repo_name_var, width=40).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(tab, text="Slug").grid(row=1, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.repo_slug_var, width=40).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(tab, text="Passphrase").grid(row=2, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.passphrase_var, width=40, show="*").grid(
            row=2,
            column=1,
            sticky="ew",
            pady=4,
        )
        self.create_button = ttk.Button(tab, text="Create Repository", command=self.create_repo)
        self.create_button.grid(
            row=3,
            column=1,
            sticky="e",
            pady=10,
        )
        tab.columnconfigure(1, weight=1)

    def _build_services_tab(self, tab: ttk.Frame) -> None:
        for row, (key, var) in enumerate(self.services_vars.items()):
            ttk.Label(tab, text=key).grid(row=row, column=0, sticky="w")
            ttk.Entry(tab, textvariable=var, width=72, show="*" if "key" in key else "").grid(
                row=row,
                column=1,
                sticky="ew",
                pady=3,
            )
        actions = ttk.Frame(tab)
        actions.grid(row=len(self.services_vars), column=1, sticky="e", pady=10)
        self.load_services_button = ttk.Button(actions, text="Load From Repo", command=self.load_services)
        self.load_services_button.pack(side=LEFT)
        self.save_services_button = ttk.Button(actions, text="Save To Repo", command=self.save_services)
        self.save_services_button.pack(side=LEFT, padx=6)
        self.health_button = ttk.Button(actions, text="Health Check", command=self.health_check)
        self.health_button.pack(side=LEFT)
        self.health_output = tk.Text(tab, height=8, wrap="word")
        self.health_output.grid(row=len(self.services_vars) + 1, column=0, columnspan=2, sticky="nsew")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(len(self.services_vars) + 1, weight=1)

    def _build_operations_tab(self, tab: ttk.Frame) -> None:
        ingest_frame = ttk.LabelFrame(tab, text="Ingest", padding=12)
        ingest_frame.pack(fill="x")
        ttk.Entry(ingest_frame, textvariable=self.ingest_path_var, width=80).pack(side=LEFT, fill="x", expand=True)
        self.browse_button = ttk.Button(ingest_frame, text="Browse", command=self.pick_ingest_path)
        self.browse_button.pack(side=LEFT, padx=6)
        self.ingest_button = ttk.Button(ingest_frame, text="Run Ingest", command=self.run_ingest)
        self.ingest_button.pack(side=LEFT)

        query_frame = ttk.LabelFrame(tab, text="Query", padding=12)
        query_frame.pack(fill=BOTH, expand=True, pady=(12, 0))
        ttk.Entry(query_frame, textvariable=self.question_var).pack(fill="x")
        self.query_button = ttk.Button(query_frame, text="Ask", command=self.run_query)
        self.query_button.pack(anchor="e", pady=8)
        self.query_notebook = ttk.Notebook(query_frame)
        self.query_notebook.pack(fill=BOTH, expand=True)

        answer_tab = ttk.Frame(self.query_notebook)
        evidence_tab = ttk.Frame(self.query_notebook)
        self.query_notebook.add(answer_tab, text="Answer")
        self.query_notebook.add(evidence_tab, text="Evidence")

        self.query_output = tk.Text(answer_tab, wrap="word")
        self.query_output.pack(fill=BOTH, expand=True)
        self.evidence_output = tk.Text(evidence_tab, wrap="word")
        self.evidence_output.pack(fill=BOTH, expand=True)

        export_frame = ttk.Frame(tab)
        export_frame.pack(fill="x", pady=12)
        self.export_button = ttk.Button(export_frame, text="Export Bundle", command=self.export_bundle)
        self.export_button.pack(side=RIGHT)

    def _build_history_tab(self, tab: ttk.Frame) -> None:
        self.refresh_history_button = ttk.Button(tab, text="Refresh History", command=self.refresh_history)
        self.refresh_history_button.pack(anchor="e", pady=(0, 8))
        split = ttk.Panedwindow(tab, orient=tk.HORIZONTAL)
        split.pack(fill=BOTH, expand=True)
        left = ttk.Frame(split)
        right = ttk.Frame(split)
        split.add(left, weight=1)
        split.add(right, weight=3)
        self.history_listbox = tk.Listbox(left)
        self.history_listbox.pack(fill=BOTH, expand=True)
        self.history_listbox.bind("<<ListboxSelect>>", lambda _event: self._on_history_selected())
        self.history_output = tk.Text(right, wrap="word")
        self.history_output.pack(fill=BOTH, expand=True)

    def _build_updates_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="Manifest URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.update_manifest_var, width=72).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(tab, text="Channel").grid(row=1, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.update_channel_var, width=20).grid(row=1, column=1, sticky="w", pady=4)
        actions = ttk.Frame(tab)
        actions.grid(row=2, column=1, sticky="e", pady=8)
        self.save_update_button = ttk.Button(actions, text="Save Update Config", command=self.save_update_settings)
        self.save_update_button.pack(side=LEFT)
        self.check_update_button = ttk.Button(actions, text="Check Updates", command=self.check_updates)
        self.check_update_button.pack(side=LEFT, padx=6)
        self.update_output = tk.Text(tab, wrap="word")
        self.update_output.grid(row=3, column=0, columnspan=2, sticky="nsew")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)

    def _repo_services(self) -> ConfidentialServices:
        data = {key: var.get() for key, var in self.services_vars.items()}
        return ConfidentialServices.from_dict(data)

    def _require_repo(self) -> str:
        if not self.selected_repo_uuid:
            raise ValueError("请先选择知识库")
        return self.selected_repo_uuid

    def _require_passphrase(self) -> str:
        passphrase = self.passphrase_var.get().strip()
        if not passphrase:
            raise ValueError("请先输入口令")
        return passphrase

    def refresh_repo_list(self) -> None:
        self.repo_listbox.delete(0, END)
        self._repo_index: list[str] = []
        for item in self.controller.list_repositories():
            self._repo_index.append(item.repo_uuid)
            self.repo_listbox.insert(END, f"{item.name} [{item.slug}]")

    def _on_repo_selected(self) -> None:
        selection = self.repo_listbox.curselection()
        if not selection:
            return
        self.selected_repo_uuid = self._repo_index[selection[0]]
        self.refresh_history()

    def _on_history_selected(self) -> None:
        selection = self.history_listbox.curselection()
        if not selection:
            return
        item = self._history_rows[selection[0]]
        text = (
            f"时间: {item.get('created_at', '')}\n\n"
            f"问题:\n{item.get('question', '')}\n\n"
            f"回答:\n{item.get('answer', '')}"
        )
        self.history_output.delete("1.0", END)
        self.history_output.insert("1.0", text)

    def create_repo(self) -> None:
        try:
            repo = self.controller.create_repository(
                name=self.repo_name_var.get().strip(),
                slug=self.repo_slug_var.get().strip(),
                passphrase=self._require_passphrase(),
                services=self._repo_services(),
            )
        except Exception as exc:
            messagebox.showerror("Create Failed", str(exc))
            return
        self.refresh_repo_list()
        self.selected_repo_uuid = repo.repo_uuid
        messagebox.showinfo("Created", f"已创建知识库：{repo.name}")

    def import_bundle(self) -> None:
        bundle_path = filedialog.askopenfilename(filetypes=[("Bundle", "*.tgz"), ("All files", "*.*")])
        if not bundle_path:
            return
        try:
            repo = self.controller.import_repository(bundle_path)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc))
            return
        self.refresh_repo_list()
        self.selected_repo_uuid = repo.repo_uuid
        messagebox.showinfo("Imported", f"已导入：{repo.name}")

    def delete_repo(self) -> None:
        try:
            repo_uuid = self._require_repo()
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc))
            return
        if not messagebox.askyesno("Delete", "确认删除本地知识库？"):
            return
        self.controller.delete_repository(repo_uuid)
        self.selected_repo_uuid = None
        self.refresh_repo_list()

    def load_services(self) -> None:
        try:
            services = self.controller.load_services(self._require_repo(), self._require_passphrase())
        except Exception as exc:
            messagebox.showerror("Load Failed", str(exc))
            return
        for key, var in self.services_vars.items():
            var.set(str(getattr(services, key)))

    def save_services(self) -> None:
        try:
            self.controller.update_services(
                self._require_repo(),
                passphrase=self._require_passphrase(),
                services=self._repo_services(),
            )
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc))
            return
        messagebox.showinfo("Saved", "服务配置已保存到本地机密库。")

    def health_check(self) -> None:
        self._run_background(
            "Health Check",
            lambda: self.controller.check_services(self._repo_services()),
            self._on_health_result,
        )

    def pick_ingest_path(self) -> None:
        path = filedialog.askopenfilename()
        if path:
            self.ingest_path_var.set(path)

    def run_ingest(self) -> None:
        self._run_background(
            "Ingest",
            lambda: self.controller.ingest_file(
                self._require_repo(),
                self._require_passphrase(),
                self.ingest_path_var.get().strip(),
            ),
            self._on_ingest_result,
        )

    def _on_ingest_result(self, events: list[dict]) -> None:
        self.query_output.delete("1.0", END)
        self.query_output.insert("1.0", json.dumps(events, ensure_ascii=False, indent=2))
        self.refresh_history()

    def run_query(self) -> None:
        self._run_background(
            "Query",
            lambda: self.controller.query(
                self._require_repo(),
                self._require_passphrase(),
                self.question_var.get().strip(),
            ),
            self._on_query_result,
        )

    def _on_query_result(self, result) -> None:
        self._last_query_result = result
        self.query_output.delete("1.0", END)
        self.query_output.insert("1.0", result.answer)
        self.query_output.insert(END, "\n\n")
        self.query_output.insert(END, json.dumps(result.confidence, ensure_ascii=False, indent=2))
        self.evidence_output.delete("1.0", END)
        self.evidence_output.insert("1.0", self._format_evidence(result))
        self.refresh_history()

    def _on_health_result(self, result: dict) -> None:
        self.health_output.delete("1.0", END)
        self.health_output.insert("1.0", json.dumps(result, ensure_ascii=False, indent=2))

    def refresh_history(self) -> None:
        self.history_output.delete("1.0", END)
        self.history_listbox.delete(0, END)
        if not self.selected_repo_uuid:
            return
        try:
            history = self.controller.history(self.selected_repo_uuid, self._require_passphrase())
        except Exception:
            return
        self._history_rows = history
        for item in history:
            self.history_listbox.insert(END, f"[{item.get('created_at', '')}] {item.get('question', '')[:48]}")
        if history:
            self.history_listbox.selection_clear(0, END)
            self.history_listbox.selection_set(END)
            self._on_history_selected()

    def export_bundle(self) -> None:
        try:
            repo_uuid = self._require_repo()
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc))
            return
        output_path = filedialog.asksaveasfilename(
            defaultextension=".tgz",
            filetypes=[("Bundle", "*.tgz")],
            initialfile=f"{repo_uuid}.tgz",
        )
        if not output_path:
            return
        path = self.controller.export_repository(repo_uuid, output_path)
        messagebox.showinfo("Exported", f"已导出到：{Path(path)}")

    def save_update_settings(self) -> None:
        settings = self.controller.save_client_settings(
            {
                "update_manifest_url": self.update_manifest_var.get().strip(),
                "update_channel": self.update_channel_var.get().strip(),
            }
        )
        self.update_manifest_var.set(settings["update_manifest_url"])
        self.update_channel_var.set(settings["update_channel"])
        messagebox.showinfo("Saved", "更新配置已保存。")

    def check_updates(self) -> None:
        self._run_background(
            "Update Check",
            lambda: self.controller.check_for_updates(
                manifest_url=self.update_manifest_var.get().strip(),
                channel=self.update_channel_var.get().strip(),
            ),
            self._on_update_result,
        )

    def _on_update_result(self, result) -> None:
        self.update_output.delete("1.0", END)
        self.update_output.insert("1.0", json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

    def _run_background(self, name: str, fn, on_success) -> None:
        if self._busy:
            messagebox.showwarning("Busy", "当前有任务正在执行，请等待完成。")
            return
        self._set_busy(True, f"{name} running...")

        def worker() -> None:
            try:
                result = fn()
            except Exception as exc:
                self._worker_queue.put(("error", name, exc))
                return
            self._worker_queue.put(("success", name, result, on_success))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_worker_queue(self) -> None:
        try:
            while True:
                item = self._worker_queue.get_nowait()
                if item[0] == "error":
                    _, name, exc = item
                    self._set_busy(False, f"{name} failed")
                    messagebox.showerror(f"{name} Failed", str(exc))
                    continue
                _, name, result, callback = item
                self._set_busy(False, f"{name} done")
                callback(result)
        except queue.Empty:
            pass
        self.after(100, self._poll_worker_queue)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self.status_var.set(status)
        state = tk.DISABLED if busy else tk.NORMAL
        for button in [
            self.refresh_button,
            self.import_button,
            self.delete_button,
            self.create_button,
            self.load_services_button,
            self.save_services_button,
            self.health_button,
            self.save_update_button,
            self.check_update_button,
            self.browse_button,
            self.ingest_button,
            self.query_button,
            self.export_button,
            self.refresh_history_button,
        ]:
            button.configure(state=state)

    def _format_evidence(self, result) -> str:
        lines = [
            "Confidence",
            json.dumps(result.confidence, ensure_ascii=False, indent=2),
            "",
            "Wiki Evidence",
        ]
        for item in result.wiki_evidence:
            lines.append(
                f"- {item.get('title', '')} | {item.get('type', '')} | {item.get('reason', '')} | {item.get('url', '')}"
            )
        lines.append("")
        lines.append("Chunk Evidence")
        for item in result.chunk_evidence:
            lines.append(
                f"- {item.get('title', '')} / {item.get('heading', '')} | score={item.get('score', 0)}"
            )
            lines.append(f"  {item.get('snippet', '')}")
        lines.append("")
        lines.append("Fact Evidence")
        for item in result.fact_evidence:
            lines.append(
                f"- {item.get('source_markdown_filename', '')} | {item.get('sheet', '')} row={item.get('row_index', 0)}"
            )
            lines.append(f"  {json.dumps(item.get('fields', {}), ensure_ascii=False)}")
        lines.append("")
        lines.append(f"Summary: {result.evidence_summary}")
        return "\n".join(lines)


def launch_desktop_app(controller: ConfidentialClientController | None = None) -> None:
    app = ConfidentialDesktopApp(controller=controller)
    app.mainloop()
