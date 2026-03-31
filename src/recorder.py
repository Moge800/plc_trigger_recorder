"""ドライブレコーダースレッド — リングバッファキャプチャおよびトリガーベースの動画保存。"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

import cv2
import numpy as np

from config import VIDEO_FORMATS

if TYPE_CHECKING:
    from config import AppConfig, CameraConfig, RecordConfig

# ---------------------------------------------------------------------------
# Events posted to the GUI queue
# ---------------------------------------------------------------------------


@dataclass
class RecordStartEvent:
    """トリガーによる動画録画が書き込みを開始したときに発火される。

    Attributes:
        device_label: トリガーしたデバイスのラベル。
        save_path: 保存先ファイルのパス。
    """

    device_label: str
    save_path: Path


@dataclass
class RecordDoneEvent:
    """トリガーによる動画録画が正常に完了したときに発火される。

    Attributes:
        device_label: トリガーしたデバイスのラベル。
        save_path: 保存先ファイルのパス。
        frame_count: 書き込んだフレーム数。
        duration_sec: 動画の実時間（秒）。
    """

    device_label: str
    save_path: Path
    frame_count: int
    duration_sec: float


@dataclass
class RecordErrorEvent:
    """トリガーによる動画録画が失敗したときに発火される。

    Attributes:
        device_label: トリガーしたデバイスのラベル。
        message: エラーの詳細メッセージ。
        save_path: 保存先ファイルのパス（判明している場合）。
    """

    device_label: str
    message: str
    save_path: Path | None = None


# ---------------------------------------------------------------------------
# レコーダースレッド
# ---------------------------------------------------------------------------

_CAPTURE_MARGIN_SEC = 2.0  # pre+post ウィンドウを超えたリングバッファの余裕分（秒）


class RecorderThread(threading.Thread):
    """連続リングバッファキャプチャとトリガーベースの動画保存を行うバックグラウンドスレッド。

    使い方
    --------
    1. インスタンスを生成しスレッドを開始する。
    2. GUI スレッドから :meth:`get_preview_frame` を呼び出してライブプレビューを取得する。
    3. PLC 立ち上がりエッジで :meth:`trigger_record` を呼び出す。
    4. *event_queue* を :class:`RecordStartEvent` / :class:`RecordDoneEvent` のためにポーリングする。
    5. :meth:`stop` を呼び出してスレッドを安全に停止する。

    リングバッファメモリ推定（640×480 BGR、30 fps、pre+post=20 秒）:
        660 フレーム × 921 600 B ≈ 580 MB。
    RAM 使用量を下げるにはキャプチャ解像度またはウィンドウ時間を短縮してください。
    """

    def __init__(
        self,
        cfg: AppConfig,
        event_queue: Queue[RecordStartEvent | RecordDoneEvent | RecordErrorEvent],
    ) -> None:
        """レコーダースレッドを初期化する。

        Args:
            cfg: アプリケーション設定。カメラ設定と録画設定を使用する。
            event_queue: イベント投入先のキュー。
        """
        super().__init__(daemon=True, name="RecorderThread")
        self._cam_cfg: CameraConfig = cfg.camera
        self._rec_cfg: RecordConfig = cfg.record
        self._event_queue = event_queue
        self._stop_event = threading.Event()

        # リングバッファ: (単調増加タイムスタンプ, BGR フレーム) のデック
        self._buf_lock = threading.Lock()
        self._buf: deque[tuple[float, np.ndarray]] = deque(maxlen=self._calc_maxlen())  # type: ignore[type-arg]

        # GUI プレビュー用最新縮小フレーム
        self._preview_lock = threading.Lock()
        self._preview_frame: np.ndarray | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """スレッドに停止を指示する。"""
        self._stop_event.set()

    def update_config(self, cfg: AppConfig) -> None:
        """設定をホット更新する（解像度変更にはカメラ再起動が必要）。

        Args:
            cfg: 新しい設定値。
        """
        self._cam_cfg = cfg.camera
        self._rec_cfg = cfg.record
        new_maxlen = self._calc_maxlen()
        with self._buf_lock:
            self._buf = deque(self._buf, maxlen=new_maxlen)

    def get_preview_frame(self) -> np.ndarray | None:  # type: ignore[type-arg]
        """最新のプレビューサイズ BGR フレームを返す。未取得の場合は ``None``。

        Returns:
            BGR バイト配列、またはフレーム未取得時は ``None``。
        """
        with self._preview_lock:
            return None if self._preview_frame is None else self._preview_frame.copy()

    def trigger_record(self, device_label: str) -> None:
        """pre/post トリガー映像をディスクに書き込むセーバースレッドを生成する。

        任意のスレッドから安全に呼び出せる。複数の同時呼び出しに対応している
        （トリガーイベントごとに独立したセーバースレッドが生成される）。
        """
        trigger_time = time.monotonic()
        cutoff = trigger_time - self._rec_cfg.pre_trigger_sec

        # Snapshot pre-trigger frames now so the ring buffer can't evict them
        with self._buf_lock:
            pre_frames: list[tuple[float, np.ndarray]] = [  # type: ignore[type-arg]
                (t, f.copy()) for t, f in self._buf if t >= cutoff
            ]

        save_path = self._build_save_path(device_label)
        saver = threading.Thread(
            target=self._save_video,
            args=(device_label, trigger_time, pre_frames, save_path),
            daemon=True,
            name=f"VideoSaver-{save_path.name}",
        )
        saver.start()

    # ------------------------------------------------------------------
    # スレッドエントリポイント
    # ------------------------------------------------------------------

    def run(self) -> None:
        """スレッドメインループ。カメラを開き、失敗した場合は再接続する。"""
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

    def _calc_maxlen(self) -> int:
        """リングバッファの最大フレーム数を計算する。"""
        fps = max(self._cam_cfg.fps, 1.0)
        window = (
            self._rec_cfg.pre_trigger_sec
            + self._rec_cfg.post_trigger_sec
            + _CAPTURE_MARGIN_SEC
        )
        return max(int(window * fps), 60)

    def _open_camera(self) -> cv2.VideoCapture | None:
        """カメラを開きキャプチャ解像度を設定する。

        Windows では MSMF より起動が速い DirectShow (CAP_DSHOW) を優先する。

        Returns:
            成功時は :class:`cv2.VideoCapture`、失敗時は ``None``。
        """
        # Windows では CAP_DSHOW を使うと MSMF より起動が大幅に速い
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._cam_cfg.index, backend)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._cam_cfg.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._cam_cfg.capture_height)
        cap.set(cv2.CAP_PROP_FPS, self._cam_cfg.fps)
        return cap

    def _capture_loop(self, cap: cv2.VideoCapture) -> None:
        """カメラからフレームを連続取得しリングバッファとプレビューを更新する。

        Args:
            cap: 開放済みの :class:`cv2.VideoCapture` インスタンス。
        """
        while not self._stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                break
            now = time.monotonic()
            with self._buf_lock:
                self._buf.append((now, frame.copy()))

            # プレビューを更新（縮小済み。GUI 側でコピーする）
            pw, ph = self._cam_cfg.preview_width, self._cam_cfg.preview_height
            preview = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_LINEAR)
            with self._preview_lock:
                self._preview_frame = preview

    def _save_video(
        self,
        device_label: str,
        trigger_time: float,
        pre_frames: list[tuple[float, np.ndarray]],  # type: ignore[type-arg]
        save_path: Path,
    ) -> None:
        """pre/post トリガーフレームを動画ファイルとして保存する。

        Args:
            device_label: トリガーしたデバイスのラベル。
            trigger_time: トリガー発火時の単調増加タイムスタンプ。
            pre_frames: トリガー前フレームのリスト（タイムスタンプ付き）。
            save_path: 保存先ファイルのパス。
        """
        self._event_queue.put(
            RecordStartEvent(device_label=device_label, save_path=save_path)
        )
        try:
            # ポストトリガーウィンドウが経過するまで待機
            deadline = trigger_time + self._rec_cfg.post_trigger_sec
            remaining = deadline - time.monotonic()
            if remaining > 0:
                stop_requested = self._stop_event.wait(timeout=remaining)
                if stop_requested:
                    return  # graceful shutdown — skip saving

            # リングバッファからポストトリガーフレームをスナップショット
            with self._buf_lock:
                post_frames: list[tuple[float, np.ndarray]] = [  # type: ignore[type-arg]
                    (t, f.copy()) for t, f in self._buf if trigger_time < t <= deadline
                ]

            all_frames = pre_frames + post_frames
            if not all_frames:
                self._event_queue.put(
                    RecordErrorEvent(
                        device_label=device_label, message="No frames captured"
                    )
                )
                return

            # 最初のフレームから実際の解像度を取得
            h, w = all_frames[0][1].shape[:2]

            # フレームタイムスタンプから実際の FPS を推定
            if len(all_frames) > 1:
                span = all_frames[-1][0] - all_frames[0][0]
                actual_fps = (
                    (len(all_frames) - 1) / span if span > 0 else self._cam_cfg.fps
                )
            else:
                actual_fps = self._cam_cfg.fps

            ext, codecs = VIDEO_FORMATS.get(
                self._rec_cfg.video_format, (".mp4", ["mp4v"])
            )
            codec = (
                self._rec_cfg.video_codec
                if self._rec_cfg.video_codec in codecs
                else codecs[0]
            )
            fourcc = cv2.VideoWriter_fourcc(*codec)

            save_path.parent.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(str(save_path), fourcc, actual_fps, (w, h))
            if not writer.isOpened():
                raise RuntimeError(
                    f"VideoWriter failed to open (codec={codec}): {save_path}"
                )
            try:
                for _, frame in all_frames:
                    writer.write(frame)
            finally:
                writer.release()

            duration = len(all_frames) / actual_fps
            self._event_queue.put(
                RecordDoneEvent(
                    device_label=device_label,
                    save_path=save_path,
                    frame_count=len(all_frames),
                    duration_sec=duration,
                )
            )
        except Exception as exc:
            self._event_queue.put(
                RecordErrorEvent(
                    device_label=device_label,
                    message=str(exc),
                    save_path=save_path,
                )
            )

    def _build_save_path(self, device_label: str) -> Path:
        """デバイスラベルと現在時刻から保存先パスを構築する。

        Args:
            device_label: ファイル名に埋め込むデバイスラベル。

        Returns:
            動画保存先の :class:`~pathlib.Path`。
        """
        now = datetime.now()
        safe_label = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in device_label
        )
        ext, _ = VIDEO_FORMATS.get(self._rec_cfg.video_format, (".mp4", []))
        try:
            filename = (
                now.strftime(self._rec_cfg.filename_format).format(device=safe_label)
                + ext
            )
        except (ValueError, KeyError):
            filename = now.strftime("%Y%m%d_%H%M%S") + f"_{safe_label}{ext}"

        base = Path(self._rec_cfg.save_path)
        if self._rec_cfg.daily_folder:
            base = base / now.strftime("%Y-%m-%d")
        if self._rec_cfg.device_subfolder:
            base = base / safe_label
        return base / filename
