"""PLC トリガースイート — メインアプリケーションウィンドウ。

Photo (PNG) と Video (MP4/AVI) の2モードを単一GUIで提供する。
PLCへの接続は MC Direct (pymcprotocol) または gomc-rest (HTTP) を選択できる。
"""

from __future__ import annotations

import contextlib
import os
import queue
import tkinter as tk
from datetime import datetime
from queue import Queue
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from PIL import Image, ImageTk

from camera import CameraThread
from config import load_config, save_config
from plc_monitor import BitStateEvent, PlcMonitor, PlcStatus, StatusEvent, TriggerEvent
from recorder import RecordDoneEvent, RecordErrorEvent, RecorderThread, RecordStartEvent
from settings_dialog import SettingsDialog

try:
    import beep_lite as _beep
except ImportError:
    _beep = None

_REFRESH_MS = 33

_GUI_EVENT_QUEUE: Queue[TriggerEvent | StatusEvent | BitStateEvent] = Queue()
_REC_EVENT_QUEUE: Queue[RecordStartEvent | RecordDoneEvent | RecordErrorEvent] = Queue()

_LOG_MAX_LINES = 500


# ---------------------------------------------------------------------------
# ステータスインジケータウィジェット
# ---------------------------------------------------------------------------


class _StatusLight(tk.Canvas):
    """丸形の色付きインジケータウィジェット。"""

    _RADIUS = 8
    _SIZE = _RADIUS * 2 + 4

    def __init__(self, parent: tk.Misc, **kwargs: Any) -> None:
        super().__init__(parent, width=self._SIZE, height=self._SIZE, highlightthickness=0, **kwargs)
        self._oval = self.create_oval(2, 2, self._SIZE - 2, self._SIZE - 2, fill="gray", outline="")

    def set_color(self, color: str) -> None:
        self.itemconfig(self._oval, fill=color)


# ---------------------------------------------------------------------------
# メインアプリケーション
# ---------------------------------------------------------------------------


class App(tk.Tk):
    """アプリケーションメインウィンドウ。

    Photo モード: CameraThread が USB カメラから PNG を保存。
    Video モード: RecorderThread がリングバッファ録画で動画を保存。
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("PLC Trigger Suite")
        self.resizable(True, True)

        self._cfg = load_config()
        self._simulate_mode = False
        self._closing = False

        self._active_recs: dict[str, str] = {}

        self._plc_monitor: PlcMonitor | None = None
        self._recorder: RecorderThread | None = None
        self._camera: CameraThread | None = None

        self._build_ui()
        self._apply_config_to_ui()
        self._start_backend()
        if _beep is not None and self._beep_enabled():
            _beep.preload_all()
        self._schedule_refresh()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_menubar()
        self._build_toolbar()
        self._build_main_panel()
        self._build_status_bar()

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Settings…", command=self._open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

        debug_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Debug", menu=debug_menu)
        debug_menu.add_command(label="Toggle Simulation Mode", command=self._toggle_simulation)

    def _build_toolbar(self) -> None:
        tb = ttk.Frame(self, relief="raised")
        tb.pack(fill="x", side="top")

        self._btn_connect = ttk.Button(tb, text="Connect PLC", command=self._toggle_plc_connection)
        self._btn_connect.pack(side="left", padx=4, pady=4)

        self._btn_action = ttk.Button(tb, text="Manual Record", command=self._manual_action)
        self._btn_action.pack(side="left", padx=2, pady=4)
        ttk.Button(tb, text="Settings…", command=self._open_settings).pack(side="left", padx=2, pady=4)

        # シミュレーションコントロール
        self._sim_frame = ttk.Frame(tb)
        ttk.Label(self._sim_frame, text="Sim device:").pack(side="left")
        self._sim_combo: ttk.Combobox = ttk.Combobox(self._sim_frame, width=14, state="readonly")
        self._sim_combo.pack(side="left", padx=2)
        ttk.Button(self._sim_frame, text="Fire!", command=self._sim_fire_trigger).pack(side="left")
        self._sim_frame.pack_forget()

    def _build_main_panel(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=4, pady=4)

        # 左: カメラプレビュー
        left = ttk.LabelFrame(paned, text="Camera Preview")
        paned.add(left, weight=3)
        self._preview_canvas = tk.Canvas(left, bg="black", width=640, height=480)
        self._preview_canvas.pack(fill="both", expand=True)
        self._preview_image_id = self._preview_canvas.create_image(0, 0, anchor="nw")
        self._preview_tk_img: ImageTk.PhotoImage | None = None

        # 右: ステータス + ログ
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        plc_panel = ttk.LabelFrame(right, text="PLC Status")
        plc_panel.pack(fill="x", padx=4, pady=(0, 4))
        row = ttk.Frame(plc_panel)
        row.pack(fill="x", padx=6, pady=4)
        bg = ttk.Style().lookup("TFrame", "background") or "SystemButtonFace"
        self._plc_light = _StatusLight(row, bg=bg)
        self._plc_light.pack(side="left")
        self._plc_status_label = ttk.Label(row, text="Disconnected")
        self._plc_status_label.pack(side="left", padx=4)

        dev_panel = ttk.LabelFrame(right, text="Device States")
        dev_panel.pack(fill="x", padx=4, pady=(0, 4))
        cols = ("address", "label", "state")
        self._dev_tree = ttk.Treeview(dev_panel, columns=cols, show="headings", height=5)
        self._dev_tree.heading("address", text="Address")
        self._dev_tree.heading("label", text="Label")
        self._dev_tree.heading("state", text="State")
        self._dev_tree.column("address", width=100)
        self._dev_tree.column("label", width=120)
        self._dev_tree.column("state", width=60, anchor="center")
        self._dev_tree.pack(fill="x", padx=4, pady=4)

        # Video モード専用: 実行中録画
        self._rec_panel = ttk.LabelFrame(right, text="Active Recordings")
        self._active_recs_lb = tk.Listbox(self._rec_panel, height=4, font=("Courier", 9))
        self._active_recs_lb.pack(fill="x", padx=4, pady=4)

        # Photo モード専用: 最終キャプチャ
        self._photo_panel = ttk.LabelFrame(right, text="Last Capture")
        self._last_capture_label = ttk.Label(self._photo_panel, text="—", wraplength=200, anchor="w")
        self._last_capture_label.pack(fill="x", padx=4, pady=4)

        # ログ
        self._log_panel = ttk.LabelFrame(right, text="Event Log")
        self._log_panel.pack(fill="both", expand=True, padx=4)
        self._log = scrolledtext.ScrolledText(
            self._log_panel, height=10, state="disabled", font=("Courier", 9)
        )
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

        # モードに合ったパネルを表示
        self._refresh_mode_panels()

    def _build_status_bar(self) -> None:
        sb = ttk.Frame(self, relief="sunken")
        sb.pack(fill="x", side="bottom")
        self._status_bar_label = ttk.Label(sb, text="Ready", anchor="w")
        self._status_bar_label.pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # 設定値の反映
    # ------------------------------------------------------------------

    def _apply_config_to_ui(self) -> None:
        for item in self._dev_tree.get_children():
            self._dev_tree.delete(item)
        for dev in self._cfg.plc.devices:
            self._dev_tree.insert("", "end", iid=dev.address, values=(dev.address, dev.label, "—"))
        addrs = [d.address for d in self._cfg.plc.devices if d.enabled]
        self._sim_combo["values"] = addrs
        if addrs:
            self._sim_combo.set(addrs[0])
        # ツールバーボタンのラベルをモードに合わせる
        if self._cfg.capture_mode == "capture":
            self._btn_action.config(text="Manual Capture")
        else:
            self._btn_action.config(text="Manual Record")
        self._refresh_mode_panels()

    def _refresh_mode_panels(self) -> None:
        """モードに応じてステータスパネルを切り替える。"""
        if self._cfg.capture_mode == "capture":
            self._rec_panel.pack_forget()
            self._photo_panel.pack(fill="x", padx=4, pady=(0, 4), before=self._log_panel)
        else:
            self._photo_panel.pack_forget()
            self._rec_panel.pack(fill="x", padx=4, pady=(0, 4), before=self._log_panel)

    # ------------------------------------------------------------------
    # バックエンド管理
    # ------------------------------------------------------------------

    def _start_backend(self) -> None:
        """設定の capture_mode に応じてバックエンドスレッドを起動する。"""
        if not self._stop_backend():
            # 旧スレッドが停止しきれていない場合、新規起動するとデバイスを奪い合う。
            self._set_status("Backend still stopping — restart skipped.")
            return
        if self._cfg.capture_mode == "capture":
            self._camera = CameraThread(self._cfg)
            self._camera.start()
            self._set_status("Camera started (Photo mode).")
        else:
            self._recorder = RecorderThread(self._cfg, _REC_EVENT_QUEUE)
            self._recorder.start()
            self._set_status("Recorder started (Video mode).")

    def _stop_backend(self) -> bool:
        """バックエンドスレッドを停止する。

        スレッドが実際に終了した場合のみ参照をクリアする。join がタイムアウトして
        スレッドがまだ生存している場合は参照を残し ``False`` を返す（新規起動を防ぐ）。
        """
        ok = True
        if self._camera:
            self._camera.stop()
            self._camera.join(timeout=2.0)
            if self._camera.is_alive():
                ok = False
            else:
                self._camera = None
        if self._recorder:
            self._recorder.stop()
            self._recorder.join(timeout=2.0)
            if self._recorder.is_alive():
                ok = False
            else:
                self._recorder = None
        return ok

    # ------------------------------------------------------------------
    # GUI リフレッシュループ
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        self.after(_REFRESH_MS, self._refresh)

    def _refresh(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = _GUI_EVENT_QUEUE.get_nowait()
                self._handle_plc_event(event)
        except queue.Empty:
            pass
        try:
            while True:
                event = _REC_EVENT_QUEUE.get_nowait()
                self._handle_rec_event(event)
        except queue.Empty:
            pass
        self._update_preview()
        self._schedule_refresh()

    def _handle_plc_event(self, event: TriggerEvent | StatusEvent | BitStateEvent) -> None:
        if isinstance(event, TriggerEvent):
            self._on_trigger(event.label)
        elif isinstance(event, StatusEvent):
            self._on_plc_status(event)
        elif isinstance(event, BitStateEvent):
            self._update_device_states(event.states)

    def _on_trigger(self, device_label: str) -> None:
        """モードに応じてトリガーを処理する。"""
        if self._cfg.capture_mode == "capture":
            self._do_capture(device_label)
        else:
            self._do_trigger_record(device_label)

    def _handle_rec_event(self, event: RecordStartEvent | RecordDoneEvent | RecordErrorEvent) -> None:
        if isinstance(event, RecordStartEvent):
            key = str(event.save_path)
            self._active_recs[key] = f"{event.device_label}: {event.save_path.name}"
            self._refresh_active_recs_lb()
            self._log_append(f"[{_ts()}] Recording started: {event.device_label} → {event.save_path.name}")
            self._set_status(f"Recording: {event.save_path.name}")
        elif isinstance(event, RecordDoneEvent):
            key = str(event.save_path)
            self._active_recs.pop(key, None)
            self._refresh_active_recs_lb()
            self._log_append(
                f"[{_ts()}] Saved: {event.save_path.name}"
                f"  ({event.frame_count} frames, {event.duration_sec:.1f} s)"
            )
            self._set_status(f"Saved: {event.save_path.name}")
        elif isinstance(event, RecordErrorEvent):
            if event.save_path is not None:
                self._active_recs.pop(str(event.save_path), None)
            else:
                self._active_recs.pop(
                    next((k for k, v in self._active_recs.items() if event.device_label in v), ""), None
                )
            self._refresh_active_recs_lb()
            self._log_append(f"[{_ts()}] ERROR ({event.device_label}): {event.message}")
            self._set_status(f"Record error: {event.message}")

    def _refresh_active_recs_lb(self) -> None:
        self._active_recs_lb.delete(0, "end")
        for display in self._active_recs.values():
            self._active_recs_lb.insert("end", display)

    def _update_preview(self) -> None:
        frame = None
        if self._cfg.capture_mode == "capture" and self._camera:
            frame = self._camera.get_preview_frame()
        elif self._recorder:
            frame = self._recorder.get_preview_frame()
        if frame is None:
            return
        rgb = frame[:, :, ::-1]
        img = Image.fromarray(rgb)
        cw = self._preview_canvas.winfo_width() or self._cfg.camera.preview_width
        ch = self._preview_canvas.winfo_height() or self._cfg.camera.preview_height
        img.thumbnail((cw, ch), Image.Resampling.LANCZOS)
        self._preview_tk_img = ImageTk.PhotoImage(img)
        self._preview_canvas.itemconfig(self._preview_image_id, image=self._preview_tk_img)

    # ------------------------------------------------------------------
    # PLC 接続
    # ------------------------------------------------------------------

    def _toggle_plc_connection(self) -> None:
        if self._plc_monitor and self._plc_monitor.is_alive():
            self._plc_monitor.stop()
            self._plc_monitor = None
            self._btn_connect.config(text="Connect PLC")
            self._plc_light.set_color("gray")
            self._plc_status_label.config(text="Disconnected")
            self._set_status("PLC disconnected.")
        else:
            self._start_plc_monitor()

    def _start_plc_monitor(self) -> None:
        self._plc_monitor = PlcMonitor(self._cfg.plc, _GUI_EVENT_QUEUE, simulate=self._simulate_mode)
        self._plc_monitor.start()
        self._btn_connect.config(text="Disconnect PLC")
        if self._cfg.plc.connection_type == "gomc_rest":
            self._set_status(f"Connecting to gomc-rest: {self._cfg.plc.gomc_rest_url}…")
        else:
            self._set_status(f"Connecting to {self._cfg.plc.ip}:{self._cfg.plc.port}…")

    def _on_plc_status(self, event: StatusEvent) -> None:
        if event.status == PlcStatus.CONNECTED:
            self._plc_light.set_color("green")
            if self._cfg.plc.connection_type == "gomc_rest":
                self._plc_status_label.config(text=f"Connected  {self._cfg.plc.gomc_rest_url}")
            else:
                self._plc_status_label.config(text=f"Connected  {self._cfg.plc.ip}:{self._cfg.plc.port}")
        elif event.status == PlcStatus.CONNECTING:
            self._plc_light.set_color("yellow")
            self._plc_status_label.config(text="Connecting…")
        elif event.status == PlcStatus.ERROR:
            self._plc_light.set_color("red")
            self._plc_status_label.config(text="Error")
        else:
            self._plc_light.set_color("gray")
            self._plc_status_label.config(text="Disconnected")
        self._set_status(event.message or event.status.name)

    def _update_device_states(self, states: dict[str, bool]) -> None:
        for addr, on in states.items():
            with contextlib.suppress(tk.TclError):
                self._dev_tree.set(addr, "state", "ON" if on else "OFF")

    # ------------------------------------------------------------------
    # シミュレーションモード
    # ------------------------------------------------------------------

    def _toggle_simulation(self) -> None:
        self._simulate_mode = not self._simulate_mode
        if self._simulate_mode:
            self._sim_frame.pack(side="right", padx=2, pady=2)
            messagebox.showinfo(
                "Simulation Mode", "Simulation mode enabled.\nNo real PLC connection will be made."
            )
        else:
            self._sim_frame.pack_forget()
        if self._plc_monitor and self._plc_monitor.is_alive():
            self._plc_monitor.stop()
            self._start_plc_monitor()

    def _sim_fire_trigger(self) -> None:
        addr = self._sim_combo.get()
        if addr and self._plc_monitor:
            self._plc_monitor.simulate_trigger(addr)

    # ------------------------------------------------------------------
    # Photo モード: キャプチャ
    # ------------------------------------------------------------------

    def _do_capture(self, device_label: str) -> None:
        if self._camera is None:
            return
        path = self._camera.capture_hires(device_label)
        if path is not None:
            self._last_capture_label.config(text=path.name)
            self._log_append(f"[{_ts()}] Captured: {device_label} → {path.name}")
            self._set_status(f"Captured: {path.name}")
        else:
            self._log_append(f"[{_ts()}] Capture failed (no frame): {device_label}")
            self._set_status("Capture failed: no frame available.")
        if _beep is not None and self._cfg.capture.beep_on_trigger:
            _beep.ok() if path else _beep.ng()

    # ------------------------------------------------------------------
    # Video モード: 録画
    # ------------------------------------------------------------------

    def _do_trigger_record(self, device_label: str) -> None:
        if self._recorder is None:
            return
        self._recorder.trigger_record(device_label)
        if _beep is not None and self._cfg.record.beep_on_trigger:
            _beep.ok()

    # ------------------------------------------------------------------
    # 手動アクション（ツールバーボタン）
    # ------------------------------------------------------------------

    def _manual_action(self) -> None:
        if self._cfg.capture_mode == "capture":
            self._do_capture("manual")
        else:
            self._do_trigger_record("manual")

    # ------------------------------------------------------------------
    # 設定ダイアログ
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        prev_mode = self._cfg.capture_mode
        prev_cfg = self._cfg
        dlg = SettingsDialog(self, self._cfg)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._cfg = dlg.result
        save_config(self._cfg)
        self._apply_config_to_ui()
        if _beep is not None and self._beep_enabled():
            _beep.preload_all()
        # モードまたはカメラ設定が変わった場合はバックエンドを再起動
        # (Camera index/解像度/fps は VideoCapture の reopen が必要)
        camera_changed = self._cfg.camera != prev_cfg.camera
        if self._cfg.capture_mode != prev_mode or camera_changed:
            self._start_backend()
        else:
            if self._camera:
                self._camera.update_config(self._cfg)
            if self._recorder:
                self._recorder.update_config(self._cfg)
        if self._plc_monitor:
            self._plc_monitor.update_config(self._cfg.plc)

    def _beep_enabled(self) -> bool:
        if self._cfg.capture_mode == "capture":
            return self._cfg.capture.beep_on_trigger
        return self._cfg.record.beep_on_trigger

    # ------------------------------------------------------------------
    # ログヘルパー
    # ------------------------------------------------------------------

    def _log_append(self, text: str) -> None:
        self._log.config(state="normal")
        self._log.insert("end", text + "\n")
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > _LOG_MAX_LINES:
            self._log.delete("1.0", f"{lines - _LOG_MAX_LINES}.0")
        self._log.see("end")
        self._log.config(state="disabled")

    def _set_status(self, msg: str) -> None:
        self._status_bar_label.config(text=msg)

    # ------------------------------------------------------------------
    # 終了処理
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        self._closing = True
        if self._plc_monitor:
            self._plc_monitor.stop()
        self._stop_backend()
        save_config(self._cfg)
        if self._plc_monitor:
            self._plc_monitor.join(timeout=2.0)
        self.destroy()
        os._exit(0)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
