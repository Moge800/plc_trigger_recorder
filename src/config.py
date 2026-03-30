"""Configuration dataclasses and JSON persistence for PLC Trigger Recorder."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

# ---------------------------------------------------------------------------
# PLC types and protocol types
# ---------------------------------------------------------------------------
PLC_TYPES = ["Q", "L", "QnA", "iQ-L", "iQ-R"]
PROTOCOL_TYPES = ["3E", "4E"]

# ---------------------------------------------------------------------------
# Video format / codec mapping
# ---------------------------------------------------------------------------
# { format_name: (file_extension, [available_codecs]) }
VIDEO_FORMATS: dict[str, tuple[str, list[str]]] = {
    "mp4": (".mp4", ["mp4v", "avc1"]),
    "avi": (".avi", ["MJPG", "XVID"]),
}
VIDEO_FORMAT_NAMES = list(VIDEO_FORMATS.keys())

# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DeviceConfig:
    """A single PLC bit device to monitor."""

    address: str = "M100"
    label: str = "Trigger"
    enabled: bool = True


@dataclass
class PlcConfig:
    """PLC connection settings."""

    ip: str = "192.168.1.10"
    port: int = 1025
    plc_type: str = "Q"  # one of PLC_TYPES
    protocol: str = "3E"  # "3E" or "4E"
    poll_interval_ms: int = 100
    devices: list[DeviceConfig] = field(default_factory=lambda: [DeviceConfig()])


@dataclass
class CameraConfig:
    """USB camera settings."""

    index: int = 0
    capture_width: int = 640
    capture_height: int = 480
    preview_width: int = 640
    preview_height: int = 480
    fps: float = 30.0


@dataclass
class RecordConfig:
    """Video recording settings."""

    pre_trigger_sec: float = 10.0  # seconds of footage before trigger to keep
    post_trigger_sec: float = 10.0  # seconds of footage after trigger to capture
    video_format: str = "mp4"  # key in VIDEO_FORMATS
    video_codec: str = "mp4v"  # fourcc string
    save_path: str = str(Path.home() / "Videos" / "plc_trigger_recorder")
    filename_format: str = "%Y%m%d_%H%M%S_{device}"
    daily_folder: bool = True  # create YYYY-MM-DD sub-folder
    device_subfolder: bool = False  # create sub-folder per device label


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """Root application config."""

    plc: PlcConfig = field(default_factory=PlcConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    record: RecordConfig = field(default_factory=RecordConfig)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _plc_from_dict(d: dict) -> PlcConfig:  # type: ignore[type-arg]
    d = d.copy()
    devices = [DeviceConfig(**dev) for dev in d.pop("devices", [])]
    return PlcConfig(**d, devices=devices)


def config_from_dict(d: dict) -> AppConfig:  # type: ignore[type-arg]
    plc = _plc_from_dict(d.get("plc", {}))
    camera = CameraConfig(**d.get("camera", {}))
    record = RecordConfig(**d.get("record", {}))
    return AppConfig(plc=plc, camera=camera, record=record)


def load_config(path: Path = CONFIG_FILE) -> AppConfig:
    """Load config from *path*; return defaults if file does not exist."""
    if not path.exists():
        return AppConfig()
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return config_from_dict(raw)
    except Exception:
        return AppConfig()


def save_config(cfg: AppConfig, path: Path = CONFIG_FILE) -> None:
    """Persist *cfg* to *path* as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(cfg), fh, indent=2, ensure_ascii=False)
