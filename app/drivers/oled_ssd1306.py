"""
SSD1306 OLED Display Driver
============================
Hardware
--------
0.96" SSD1306 OLED (128 × 64 px, monochrome) wired to Raspberry Pi 3B:
    GND  → GND   (Pin 9)
    VDD  → 3.3 V (Pin 1)
    SCK  → GPIO 3 / SCL (Pin 5)
    SDA  → GPIO 2 / SDA (Pin 3)
    I2C address: 0x3C (confirmed via i2cdetect -y 1)

Depends on luma.oled which must be present in the project venv:
    .venv/bin/pip install luma.oled

Current display layout
----------------------
Normal (show_channel):
    ┌──────────────────┐
    │  CH 3 / 8        │  ← 20px bold font
    │ ──────────────── │  ← 1px rule
    │  News API        │  ← 14px regular font (first module name)
    └──────────────────┘

Status (show_status):
    ┌──────────────────┐
    │  Printing...     │  ← 20px bold
    │ ──────────────── │
    │  News API        │  ← sub-line (optional)
    └──────────────────┘

The display is thread-safe: show_channel / show_status acquire a lock,
so they can safely be called from the encoder's monitor thread, the
asyncio event loop, or any daemon thread.

Extension ideas for future UI/UX work
--------------------------------------
Icons / glyphs:
    Use Pillow to paste a small bitmap icon beside the channel name.
    A 16×16 icon set (e.g. for weather, calendar, news) would fit
    neatly in the remaining horizontal space.

Progress bar during print:
    show_status could accept an optional 0–1 float and draw a thin
    progress bar below the rule.  paper-console does not currently
    expose print progress, but a time-based estimate would work.

Animations / transitions:
    luma.oled supports drawing arbitrary frames.  A brief slide or
    fade when the channel changes would give a polished feel.

Idle screensaver:
    After N minutes of inactivity, show a clock or blank the display
    (device.hide()) to prevent burn-in.  Wake on any encoder event.

Multi-line channel summary:
    Show the first two module names stacked, each on its own line,
    using a smaller font (e.g. 11px).
"""

import threading
from typing import Optional

_LUMA_AVAILABLE = False
try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from luma.core.render import canvas
    from PIL import ImageFont, Image, ImageDraw

    _LUMA_AVAILABLE = True
except Exception:
    pass

_BOLD_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_REGULAR_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


class OLEDDriver:
    """
    SSD1306 128×64 OLED display driver using luma.oled.

    show_channel() and show_status() are thread-safe and can be called from
    any thread (e.g. the encoder's monitor thread or the asyncio event loop).
    """

    def __init__(self, i2c_port: int = 1, i2c_address: int = 0x3C):
        self._lock = threading.Lock()
        self._device = None

        if not _LUMA_AVAILABLE:
            return

        try:
            serial = i2c(port=i2c_port, address=i2c_address)
            self._device = ssd1306(serial, width=128, height=64)
            self._font_large = _load_font(_BOLD_FONT_PATH, 20)
            self._font_small = _load_font(_REGULAR_FONT_PATH, 14)
        except Exception:
            self._device = None

    def _draw(self, render_fn) -> None:
        if not self._device:
            return
        with self._lock:
            try:
                with canvas(self._device) as draw:
                    render_fn(draw)
            except Exception:
                pass

    def show_channel(self, position: int, name: str) -> None:
        """Display the current channel number and module name."""
        header = f"CH {position} / 8"
        # Truncate name so it fits within 128 px
        display_name = name[:16] if len(name) > 16 else name

        def render(draw):
            draw.text((0, 4), header, font=self._font_large, fill="white")
            draw.line([(0, 30), (128, 30)], fill="white", width=1)
            draw.text((0, 36), display_name, font=self._font_small, fill="white")

        self._draw(render)

    def show_status(self, msg: str, sub: str = "") -> None:
        """Display a status message (e.g. 'Printing...') with an optional sub-line."""
        display_sub = sub[:18] if len(sub) > 18 else sub

        def render(draw):
            draw.text((0, 4), msg, font=self._font_large, fill="white")
            if display_sub:
                draw.line([(0, 30), (128, 30)], fill="white", width=1)
                draw.text((0, 36), display_sub, font=self._font_small, fill="white")

        self._draw(render)

    def cleanup(self) -> None:
        pass
