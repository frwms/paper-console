from app.config import PRINTER_WIDTH
from app.drivers.printer_serial import PrinterDriver


class CapturePrinter(PrinterDriver):
    """Printer driver that captures raster bitmaps to memory instead of sending to hardware."""

    def __init__(self, width: int = PRINTER_WIDTH):
        super().__init__(width=width, init_serial=False)
        self.captured_bitmaps = []

    def _send_bitmap(self, img):  # type: ignore[override]
        if img is not None:
            self.captured_bitmaps.append(img.copy())

    def blip(self):
        return

    def feed_direct(self, lines: int = 3):
        return

    def clear_hardware_buffer(self):
        self.print_buffer.clear()
        self.lines_printed = 0
        self.max_lines = 0
        self._max_lines_hit = False
