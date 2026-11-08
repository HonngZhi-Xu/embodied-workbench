"""PaXini GEN3 UART, user manual V1.0.5 sections 5.4 and 5.6.

Read requests only: no calibration, writes or protocol-mode changes.
"""
import struct
import time

MODEL = 'CP-M3025-Core'
POINTS = 77


def read_request(address, size, device_id=1):
    if not 1 <= device_id <= 255 or not 1 <= size <= 4096:
        raise ValueError('Invalid sensor address or read size.')
    frame = b'\x55\xaa' + struct.pack('<HBBB I H', 9, device_id, 0, 0xfb, address, size)
    return frame + bytes([(-sum(frame)) & 0xff])


def decode_response(frame, address, size, device_id=1):
    if len(frame) != size + 15 or frame[:2] != b'\xaa\x55':
        raise ValueError('Invalid UART response header/size.')
    length, dev, reserved, func, reg, returned = struct.unpack('<HBBB I H', frame[2:13])
    if length != size + 10 or (dev, reserved, func, reg, returned) != (device_id, 0, 0xfb, address, size):
        raise ValueError('UART response does not match request.')
    if sum(frame) & 0xff:
        raise ValueError('UART LRC mismatch.')
    if frame[13] != 0:
        raise ValueError(f'UART status={frame[13]}; verify communication-board protocol before accepting it.')
    return frame[14:-1]


def xyz_counts(data):
    if len(data) % 3:
        raise ValueError('Expected packed Fx/Fy/Fz triples.')
    return [[x - 256 if x >= 128 else x, y - 256 if y >= 128 else y, z]
            for x, y, z in zip(data[0::3], data[1::3], data[2::3])]


class PaxiniM3025:
    def __init__(self, port, device_id=1, baudrate=921600, transport=None):
        if transport is None:
            import serial
            transport = serial.Serial(port, baudrate=baudrate, bytesize=8, parity='N',
                                      stopbits=1, timeout=0.05, write_timeout=0.5,
                                      xonxoff=False, rtscts=False, dsrdtr=False)
        self.serial, self.device_id = transport, int(device_id)
        self.buffer = bytearray()
        self.sequence = 0

    def _read(self, address, size):
        self.serial.write(read_request(address, size, self.device_id))
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            self.buffer.extend(self.serial.read(max(1, getattr(self.serial, 'in_waiting', 0))))
            while len(self.buffer) >= 4:
                start = self.buffer.find(b'\xaa\x55')
                if start < 0:
                    self.buffer[:] = self.buffer[-1:]
                    break
                del self.buffer[:start]
                if len(self.buffer) < 4:
                    break
                length = int.from_bytes(self.buffer[2:4], 'little')
                if not 10 <= length <= 4106:
                    del self.buffer[0]
                    continue
                total = length + 5
                if len(self.buffer) < total:
                    break
                frame = bytes(self.buffer[:total])
                del self.buffer[:total]
                return decode_response(frame, address, size, self.device_id)
        raise TimeoutError('No complete PaXini UART response; check port, module address and communication board.')

    def read(self):
        started = time.time()
        resultant = xyz_counts(self._read(1008, 3))[0]
        points = xyz_counts(self._read(1038, POINTS * 3))
        self.sequence += 1
        return dict(model=MODEL, seq=self.sequence, timestamp=time.time(),
                    read_span_ms=(time.time() - started) * 1000,
                    points=points, unit='counts', resultant_n=[v * 0.1 for v in resultant],
                    layout='point-index; physical coordinates not configured')

    def close(self):
        self.serial.close()
