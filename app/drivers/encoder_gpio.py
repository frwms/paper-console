"""
KY-040 Rotary Encoder Driver
=============================
Hardware
--------
KY-040 wired to Raspberry Pi 3B:
    CLK  → GPIO 17  (Pin 11)
    DT   → GPIO 27  (Pin 13)
    SW   → GPIO 22  (Pin 15)
    +    → 3.3 V    (Pin 17)
    GND  → GND      (Pin 20)

All three pins use the internal pull-up resistors on the GPIO chip; no
external resistors are needed.

How it works
------------
The driver opens /dev/gpiochip0 and requests three kernel-level event
handles (one per pin) so that CLK and DT transitions are delivered as
file-descriptor events.  A single background thread blocks on select()
over [clk_fd, dt_fd, sw_fd] and wakes only when something changes —
no busy-polling.

Rotation — half-step quadrature state machine
----------------------------------------------
KY-040 encoders have physical detents at *both* the 11 (rest, both pins
high) and 00 (mid-cycle, both pins low) positions.  Each physical click
therefore covers only half a quadrature cycle:

    click 1 CW : 11 → 01 → 00   (emit +1 on arrival at 00)
    click 2 CW : 00 → 10 → 11   (emit +1 on arrival at 11)

Using a 7-state machine that fires at both positions gives exactly one
step per physical click.  Contact bounce that does not reach the next
detent position is absorbed without emitting spurious steps.

Button (SW pin)
---------------
The encoder's push-button is wired to SW (GPIO 22).  The driver detects
FALLING_EDGE events and applies a 300 ms software debounce.  Currently
only a short-press callback is exposed (set_button_callback).

Interface (drop-in replacement for dial_gpio.DialDriver)
---------------------------------------------------------
    read_position() → int          current channel (1–8)
    set_position(pos)              override from API; fires callbacks
    register_callback(fn)          fn(position) on rotation
    set_button_callback(fn)        fn() on SW short press
    cleanup()                      release GPIO handles

Extension ideas for future UI/UX work
--------------------------------------
Long press on SW:
    Track press start time inside _handle_button; if elapsed > threshold
    (e.g. 0.8 s) call a separate long_press_callback instead of the
    short-press one.  Pattern already used by button_gpio.ButtonDriver
    (set_long_press_callback / set_factory_reset_callback).

Double press:
    Record last_press_time; if a second press arrives within ~400 ms,
    call a double_press_callback.

Haptic / LED feedback:
    If a GPIO output pin is wired to an LED or small buzzer, drive it
    from _handle_rotation for a click feel without needing audio.

Step acceleration:
    In _step_state_machine, track the rate of recent steps.  If the
    knob is being spun fast, multiply the step size (e.g. 2 or 4) so
    skipping large distances is easy.  Reset multiplier after ~200 ms
    of inactivity.
"""

import os
import select
import threading
import time
from typing import Callable, List, Optional, Tuple

GPIO_AVAILABLE = False
try:
    from app.drivers.gpio_ioctl import (
        GpioChip,
        GPIOHANDLE_REQUEST_INPUT,
        GPIOHANDLE_REQUEST_BIAS_PULL_UP,
        GPIOEVENT_REQUEST_BOTH_EDGES,
        GPIOEVENT_REQUEST_FALLING_EDGE,
        GPIOEVENT_EVENT_FALLING_EDGE,
    )

    if os.path.exists("/dev/gpiochip0"):
        GPIO_AVAILABLE = True
except Exception:
    pass

# BCM pin numbers matching the wired hardware
_DEFAULT_PIN_CLK = 17
_DEFAULT_PIN_DT = 27
_DEFAULT_PIN_SW = 22

_MIN_BUTTON_INTERVAL = 0.300  # 300 ms minimum between button presses

# ---------------------------------------------------------------------------
# Half-step quadrature state machine for KY-040
#
# KY-040 at rest: CLK=1, DT=1 (pull-ups).
# Input encoding: (CLK << 1) | DT  →  00=0, 01=1, 10=2, 11=3
#
# Physical detents occur at BOTH the 11 (rest) and 00 (mid) positions,
# so each physical click covers only half a quadrature cycle.  Emitting a
# step at both transition points gives exactly one step per physical click.
#
# CW  detent sequence per click: 11→01→00 (emit) then 00→10→11 (emit)
# CCW detent sequence per click: 11→10→00 (emit) then 00→01→11 (emit)
#
# Contact bounce that doesn't reach the next detent position is absorbed.
# ---------------------------------------------------------------------------
_S_R    = 0  # rest at 11 (both high)
_S_CW1  = 1  # CW started: CLK fell to 01
_S_CW2  = 2  # CW mid: both fell to 00 — step emitted on arrival
_S_CW3  = 3  # CW finishing: CLK rose to 10
_S_CCW1 = 4  # CCW started: DT fell to 10
_S_CCW2 = 5  # CCW mid: both fell to 00 — step emitted on arrival
_S_CCW3 = 6  # CCW finishing: DT rose to 01

# _TABLE[state][input] = (next_state, step)
_TABLE: List[List[Tuple[int, int]]] = [
    #          input 00           01          10           11
    [(_S_CW2, +1), (_S_CW1,  0), (_S_CCW1, 0), (_S_R,    0)],  # _S_R
    [(_S_CW2, +1), (_S_CW1,  0), (_S_R,    0), (_S_R,    0)],  # _S_CW1  → +1 at 00
    [(_S_CW2,  0), (_S_R,    0), (_S_CW3,  0), (_S_R,    0)],  # _S_CW2
    [(_S_CW2,  0), (_S_R,    0), (_S_CW3,  0), (_S_R,   +1)],  # _S_CW3  → +1 at 11
    [(_S_CCW2,-1), (_S_R,    0), (_S_CCW1, 0), (_S_R,    0)],  # _S_CCW1 → -1 at 00
    [(_S_CCW2, 0), (_S_CCW3, 0), (_S_R,    0), (_S_R,    0)],  # _S_CCW2
    [(_S_CCW2, 0), (_S_CCW3, 0), (_S_R,    0), (_S_R,   -1)],  # _S_CCW3 → -1 at 11
]


class DialDriver:
    """
    KY-040 quadrature rotary encoder driver using the Linux GPIO character device.

    Uses a full-step state machine that monitors both CLK and DT via kernel
    edge-detect events.  A position change is only emitted after a complete
    detent cycle, so contact bounce mid-cycle is silently absorbed.

    Exposes the same interface as dial_gpio.DialDriver so it can be used as a
    drop-in replacement.  Additional method set_button_callback() wires up the
    encoder's push-button (SW pin).
    """

    def __init__(
        self,
        pin_clk: int = _DEFAULT_PIN_CLK,
        pin_dt: int = _DEFAULT_PIN_DT,
        pin_sw: int = _DEFAULT_PIN_SW,
    ):
        self.pin_clk = pin_clk
        self.pin_dt = pin_dt
        self.pin_sw = pin_sw

        self.current_position: int = 1
        self.callbacks: List[Callable[[int], None]] = []
        self.button_callback: Optional[Callable[[], None]] = None

        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None

        self.gpio_available = GPIO_AVAILABLE
        self.chip: Optional[GpioChip] = None
        self.clk_event = None
        self.dt_event = None
        self.sw_event = None

        self._sm_state: int = _S_R
        self._last_button_time: float = 0.0

        if not self.gpio_available:
            return

        try:
            self.chip = GpioChip("/dev/gpiochip0")
            handle_flags = GPIOHANDLE_REQUEST_INPUT | GPIOHANDLE_REQUEST_BIAS_PULL_UP

            # Both CLK and DT monitored with BOTH_EDGES so the state machine
            # sees every transition on both lines.
            self.clk_event = self.chip.request_event(
                self.pin_clk, handle_flags, GPIOEVENT_REQUEST_BOTH_EDGES, label="enc_clk"
            )
            self.dt_event = self.chip.request_event(
                self.pin_dt, handle_flags, GPIOEVENT_REQUEST_BOTH_EDGES, label="enc_dt"
            )
            self.sw_event = self.chip.request_event(
                self.pin_sw, handle_flags, GPIOEVENT_REQUEST_FALLING_EDGE, label="enc_sw"
            )

            self.monitoring = True
            self.monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True
            )
            self.monitor_thread.start()

        except Exception:
            self.gpio_available = False
            self.cleanup()

    def _step_state_machine(self) -> None:
        """Read current CLK+DT values and advance the quadrature state machine."""
        try:
            clk = self.clk_event.read_value()
            dt = self.dt_event.read_value()
        except Exception:
            return

        inp = (clk << 1) | dt
        next_state, step = _TABLE[self._sm_state][inp]
        self._sm_state = next_state

        if step:
            new_pos = max(1, min(8, self.current_position + step))
            if new_pos != self.current_position:
                self.current_position = new_pos
                for cb in self.callbacks:
                    try:
                        cb(new_pos)
                    except Exception:
                        pass

    def _handle_button(self) -> None:
        now = time.monotonic()
        if now - self._last_button_time < _MIN_BUTTON_INTERVAL:
            return
        self._last_button_time = now
        if self.button_callback:
            try:
                self.button_callback()
            except Exception:
                pass

    def _monitor_loop(self) -> None:
        while self.monitoring:
            try:
                fds = []
                if self.clk_event and self.clk_event.fd is not None:
                    fds.append(self.clk_event.fd)
                if self.dt_event and self.dt_event.fd is not None:
                    fds.append(self.dt_event.fd)
                if self.sw_event and self.sw_event.fd is not None:
                    fds.append(self.sw_event.fd)

                if not fds:
                    time.sleep(0.05)
                    continue

                ready, _, _ = select.select(fds, [], [], 0.05)

                for fd in ready:
                    if self.clk_event and fd == self.clk_event.fd:
                        self.clk_event.read_event()
                        self._step_state_machine()
                    elif self.dt_event and fd == self.dt_event.fd:
                        self.dt_event.read_event()
                        self._step_state_machine()
                    elif self.sw_event and fd == self.sw_event.fd:
                        event_id = self.sw_event.read_event()
                        if event_id == GPIOEVENT_EVENT_FALLING_EDGE:
                            self._handle_button()

            except OSError:
                if self.monitoring:
                    time.sleep(0.5)
            except Exception:
                if self.monitoring:
                    time.sleep(0.01)

    def register_callback(self, callback: Callable[[int], None]) -> None:
        """Register a function called with the new position on every rotation."""
        self.callbacks.append(callback)

    def set_button_callback(self, callback: Callable[[], None]) -> None:
        """Register a function called when the encoder push-button is pressed."""
        self.button_callback = callback

    def read_position(self) -> int:
        """Return the current channel position (1-8)."""
        return self.current_position

    def set_position(self, position: int) -> None:
        """Override the current position (e.g. from the web API). Fires callbacks."""
        if 1 <= position <= 8 and position != self.current_position:
            self.current_position = position
            for cb in self.callbacks:
                try:
                    cb(position)
                except Exception:
                    pass

    def cleanup(self) -> None:
        self.monitoring = False

        if self.monitor_thread:
            self.monitor_thread.join(timeout=1)
            self.monitor_thread = None

        for handle in (self.clk_event, self.dt_event, self.sw_event):
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
        self.clk_event = None
        self.dt_event = None
        self.sw_event = None

        if self.chip:
            try:
                self.chip.close()
            except Exception:
                pass
            self.chip = None
