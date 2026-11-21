""" Bus Operations of the FMI-LS-BUS Network Abstraction ("low cut") for CAN, CAN FD and CAN XL

The Rx_Data / Tx_Data Binary variables of a CAN Bus Terminal contain zero or more Bus Operations.
Every operation starts with an 8 byte header (OP code and total length incl. the header, both
UInt32) followed by the arguments. All numbers are little-endian and not padded.
"""
from __future__ import annotations

import struct

from attrs import define, field


# OP codes
FORMAT_ERROR = 0x01
CAN_TRANSMIT = 0x10
CAN_FD_TRANSMIT = 0x11
CAN_XL_TRANSMIT = 0x12
CONFIRM = 0x20
ARBITRATION_LOST = 0x30
BUS_ERROR = 0x31
CONFIGURATION = 0x40
STATUS = 0x41
WAKEUP = 0x42

OPERATION_NAMES = {
    FORMAT_ERROR: 'Format Error',
    CAN_TRANSMIT: 'CAN Transmit',
    CAN_FD_TRANSMIT: 'CAN FD Transmit',
    CAN_XL_TRANSMIT: 'CAN XL Transmit',
    CONFIRM: 'Confirm',
    ARBITRATION_LOST: 'Arbitration Lost',
    BUS_ERROR: 'Bus Error',
    CONFIGURATION: 'Configuration',
    STATUS: 'Status',
    WAKEUP: 'Wakeup',
}

# Bus Error: error codes
BIT_ERROR = 1
BIT_STUFFING_ERROR = 2
FORM_ERROR = 3
CRC_ERROR = 4
ACK_ERROR = 5
BROKEN_ERROR_FRAME = 6

# Bus Error: error flags
PRIMARY_ERROR_FLAG = 1
SECONDARY_ERROR_FLAG = 2

# Configuration: parameter types
CAN_BAUDRATE = 1
CAN_FD_BAUDRATE = 2
CAN_XL_BAUDRATE = 3
ARBITRATION_LOST_BEHAVIOR = 4

PARAMETER_TYPE_NAMES = {
    CAN_BAUDRATE: 'CAN_BAUDRATE',
    CAN_FD_BAUDRATE: 'CAN_FD_BAUDRATE',
    CAN_XL_BAUDRATE: 'CAN_XL_BAUDRATE',
    ARBITRATION_LOST_BEHAVIOR: 'ARBITRATION_LOST_BEHAVIOR',
}

# Configuration: arbitration lost behavior
BUFFER_AND_RETRANSMIT = 1
DISCARD_AND_NOTIFY = 2

# Status
ERROR_ACTIVE = 1
ERROR_PASSIVE = 2
BUS_OFF = 3

STATUS_NAMES = {
    ERROR_ACTIVE: 'ERROR_ACTIVE',
    ERROR_PASSIVE: 'ERROR_PASSIVE',
    BUS_OFF: 'BUS_OFF',
}

_HEADER = struct.Struct('<II')


@define
class Operation:
    """ A decoded Bus Operation """

    code: int
    "OP code"

    arguments: dict = field(factory=dict)
    "Decoded arguments of the operation (empty for unknown OP codes)"

    raw: bytes = field(default=b'', repr=False)
    "The encoded operation incl. the header"

    @property
    def name(self) -> str:
        return OPERATION_NAMES.get(self.code, f'Unknown (0x{self.code:02X})')

    def __str__(self) -> str:

        arguments = []

        for key, value in self.arguments.items():
            if isinstance(value, (bytes, bytearray)):
                value = bytes(value).hex(' ').upper()
                arguments.append(f'{key}=[{value}]')
            elif key == 'id':
                arguments.append(f'id=0x{value:X}')
            elif key == 'status':
                arguments.append(f'status={STATUS_NAMES.get(value, value)}')
            elif key == 'parameter_type':
                arguments.append(f'parameter_type={PARAMETER_TYPE_NAMES.get(value, value)}')
            else:
                arguments.append(f'{key}={value}')

        return f"{self.name}({', '.join(arguments)})"


def _encode(code: int, arguments: bytes = b'') -> bytes:
    return _HEADER.pack(code, _HEADER.size + len(arguments)) + arguments


def create_can_transmit(id: int, data: bytes = b'', ide: bool = False, rtr: bool = False) -> bytes:
    """ Create a CAN Transmit operation (classical CAN frame) """
    return _encode(CAN_TRANSMIT, struct.pack('<IBBH', id, ide, rtr, len(data)) + bytes(data))


def create_can_fd_transmit(id: int, data: bytes = b'', ide: bool = False, brs: bool = False, esi: bool = False) -> bytes:
    """ Create a CAN FD Transmit operation """
    return _encode(CAN_FD_TRANSMIT, struct.pack('<IBBBH', id, ide, brs, esi, len(data)) + bytes(data))


def create_can_xl_transmit(id: int, data: bytes = b'', ide: bool = False, sec: bool = False, sdt: int = 0,
                           vcid: int = 0, af: int = 0) -> bytes:
    """ Create a CAN XL Transmit operation """
    return _encode(CAN_XL_TRANSMIT, struct.pack('<IBBBBIH', id, ide, sec, sdt, vcid, af, len(data)) + bytes(data))


def create_confirm(id: int) -> bytes:
    """ Create a Confirm operation """
    return _encode(CONFIRM, struct.pack('<I', id))


def create_arbitration_lost(id: int) -> bytes:
    """ Create an Arbitration Lost operation """
    return _encode(ARBITRATION_LOST, struct.pack('<I', id))


def create_bus_error(id: int, error_code: int, error_flag: int, is_sender: bool) -> bytes:
    """ Create a Bus Error operation """
    return _encode(BUS_ERROR, struct.pack('<IBBB', id, error_code, error_flag, is_sender))


def create_configuration(parameter_type: int, value: int) -> bytes:
    """ Create a Configuration operation

    Parameters:
        parameter_type  CAN_BAUDRATE, CAN_FD_BAUDRATE, CAN_XL_BAUDRATE or ARBITRATION_LOST_BEHAVIOR
        value           the baud rate in bit/s or BUFFER_AND_RETRANSMIT / DISCARD_AND_NOTIFY
    """
    if parameter_type == ARBITRATION_LOST_BEHAVIOR:
        return _encode(CONFIGURATION, struct.pack('<BB', parameter_type, value))
    return _encode(CONFIGURATION, struct.pack('<BI', parameter_type, value))


def create_status(status: int) -> bytes:
    """ Create a Status operation (ERROR_ACTIVE, ERROR_PASSIVE or BUS_OFF) """
    return _encode(STATUS, struct.pack('<B', status))


def create_wakeup() -> bytes:
    """ Create a Wakeup operation """
    return _encode(WAKEUP)


def create_format_error(operation: bytes) -> bytes:
    """ Create a Format Error operation for an operation that could not be processed """
    return _encode(FORMAT_ERROR, struct.pack('<H', len(operation)) + bytes(operation))


def _decode_arguments(code: int, payload: bytes) -> dict:

    def frame(fmt: str, names: list[str], flags: set[str]) -> dict:
        s = struct.Struct(fmt)
        values = s.unpack_from(payload)
        arguments = {name: bool(value) if name in flags else value for name, value in zip(names, values)}
        data_length = arguments.pop('data_length')
        data = payload[s.size:s.size + data_length]
        if len(data) != data_length:
            raise ValueError(f"expected {data_length} data bytes but only {len(data)} are available")
        arguments['data'] = data
        return arguments

    if code == CAN_TRANSMIT:
        return frame('<IBBH', ['id', 'ide', 'rtr', 'data_length'], {'ide', 'rtr'})

    if code == CAN_FD_TRANSMIT:
        return frame('<IBBBH', ['id', 'ide', 'brs', 'esi', 'data_length'], {'ide', 'brs', 'esi'})

    if code == CAN_XL_TRANSMIT:
        return frame('<IBBBBIH', ['id', 'ide', 'sec', 'sdt', 'vcid', 'af', 'data_length'], {'ide', 'sec'})

    if code == FORMAT_ERROR:
        return frame('<H', ['data_length'], set())

    if code in {CONFIRM, ARBITRATION_LOST}:
        id, = struct.unpack_from('<I', payload)
        return {'id': id}

    if code == BUS_ERROR:
        id, error_code, error_flag, is_sender = struct.unpack_from('<IBBB', payload)
        return {'id': id, 'error_code': error_code, 'error_flag': error_flag, 'is_sender': bool(is_sender)}

    if code == CONFIGURATION:
        parameter_type, = struct.unpack_from('<B', payload)
        if parameter_type == ARBITRATION_LOST_BEHAVIOR:
            value, = struct.unpack_from('<B', payload, 1)
        else:
            value, = struct.unpack_from('<I', payload, 1)
        return {'parameter_type': parameter_type, 'value': value}

    if code == STATUS:
        status, = struct.unpack_from('<B', payload)
        return {'status': status}

    return {}  # Wakeup and unknown operations


def parse_operations(data: bytes) -> list[Operation]:
    """ Decode the Bus Operations contained in the value of an Rx_Data or Tx_Data variable

    Parameters:
        data   the value of the Binary variable

    returns:
        a list of the contained operations (unknown OP codes are returned without arguments)
    """

    operations = []
    offset = 0
    data = bytes(data)

    while offset < len(data):

        if len(data) - offset < _HEADER.size:
            raise ValueError(f"Truncated operation header at offset {offset}.")

        code, length = _HEADER.unpack_from(data, offset)

        if length < _HEADER.size or length > len(data) - offset:
            raise ValueError(f"Invalid length {length} of operation 0x{code:02X} at offset {offset}.")

        raw = data[offset:offset + length]

        try:
            arguments = _decode_arguments(code, raw[_HEADER.size:])
        except (struct.error, ValueError) as e:
            raise ValueError(f"Failed to decode the arguments of operation 0x{code:02X} at offset {offset}: {e}")

        operations.append(Operation(code=code, arguments=arguments, raw=raw))

        offset += length

    return operations
