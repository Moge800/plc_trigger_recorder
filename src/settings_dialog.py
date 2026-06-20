"""設定ダイアログ — PLCトリガースイートのタブ形式設定UI。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from config import (
    PLC_TYPES,
    PROTOCOL_TYPES,
    VIDEO_FORMAT_NAMES,
    VIDEO_FORMATS,
    AppConfig,
    CameraConfig,
    DeviceConfig,
    PlcConfig,
    RecordConfig,
    SaveConfig,
)

if TYPE_CHECKING:
    pass


class SettingsDialog(tk.Toplevel):
    """タブ形式のモーダル設定ダイアログ。

    OKボタン押下後は ``self.result`` に更新済みの
    :class:`~config.AppConfig` が格納される。キャンセル時は ``None``。

    タブ構成
    --------
    PLC → Devices → Camera → Mode → Options
    """

    def __init__(self, parent: tk.Misc, cfg: AppConfig) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.resizable(False, False)
        self.result: AppConfig | None = None

        self._devices: list[DeviceConfig] = [
            DeviceConfig(address=d.address, label=d.label, enabled=d.enabled)
            for d in cfg.plc.devices
        ]

        self._build_ui()
        self._populate(cfg)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_frame, text="OK", command=self._on_ok, width=10).pack(side="right", padx=2)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel, width=10).pack(side="right")

        if isinstance(parent, tk.Wm):
            self.transient(parent)
        self.grab_set()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=12)
        nb.add(self._build_tab_plc(nb), text="PLC")
        nb.add(self._build_tab_devices(nb), text="Devices")
        nb.add(self._build_tab_camera(nb), text="Camera")
        nb.add(self._build_tab_mode(nb), text="Mode")
        nb.add(self._build_tab_options(nb), text="Options")

    # ---- PLC タブ --------------------------------------------------------

    def _build_tab_plc(self, parent: ttk.Notebook) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)

        # 接続方式ラジオボタン
        ttk.Label(f, text="Connection:").grid(row=0, column=0, sticky="w", pady=3)
        self._conn_type = tk.StringVar(value="mc_direct")
        conn_frame = ttk.Frame(f)
        conn_frame.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(
            conn_frame, text="MC Direct", variable=self._conn_type,
            value="mc_direct", command=self._on_conn_type_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            conn_frame, text="gomc-rest", variable=self._conn_type,
            value="gomc_rest", command=self._on_conn_type_changed,
        ).pack(side="left", padx=(8, 0))

        # MC Direct 専用フィールド群
        self._mc_frame = ttk.Frame(f)
        self._mc_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._plc_ip = self._labeled_entry(self._mc_frame, "IP Address:", 0)
        self._plc_port = self._labeled_entry(self._mc_frame, "Port:", 1)
        ttk.Label(self._mc_frame, text="PLC Type:").grid(row=2, column=0, sticky="w", pady=3)
        self._plc_type = ttk.Combobox(self._mc_frame, values=PLC_TYPES, state="readonly", width=18)
        self._plc_type.grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(self._mc_frame, text="Protocol:").grid(row=3, column=0, sticky="w", pady=3)
        self._plc_protocol = ttk.Combobox(
            self._mc_frame, values=PROTOCOL_TYPES, state="readonly", width=18
        )
        self._plc_protocol.grid(row=3, column=1, sticky="w", pady=3)

        # gomc-rest 専用フィールド群
        self._gomc_frame = ttk.Frame(f)
        self._gomc_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._gomc_url = self._labeled_entry(self._gomc_frame, "gomc-rest URL:", 0)
        ttk.Label(
            self._gomc_frame,
            text="  例: http://localhost:8080",
            foreground="gray",
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        # 共通フィールド（ポーリング間隔）
        self._plc_poll = self._labeled_entry(f, "Poll interval (ms):", 2)

        # 初期表示状態を設定
        self._gomc_frame.grid_remove()
        return f

    def _on_conn_type_changed(self) -> None:
        if self._conn_type.get() == "gomc_rest":
            self._mc_frame.grid_remove()
            self._gomc_frame.grid()
        else:
            self._gomc_frame.grid_remove()
            self._mc_frame.grid()

    # ---- デバイスタブ ----------------------------------------------------

    def _build_tab_devices(self, parent: ttk.Notebook) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)
        cols = ("address", "label", "enabled")
        self._dev_tree = ttk.Treeview(f, columns=cols, show="headings", height=8)
        self._dev_tree.heading("address", text="Device Address")
        self._dev_tree.heading("label", text="Label")
        self._dev_tree.heading("enabled", text="Enabled")
        self._dev_tree.column("address", width=140)
        self._dev_tree.column("label", width=140)
        self._dev_tree.column("enabled", width=70, anchor="center")
        self._dev_tree.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 6))
        ttk.Button(f, text="Add", command=self._dev_add, width=8).grid(row=1, column=0, padx=2)
        ttk.Button(f, text="Edit", command=self._dev_edit, width=8).grid(row=1, column=1, padx=2)
        ttk.Button(f, text="Delete", command=self._dev_delete, width=8).grid(row=1, column=2, padx=2)
        ttk.Button(f, text="Toggle", command=self._dev_toggle, width=8).grid(row=1, column=3, padx=2)
        f.columnconfigure(0, weight=1)
        return f

    # ---- カメラタブ -------------------------------------------------------

    def _build_tab_camera(self, parent: ttk.Notebook) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)
        self._cam_index = self._labeled_entry(f, "Camera Index:", 0)
        self._cam_cap_w = self._labeled_entry(f, "Capture Width:", 1)
        self._cam_cap_h = self._labeled_entry(f, "Capture Height:", 2)
        self._cam_prev_w = self._labeled_entry(f, "Preview Width:", 3)
        self._cam_prev_h = self._labeled_entry(f, "Preview Height:", 4)
        self._cam_fps = self._labeled_entry(f, "FPS:", 5)
        ttk.Label(
            f,
            text="  Note: large capture resolution increases RAM usage significantly.",
            foreground="gray",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return f

    # ---- モードタブ -------------------------------------------------------

    def _build_tab_mode(self, parent: ttk.Notebook) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)

        # キャプチャモード選択ラジオ
        ttk.Label(f, text="Capture Mode:").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self._capture_mode = tk.StringVar(value="record")
        mode_frame = ttk.Frame(f)
        mode_frame.grid(row=0, column=1, sticky="w", pady=(0, 6))
        ttk.Radiobutton(
            mode_frame, text="Photo (PNG)", variable=self._capture_mode,
            value="capture", command=self._on_mode_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            mode_frame, text="Video", variable=self._capture_mode,
            value="record", command=self._on_mode_changed,
        ).pack(side="left", padx=(12, 0))

        # --- Photo 設定フレーム ---
        self._photo_lf = ttk.LabelFrame(f, text="Photo Settings", padding=8)
        self._photo_lf.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._cap_save_path_entry = ttk.Entry(self._photo_lf, width=28)
        ttk.Label(self._photo_lf, text="Save Path:").grid(row=0, column=0, sticky="w", pady=3)
        path_frame1 = ttk.Frame(self._photo_lf)
        path_frame1.grid(row=0, column=1, sticky="ew")
        self._cap_save_path_entry.pack(in_=path_frame1, side="left")
        ttk.Button(
            path_frame1, text="…", width=3,
            command=lambda: self._browse_path(self._cap_save_path_entry),
        ).pack(side="left", padx=(4, 0))
        self._cap_filename_fmt = self._labeled_entry(self._photo_lf, "Filename Format:", 1)
        ttk.Label(
            self._photo_lf, text="  e.g. %Y%m%d_%H%M%S_{ms:03d}_{device}", foreground="gray"
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(self._photo_lf, text="PNG Compression:").grid(row=3, column=0, sticky="w", pady=3)
        # ttk.Scale はドラッグ中に浮動小数文字列("1.0")を変数へ書き込むため、
        # IntVar だと TclError になる。DoubleVar を使い表示時に int 化する。
        self._cap_png_compression = tk.DoubleVar(value=1.0)
        png_frame = ttk.Frame(self._photo_lf)
        png_frame.grid(row=3, column=1, sticky="w")
        ttk.Scale(
            png_frame, from_=0, to=9, orient="horizontal",
            variable=self._cap_png_compression, length=120,
        ).pack(side="left")
        self._cap_png_label = ttk.Label(png_frame, text="1", width=2)
        self._cap_png_label.pack(side="left", padx=(4, 0))
        self._cap_png_compression.trace_add(
            "write",
            lambda *_: self._cap_png_label.config(text=str(int(self._cap_png_compression.get()))),
        )

        # --- Video 設定フレーム ---
        self._video_lf = ttk.LabelFrame(f, text="Video Settings", padding=8)
        self._video_lf.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._rec_pre = self._labeled_entry(self._video_lf, "Pre-trigger (sec):", 0)
        self._rec_post = self._labeled_entry(self._video_lf, "Post-trigger (sec):", 1)
        ttk.Label(self._video_lf, text="Video Format:").grid(row=2, column=0, sticky="w", pady=3)
        self._rec_format = ttk.Combobox(
            self._video_lf, values=VIDEO_FORMAT_NAMES, state="readonly", width=18
        )
        self._rec_format.grid(row=2, column=1, sticky="w", pady=3)
        self._rec_format.bind("<<ComboboxSelected>>", self._on_format_changed)
        ttk.Label(self._video_lf, text="Video Codec:").grid(row=3, column=0, sticky="w", pady=3)
        self._rec_codec = ttk.Combobox(self._video_lf, values=[], state="readonly", width=18)
        self._rec_codec.grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(self._video_lf, text="Save Path:").grid(row=4, column=0, sticky="w", pady=3)
        path_frame2 = ttk.Frame(self._video_lf)
        path_frame2.grid(row=4, column=1, sticky="ew")
        self._rec_save_path = ttk.Entry(path_frame2, width=28)
        self._rec_save_path.pack(side="left")
        ttk.Button(
            path_frame2, text="…", width=3,
            command=lambda: self._browse_path(self._rec_save_path),
        ).pack(side="left", padx=(4, 0))
        self._rec_filename_fmt = self._labeled_entry(self._video_lf, "Filename Format:", 5)
        ttk.Label(
            self._video_lf, text="  e.g. %Y%m%d_%H%M%S_{device}", foreground="gray"
        ).grid(row=6, column=0, columnspan=2, sticky="w")

        return f

    def _on_mode_changed(self) -> None:
        """モード切替時に、アクティブなモードの設定フレームを強調表示する。"""
        mode = self._capture_mode.get()
        self._photo_lf.config(text="Photo Settings" + (" (active)" if mode == "capture" else ""))
        self._video_lf.config(text="Video Settings" + (" (active)" if mode == "record" else ""))

    # ---- オプションタブ --------------------------------------------------

    def _build_tab_options(self, parent: ttk.Notebook) -> ttk.Frame:
        f = ttk.Frame(parent, padding=12)
        self._daily_folder = tk.BooleanVar()
        ttk.Checkbutton(
            f, text="Create daily sub-folder (YYYY-MM-DD)", variable=self._daily_folder
        ).grid(row=0, column=0, sticky="w", pady=4)
        self._device_subfolder = tk.BooleanVar()
        ttk.Checkbutton(
            f, text="Create sub-folder per device label", variable=self._device_subfolder
        ).grid(row=1, column=0, sticky="w", pady=4)
        self._beep_on_trigger = tk.BooleanVar()
        ttk.Checkbutton(
            f,
            text="Beep on trigger  (要 beep-lite: uv sync --extra audio)",
            variable=self._beep_on_trigger,
        ).grid(row=2, column=0, sticky="w", pady=4)
        return f

    # ------------------------------------------------------------------
    # 設定値を各ウィジェットへ反映
    # ------------------------------------------------------------------

    def _populate(self, cfg: AppConfig) -> None:
        # PLC 設定
        self._conn_type.set(cfg.plc.connection_type)
        self._plc_ip.delete(0, "end")
        self._plc_ip.insert(0, cfg.plc.ip)
        self._plc_port.delete(0, "end")
        self._plc_port.insert(0, str(cfg.plc.port))
        self._plc_type.set(cfg.plc.plc_type)
        self._plc_protocol.set(cfg.plc.protocol)
        self._gomc_url.delete(0, "end")
        self._gomc_url.insert(0, cfg.plc.gomc_rest_url)
        self._plc_poll.delete(0, "end")
        self._plc_poll.insert(0, str(cfg.plc.poll_interval_ms))
        self._on_conn_type_changed()

        # デバイス設定
        self._refresh_dev_tree()

        # カメラ設定
        self._cam_index.delete(0, "end")
        self._cam_index.insert(0, str(cfg.camera.index))
        self._cam_cap_w.delete(0, "end")
        self._cam_cap_w.insert(0, str(cfg.camera.capture_width))
        self._cam_cap_h.delete(0, "end")
        self._cam_cap_h.insert(0, str(cfg.camera.capture_height))
        self._cam_prev_w.delete(0, "end")
        self._cam_prev_w.insert(0, str(cfg.camera.preview_width))
        self._cam_prev_h.delete(0, "end")
        self._cam_prev_h.insert(0, str(cfg.camera.preview_height))
        self._cam_fps.delete(0, "end")
        self._cam_fps.insert(0, str(cfg.camera.fps))

        # モード設定
        self._capture_mode.set(cfg.capture_mode)

        # Photo 設定
        self._cap_save_path_entry.delete(0, "end")
        self._cap_save_path_entry.insert(0, cfg.capture.save_path)
        self._cap_filename_fmt.delete(0, "end")
        self._cap_filename_fmt.insert(0, cfg.capture.filename_format)
        self._cap_png_compression.set(cfg.capture.png_compression)

        # Video 設定
        self._rec_pre.delete(0, "end")
        self._rec_pre.insert(0, str(cfg.record.pre_trigger_sec))
        self._rec_post.delete(0, "end")
        self._rec_post.insert(0, str(cfg.record.post_trigger_sec))
        self._rec_format.set(cfg.record.video_format)
        self._update_codec_choices(cfg.record.video_format)
        self._rec_codec.set(cfg.record.video_codec)
        self._rec_save_path.delete(0, "end")
        self._rec_save_path.insert(0, cfg.record.save_path)
        self._rec_filename_fmt.delete(0, "end")
        self._rec_filename_fmt.insert(0, cfg.record.filename_format)

        # オプション（アクティブモードの値で初期化; 両方に同じ値を適用）
        active_daily = cfg.capture.daily_folder if cfg.capture_mode == "capture" else cfg.record.daily_folder
        active_sub = cfg.capture.device_subfolder if cfg.capture_mode == "capture" else cfg.record.device_subfolder
        active_beep = cfg.capture.beep_on_trigger if cfg.capture_mode == "capture" else cfg.record.beep_on_trigger
        self._daily_folder.set(active_daily)
        self._device_subfolder.set(active_sub)
        self._beep_on_trigger.set(active_beep)

        # アクティブモードの強調表示を初期化
        self._on_mode_changed()

    # ------------------------------------------------------------------
    # ウィジェット値を取得 → AppConfig
    # ------------------------------------------------------------------

    def _collect(self) -> AppConfig | None:
        conn_type = self._conn_type.get()
        # Port は MC Direct 時のみ必須。gomc-rest 時は隠れフィールドの値を検証しない。
        try:
            poll = int(self._plc_poll.get().strip())
            if conn_type == "mc_direct":
                port = int(self._plc_port.get().strip())
            else:
                try:
                    port = int(self._plc_port.get().strip())
                except ValueError:
                    port = PlcConfig().port
        except ValueError:
            messagebox.showerror("Invalid input", "Port and Poll interval must be integers.", parent=self)
            return None

        try:
            cam_index = int(self._cam_index.get().strip())
            cap_w = int(self._cam_cap_w.get().strip())
            cap_h = int(self._cam_cap_h.get().strip())
            prev_w = int(self._cam_prev_w.get().strip())
            prev_h = int(self._cam_prev_h.get().strip())
            fps = float(self._cam_fps.get().strip())
            if fps <= 0 or cap_w <= 0 or cap_h <= 0 or prev_w <= 0 or prev_h <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid input", "Camera values must be positive numbers.", parent=self)
            return None

        mode = self._capture_mode.get()

        # Photo モードで Pre/Post が両方空欄ならデフォルト値を使用。
        # Video モードでは従来どおり float 必須・非負を検証する。
        pre_str = self._rec_pre.get().strip()
        post_str = self._rec_post.get().strip()
        if mode == "capture" and not pre_str and not post_str:
            pre = RecordConfig().pre_trigger_sec
            post = RecordConfig().post_trigger_sec
        else:
            try:
                pre = float(pre_str)
                post = float(post_str)
                if pre < 0 or post < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid input",
                    "Pre/Post-trigger seconds must be non-negative numbers.",
                    parent=self,
                )
                return None

        # gomc-rest 選択時は URL を必須とする
        gomc_url = self._gomc_url.get().strip()
        if conn_type == "gomc_rest" and not gomc_url:
            messagebox.showerror("Invalid input", "gomc-rest URL cannot be empty.", parent=self)
            return None

        # アクティブなモードのみ Save Path を必須とし、非アクティブ側は空ならデフォルトを使う
        cap_path = self._cap_save_path_entry.get().strip()
        rec_path = self._rec_save_path.get().strip()
        if mode == "capture" and not cap_path:
            messagebox.showerror("Invalid input", "Photo Save path cannot be empty.", parent=self)
            return None
        if mode == "record" and not rec_path:
            messagebox.showerror("Invalid input", "Video Save path cannot be empty.", parent=self)
            return None
        if not cap_path:
            cap_path = SaveConfig().save_path
        if not rec_path:
            rec_path = RecordConfig().save_path

        daily = self._daily_folder.get()
        dev_sub = self._device_subfolder.get()
        beep = self._beep_on_trigger.get()

        plc = PlcConfig(
            ip=self._plc_ip.get().strip(),
            port=port,
            plc_type=self._plc_type.get(),
            protocol=self._plc_protocol.get(),
            poll_interval_ms=poll,
            devices=list(self._devices),
            connection_type=conn_type,
            gomc_rest_url=gomc_url,
        )
        camera = CameraConfig(
            index=cam_index,
            capture_width=cap_w,
            capture_height=cap_h,
            preview_width=prev_w,
            preview_height=prev_h,
            fps=fps,
        )
        png_compression = max(0, min(9, int(self._cap_png_compression.get())))
        capture = SaveConfig(
            save_path=cap_path,
            png_compression=png_compression,
            filename_format=self._cap_filename_fmt.get().strip() or "%Y%m%d_%H%M%S_{ms:03d}_{device}",
            daily_folder=daily,
            device_subfolder=dev_sub,
            beep_on_trigger=beep,
        )
        fmt = self._rec_format.get()
        codec = self._rec_codec.get()
        record = RecordConfig(
            pre_trigger_sec=pre,
            post_trigger_sec=post,
            video_format=fmt,
            video_codec=codec,
            save_path=rec_path,
            filename_format=self._rec_filename_fmt.get().strip() or "%Y%m%d_%H%M%S_{device}",
            daily_folder=daily,
            device_subfolder=dev_sub,
            beep_on_trigger=beep,
        )
        return AppConfig(
            plc=plc,
            camera=camera,
            capture=capture,
            record=record,
            capture_mode=self._capture_mode.get(),
        )

    # ------------------------------------------------------------------
    # ダイアログボタン
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        cfg = self._collect()
        if cfg is None:
            return
        self.result = cfg
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()

    # ------------------------------------------------------------------
    # デバイスリスト操作
    # ------------------------------------------------------------------

    def _refresh_dev_tree(self) -> None:
        self._dev_tree.delete(*self._dev_tree.get_children())
        for dev in self._devices:
            self._dev_tree.insert(
                "", "end", values=(dev.address, dev.label, "Yes" if dev.enabled else "No")
            )

    def _dev_add(self) -> None:
        dlg = _DeviceEditDialog(self, DeviceConfig())
        self.wait_window(dlg)
        if dlg.result:
            if any(d.address == dlg.result.address for d in self._devices):
                messagebox.showerror(
                    "Duplicate address",
                    f"Device address '{dlg.result.address}' is already in use.",
                    parent=self,
                )
                return
            self._devices.append(dlg.result)
            self._refresh_dev_tree()

    def _dev_edit(self) -> None:
        sel = self._dev_tree.selection()
        if not sel:
            return
        idx = self._dev_tree.index(sel[0])
        dlg = _DeviceEditDialog(self, self._devices[idx])
        self.wait_window(dlg)
        if dlg.result:
            if any(i != idx and d.address == dlg.result.address for i, d in enumerate(self._devices)):
                messagebox.showerror(
                    "Duplicate address",
                    f"Device address '{dlg.result.address}' is already in use.",
                    parent=self,
                )
                return
            self._devices[idx] = dlg.result
            self._refresh_dev_tree()

    def _dev_delete(self) -> None:
        sel = self._dev_tree.selection()
        if not sel:
            return
        idx = self._dev_tree.index(sel[0])
        del self._devices[idx]
        self._refresh_dev_tree()

    def _dev_toggle(self) -> None:
        sel = self._dev_tree.selection()
        if not sel:
            return
        idx = self._dev_tree.index(sel[0])
        dev = self._devices[idx]
        self._devices[idx] = DeviceConfig(address=dev.address, label=dev.label, enabled=not dev.enabled)
        self._refresh_dev_tree()

    # ------------------------------------------------------------------
    # モードタブヘルパー
    # ------------------------------------------------------------------

    def _on_format_changed(self, _event: object = None) -> None:
        self._update_codec_choices(self._rec_format.get())

    def _update_codec_choices(self, fmt: str) -> None:
        _, codecs = VIDEO_FORMATS.get(fmt, (".mp4", ["mp4v"]))
        self._rec_codec["values"] = codecs
        if self._rec_codec.get() not in codecs:
            self._rec_codec.set(codecs[0])

    def _browse_path(self, entry: ttk.Entry) -> None:
        current = entry.get().strip()
        initial = current if Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, parent=self)
        if chosen:
            entry.delete(0, "end")
            entry.insert(0, chosen)

    # ------------------------------------------------------------------
    # 共通ウィジェットファクトリー
    # ------------------------------------------------------------------

    def _labeled_entry(self, parent: ttk.Frame | ttk.LabelFrame, label: str, row: int) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, width=22)
        entry.grid(row=row, column=1, sticky="w", pady=3)
        return entry


# ---------------------------------------------------------------------------
# デバイス追加/編集サブダイアログ
# ---------------------------------------------------------------------------


class _DeviceEditDialog(tk.Toplevel):
    """デバイスの追加／編集を行うサブダイアログ。"""

    def __init__(self, parent: tk.Misc, dev: DeviceConfig) -> None:
        super().__init__(parent)
        self.title("Edit Device")
        self.resizable(False, False)
        self.result: DeviceConfig | None = None

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Device Address:").grid(row=0, column=0, sticky="w", pady=4)
        self._address = ttk.Entry(f, width=18)
        self._address.insert(0, dev.address)
        self._address.grid(row=0, column=1, pady=4)
        ttk.Label(f, text="Label:").grid(row=1, column=0, sticky="w", pady=4)
        self._label = ttk.Entry(f, width=18)
        self._label.insert(0, dev.label)
        self._label.grid(row=1, column=1, pady=4)
        self._enabled = tk.BooleanVar(value=dev.enabled)
        ttk.Checkbutton(f, text="Enabled", variable=self._enabled).grid(
            row=2, column=0, columnspan=2, sticky="w"
        )

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_frame, text="OK", command=self._on_ok, width=8).pack(side="right", padx=2)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy, width=8).pack(side="right")

        if isinstance(parent, tk.Wm):
            self.transient(parent)
        self.grab_set()

    def _on_ok(self) -> None:
        addr = self._address.get().strip()
        lbl = self._label.get().strip()
        if not addr:
            messagebox.showerror("Invalid input", "Device address cannot be empty.", parent=self)
            return
        self.result = DeviceConfig(address=addr, label=lbl or addr, enabled=self._enabled.get())
        self.destroy()
