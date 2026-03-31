"""設定ダイアログ — PLCトリガーレコーダーのタブ形式設定UI。"""

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
)

if TYPE_CHECKING:
    pass


class SettingsDialog(tk.Toplevel):
    """タブ形式のモーダル設定ダイアログ。

    OKボタン押下後は ``self.result`` に更新済みの
    :class:`~config.AppConfig` が格納される。キャンセル時は ``None``。
    """

    def __init__(self, parent: tk.Misc, cfg: AppConfig) -> None:
        """設定ダイアログを初期化する。

        Args:
            parent: 親ウィジェット。
            cfg: 現在の設定値。
        """
        super().__init__(parent)
        self.title("Settings")
        self.resizable(False, False)
        self.result: AppConfig | None = None

        # 作業用コピー
        self._devices: list[DeviceConfig] = [
            DeviceConfig(address=d.address, label=d.label, enabled=d.enabled)
            for d in cfg.plc.devices
        ]

        self._build_ui()
        self._populate(cfg)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_frame, text="OK", command=self._on_ok, width=10).pack(
            side="right", padx=2
        )
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel, width=10).pack(
            side="right"
        )

        if isinstance(parent, tk.Wm):
            self.transient(parent)
        self.grab_set()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """ノートブックと各タブ、ボタン行を生成する。"""
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=12)
        nb.add(self._build_tab_plc(nb), text="PLC")
        nb.add(self._build_tab_devices(nb), text="Devices")
        nb.add(self._build_tab_camera(nb), text="Camera")
        nb.add(self._build_tab_record(nb), text="Record")
        nb.add(self._build_tab_options(nb), text="Options")

    # ---- PLCタブ ---------------------------------------------------------

    def _build_tab_plc(self, parent: ttk.Notebook) -> ttk.Frame:
        """PLCタブのウィジェットを構築する。

        Args:
            parent: 追加先の Notebook。

        Returns:
            構築したタブフレーム。
        """
        f = ttk.Frame(parent, padding=12)
        self._plc_ip = self._labeled_entry(f, "IP Address:", 0)
        self._plc_port = self._labeled_entry(f, "Port:", 1)
        ttk.Label(f, text="PLC Type:").grid(row=2, column=0, sticky="w", pady=3)
        self._plc_type = ttk.Combobox(f, values=PLC_TYPES, state="readonly", width=18)
        self._plc_type.grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Protocol:").grid(row=3, column=0, sticky="w", pady=3)
        self._plc_protocol = ttk.Combobox(
            f, values=PROTOCOL_TYPES, state="readonly", width=18
        )
        self._plc_protocol.grid(row=3, column=1, sticky="w", pady=3)
        self._plc_poll = self._labeled_entry(f, "Poll interval (ms):", 4)
        return f

    # ---- デバイスタブ -----------------------------------------------------

    def _build_tab_devices(self, parent: ttk.Notebook) -> ttk.Frame:
        """デバイスタブのウィジェットを構築する。

        Args:
            parent: 追加先の Notebook。

        Returns:
            構築したタブフレーム。
        """
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
        ttk.Button(f, text="Add", command=self._dev_add, width=8).grid(
            row=1, column=0, padx=2
        )
        ttk.Button(f, text="Edit", command=self._dev_edit, width=8).grid(
            row=1, column=1, padx=2
        )
        ttk.Button(f, text="Delete", command=self._dev_delete, width=8).grid(
            row=1, column=2, padx=2
        )
        ttk.Button(f, text="Toggle", command=self._dev_toggle, width=8).grid(
            row=1, column=3, padx=2
        )
        f.columnconfigure(0, weight=1)
        return f

    # ---- カメラタブ ------------------------------------------------------

    def _build_tab_camera(self, parent: ttk.Notebook) -> ttk.Frame:
        """カメラタブのウィジェットを構築する。

        Args:
            parent: 追加先の Notebook。

        Returns:
            構築したタブフレーム。
        """
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

    # ---- 録画タブ ------------------------------------------------------

    def _build_tab_record(self, parent: ttk.Notebook) -> ttk.Frame:
        """録画タブのウィジェットを構築する。

        Args:
            parent: 追加先の Notebook。

        Returns:
            構築したタブフレーム。
        """
        f = ttk.Frame(parent, padding=12)

        self._rec_pre = self._labeled_entry(f, "Pre-trigger (sec):", 0)
        self._rec_post = self._labeled_entry(f, "Post-trigger (sec):", 1)

        ttk.Label(f, text="Video Format:").grid(row=2, column=0, sticky="w", pady=3)
        self._rec_format = ttk.Combobox(
            f, values=VIDEO_FORMAT_NAMES, state="readonly", width=18
        )
        self._rec_format.grid(row=2, column=1, sticky="w", pady=3)
        self._rec_format.bind("<<ComboboxSelected>>", self._on_format_changed)

        ttk.Label(f, text="Video Codec:").grid(row=3, column=0, sticky="w", pady=3)
        self._rec_codec = ttk.Combobox(f, values=[], state="readonly", width=18)
        self._rec_codec.grid(row=3, column=1, sticky="w", pady=3)

        ttk.Label(f, text="Save Path:").grid(row=4, column=0, sticky="w", pady=3)
        path_frame = ttk.Frame(f)
        path_frame.grid(row=4, column=1, sticky="ew")
        self._rec_save_path = ttk.Entry(path_frame, width=28)
        self._rec_save_path.pack(side="left")
        ttk.Button(path_frame, text="…", width=3, command=self._browse_save_path).pack(
            side="left", padx=(4, 0)
        )

        self._rec_filename_fmt = self._labeled_entry(f, "Filename Format:", 5)
        ttk.Label(
            f,
            text="  e.g. %Y%m%d_%H%M%S_{device}",
            foreground="gray",
        ).grid(row=6, column=0, columnspan=2, sticky="w")
        return f

    # ---- オプションタブ -----------------------------------------------------

    def _build_tab_options(self, parent: ttk.Notebook) -> ttk.Frame:
        """オプションタブのウィジェットを構築する。

        Args:
            parent: 追加先の Notebook。

        Returns:
            構築したタブフレーム。
        """
        f = ttk.Frame(parent, padding=12)
        self._daily_folder = tk.BooleanVar()
        ttk.Checkbutton(
            f, text="Create daily sub-folder (YYYY-MM-DD)", variable=self._daily_folder
        ).grid(row=0, column=0, sticky="w", pady=4)
        self._device_subfolder = tk.BooleanVar()
        ttk.Checkbutton(
            f,
            text="Create sub-folder per device label",
            variable=self._device_subfolder,
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
        """設定値を各ウィジェットへ反映する。

        Args:
            cfg: 反映する設定値。
        """
        # PLC設定
        self._plc_ip.delete(0, "end")
        self._plc_ip.insert(0, cfg.plc.ip)
        self._plc_port.delete(0, "end")
        self._plc_port.insert(0, str(cfg.plc.port))
        self._plc_type.set(cfg.plc.plc_type)
        self._plc_protocol.set(cfg.plc.protocol)
        self._plc_poll.delete(0, "end")
        self._plc_poll.insert(0, str(cfg.plc.poll_interval_ms))
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
        # 録画設定
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
        # オプション設定
        self._daily_folder.set(cfg.record.daily_folder)
        self._device_subfolder.set(cfg.record.device_subfolder)
        self._beep_on_trigger.set(cfg.record.beep_on_trigger)

    # ------------------------------------------------------------------
    # ウィジェット値を取得 → AppConfig
    # ------------------------------------------------------------------

    def _collect(self) -> AppConfig | None:
        """全ウィジェットの値を読み取り新しい AppConfig を返す。入力エラー時は None。"""
        try:
            port = int(self._plc_port.get().strip())
            poll = int(self._plc_poll.get().strip())
        except ValueError:
            messagebox.showerror(
                "Invalid input", "Port and Poll interval must be integers.", parent=self
            )
            return None

        try:
            pre = float(self._rec_pre.get().strip())
            post = float(self._rec_post.get().strip())
            if pre < 0 or post < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Pre/Post-trigger seconds must be non-negative numbers.",
                parent=self,
            )
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
            messagebox.showerror(
                "Invalid input", "Camera values must be positive numbers.", parent=self
            )
            return None

        save_path = self._rec_save_path.get().strip()
        if not save_path:
            messagebox.showerror(
                "Invalid input", "Save path cannot be empty.", parent=self
            )
            return None

        fmt = self._rec_format.get()
        codec = self._rec_codec.get()

        plc = PlcConfig(
            ip=self._plc_ip.get().strip(),
            port=port,
            plc_type=self._plc_type.get(),
            protocol=self._plc_protocol.get(),
            poll_interval_ms=poll,
            devices=list(self._devices),
        )
        camera = CameraConfig(
            index=cam_index,
            capture_width=cap_w,
            capture_height=cap_h,
            preview_width=prev_w,
            preview_height=prev_h,
            fps=fps,
        )
        record = RecordConfig(
            pre_trigger_sec=pre,
            post_trigger_sec=post,
            video_format=fmt,
            video_codec=codec,
            save_path=save_path,
            filename_format=self._rec_filename_fmt.get().strip()
            or "%Y%m%d_%H%M%S_{device}",
            daily_folder=self._daily_folder.get(),
            device_subfolder=self._device_subfolder.get(),
            beep_on_trigger=self._beep_on_trigger.get(),
        )
        return AppConfig(plc=plc, camera=camera, record=record)

    # ------------------------------------------------------------------
    # ダイアログボタン
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        """入力値を検証して AppConfig を生成し、ダイアログを邉じる。"""
        cfg = self._collect()
        if cfg is None:
            return
        self.result = cfg
        self.destroy()

    def _on_cancel(self) -> None:
        """変更を破棄してダイアログを邉じる。"""
        self.destroy()

    # ------------------------------------------------------------------
    # デバイスリスト操作
    # ------------------------------------------------------------------

    def _refresh_dev_tree(self) -> None:
        """デバイスツリーを ``self._devices`` の内容で再描画する。"""
        self._dev_tree.delete(*self._dev_tree.get_children())
        for dev in self._devices:
            self._dev_tree.insert(
                "",
                "end",
                values=(dev.address, dev.label, "Yes" if dev.enabled else "No"),
            )

    def _dev_add(self) -> None:
        """デバイス追加ダイアログを開き、結果をリストへ追記する。"""
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
        """選択中のデバイスを編集ダイアログで更新する。"""
        sel = self._dev_tree.selection()
        if not sel:
            return
        idx = self._dev_tree.index(sel[0])
        dlg = _DeviceEditDialog(self, self._devices[idx])
        self.wait_window(dlg)
        if dlg.result:
            if any(
                i != idx and d.address == dlg.result.address
                for i, d in enumerate(self._devices)
            ):
                messagebox.showerror(
                    "Duplicate address",
                    f"Device address '{dlg.result.address}' is already in use.",
                    parent=self,
                )
                return
            self._devices[idx] = dlg.result
            self._refresh_dev_tree()

    def _dev_delete(self) -> None:
        """選択中のデバイスをリストから削除する。"""
        sel = self._dev_tree.selection()
        if not sel:
            return
        idx = self._dev_tree.index(sel[0])
        del self._devices[idx]
        self._refresh_dev_tree()

    def _dev_toggle(self) -> None:
        """選択中のデバイスの有効／無効を切り替える。"""
        sel = self._dev_tree.selection()
        if not sel:
            return
        idx = self._dev_tree.index(sel[0])
        dev = self._devices[idx]
        self._devices[idx] = DeviceConfig(
            address=dev.address, label=dev.label, enabled=not dev.enabled
        )
        self._refresh_dev_tree()

    # ------------------------------------------------------------------
    # 録画タブヘルパー
    # ------------------------------------------------------------------

    def _on_format_changed(self, _event: object = None) -> None:
        """フォーマット選択変更時にコーデック選択肉を更新する。"""
        self._update_codec_choices(self._rec_format.get())

    def _update_codec_choices(self, fmt: str) -> None:
        """フォーマットに応じてコーデック選択肉を更新する。

        Args:
            fmt: VIDEO_FORMATS のキー文字列。
        """
        _, codecs = VIDEO_FORMATS.get(fmt, (".mp4", ["mp4v"]))
        self._rec_codec["values"] = codecs
        if self._rec_codec.get() not in codecs:
            self._rec_codec.set(codecs[0])

    def _browse_save_path(self) -> None:
        """フォルダ選択ダイアログを開き、保存先パスを更新する。"""
        current = self._rec_save_path.get().strip()
        initial = current if Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, parent=self)
        if chosen:
            self._rec_save_path.delete(0, "end")
            self._rec_save_path.insert(0, chosen)

    # ------------------------------------------------------------------
    # 共通ウィジェットファクトリー
    # ------------------------------------------------------------------

    def _labeled_entry(self, parent: ttk.Frame, label: str, row: int) -> ttk.Entry:
        """ラベルとエントリをグリッドに配置して返す。

        Args:
            parent: 配置先のフレーム。
            label: ラベルテキスト。
            row: グリッド行番号。

        Returns:
            生成した Entry ウィジェット。
        """
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, width=22)
        entry.grid(row=row, column=1, sticky="w", pady=3)
        return entry


# ---------------------------------------------------------------------------
# デバイス追加/編集サブダイアログ
# ---------------------------------------------------------------------------


class _DeviceEditDialog(tk.Toplevel):
    """デバイスの追加／編集を行うサブダイアログ。

    OKボタン押下後は ``self.result`` に :class:`~config.DeviceConfig` が格納される。
    キャンセル時は ``None``。
    """

    def __init__(self, parent: tk.Misc, dev: DeviceConfig) -> None:
        """デバイス編集ダイアログを初期化する。

        Args:
            parent: 親ウィジェット。
            dev: 編集対象のデバイス設定。新規追加時はデフォルト値を渡す。
        """
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
        ttk.Button(btn_frame, text="OK", command=self._on_ok, width=8).pack(
            side="right", padx=2
        )
        ttk.Button(btn_frame, text="Cancel", command=self.destroy, width=8).pack(
            side="right"
        )

        if isinstance(parent, tk.Wm):
            self.transient(parent)
        self.grab_set()

    def _on_ok(self) -> None:
        """入力値を検証して DeviceConfig を生成し、ダイアログを邉じる。"""
        addr = self._address.get().strip()
        lbl = self._label.get().strip()
        if not addr:
            messagebox.showerror(
                "Invalid input", "Device address cannot be empty.", parent=self
            )
            return
        self.result = DeviceConfig(
            address=addr, label=lbl or addr, enabled=self._enabled.get()
        )
        self.destroy()
