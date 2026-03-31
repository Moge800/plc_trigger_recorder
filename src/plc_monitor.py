"""PLC モニタースレッド — PLC ビットをポーリングし、立ち上がりエッジでコールバックを発火する。"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from queue import Queue
from typing import TYPE_CHECKING

import pymcprotocol  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from config import PlcConfig

# ---------------------------------------------------------------------------
# 公開イベント型
# ---------------------------------------------------------------------------


class PlcStatus(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    ERROR = auto()


@dataclass
class TriggerEvent:
    """監視ビットに立ち上がりエッジ（OFF → ON）が検出されたときに発火される。

    Attributes:
        device_address: トリガーしたデバイスのアドレス。
        label: デバイスの表示ラベル。
    """

    device_address: str
    label: str


@dataclass
class StatusEvent:
    """PLC 接続ステータス変化通知。

    Attributes:
        status: 新しいステータス。
        message: 詳細メッセージ（任意）。
    """

    status: PlcStatus
    message: str = ""


@dataclass
class BitStateEvent:
    """監視対象の全デバイスの現在 ON/OFF 状態（ポーリングのたびに発火）。

    Attributes:
        states: アドレス -> 現在状態の辞書。
    """

    states: dict[str, bool]  # アドレス -> 現在状態


# ---------------------------------------------------------------------------
# PLC モニタースレッド
# ---------------------------------------------------------------------------

_RETRY_INTERVAL_S = 5.0


class PlcMonitor(threading.Thread):
    """PLC ビットをポーリングし、*queue* にイベントを投入するバックグラウンドスレッド。

    キューに投入されるイベント:
    - :class:`StatusEvent`     — 接続状態の変化
    - :class:`TriggerEvent`    — デバイスの立ち上がりエッジ検出
    - :class:`BitStateEvent`   — 現在のビット状態（ポーリング毎回）

    シミュレーションモード
    ------------------
    *simulate* が ``True`` の場合、実際の PLC には接続しない。
    :meth:`simulate_trigger` を呼び出すことで立ち上がりエッジを
    プログラム的に発火できる。
    """

    def __init__(
        self,
        cfg: PlcConfig,
        queue: Queue[TriggerEvent | StatusEvent | BitStateEvent],
        *,
        simulate: bool = False,
    ) -> None:
        """モニタースレッドを初期化する。

        Args:
            cfg: PLC 接続設定。
            queue: イベント投入先のキュー。
            simulate: ``True`` の場合はシミュレーションモードで動作する。
        """
        super().__init__(daemon=True, name="PlcMonitor")
        self._cfg = cfg
        self._queue = queue
        self._simulate = simulate
        self._stop_event = threading.Event()
        self._prev_states: dict[str, bool] = {}
        # シミュレーション用: ON にトグルするアドレスのセット
        self._sim_triggers: set[str] = set()
        self._sim_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公開API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """スレッドに停止を指示する。"""
        self._stop_event.set()

    def update_config(self, cfg: PlcConfig) -> None:
        """PLC 設定をホット更新する（次の再接続サイクルで反映）。

        Args:
            cfg: 新しい PLC 設定値。
        """
        self._cfg = cfg

    def simulate_trigger(self, address: str) -> None:
        """（シミュレーションモード）*address* に立ち上がりエッジイベントを注入する。

        Args:
            address: 発火対象のデバイスアドレス。
        """
        with self._sim_lock:
            self._sim_triggers.add(address)

    # ------------------------------------------------------------------
    # スレッドエントリポイント
    # ------------------------------------------------------------------

    def run(self) -> None:
        """スレッドメインループ。シミュレーションモードに応じて分岐する。"""
        if self._simulate:
            self._run_simulation()
        else:
            self._run_real()

    # ------------------------------------------------------------------
    # 実PLC ポーリングループ
    # ------------------------------------------------------------------

    def _run_real(self) -> None:
        """実 PLC に接続しポーリングを繰り返す。"""
        while not self._stop_event.is_set():
            pymc = self._connect()
            if pymc is None:
                continue
            self._poll_loop(pymc)
            with contextlib.suppress(Exception):
                pymc.close()

    def _connect(self) -> pymcprotocol.Type3E | pymcprotocol.Type4E | None:
        """プロトコルに応じて PLC に接続する。

        Returns:
            成功時は接続済みインスタンス、失敗時は ``None``。
        """
        self._queue.put(
            StatusEvent(
                PlcStatus.CONNECTING, f"Connecting to {self._cfg.ip}:{self._cfg.port}…"
            )
        )
        try:
            if self._cfg.protocol == "4E":
                pymc: pymcprotocol.Type3E | pymcprotocol.Type4E = pymcprotocol.Type4E(
                    plctype=self._cfg.plc_type
                )
            else:
                pymc = pymcprotocol.Type3E(plctype=self._cfg.plc_type)
            pymc.connect(self._cfg.ip, self._cfg.port)
            self._queue.put(
                StatusEvent(
                    PlcStatus.CONNECTED, f"Connected to {self._cfg.ip}:{self._cfg.port}"
                )
            )
            self._prev_states = {}
            return pymc
        except Exception as exc:
            self._queue.put(StatusEvent(PlcStatus.ERROR, f"Connection failed: {exc}"))
            self._stop_event.wait(timeout=_RETRY_INTERVAL_S)
            return None

    def _poll_loop(self, pymc: pymcprotocol.Type3E | pymcprotocol.Type4E) -> None:
        """接続済み PLC をポーリングし、立ち上がりエッジを発火する。

        Args:
            pymc: 接続済みの pymcprotocol インスタンス。
        """
        interval_s = max(self._cfg.poll_interval_ms, 10) / 1000.0
        while not self._stop_event.is_set():
            enabled = [d for d in self._cfg.devices if d.enabled]
            if not enabled:
                time.sleep(interval_s)
                continue

            current_states: dict[str, bool] = {}
            try:
                for dev in enabled:
                    values: list[int] = pymc.batchread_bitunits(
                        headdevice=dev.address, readsize=1
                    )
                    current_states[dev.address] = bool(values[0])
            except Exception as exc:
                self._queue.put(StatusEvent(PlcStatus.ERROR, f"Poll error: {exc}"))
                return

            # 立ち上がりエッジを検出
            for dev in enabled:
                addr = dev.address
                prev = self._prev_states.get(addr, False)
                curr = current_states.get(addr, False)
                if not prev and curr:
                    self._queue.put(TriggerEvent(device_address=addr, label=dev.label))

            self._prev_states = current_states
            self._queue.put(BitStateEvent(states=dict(current_states)))
            time.sleep(interval_s)

    # ------------------------------------------------------------------
    # シミュレーションループ
    # ------------------------------------------------------------------

    def _run_simulation(self) -> None:
        """シミュレーションモードでポーリングをエミュレートする。"""
        self._queue.put(
            StatusEvent(PlcStatus.CONNECTED, "Simulation mode — no real PLC")
        )
        interval_s = max(self._cfg.poll_interval_ms, 50) / 1000.0
        sim_states: dict[str, bool] = {}

        while not self._stop_event.is_set():
            enabled = [d for d in self._cfg.devices if d.enabled]
            with self._sim_lock:
                triggered = self._sim_triggers.copy()
                self._sim_triggers.clear()

            for dev in enabled:
                addr = dev.address
                prev = sim_states.get(addr, False)
                curr = addr in triggered
                sim_states[addr] = curr
                if not prev and curr:
                    self._queue.put(TriggerEvent(device_address=addr, label=dev.label))

            self._queue.put(BitStateEvent(states=dict(sim_states)))
            time.sleep(interval_s)
