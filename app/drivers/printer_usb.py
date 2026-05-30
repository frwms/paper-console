import time
import logging
from app.drivers.printer_serial import PrinterDriver as _SerialBase

logger = logging.getLogger(__name__)

USB_DEVICE = "/dev/usb/lp0"


class PrinterDriver(_SerialBase):
    """USB printer driver for /dev/usb/lp0 (raw ESC/POS over USB).

    Subclasses printer_serial.PrinterDriver, reusing all rendering/buffering
    logic and overriding only the hardware I/O layer.
    """

    def __init__(self, width=42, device=USB_DEVICE):
        self._usb_device = device
        self._usb_fh = None
        super().__init__(width=width, init_serial=False)
        try:
            self._usb_fh = open(device, "wb", buffering=0)
            self._initialize_printer()
        except OSError as e:
            self.last_init_error = str(e)
            logger.warning("USB printer init failed on %s: %s", device, e)

    def is_available(self):
        return self._usb_fh is not None and not self._usb_fh.closed

    def _reopen(self):
        """Close the stale handle and reopen the USB device."""
        try:
            if self._usb_fh:
                self._usb_fh.close()
        except Exception:
            pass
        self._usb_fh = None
        try:
            self._usb_fh = open(self._usb_device, "wb", buffering=0)
            logger.info("USB printer reconnected on %s", self._usb_device)
            self._initialize_printer()
        except OSError as e:
            logger.warning("USB printer reconnect failed on %s: %s", self._usb_device, e)

    def _write(self, data):
        try:
            with self._io_lock:
                if self._usb_fh and not self._usb_fh.closed:
                    self._usb_fh.write(data)
        except OSError as e:
            if e.errno == 19:  # ENODEV — device disconnected or went stale
                logger.warning("USB printer lost (ENODEV), attempting reconnect")
                self._reopen()
            else:
                logger.exception("USB write failed")

    def _read(self, size=1, timeout=1.0):
        return b""

    def _initialize_busy_pin(self):
        pass

    def _read_busy_pin(self):
        return None

    def is_printer_busy(self):
        return False

    def wait_for_idle(self, timeout=3.0, quiet_period=0.35):
        if not self.is_available():
            return
        try:
            with self._io_lock:
                self._usb_fh.flush()
        except Exception:
            pass
        time.sleep(quiet_period)
