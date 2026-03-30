"""Drive recorder thread — ring-buffer frame capture and trigger-based video save."""

from __future__ import annotations

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
    """Fired when a triggered video recording starts writing."""

    device_label: str
    save_path: Path


@dataclass
class RecordDoneEvent:
    """Fired when a triggered video recording completes successfully."""

    device_label: str
    save_path: Path
    frame_count: int
    duration_sec: float


@dataclass
class RecordErrorEvent:
    """Fired when a triggered video recording fails."""

    device_label: str
    message: str
    save_path: Path | None = None


# ---------------------------------------------------------------------------
# Recorder thread
# ---------------------------------------------------------------------------

_CAPTURE_MARGIN_SEC = 2.0  # extra ring-buffer margin beyond pre+post window


class RecorderThread(threading.Thread):
    """Background thread: continuous ring-buffer capture + trigger-based video save.

    Usage
    -----
    1. Construct and start the thread.
    2. Call :meth:`get_preview_frame` from the GUI thread for live preview.
    3. Call :meth:`trigger_record` on a PLC rising-edge event.
    4. Poll *event_queue* for :class:`RecordStartEvent` / :class:`RecordDoneEvent`.
    5. Call :meth:`stop` to terminate gracefully.

    Ring-buffer memory estimate (640×480 BGR, 30 fps, pre+post=20 s):
        660 frames × 921 600 B ≈ 580 MB.
    Reduce capture resolution or window duration to lower RAM usage.
    """

    def __init__(
        self,
        cfg: AppConfig,
        event_queue: Queue[RecordStartEvent | RecordDoneEvent | RecordErrorEvent],
    ) -> None:
        super().__init__(daemon=True, name="RecorderThread")
        self._cam_cfg: CameraConfig = cfg.camera
        self._rec_cfg: RecordConfig = cfg.record
        self._event_queue = event_queue
        self._stop_event = threading.Event()

        # Ring buffer: deque of (monotonic_timestamp, BGR_frame)
        self._buf_lock = threading.Lock()
        self._buf: deque[tuple[float, np.ndarray]] = deque(maxlen=self._calc_maxlen())  # type: ignore[type-arg]

        # Latest downscaled frame for GUI preview
        self._preview_lock = threading.Lock()
        self._preview_frame: np.ndarray | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._stop_event.set()

    def update_config(self, cfg: AppConfig) -> None:
        """Hot-update config (camera restart required for resolution changes)."""
        self._cam_cfg = cfg.camera
        self._rec_cfg = cfg.record
        new_maxlen = self._calc_maxlen()
        with self._buf_lock:
            self._buf = deque(self._buf, maxlen=new_maxlen)

    def get_preview_frame(self) -> np.ndarray | None:  # type: ignore[type-arg]
        """Return the latest preview-scaled BGR frame, or ``None``."""
        with self._preview_lock:
            return None if self._preview_frame is None else self._preview_frame.copy()

    def trigger_record(self, device_label: str) -> None:
        """Spawn a saver thread to write pre/post-trigger footage to disk.

        Safe to call from any thread.  Multiple concurrent calls are supported
        (one independent saver thread is spawned per trigger event).
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
    # Thread entry point
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
    # Internal helpers
    # ------------------------------------------------------------------

    def _calc_maxlen(self) -> int:
        fps = max(self._cam_cfg.fps, 1.0)
        window = (
            self._rec_cfg.pre_trigger_sec
            + self._rec_cfg.post_trigger_sec
            + _CAPTURE_MARGIN_SEC
        )
        return max(int(window * fps), 60)

    def _open_camera(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self._cam_cfg.index, cv2.CAP_ANY)
        if not cap.isOpened():
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
            now = time.monotonic()
            with self._buf_lock:
                self._buf.append((now, frame.copy()))

            # Update preview (downscaled, not copied — GUI will copy if needed)
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
        self._event_queue.put(
            RecordStartEvent(device_label=device_label, save_path=save_path)
        )
        try:
            # Wait for the post-trigger window to elapse
            deadline = trigger_time + self._rec_cfg.post_trigger_sec
            remaining = deadline - time.monotonic()
            if remaining > 0:
                stop_requested = self._stop_event.wait(timeout=remaining)
                if stop_requested:
                    return  # graceful shutdown — skip saving

            # Snapshot post-trigger frames from the ring buffer
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

            # Determine actual resolution from the first captured frame
            h, w = all_frames[0][1].shape[:2]

            # Estimate actual FPS from frame timestamps
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
