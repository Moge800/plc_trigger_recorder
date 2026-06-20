"""PLCトリガースイートの設定データクラスおよびJSON永続化。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
PLC_TYPES = ["Q", "L", "QnA", "iQ-L", "iQ-R"]
PROTOCOL_TYPES = ["3E", "4E"]
CONNECTION_TYPES = ["mc_direct", "gomc_rest"]
CAPTURE_MODES = ["capture", "record"]

VIDEO_FORMATS: dict[str, tuple[str, list[str]]] = {
    "mp4": (".mp4", ["mp4v", "avc1"]),
    "avi": (".avi", ["MJPG", "XVID"]),
}
VIDEO_FORMAT_NAMES = list(VIDEO_FORMATS.keys())

# ---------------------------------------------------------------------------
# サブ設定データクラス
# ---------------------------------------------------------------------------


@dataclass
class DeviceConfig:
    """監視対象の PLC ビットデバイスの設定。"""

    address: str = "M100"
    label: str = "Trigger"
    enabled: bool = True


@dataclass
class PlcConfig:
    """PLC 接続設定。

    Attributes:
        connection_type: 接続方式。``"mc_direct"`` (pymcprotocol 直結) または
            ``"gomc_rest"`` (gomc-rest 経由 HTTP)。
        gomc_rest_url: gomc-rest サーバーの URL（``connection_type=="gomc_rest"`` 時のみ使用）。
    """

    ip: str = "192.168.1.10"
    port: int = 1025
    plc_type: str = "Q"
    protocol: str = "3E"
    poll_interval_ms: int = 100
    devices: list[DeviceConfig] = field(default_factory=lambda: [DeviceConfig()])
    connection_type: str = "mc_direct"
    gomc_rest_url: str = "http://localhost:8080"


@dataclass
class CameraConfig:
    """USBカメラの設定。"""

    index: int = 0
    capture_width: int = 640
    capture_height: int = 480
    preview_width: int = 640
    preview_height: int = 480
    fps: float = 30.0


@dataclass
class SaveConfig:
    """静止画（PNG）保存の設定。"""

    save_path: str = str(Path.home() / "Pictures" / "plc_trigger")
    png_compression: int = 1
    filename_format: str = "%Y%m%d_%H%M%S_{ms:03d}_{device}"
    daily_folder: bool = True
    device_subfolder: bool = False
    beep_on_trigger: bool = False


@dataclass
class RecordConfig:
    """動画録画設定。"""

    pre_trigger_sec: float = 10.0
    post_trigger_sec: float = 10.0
    video_format: str = "mp4"
    video_codec: str = "mp4v"
    save_path: str = str(Path.home() / "Videos" / "plc_trigger")
    filename_format: str = "%Y%m%d_%H%M%S_{device}"
    daily_folder: bool = True
    device_subfolder: bool = False
    beep_on_trigger: bool = False


# ---------------------------------------------------------------------------
# ルート設定
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """アプリケーションのルート設定。

    Attributes:
        capture_mode: 動作モード。``"capture"`` (静止画) または ``"record"`` (動画)。
        capture: 静止画モードの保存設定。
        record: 動画モードの録画設定。
    """

    plc: PlcConfig = field(default_factory=PlcConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    capture: SaveConfig = field(default_factory=SaveConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    capture_mode: str = "record"


# ---------------------------------------------------------------------------
# シリアライズヘルパー
# ---------------------------------------------------------------------------


def _plc_from_dict(d: dict) -> PlcConfig:  # type: ignore[type-arg]
    d = d.copy()
    devices = [DeviceConfig(**dev) for dev in d.pop("devices", [])]
    d.setdefault("connection_type", "mc_direct")
    # 想定外の値（手編集 config.json 等）は MC Direct にフォールバックする。
    if d["connection_type"] not in CONNECTION_TYPES:
        d["connection_type"] = "mc_direct"
    d.setdefault("gomc_rest_url", "http://localhost:8080")
    return PlcConfig(**d, devices=devices)


def config_from_dict(d: dict) -> AppConfig:  # type: ignore[type-arg]
    """dict から :class:`AppConfig` を生成する。旧 config.json のキーにも対応する。"""
    plc = _plc_from_dict(d.get("plc", {}))

    camera_d = d.get("camera", {})
    camera_d.setdefault("fps", 30.0)
    camera = CameraConfig(**camera_d)

    # 後方互換: 旧バージョンのトップレベル "save" キーも capture として受け付ける。
    capture_d = d.get("capture", d.get("save", {}))
    capture = SaveConfig(**capture_d) if capture_d else SaveConfig()

    record = RecordConfig(**d.get("record", {})) if d.get("record") else RecordConfig()

    # 想定外の値はデフォルト（record）にフォールバックする。
    capture_mode = d.get("capture_mode", "record")
    if capture_mode not in CAPTURE_MODES:
        capture_mode = "record"
    return AppConfig(plc=plc, camera=camera, capture=capture, record=record, capture_mode=capture_mode)


def load_config(path: Path = CONFIG_FILE) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return config_from_dict(raw)
    except Exception:
        return AppConfig()


def save_config(cfg: AppConfig, path: Path = CONFIG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(cfg), fh, indent=2, ensure_ascii=False)
