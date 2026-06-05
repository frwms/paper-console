class OLEDDriver:
    """No-op OLED driver for non-Pi development and testing."""

    def show_channel(self, position: int, name: str) -> None:
        pass

    def show_status(self, msg: str, sub: str = "") -> None:
        pass

    def enter_sysinfo(self) -> None:
        pass

    def exit_sysinfo(self) -> None:
        pass

    def is_in_sysinfo(self) -> bool:
        return False

    def cleanup(self) -> None:
        pass
