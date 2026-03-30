"""PLC monitor thread — polls PLC bits and fires callbacks on rising edges."""

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
# Public event types
# ---------------------------------------------------------------------------


class PlcStatus(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    ERROR = auto()


@dataclass
class TriggerEvent:
    """Fired when a monitored bit has a rising edge (OFF → ON)."""

    device_address: str
    label: str


@dataclass
class StatusEvent:
    """PLC connection status change."""

    status: PlcStatus
    message: str = ""


@dataclass
class BitStateEvent:
    """Current ON/OFF state of every monitored device (polled continuously)."""

    states: dict[str, bool]  # address -> current state


# ---------------------------------------------------------------------------
# PLCMonitor thread
# ---------------------------------------------------------------------------

_RETRY_INTERVAL_S = 5.0


class PlcMonitor(threading.Thread):
    """Background thread that polls PLC bits and puts events into *queue*.

    Events placed on the queue:
    - :class:`StatusEvent`     — connection state changes
    - :class:`TriggerEvent`    — rising-edge detected on a device
    - :class:`BitStateEvent`   — current bit states (every poll cycle)

    Simulation mode
    ---------------
    When *simulate* is ``True`` the thread never connects to a real PLC.
    Call :meth:`simulate_trigger` to fire a rising edge programmatically.
    """

    def __init__(
        self,
        cfg: PlcConfig,
        queue: Queue[TriggerEvent | StatusEvent | BitStateEvent],
        *,
        simulate: bool = False,
    ) -> None:
        super().__init__(daemon=True, name="PlcMonitor")
        self._cfg = cfg
        self._queue = queue
        self._simulate = simulate
        self._stop_event = threading.Event()
        self._prev_states: dict[str, bool] = {}
        # For simulation: a set of addresses that should be toggled to ON
        self._sim_triggers: set[str] = set()
        self._sim_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._stop_event.set()

    def update_config(self, cfg: PlcConfig) -> None:
        """Hot-update PLC config (takes effect on next reconnect cycle)."""
        self._cfg = cfg

    def simulate_trigger(self, address: str) -> None:
        """(Simulation mode) Inject a rising-edge event for *address*."""
        with self._sim_lock:
            self._sim_triggers.add(address)

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        if self._simulate:
            self._run_simulation()
        else:
            self._run_real()

    # ------------------------------------------------------------------
    # Real PLC polling loop
    # ------------------------------------------------------------------

    def _run_real(self) -> None:
        while not self._stop_event.is_set():
            pymc = self._connect()
            if pymc is None:
                continue
            self._poll_loop(pymc)
            with contextlib.suppress(Exception):
                pymc.close()

    def _connect(self) -> pymcprotocol.Type3E | pymcprotocol.Type4E | None:
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

            # Detect rising edges
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
    # Simulation loop
    # ------------------------------------------------------------------

    def _run_simulation(self) -> None:
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
