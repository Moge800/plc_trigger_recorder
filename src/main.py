"""PLC トリガーレコーダー — メインアプリケーションウィンドウ。"""

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

from config import load_config, save_config
from plc_monitor import BitStateEvent, PlcMonitor, PlcStatus, StatusEvent, TriggerEvent
from recorder import RecordDoneEvent, RecordErrorEvent, RecorderThread, RecordStartEvent
from settings_dialog import SettingsDialog

# beep-lite オプション（pip install beep-lite または uv sync --extra audio）
try:
    import beep_lite as _beep
except ImportError:
    _beep = None  # 未インストール時はビープをスキップ

# GUI 更新間隔（ミリ秒）— 素4 30 fps
_REFRESH_MS = 33

# tkinter スレッド安全性のため: スレッド間のイベントはこのキュー経由
_GUI_EVENT_QUEUE: Queue[TriggerEvent | StatusEvent | BitStateEvent] = Queue()
_REC_EVENT_QUEUE: Queue[RecordStartEvent | RecordDoneEvent | RecordErrorEvent] = Queue()

# 録画ログの最大行数
_LOG_MAX_LINES = 500


# ---------------------------------------------------------------------------
# ステータスインジケータウィジェット
# ---------------------------------------------------------------------------


class _StatusLight(tk.Canvas):
    """丸形の色付きインジケータウィジェット。"""

    _RADIUS = 8
    _SIZE = _RADIUS * 2 + 4

    def __init__(self, parent: tk.Misc, **kwargs: Any) -> None:
        """インジケータを初期化する。

        Args:
            parent: 親ウィジェット。
            **kwargs: :class:`tk.Canvas` に渡す追加オプション。
        """
        super().__init__(
            parent, width=self._SIZE, height=self._SIZE, highlightthickness=0, **kwargs
        )
        self._oval = self.create_oval(
            2, 2, self._SIZE - 2, self._SIZE - 2, fill="gray", outline=""
        )

    def set_color(self, color: str) -> None:
        """インジケータの色を変更する。

        Args:
            color: tkinter が認識する色文字列。
        """
        self.itemconfig(self._oval, fill=color)


# ---------------------------------------------------------------------------
# メインアプリケーション
# ---------------------------------------------------------------------------


class App(tk.Tk):
    """アプリケーションメインウィンドウ。

    プレビューパネル・デバイスステータス・録画ログを一画面にまとめたレイアウト。
    PLCモニターとレコーダースレッドを通じて自動録画を行う。
    """

    def __init__(self) -> None:
        """アプリケーションを初期化し、UI を構築しスレッドを起動する。"""
        super().__init__()
        self.title("PLC Trigger Recorder")
        self.resizable(True, True)

        self._cfg = load_config()
        self._simulate_mode = False
        self._closing = False

        # 実行中の録画: save_path_str → 表示ラベル
        self._active_recs: dict[str, str] = {}

        # バックグラウンドスレッド
        self._plc_monitor: PlcMonitor | None = None
        self._recorder: RecorderThread | None = None

        self._build_ui()
        self._apply_config_to_ui()
        self._start_recorder()
        # beep-lite 起動時プリロード（初回再生のレイテンシ低減）
        if _beep is not None and self._cfg.record.beep_on_trigger:
            _beep.preload_all()
        self._schedule_refresh()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """メニュー・ツールバー・メインパネル・ステータスバーを生成する。"""
        self._build_menubar()
        self._build_toolbar()
        self._build_main_panel()
        self._build_status_bar()

    def _build_menubar(self) -> None:
        """アプリケーションのメニューバーを構築する。"""
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Settings…", command=self._open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

        debug_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Debug", menu=debug_menu)
        debug_menu.add_command(
            label="Toggle Simulation Mode", command=self._toggle_simulation
        )

    def _build_toolbar(self) -> None:
        """ツールバーボタンとシミュレーションパネルを構築する。"""
        tb = ttk.Frame(self, relief="raised")
        tb.pack(fill="x", side="top")

        self._btn_connect = ttk.Button(
            tb, text="Connect PLC", command=self._toggle_plc_connection
        )
        self._btn_connect.pack(side="left", padx=4, pady=4)

        ttk.Button(tb, text="Manual Record", command=self._manual_record).pack(
            side="left", padx=2, pady=4
        )
        ttk.Button(tb, text="Settings…", command=self._open_settings).pack(
            side="left", padx=2, pady=4
        )

        # シミュレーションコントロール（シミュレーションモード時のみ表示）
        self._sim_frame = ttk.Frame(tb)
        self._sim_label = ttk.Label(self._sim_frame, text="Sim device:")
        self._sim_label.pack(side="left")
        self._sim_combo: ttk.Combobox = ttk.Combobox(
            self._sim_frame, width=14, state="readonly"
        )
        self._sim_combo.pack(side="left", padx=2)
        ttk.Button(self._sim_frame, text="Fire!", command=self._sim_fire_trigger).pack(
            side="left"
        )
        self._sim_frame.pack_forget()

    def _build_main_panel(self) -> None:
        """左ペイン（カメラプレビュー）と右ペイン（ステータス／ログ）を構築する。"""
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=4, pady=4)

        # --- 左: カメラプレビュー ---
        left = ttk.LabelFrame(paned, text="Camera Preview")
        paned.add(left, weight=3)
        self._preview_canvas = tk.Canvas(left, bg="black", width=640, height=480)
        self._preview_canvas.pack(fill="both", expand=True)
        self._preview_image_id = self._preview_canvas.create_image(0, 0, anchor="nw")
        self._preview_tk_img: ImageTk.PhotoImage | None = None

        # --- 右: ステータス + ログ ---
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        # PLC 接続パネル
        plc_panel = ttk.LabelFrame(right, text="PLC Status")
        plc_panel.pack(fill="x", padx=4, pady=(0, 4))
        row = ttk.Frame(plc_panel)
        row.pack(fill="x", padx=6, pady=4)
        # ttk.LabelFrame は cget("background") 非対応のため Style で解決
        bg = ttk.Style().lookup("TFrame", "background") or "SystemButtonFace"
        self._plc_light = _StatusLight(row, bg=bg)
        self._plc_light.pack(side="left")
        self._plc_status_label = ttk.Label(row, text="Disconnected")
        self._plc_status_label.pack(side="left", padx=4)

        # デバイス状態パネル
        dev_panel = ttk.LabelFrame(right, text="Device States")
        dev_panel.pack(fill="x", padx=4, pady=(0, 4))
        cols = ("address", "label", "state")
        self._dev_tree = ttk.Treeview(
            dev_panel, columns=cols, show="headings", height=5
        )
        self._dev_tree.heading("address", text="Address")
        self._dev_tree.heading("label", text="Label")
        self._dev_tree.heading("state", text="State")
        self._dev_tree.column("address", width=100)
        self._dev_tree.column("label", width=120)
        self._dev_tree.column("state", width=60, anchor="center")
        self._dev_tree.pack(fill="x", padx=4, pady=4)

        # 実行中録画パネル
        rec_panel = ttk.LabelFrame(right, text="Active Recordings")
        rec_panel.pack(fill="x", padx=4, pady=(0, 4))
        self._active_recs_lb = tk.Listbox(rec_panel, height=4, font=("Courier", 9))
        self._active_recs_lb.pack(fill="x", padx=4, pady=4)

        # 録画ログ
        log_panel = ttk.LabelFrame(right, text="Record Log")
        log_panel.pack(fill="both", expand=True, padx=4)
        self._log = scrolledtext.ScrolledText(
            log_panel, height=10, state="disabled", font=("Courier", 9)
        )
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_status_bar(self) -> None:
        """ウィンドウ下部のステータスバーを構築する。"""
        sb = ttk.Frame(self, relief="sunken")
        sb.pack(fill="x", side="bottom")
        self._status_bar_label = ttk.Label(sb, text="Ready", anchor="w")
        self._status_bar_label.pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # 設定値の反映
    # ------------------------------------------------------------------

    def _apply_config_to_ui(self) -> None:
        """設定値を元にデバイスツリー行を再構築する。"""
        for item in self._dev_tree.get_children():
            self._dev_tree.delete(item)
        for dev in self._cfg.plc.devices:
            self._dev_tree.insert(
                "", "end", iid=dev.address, values=(dev.address, dev.label, "—")
            )
        # シミュレーションコンボを更新
        addrs = [d.address for d in self._cfg.plc.devices if d.enabled]
        self._sim_combo["values"] = addrs
        if addrs:
            self._sim_combo.set(addrs[0])

    # ------------------------------------------------------------------
    # レコーダースレッド
    # ------------------------------------------------------------------

    def _start_recorder(self) -> None:
        """既存スレッドを停止しレコーダースレッドを起動する。"""
        self._recorder = RecorderThread(self._cfg, _REC_EVENT_QUEUE)
        self._recorder.start()
        self._set_status("Recorder started.")

    # ------------------------------------------------------------------
    # GUI リフレッシュループ
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        """次のリフレッシュをスケジュールする。"""
        self.after(_REFRESH_MS, self._refresh)

    def _refresh(self) -> None:
        """プレビューとイベント処理を毎 tick 実行し、次回をスケジュールする。"""
        if self._closing:
            return
        # PLC イベントを消化
        try:
            while True:
                event = _GUI_EVENT_QUEUE.get_nowait()
                self._handle_plc_event(event)
        except queue.Empty:
            pass
        # レコーダーイベントを消化
        try:
            while True:
                event = _REC_EVENT_QUEUE.get_nowait()
                self._handle_rec_event(event)
        except queue.Empty:
            pass
        # カメラプレビューを更新
        self._update_preview()
        self._schedule_refresh()

    def _handle_plc_event(
        self, event: TriggerEvent | StatusEvent | BitStateEvent
    ) -> None:
        """受信した PLC イベントを種別に応じて処理する。

        Args:
            event: キューから取得したイベント。
        """
        if isinstance(event, TriggerEvent):
            self._do_trigger_record(event.label)
        elif isinstance(event, StatusEvent):
            self._on_plc_status(event)
        elif isinstance(event, BitStateEvent):
            self._update_device_states(event.states)

    def _handle_rec_event(
        self, event: RecordStartEvent | RecordDoneEvent | RecordErrorEvent
    ) -> None:
        """受信したレコーダーイベントを種別に応じて処理する。

        Args:
            event: キューから取得したイベント。
        """
        if isinstance(event, RecordStartEvent):
            key = str(event.save_path)
            self._active_recs[key] = f"{event.device_label}: {event.save_path.name}"
            self._refresh_active_recs_lb()
            self._log_append(
                f"[{_ts()}] Recording started: {event.device_label} → {event.save_path.name}"
            )
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
                    next(
                        (
                            k
                            for k, v in self._active_recs.items()
                            if event.device_label in v
                        ),
                        "",
                    ),
                    None,
                )
            self._refresh_active_recs_lb()
            self._log_append(f"[{_ts()}] ERROR ({event.device_label}): {event.message}")
            self._set_status(f"Record error: {event.message}")

    def _refresh_active_recs_lb(self) -> None:
        self._active_recs_lb.delete(0, "end")
        for display in self._active_recs.values():
            self._active_recs_lb.insert("end", display)

    def _update_preview(self) -> None:
        """最新フレームをプレビューキャンバスに描画する。"""
        if self._recorder is None:
            return
        frame = self._recorder.get_preview_frame()
        if frame is None:
            return
        # BGR → RGB → PIL → ImageTk に変換
        rgb = frame[:, :, ::-1]
        img = Image.fromarray(rgb)
        cw = self._preview_canvas.winfo_width() or self._cfg.camera.preview_width
        ch = self._preview_canvas.winfo_height() or self._cfg.camera.preview_height
        img.thumbnail((cw, ch), Image.Resampling.LANCZOS)
        self._preview_tk_img = ImageTk.PhotoImage(img)
        self._preview_canvas.itemconfig(
            self._preview_image_id, image=self._preview_tk_img
        )

    # ------------------------------------------------------------------
    # PLC 接続
    # ------------------------------------------------------------------

    def _toggle_plc_connection(self) -> None:
        """接続中なら切断、未接続ならモニターを起動する。"""
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
        """新たに PLCモニタースレッドを生成・起動する。"""
        self._plc_monitor = PlcMonitor(
            self._cfg.plc, _GUI_EVENT_QUEUE, simulate=self._simulate_mode
        )
        self._plc_monitor.start()
        self._btn_connect.config(text="Disconnect PLC")
        self._set_status(f"Connecting to {self._cfg.plc.ip}:{self._cfg.plc.port}…")

    def _on_plc_status(self, event: StatusEvent) -> None:
        """ステータス変化イベントに応じて PLC インジケータを更新する。

        Args:
            event: 受信した :class:`~plc_monitor.StatusEvent`。
        """
        if event.status == PlcStatus.CONNECTED:
            self._plc_light.set_color("green")
            self._plc_status_label.config(
                text=f"Connected  {self._cfg.plc.ip}:{self._cfg.plc.port}"
            )
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
        """ビット状態イベントでデバイスツリーを更新する。

        Args:
            states: アドレス -> ON/OFF の辞書。
        """
        for addr, on in states.items():
            with contextlib.suppress(tk.TclError):
                self._dev_tree.set(addr, "state", "ON" if on else "off")

    # ------------------------------------------------------------------
    # シミュレーションモード
    # ------------------------------------------------------------------

    def _toggle_simulation(self) -> None:
        """シミュレーションモードの有効／無効を切り替える。"""
        self._simulate_mode = not self._simulate_mode
        if self._simulate_mode:
            self._sim_frame.pack(side="right", padx=2, pady=2)
            messagebox.showinfo(
                "Simulation Mode",
                "Simulation mode enabled.\nNo real PLC connection will be made.",
            )
        else:
            self._sim_frame.pack_forget()
        # 動作中なら新モードでモニターを再起動
        if self._plc_monitor and self._plc_monitor.is_alive():
            self._plc_monitor.stop()
            self._start_plc_monitor()

    def _sim_fire_trigger(self) -> None:
        """シミュレーションコンボで選択中のデバイスにトリガーを送信する。"""
        addr = self._sim_combo.get()
        if addr and self._plc_monitor:
            self._plc_monitor.simulate_trigger(addr)

    # ------------------------------------------------------------------
    # 録画
    # ------------------------------------------------------------------

    def _manual_record(self) -> None:
        """手動トリガーで録画を実行する。"""
        self._do_trigger_record("manual")

    def _do_trigger_record(self, device_label: str) -> None:
        """指定デバイスラベルでレコーダーのトリガーを発火する。

        Args:
            device_label: トリガーしたデバイスのラベル。
        """
        if self._recorder is None:
            return
        self._recorder.trigger_record(device_label)
        # beep-lite が利用可能かつ有効な場合は通知音を再生（ノンブロッキング）
        if _beep is not None and self._cfg.record.beep_on_trigger:
            _beep.ok()

    # ------------------------------------------------------------------
    # 設定ダイアログ
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        """設定ダイアログを開き、OK 時は設定を保存しコンポーネントを更新する。"""
        dlg = SettingsDialog(self, self._cfg)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._cfg = dlg.result
        save_config(self._cfg)
        self._apply_config_to_ui()
        # beep が有効になった場合はプリロードしてレイテンシを低減
        if _beep is not None and self._cfg.record.beep_on_trigger:
            _beep.preload_all()
        if self._recorder:
            self._recorder.update_config(self._cfg)
        if self._plc_monitor:
            self._plc_monitor.update_config(self._cfg.plc)

    # ------------------------------------------------------------------
    # ログヘルパー
    # ------------------------------------------------------------------

    def _log_append(self, text: str) -> None:
        """録画ログにテキストを追記する。

        Args:
            text: 追記するテキスト。
        """
        self._log.config(state="normal")
        self._log.insert("end", text + "\n")
        # 古い行を削除
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > _LOG_MAX_LINES:
            self._log.delete("1.0", f"{lines - _LOG_MAX_LINES}.0")
        self._log.see("end")
        self._log.config(state="disabled")

    def _set_status(self, msg: str) -> None:
        """ステータスバーのメッセージを更新する。

        Args:
            msg: 表示するメッセージ。
        """
        self._status_bar_label.config(text=msg)

    # ------------------------------------------------------------------
    # 終了処理
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        """全スレッドを安全に停止し設定を保存してウィンドウを閉じる。"""
        self._closing = True
        if self._plc_monitor:
            self._plc_monitor.stop()
        if self._recorder:
            self._recorder.stop()
        save_config(self._cfg)
        # タイムアウト付き join — cv2 内部スレッド / セーバースレッドがブロックする可能性あり
        if self._plc_monitor:
            self._plc_monitor.join(timeout=2.0)
        if self._recorder:
            self._recorder.join(timeout=2.0)
        self.destroy()
        os._exit(0)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _ts() -> str:
    """現在時刻を HH:MM:SS 形式の文字列で返す。"""
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    """アプリケーションのエントリポイント。"""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
