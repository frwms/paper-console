class OLEDDriver:
    """No-op OLED driver for non-Pi development and testing."""

    def show_channel(self, position: int, name: str) -> None:
        pass

    def show_status(self, msg: str, sub: str = "") -> None:
        pass

    def cleanup(self) -> None:
        pass
