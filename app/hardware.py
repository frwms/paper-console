import os
import platform
from app.config import PRINTER_WIDTH

# Auto-detect platform and use appropriate drivers
_is_raspberry_pi = platform.system() == "Linux" and os.path.exists(
    "/proc/device-tree/model"
)

if _is_raspberry_pi:
    try:
        if os.path.exists("/dev/usb/lp0"):
            from app.drivers.printer_usb import PrinterDriver
        else:
            from app.drivers.printer_serial import PrinterDriver
        # Use KY-040 encoder as dial by default; set PC1_USE_ENCODER=0 for rotary switch
        if os.environ.get("PC1_USE_ENCODER", "1") != "0":
            from app.drivers.encoder_gpio import DialDriver
        else:
            from app.drivers.dial_gpio import DialDriver
        from app.drivers.button_gpio import ButtonDriver
        try:
            from app.drivers.oled_ssd1306 import OLEDDriver
        except Exception:
            from app.drivers.oled_mock import OLEDDriver
    except ImportError:
        from app.drivers.printer_mock import PrinterDriver
        from app.drivers.dial_mock import DialDriver
        from app.drivers.button_mock import ButtonDriver
        from app.drivers.oled_mock import OLEDDriver
else:
    from app.drivers.printer_mock import PrinterDriver
    from app.drivers.dial_mock import DialDriver
    from app.drivers.button_mock import ButtonDriver
    from app.drivers.oled_mock import OLEDDriver

# Global Hardware Instances
printer = PrinterDriver(width=PRINTER_WIDTH)
dial = DialDriver()
oled = OLEDDriver()

# Main Interface Button (Print / WiFi Setup / Reset) - GPIO 25 (Pin 22)
button = ButtonDriver(pin=25)
