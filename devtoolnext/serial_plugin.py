"""Built-in byte-stream adapter; no dependency on the UI or HTTP layer."""
import serial
from serial.tools import list_ports


class SerialPlugin:
    plugin_id = "builtin.serial"
    capabilities = ("byte_stream",)

    def discover(self):
        return [{"port": p.device, "description": p.description, "hwid": p.hwid}
                for p in list_ports.comports()]

    def open(self, config):
        # Serial URLs are intentionally limited to loop:// for local verification.
        port = config["port"]
        if "://" in port and port != "loop://":
            raise ValueError("当前仅支持本机串口和 loop:// 测试端口")
        return serial.serial_for_url(
            port, baudrate=config["baudrate"], bytesize=config["bytesize"],
            parity=config["parity"], stopbits=config["stopbits"],
            timeout=0.1, write_timeout=1, rtscts=config["rtscts"],
            dsrdtr=False, xonxoff=False)
