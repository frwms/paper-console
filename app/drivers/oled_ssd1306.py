"""
SSD1306 OLED Display Driver — Direction A "Console" UI
=======================================================
Hardware: 128×64 SSD1306 on I2C bus 1 at 0x3C (Raspberry Pi 3B).
Set dtparam=i2c_arm_baudrate=400000 in /boot/firmware/config.txt for ~20fps.

Public API
----------
    show_channel(position, name)   — update channel; drives transitions / print-done
    show_status(msg, sub)          — trigger printing animation
    enter_sysinfo()                — open system-monitor (encoder long-press)
    exit_sysinfo()                 — return to channel (encoder short-press in sysinfo)
    is_in_sysinfo() -> bool        — checked by main.py button handler
    cleanup()                      — stop render loop + release hardware
"""

import math
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

_LUMA_AVAILABLE = False
try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306

    _LUMA_AVAILABLE = True
except Exception:
    pass

# ---------------------------------------------------------------------------
# Layout constants (mirror oled-dir-a.js)
# ---------------------------------------------------------------------------
W, H = 128, 64
TARGET_FPS = 10
FRAME_S = 1.0 / TARGET_FPS

MARGIN = 6       # left/right screen margin
G_NUM  = 4       # gap: number ink right edge → divider (tighter)
G_PILL = 7       # gap: divider → pill left edge
PILL_PAD = 2     # horizontal padding inside inverted-pill (each side)
PILL_VPAD = 3    # vertical padding inside pill (each side)
N_CH = 8         # physical channel count

CH_MS = 300.0    # channel snap-bounce duration ms
PAGE_MS = 240.0  # sysinfo page-flip duration ms
DONE_MS = 850.0  # "DONE" flash duration ms

# Bayer 4×4 ordered dither matrix
_BAYER = [
    [0,  8,  2, 10],
    [12, 4, 14,  6],
    [3, 11,  1,  9],
    [15, 7, 13,  5],
]

# 12×12 check icon for the DONE state (from oled-data.js)
_CHECK_ICON = [
    '............',
    '.#########..',
    '.#.......#..',
    '.#.....#.#.#',
    '.#....##.##.',
    '.#...##..#..',
    '.#.#.##..#..',
    '.#.###...#..',
    '.#..#....#..',
    '.#########..',
    '............',
    '............',
]

ON  = 255   # white in greyscale buffer
OFF = 0     # black

_FONT_PATH = os.path.join(os.path.dirname(__file__), "Silkscreen-Regular.ttf")


def _load_fonts():
    try:
        return (
            ImageFont.truetype(_FONT_PATH, 10),  # font_sm  — channel screen (header, pills) — kept for fallback
            ImageFont.truetype(_FONT_PATH, 24),  # font_lg  — channel number hero
            ImageFont.truetype(_FONT_PATH, 8),   # font_sys — sysinfo body
            ImageFont.truetype(_FONT_PATH, 8),   # font_pill_sm — 8px pill font (even channels)
            ImageFont.truetype(_FONT_PATH, 16),  # font_pill_lg — 16px pill font (odd channels)
        )
    except Exception:
        fb = ImageFont.load_default()
        return fb, fb, fb, fb, fb


def _pill_h(font) -> int:
    """Pill height derived from font ink height + fixed vertical padding."""
    bb = font.getbbox("X")
    return (bb[3] - bb[1]) + PILL_VPAD * 2


def _pill_step(font) -> int:
    return _pill_h(font) + 2


# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------
def _ease_out_back(t: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


# ---------------------------------------------------------------------------
# Drawing primitives (greyscale ImageDraw, ON=255 / OFF=0)
# ---------------------------------------------------------------------------
def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _ink_metrics(draw: ImageDraw.ImageDraw, text: str, font):
    """
    Return (aBBL, ink_width):
      aBBL      — left bearing: how far left of the draw origin the ink starts
                  (positive means ink starts left of origin; for most fonts ≈ 0)
      ink_width — tight bounding box width = right − left
    """
    bb = draw.textbbox((0, 0), text, font=font)
    aBBL = -bb[0]           # −left (positive when left < 0)
    ink_width = bb[2] - bb[0]
    return aBBL, ink_width


def _wrap_trunc(draw: ImageDraw.ImageDraw, text: str, max_w: int, font, max_lines: int):
    """Word-wrap to at most max_lines; hard-truncate any line still too wide."""
    words = text.split()
    lines: list = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip() if cur else w
        if _measure(draw, test, font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    for i, ln in enumerate(lines):
        while _measure(draw, ln, font) > max_w and len(ln) > 1:
            ln = ln[:-1]
        lines[i] = ln
    return lines[:max_lines]


def _pill(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, font) -> int:
    """Draw inverted (white rect + black text) pill. Returns pill width."""
    ph = _pill_h(font)
    tw = _measure(draw, text, font)
    pw = tw + PILL_PAD * 2
    draw.rectangle([(x, y), (x + pw - 1, y + ph - 1)], fill=ON)
    for cx, cy in [(x, y), (x + pw - 1, y), (x, y + ph - 1), (x + pw - 1, y + ph - 1)]:
        draw.point((cx, cy), fill=OFF)
    bb = font.getbbox(text)
    ink_h   = bb[3] - bb[1]
    bearing = bb[1]
    text_y  = y + round((ph - ink_h) / 2) - bearing + 1
    draw.text((x + PILL_PAD, text_y), text, font=font, fill=OFF)
    return pw


def _hero(img: Image.Image, draw: ImageDraw.ImageDraw, position: int, name: str,
          xoff: float, font_sm, font_lg) -> None:
    """Draw name pill(s) centred on the full screen, offset horizontally by xoff."""
    ph = _pill_h(font_sm)
    ps = _pill_step(font_sm)
    max_w = W - 2 * MARGIN - PILL_PAD * 2
    lines = _wrap_trunc(draw, name, max_w, font_sm, 2)

    name_h = len(lines) * ph + max(0, len(lines) - 1) * (ps - ph)
    ny = round(32 - name_h / 2)
    for ln in lines:
        pw = _measure(draw, ln, font_sm) + PILL_PAD * 2
        _pill(draw, ln, round((W - pw) / 2 + xoff), ny, font_sm)
        ny += ps


def _ticks(draw: ImageDraw.ImageDraw, current_idx: int) -> None:
    """8-tick position strip. current_idx is 0-based."""
    span   = W - 2 * MARGIN   # 116 px
    cell_w = span / N_CH       # 14.5 px per cell
    tw = 3
    for i in range(N_CH):
        x = round(MARGIN + i * cell_w + (cell_w - tw) / 2)
        if i == current_idx:
            draw.rectangle([(x, 57), (x + tw - 1, 62)], fill=ON)
        else:
            draw.line([(x + 1, 60), (x + 1, 62)], fill=ON, width=1)


def _dotline(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, step: int = 2) -> None:
    for i in range(0, w, step):
        draw.point((x + i, y), fill=ON)


def _channel_indicator(draw: ImageDraw.ImageDraw, current_idx: int) -> None:
    """Bottom strip: active channel = mini pill, others = 2×2 dot."""
    slot = W // N_CH   # 16px per channel slot
    pw, ph = 12, 5     # active mini-pill dimensions
    for i in range(N_CH):
        cx = i * slot + slot // 2
        if i == current_idx:
            x, y = cx - pw // 2, 57
            draw.rectangle([(x, y), (x + pw - 1, y + ph - 1)], fill=ON)
            for kx, ky in [(x, y), (x+pw-1, y), (x, y+ph-1), (x+pw-1, y+ph-1)]:
                draw.point((kx, ky), fill=OFF)
        else:
            draw.rectangle([(cx - 1, 59), (cx, 60)], fill=ON)


def _dither(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, shade: float) -> None:
    lvl = round(shade * 16)
    for yy in range(h):
        for xx in range(w):
            if _BAYER[(y + yy) & 3][(x + xx) & 3] < lvl:
                draw.point((x + xx, y + yy), fill=ON)


def _icon(draw: ImageDraw.ImageDraw, bitmap: list, x: int, y: int, scale: int = 1) -> None:
    for r, row in enumerate(bitmap):
        for c, px in enumerate(row):
            if px == '#':
                draw.rectangle(
                    [(x + c * scale, y + r * scale),
                     (x + c * scale + scale - 1, y + r * scale + scale - 1)],
                    fill=ON,
                )


# ---------------------------------------------------------------------------
# Sysinfo data collection
# ---------------------------------------------------------------------------
def _gather_sysinfo() -> list:
    # NETWORK
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown"
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
        ip = result.stdout.strip().split()[0] if result.stdout.strip() else "No IP"
    except Exception:
        ip = "No IP"
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
            capture_output=True, text=True, timeout=2,
        )
        ssid = "Disconnected"
        for line in result.stdout.splitlines():
            if line.startswith("yes:"):
                ssid = line.split(":", 1)[1][:12]
                break
    except Exception:
        ssid = "Unknown"

    # STORAGE
    try:
        du = shutil.disk_usage("/")
        pct_used = du.used / du.total
        used_gb  = du.used  / 1024 ** 3
        total_gb = du.total / 1024 ** 3
        free_gb  = du.free  / 1024 ** 3
        pct_label = f"{round(pct_used * 100)}%"
        used_str  = f"{used_gb:.0f}GB/{total_gb:.0f}GB"
        free_str  = f"{free_gb:.0f}GB"
    except Exception:
        pct_used = 0.0
        pct_label = used_str = free_str = "?"

    # MEMORY + UPTIME + LOAD
    try:
        mem: dict = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k.strip()] = int(v.split()[0])
        total_mb = mem["MemTotal"] // 1024
        avail_mb = mem["MemAvailable"] // 1024
        used_mb  = total_mb - avail_mb
        mem_pct  = used_mb / total_mb if total_mb else 0.0
        mem_label = f"{round(mem_pct * 100)}%"
        ram_str   = f"{used_mb}/{total_mb}MB"
    except Exception:
        mem_pct = 0.0
        mem_label = ram_str = "?"
    try:
        with open("/proc/uptime") as f:
            uptime_s = float(f.read().split()[0])
        h, rem = divmod(int(uptime_s), 3600)
        uptime_str = f"{h}h {rem // 60}m"
    except Exception:
        uptime_s   = 0.0
        uptime_str = "?"
    try:
        with open("/proc/loadavg") as f:
            load_str = f.read().split()[0]
    except Exception:
        load_str = "?"

    # SYSTEM
    try:
        boot_dt  = datetime.now() - timedelta(seconds=uptime_s)
        boot_str = boot_dt.strftime("%b %d %H:%M")
    except Exception:
        boot_str = "?"
    try:
        from app.config import settings as _cfg  # type: ignore
        ch_count = sum(1 for c in _cfg.channels.values() if c.modules)
        ch_str   = f"{ch_count} active"
    except Exception:
        ch_str = "?"
    ver_str = "Paper v0.4"

    return [
        {
            "key":  "NETWORK",
            "rows": [["HOST", hostname[:10]], ["IP", ip[:13]], ["WIFI", ssid[:12]]],
        },
        {
            "key":   "STORAGE",
            "meter": {"pct": pct_used, "label": pct_label},
            "rows":  [["USED", used_str], ["FREE", free_str]],
        },
        {
            "key":   "MEMORY",
            "meter": {"pct": mem_pct, "label": mem_label},
            "rows":  [["RAM", ram_str], ["UPTIME", uptime_str], ["LOAD", load_str]],
        },
        {
            "key":  "SYSTEM",
            "rows": [["BOOT", boot_str], ["CONSOLE", ver_str], ["CH", ch_str]],
        },
    ]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
class OLEDDriver:
    """
    SSD1306 128×64 animated OLED driver (Direction A "Console" UI).
    Runs a daemon render-loop thread at ~10fps. All public methods are
    thread-safe.
    """

    def __init__(self, i2c_port: int = 1, i2c_address: int = 0x3C):
        self._lock   = threading.Lock()
        self._device = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._font_sm, self._font_lg, self._font_sys, self._font_pill_sm, self._font_pill_lg = _load_fonts()

        # --- State ---
        self._mode         = "channel"   # 'channel' | 'printing' | 'sysinfo'
        self._position     = 1
        self._channel_name = ""

        # Channel transition
        self._trans: Optional[dict] = None
        # {from_pos, from_name, to_pos, to_name, dir, start_s}

        # Printing
        self._print_start_s     = 0.0
        self._print_done        = False
        self._print_done_start_s = 0.0

        # Sysinfo
        self._page: int = 0
        self._page_trans: Optional[dict] = None
        # {from_page, dir, start_s}
        self._sysinfo_pages: list = []

        if not _LUMA_AVAILABLE:
            return
        try:
            serial = i2c(port=i2c_port, address=i2c_address)
            self._device = ssd1306(serial, width=W, height=H)
            self._running = True
            self._thread = threading.Thread(target=self._render_loop, daemon=True)
            self._thread.start()
        except Exception:
            self._device = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def show_channel(self, position: int, name: str) -> None:
        with self._lock:
            old_pos  = self._position
            old_name = self._channel_name
            self._position     = position
            self._channel_name = name

            if self._mode == "channel":
                if old_pos != position:
                    direction = 1 if position > old_pos else -1
                    self._trans = {
                        "from_pos":  old_pos,
                        "from_name": old_name,
                        "to_pos":    position,
                        "to_name":   name,
                        "dir":       direction,
                        "start_s":   time.monotonic(),
                    }

            elif self._mode == "printing":
                if not self._print_done:
                    self._print_done         = True
                    self._print_done_start_s = time.monotonic()

            elif self._mode == "sysinfo":
                if old_pos != position:
                    self._start_page_flip(1 if position > old_pos else -1)

    def show_status(self, msg: str, sub: str = "") -> None:
        """Trigger the printing animation."""
        with self._lock:
            self._mode              = "printing"
            self._print_start_s     = time.monotonic()
            self._print_done        = False
            self._print_done_start_s = 0.0

    def enter_sysinfo(self) -> None:
        with self._lock:
            if self._mode != "channel":
                return
            self._mode         = "sysinfo"
            self._page         = 0
            self._page_trans   = None
            self._sysinfo_pages = []
        threading.Thread(target=self._load_sysinfo, daemon=True).start()

    def exit_sysinfo(self) -> None:
        with self._lock:
            if self._mode == "sysinfo":
                self._mode       = "channel"
                self._page_trans = None

    def is_in_sysinfo(self) -> bool:
        with self._lock:
            return self._mode == "sysinfo"

    def cleanup(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self._device:
            try:
                self._device.cleanup()
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------
    def _start_page_flip(self, direction: int) -> None:
        """Start a sysinfo page-flip. Must be called under self._lock."""
        from_page  = self._page
        self._page = (from_page + direction) % 4
        self._page_trans = {
            "from_page": from_page,
            "dir":       direction,
            "start_s":   time.monotonic(),
        }

    def _load_sysinfo(self) -> None:
        try:
            pages = _gather_sysinfo()
        except Exception:
            pages = [{"key": k, "rows": []} for k in ("NETWORK", "STORAGE", "MEMORY", "SYSTEM")]
        with self._lock:
            self._sysinfo_pages = pages

    # -----------------------------------------------------------------------
    # Render loop
    # -----------------------------------------------------------------------
    def _render_loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                self._render_frame()
            except Exception:
                pass
            sleep_s = FRAME_S - (time.monotonic() - t0)
            if sleep_s > 0:
                time.sleep(sleep_s)

    def _render_frame(self) -> None:
        if not self._device:
            return

        # Snapshot state under the lock so rendering never holds the lock
        with self._lock:
            mode       = self._mode
            position   = self._position
            ch_name    = self._channel_name
            trans      = dict(self._trans) if self._trans else None
            p_start    = self._print_start_s
            p_done     = self._print_done
            p_done_s   = self._print_done_start_s
            page       = self._page
            page_trans = dict(self._page_trans) if self._page_trans else None
            sys_pages  = list(self._sysinfo_pages)

        # Draw into greyscale buffer
        img  = Image.new("L", (W, H), OFF)
        draw = ImageDraw.Draw(img)

        if mode == "channel":
            self._screen_channel(img, draw, position, ch_name, trans)
        elif mode == "printing":
            self._screen_printing(img, draw, p_start, p_done, p_done_s)
        elif mode == "sysinfo":
            self._screen_sysinfo(img, draw, page, page_trans, sys_pages)

        # Expire transitions under the lock
        now = time.monotonic()
        with self._lock:
            if self._trans and (now - self._trans["start_s"]) * 1000 >= CH_MS:
                self._trans = None
            if self._page_trans and (now - self._page_trans["start_s"]) * 1000 >= PAGE_MS:
                self._page_trans = None
            if self._mode == "printing" and self._print_done:
                if (now - self._print_done_start_s) * 1000 >= DONE_MS:
                    self._mode       = "channel"
                    self._print_done = False

        # Threshold greyscale → 1-bit and push
        img_1bit = img.point(lambda p: 1 if p > 96 else 0, "1")
        self._device.display(img_1bit)

    # -----------------------------------------------------------------------
    # Screen: Channel
    # -----------------------------------------------------------------------
    def _pill_font(self, position: int):
        return self._font_pill_lg

    def _screen_channel(self, img: Image.Image, draw: ImageDraw.ImageDraw,
                        position: int, name: str, trans: Optional[dict]) -> None:
        if trans:
            elapsed_ms = (time.monotonic() - trans["start_s"]) * 1000
            t = min(elapsed_ms / CH_MS, 1.0)
            p = _ease_out_back(t)
            d = trans["dir"]   # +1 = channel up: old exits left, new enters from right
            _hero(img, draw, trans["from_pos"], trans["from_name"],
                  round(-d * W * p), self._pill_font(trans["from_pos"]), self._font_lg)
            _hero(img, draw, trans["to_pos"],  trans["to_name"],
                  round(d * W * (1.0 - p)), self._pill_font(trans["to_pos"]), self._font_lg)
        else:
            _hero(img, draw, position, name, 0.0, self._pill_font(position), self._font_lg)
        _channel_indicator(draw, position - 1)

    # -----------------------------------------------------------------------
    # Screen: Printing
    # -----------------------------------------------------------------------
    def _screen_printing(self, img: Image.Image, draw: ImageDraw.ImageDraw,
                         print_start_s: float, print_done: bool,
                         print_done_start_s: float) -> None:
        now        = time.monotonic()
        elapsed_ms = (now - print_start_s) * 1000

        HOUSE   = 13
        SLIT    = HOUSE + 1
        PAPER_X = 16
        PAPER_R = 112
        PAPER_W = PAPER_R - PAPER_X   # 96

        # Printer housing (white bar)
        draw.rectangle([(0, 0), (W - 1, HOUSE - 1)], fill=ON)
        dots  = "." * (int(elapsed_ms / 350) % 4)
        label = "PRINTING" + dots
        lw    = _measure(draw, label, self._font_sys)
        draw.text(((W - lw) // 2, 3), label, font=self._font_sys, fill=OFF)

        # Rotating gear
        gx, gy = 118, 6
        ga = elapsed_ms / 130.0   # radians
        draw.rectangle([(gx - 5, gy - 5), (gx + 5, gy + 5)], fill=ON)
        draw.rectangle([(gx - 4, gy - 4), (gx + 4, gy + 4)], fill=OFF)
        for ao in (0.0, 2.094, 4.189):
            draw.point((gx + round(math.cos(ga + ao) * 3),
                        gy + round(math.sin(ga + ao) * 3)), fill=ON)

        # Paper slit — dotted line
        _dotline(draw, PAPER_X - 2, HOUSE, PAPER_W + 4, step=1)

        if not print_done:
            LINE_H = 6
            LPS    = 2.2
            beat   = (elapsed_ms / 1000.0) * LPS
            cur    = int(beat)
            frac   = beat - cur
            head_x = PAPER_X + 4 + frac * (PAPER_W - 8)

            # Clip paper area via scratch image
            paper_img  = Image.new("L", (W, H), OFF)
            paper_draw = ImageDraw.Draw(paper_img)

            # Paper edges
            paper_draw.line([(PAPER_X, SLIT), (PAPER_X, H - 1)], fill=ON, width=1)
            paper_draw.line([(PAPER_R - 1, SLIT), (PAPER_R - 1, H - 1)], fill=ON, width=1)

            # Scrolling text lines
            S = beat * LINE_H
            for n in range(cur, max(cur - 13, -1), -1):
                y = round(SLIT + 2 + (S - n * LINE_H))
                if y < SLIT or y > H - 2:
                    continue
                is_active = (n == cur)
                max_x     = head_x if is_active else float(PAPER_R - 4)

                # Deterministic PRNG per line index
                r = (n * 2654435761) & 0xFFFFFFFF
                x = PAPER_X + 4 + (6 if n % 5 == 0 else 0)
                while x < PAPER_R - 4:
                    r = (r * 1103515245 + 12345) & 0xFFFFFFFF
                    w_seg = 3 + int((r / 4294967296) * 13)
                    r = (r * 1103515245 + 12345) & 0xFFFFFFFF
                    space = 2 + int((r / 4294967296) * 2)
                    r = (r * 1103515245 + 12345) & 0xFFFFFFFF
                    if (r / 4294967296) > 0.16:
                        xe = min(x + w_seg, int(max_x))
                        if xe > x:
                            paper_draw.rectangle([(x, y), (xe - 1, y + 1)], fill=ON)
                    x += w_seg + space

                if is_active:
                    hx = round(head_x)
                    paper_draw.rectangle([(hx, y - 2), (hx + 1, y + 2)], fill=ON)

            img.paste(paper_img.crop((0, SLIT, W, H)), (0, SLIT))

        else:
            # DONE state
            for x in range(PAPER_X, PAPER_R, 4):
                draw.point((x, 30), fill=ON)
                if x + 2 < PAPER_R:
                    draw.point((x + 2, 31), fill=ON)
            _icon(draw, _CHECK_ICON, 58, 38, scale=2)
            dw = _measure(draw, "DONE", self._font_sys)
            draw.text(((W - dw) // 2, 22), "DONE", font=self._font_sys, fill=ON)

    # -----------------------------------------------------------------------
    # Screen: Sysinfo
    # -----------------------------------------------------------------------
    def _screen_sysinfo(self, img: Image.Image, draw: ImageDraw.ImageDraw,
                        page: int, page_trans: Optional[dict], pages: list) -> None:
        f = self._font_sys   # 8px — sysinfo is designed for this size

        if not pages:
            bb = f.getbbox("A"); hy = round((11 - (bb[3]-bb[1])) / 2) - bb[1]
            draw.rectangle([(0, 0), (W - 1, 10)], fill=ON)
            draw.text((3, hy), "LOADING...", font=f, fill=OFF)
            return

        n_pages  = len(pages)
        cur_page = page % n_pages

        # Header — white bar, not clipped
        HEADER_H = 11
        draw.rectangle([(0, 0), (W - 1, HEADER_H - 1)], fill=ON)
        # Centre text vertically using ink bounds
        bb = f.getbbox("A")
        ink_h, bearing = bb[3] - bb[1], bb[1]
        hy = round((HEADER_H - ink_h) / 2) - bearing
        draw.text((3, hy), pages[cur_page]["key"], font=f, fill=OFF)
        time_str = datetime.now().strftime("%H:%M")
        tw = _measure(draw, time_str, f)
        draw.text((W - 3 - tw, hy), time_str, font=f, fill=OFF)

        # Body — 3×W scratch for horizontal slide
        body_img  = Image.new("L", (W * 3, H), OFF)
        body_draw = ImageDraw.Draw(body_img)

        def _draw_page(pg_idx: int, x0: int) -> None:
            if pg_idx < 0 or pg_idx >= n_pages:
                return
            pg = pages[pg_idx]
            y = 13
            if "meter" in pg:
                m = pg["meter"]
                lbl = m["label"]
                lw  = _measure(body_draw, lbl, f)
                body_draw.text((x0 + W - 3 - lw, y), lbl, font=f, fill=ON)
                # Rounded-rect outline (clear 4 corners for pill feel)
                rx, ry, rw, rh = x0 + 2, y, 96, 9
                body_draw.rectangle([(rx, ry), (rx + rw - 1, ry + rh - 1)], outline=ON)
                for cx, cy in [(rx, ry), (rx + rw - 1, ry), (rx, ry + rh - 1), (rx + rw - 1, ry + rh - 1)]:
                    body_draw.point((cx, cy), fill=OFF)
                # Dithered fill
                fill_w = round(92 * max(0.0, min(1.0, m["pct"])))
                _dither(body_draw, rx + 2, ry + 2, fill_w, 5, 1.0)
                if m["pct"] < 0.04:   # always show a sliver at low %
                    body_draw.rectangle([(rx + 2, ry + 2), (rx + 3, ry + 6)], fill=ON)
                y += 11
            for k, v in pg["rows"]:
                body_draw.text((x0 + 2, y), k, font=f, fill=ON)
                vw = _measure(body_draw, v, f)
                body_draw.text((x0 + W - 3 - vw, y), v, font=f, fill=ON)
                y += 9

        if page_trans:
            elapsed_ms = (time.monotonic() - page_trans["start_s"]) * 1000
            t   = min(elapsed_ms / PAGE_MS, 1.0)
            p   = _ease_out_cubic(t)
            d   = page_trans["dir"]
            _draw_page(page_trans["from_page"], W + round(-d * W * p))
            _draw_page(cur_page,               W + round( d * W * (1.0 - p)))
        else:
            _draw_page(cur_page, W)

        # Crop viewport (y=12..54) from centre strip and paste
        body_crop = body_img.crop((W, 12, W * 2, 55))
        img.paste(body_crop, (0, 12))

        # Footer separator
        _dotline(draw, 0, 55, W, step=2)

        # Page dots
        dot_spacing = 6
        sx = W - 2 - n_pages * dot_spacing
        for i in range(n_pages):
            if i == cur_page:
                draw.rectangle([(sx + i * dot_spacing, 57),
                                 (sx + i * dot_spacing + 3, 61)], fill=ON)
            else:
                draw.rectangle([(sx + i * dot_spacing + 1, 58),
                                 (sx + i * dot_spacing + 2, 60)], fill=ON)
