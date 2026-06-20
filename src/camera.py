"""カメラキャプチャスレッド — 連続プレビューフレーム取得および高解像度 PNG 保存。"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from config import AppConfig, CameraConfig, SaveConfig


class CameraThread(threading.Thread):
    """USB カメラから連続的にフレームを取得するバックグラウンドスレッド。

    使い方
    --------
    1. インスタンスを生成しスレッドを開始する。
    2. GUI スレッドから :meth:`get_preview_frame` を呼び出して最新の
       縮小プレビューフレーム（numpy BGR 配列または ``None``）を取得する。
    3. :meth:`capture_hires` を呼び出してフル解像度 PNG を保存し、
       :class:`~pathlib.Path` を取得する。
    4. :meth:`stop` を呼び出してスレッドを安全に停止する。
    """

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__(daemon=True, name="CameraThread")
        self._cam_cfg: CameraConfig = cfg.camera
        self._save_cfg: SaveConfig = cfg.capture
        self._stop_event = threading.Event()

        self._frame_lock = threading.Lock()
        self._frame: np.ndarray | None = None  # type: ignore[type-arg]
        self._capture_lock = threading.Lock()
        # プレビューはキャプチャスレッド側で縮小済みのものを保持し、
        # GUI スレッドでは resize せずコピーを返すだけにする（UI スタッター回避）。
        self._preview_lock = threading.Lock()
        self._preview_frame: np.ndarray | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()

    def update_config(self, cfg: AppConfig) -> None:
        self._cam_cfg = cfg.camera
        self._save_cfg = cfg.capture

    def get_preview_frame(self) -> np.ndarray | None:  # type: ignore[type-arg]
        with self._preview_lock:
            return None if self._preview_frame is None else self._preview_frame.copy()

    def capture_hires(self, device_label: str = "manual") -> Path | None:
        with self._capture_lock:
            with self._frame_lock:
                frame = None if self._frame is None else self._frame.copy()
            if frame is None:
                return None
            save_path = self._build_save_path(device_label)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            # config.json 由来の値が範囲外/非整数でも cv2.imwrite が受け付けるよう 0–9 にクランプ。
            try:
                compression = max(0, min(9, int(self._save_cfg.png_compression)))
            except (TypeError, ValueError):
                compression = 1
            params = [cv2.IMWRITE_PNG_COMPRESSION, compression]
            ok = cv2.imwrite(str(save_path), frame, params)
            if not ok:
                # 保存失敗（権限・パス等）はフレーム無しと区別できるよう例外にする。
                raise OSError(f"cv2.imwrite failed to save: {save_path}")
            return save_path

    # ------------------------------------------------------------------
    # スレッドエントリポイント
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            cap = self._open_camera()
            if cap is None:
                self._stop_event.wait(timeout=3.0)
                continue
            self._capture_loop(cap)
            cap.release()

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _open_camera(self) -> cv2.VideoCapture | None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._cam_cfg.index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._cam_cfg.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._cam_cfg.capture_height)
        cap.set(cv2.CAP_PROP_FPS, self._cam_cfg.fps)
        return cap

    def _capture_loop(self, cap: cv2.VideoCapture) -> None:
        while not self._stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                break
            with self._frame_lock:
                self._frame = frame
            pw, ph = self._cam_cfg.preview_width, self._cam_cfg.preview_height
            preview = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_LINEAR)
            with self._preview_lock:
                self._preview_frame = preview

    def _build_save_path(self, device_label: str) -> Path:
        now = datetime.now()
        ms = now.microsecond // 1000
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in device_label)
        try:
            filename = (
                now.strftime(self._save_cfg.filename_format).format(ms=ms, device=safe_label)
                + ".png"
            )
        except (ValueError, KeyError, IndexError):
            filename = now.strftime("%Y%m%d_%H%M%S") + f"_{ms:03d}_{safe_label}.png"
        base = Path(self._save_cfg.save_path)
        if self._save_cfg.daily_folder:
            base = base / now.strftime("%Y-%m-%d")
        if self._save_cfg.device_subfolder:
            base = base / safe_label
        return base / filename
