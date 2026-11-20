import pytest

from fmpy.ls_bus import can


def test_can_transmit():

    data = can.create_can_transmit(id=0x123, data=bytes([1, 2, 3, 4]), ide=False, rtr=False)

    # OP code, length, ID, Ide, Rtr, data length, data (little-endian, not padded)
    assert data == bytes.fromhex('10000000' '14000000' '23010000' '00' '00' '0400' '01020304')

    operation, = can.parse_operations(data)

    assert operation.code == can.CAN_TRANSMIT
    assert operation.name == 'CAN Transmit'
    assert operation.arguments == {'id': 0x123, 'ide': False, 'rtr': False, 'data': bytes([1, 2, 3, 4])}
    assert operation.raw == data
    assert str(operation) == 'CAN Transmit(id=0x123, ide=False, rtr=False, data=[01 02 03 04])'


def test_can_fd_transmit():

    payload = bytes(range(16))

    data = can.create_can_fd_transmit(id=0x1ABCDEF, data=payload, ide=True, brs=True, esi=False)

    assert len(data) == 17 + 16

    operation, = can.parse_operations(data)

    assert operation.code == can.CAN_FD_TRANSMIT
    assert operation.arguments == {'id': 0x1ABCDEF, 'ide': True, 'brs': True, 'esi': False, 'data': payload}


def test_can_xl_transmit():

    payload = bytes(100)

    data = can.create_can_xl_transmit(id=0x7FF, data=payload, sec=True, sdt=3, vcid=4, af=0xDEADBEEF)

    assert len(data) == 22 + 100

    operation, = can.parse_operations(data)

    assert operation.arguments == {'id': 0x7FF, 'ide': False, 'sec': True, 'sdt': 3, 'vcid': 4, 'af': 0xDEADBEEF,
                                   'data': payload}


@pytest.mark.parametrize('data, code, length, arguments', [
    (can.create_confirm(0x100), can.CONFIRM, 12, {'id': 0x100}),
    (can.create_arbitration_lost(0x101), can.ARBITRATION_LOST, 12, {'id': 0x101}),
    (can.create_bus_error(0x102, can.CRC_ERROR, can.PRIMARY_ERROR_FLAG, True), can.BUS_ERROR, 15,
     {'id': 0x102, 'error_code': can.CRC_ERROR, 'error_flag': can.PRIMARY_ERROR_FLAG, 'is_sender': True}),
    (can.create_configuration(can.CAN_BAUDRATE, 500000), can.CONFIGURATION, 13,
     {'parameter_type': can.CAN_BAUDRATE, 'value': 500000}),
    (can.create_configuration(can.CAN_FD_BAUDRATE, 2000000), can.CONFIGURATION, 13,
     {'parameter_type': can.CAN_FD_BAUDRATE, 'value': 2000000}),
    (can.create_configuration(can.ARBITRATION_LOST_BEHAVIOR, can.DISCARD_AND_NOTIFY), can.CONFIGURATION, 10,
     {'parameter_type': can.ARBITRATION_LOST_BEHAVIOR, 'value': can.DISCARD_AND_NOTIFY}),
    (can.create_status(can.BUS_OFF), can.STATUS, 9, {'status': can.BUS_OFF}),
    (can.create_wakeup(), can.WAKEUP, 8, {}),
])
def test_operations(data, code, length, arguments):

    assert len(data) == length

    operation, = can.parse_operations(data)

    assert operation.code == code
    assert operation.arguments == arguments


def test_format_error():

    invalid = can.create_confirm(0x1)[:-1]

    operation, = can.parse_operations(can.create_format_error(invalid))

    assert operation.code == can.FORMAT_ERROR
    assert operation.arguments == {'data': invalid}


def test_multiple_operations():

    data = (can.create_configuration(can.CAN_BAUDRATE, 500000)
            + can.create_can_transmit(0x1, b'\x00\x00')  # zero bytes must not truncate the data
            + can.create_confirm(0x2)
            + can.create_can_fd_transmit(0x3, bytes(64)))

    operations = can.parse_operations(data)

    assert [o.code for o in operations] == [can.CONFIGURATION, can.CAN_TRANSMIT, can.CONFIRM, can.CAN_FD_TRANSMIT]
    assert b''.join(o.raw for o in operations) == data

    assert can.parse_operations(b'') == []


def test_unknown_operation():

    data = bytes.fromhex('99000000' '0a000000' 'abcd') + can.create_wakeup()

    unknown, wakeup = can.parse_operations(data)

    assert unknown.code == 0x99
    assert unknown.arguments == {}
    assert unknown.name == 'Unknown (0x99)'
    assert wakeup.code == can.WAKEUP


@pytest.mark.parametrize('data, message', [
    (bytes(7), 'Truncated operation header'),
    (bytes.fromhex('20000000' '04000000'), 'Invalid length'),  # length smaller than the header
    (can.create_confirm(0x1)[:-1], 'Invalid length'),  # length exceeds the available data
    (bytes.fromhex('20000000' '0a000000' '0000'), 'Failed to decode'),  # arguments too short
    (bytes.fromhex('10000000' '10000000' '01000000' '00' '00' '0800'), 'Failed to decode'),  # data missing
])
def test_invalid_operations(data, message):

    with pytest.raises(ValueError, match=message):
        can.parse_operations(data)
