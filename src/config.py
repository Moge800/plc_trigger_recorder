"""PLCトリガーレコーダーの設定データクラスおよびJSON永続化。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config.json"

# ---------------------------------------------------------------------------
# PLCタイプとプロトコルタイプ
# ---------------------------------------------------------------------------
PLC_TYPES = ["Q", "L", "QnA", "iQ-L", "iQ-R"]
PROTOCOL_TYPES = ["3E", "4E"]

# ---------------------------------------------------------------------------
# 動画フォーマット / コーデックマッピング
# ---------------------------------------------------------------------------
# { format_name: (file_extension, [available_codecs]) }
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
    """監視対象の PLC ビットデバイスの設定。

    Attributes:
        address: デバイスアドレス（例: ``M100``）。
        label: 表示ラベル。
        enabled: 有効フラグ。
    """

    address: str = "M100"
    label: str = "Trigger"
    enabled: bool = True


@dataclass
class PlcConfig:
    """PLC 接続設定。

    Attributes:
        ip: PLC の IP アドレス。
        port: 接続ポート番号。
        plc_type: PLCタイプ（:data:`PLC_TYPES` のいずれか）。
        protocol: 通信プロトコル（``"3E"`` または ``"4E"``）。
        poll_interval_ms: ポーリング間隔（ミリ秒）。
        devices: 監視対象デバイスのリスト。
    """

    ip: str = "192.168.1.10"
    port: int = 1025
    plc_type: str = "Q"  # PLC_TYPES のいずれか
    protocol: str = "3E"  # "3E" または "4E"
    poll_interval_ms: int = 100
    devices: list[DeviceConfig] = field(default_factory=lambda: [DeviceConfig()])


@dataclass
class CameraConfig:
    """USBカメラの設定。

    Attributes:
        index: OpenCVカメラインデックス。
        capture_width: キャプチャ解像度の幅。
        capture_height: キャプチャ解像度の高さ。
        preview_width: プレビュー解像度の幅。
        preview_height: プレビュー解像度の高さ。
        fps: フレームレート（fps）。
    """

    index: int = 0
    capture_width: int = 640
    capture_height: int = 480
    preview_width: int = 640
    preview_height: int = 480
    fps: float = 30.0


@dataclass
class RecordConfig:
    """動画録画設定。

    Attributes:
        pre_trigger_sec: トリガー前に保持する映像の秒数。
        post_trigger_sec: トリガー後にキャプチャする映像の秒数。
        video_format: 動画ファーマット（VIDEO_FORMATS のキー）。
        video_codec: コーデックの fourcc 文字列。
        save_path: 保存先ディレクトリのパス。
        filename_format: ファイル名ファーマット（strftime + {device}）。
        daily_folder: ``True`` の場合は YYYY-MM-DD サブフォルダを作成。
        device_subfolder: ``True`` の場合はデバイスラベルごとのサブフォルダを作成。
        beep_on_trigger: ``True`` の場合はトリガー時に通知音を再生する（beep-lite 必須）。
    """

    pre_trigger_sec: float = 10.0  # トリガー前に保持する秒数
    post_trigger_sec: float = 10.0  # トリガー後にキャプチャする秒数
    video_format: str = "mp4"  # VIDEO_FORMATS のキー
    video_codec: str = "mp4v"  # fourcc 文字列
    save_path: str = str(Path.home() / "Videos" / "plc_trigger_recorder")
    filename_format: str = "%Y%m%d_%H%M%S_{device}"
    daily_folder: bool = True  # YYYY-MM-DD サブフォルダを作成
    device_subfolder: bool = False  # デバイスラベルごとのサブフォルダを作成
    beep_on_trigger: bool = False  # トリガー時に通知音を再生


# ---------------------------------------------------------------------------
# ルート設定
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """アプリケーションのルート設定。

    Attributes:
        plc: PLC接続設定。
        camera: カメラ設定。
        record: 録画設定。
    """

    plc: PlcConfig = field(default_factory=PlcConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    record: RecordConfig = field(default_factory=RecordConfig)


# ---------------------------------------------------------------------------
# シリアライズヘルパー
# ---------------------------------------------------------------------------


def _plc_from_dict(d: dict) -> PlcConfig:  # type: ignore[type-arg]
    """dict から :class:`PlcConfig` を生成するプライベートヘルパー。

    Args:
        d: ``"devices"`` キーを含む可能性のある辞書。

    Returns:
        復元された :class:`PlcConfig` インスタンス。
    """
    d = d.copy()
    devices = [DeviceConfig(**dev) for dev in d.pop("devices", [])]
    return PlcConfig(**d, devices=devices)


def config_from_dict(d: dict) -> AppConfig:  # type: ignore[type-arg]
    """dict から :class:`AppConfig` を生成する。

    Args:
        d: JSON読み込み結果の辞書。

    Returns:
        復元された :class:`AppConfig`。
    """
    plc = _plc_from_dict(d.get("plc", {}))
    camera = CameraConfig(**d.get("camera", {}))
    record = RecordConfig(**d.get("record", {}))
    return AppConfig(plc=plc, camera=camera, record=record)


def load_config(path: Path = CONFIG_FILE) -> AppConfig:
    """*path* から設定を読み込む。ファイルが存在しない場合はデフォルト値を返す。

    Args:
        path: 設定JSONファイルのパス。

    Returns:
        読み込んだ :class:`AppConfig`。パース失敗時はデフォルト値。
    """
    if not path.exists():
        return AppConfig()
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return config_from_dict(raw)
    except Exception:
        return AppConfig()


def save_config(cfg: AppConfig, path: Path = CONFIG_FILE) -> None:
    """*cfg* を *path* に JSON として保存する。

    Args:
        cfg: 保存する設定値。
        path: 保存先の JSON ファイルパス。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(cfg), fh, indent=2, ensure_ascii=False)
