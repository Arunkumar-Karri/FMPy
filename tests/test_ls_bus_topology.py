import zipfile

import pytest

from fmpy import extract
from fmpy.ls_bus import BusTopologyError, connect_bus_system, read_bus_component, read_ls_bus_manifest

from ls_bus_utils import create_bus_fmu


def connections(system):
    return [(c.a.qualified_name, c.b.qualified_name) for c in system.connections]


@pytest.fixture
def bus(tmp_path):
    """ A Bus Simulation FMU with two channels on network canbus1 and one channel on network canbus2 """
    return create_bus_fmu(tmp_path / 'bus.fmu', terminals=['canbus1', 'canbus1_b', 'canbus2'], is_bus_simulation_fmu=True)


def test_manifest(tmp_path):

    node = create_bus_fmu(tmp_path / 'node.fmu')
    bus = create_bus_fmu(tmp_path / 'bus.fmu', is_bus_simulation_fmu=True)
    plain = create_bus_fmu(tmp_path / 'plain.fmu', terminals=[], manifest=None)

    manifest = read_ls_bus_manifest(node)

    assert manifest.name == 'org.fmi-standard.fmi-ls-bus'
    assert manifest.version == '1.0.0'
    assert manifest.description.startswith('Layered Standard for the simulation of bus communication')
    assert manifest.is_bus_simulation_fmu is False
    assert manifest.problems == []

    assert read_ls_bus_manifest(bus).is_bus_simulation_fmu is True
    assert read_ls_bus_manifest(extract(bus, unzipdir=tmp_path / 'unzipped')).is_bus_simulation_fmu is True

    assert read_ls_bus_manifest(plain) is None


def test_non_standard_manifest(tmp_path):

    manifest = '''<fmiLsManifest xmlns="https://github.com/modelica/fmi-ls-bus" lsBusVersion="1.0.0">
    <NetworkFMU isBusSimulationFMU="true"/>
</fmiLsManifest>'''

    filename = create_bus_fmu(tmp_path / 'node.fmu', manifest=manifest)

    manifest = read_ls_bus_manifest(filename)

    assert manifest.is_bus_simulation_fmu is True
    assert manifest.name is None
    assert len(manifest.problems) == 4

    component = read_bus_component('node', filename)

    assert component.is_bus_simulation_fmu is True
    assert len(component.notes) == 4


def test_bus_terminals(tmp_path):

    filename = create_bus_fmu(tmp_path / 'node.fmu', terminals=['canbus1', 'canbus2'], separator='_',
                              bus_notifications='causality="parameter" variability="fixed"')

    component = read_bus_component('ecu', filename)

    assert component.role == 'Network FMU'
    assert component.notes == []

    canbus1, canbus2 = component.bus_terminals

    assert canbus1.qualified_name == 'ecu.canbus1'
    assert canbus1.bus_type == 'can'
    assert canbus1.version == '1.0.0'

    # the variables are resolved by the variableName of the terminal members (not by a naming convention)
    assert (canbus1.rx_data.name, canbus1.rx_data.valueReference) == ('canbus1_Rx_Data', 1000)
    assert (canbus1.rx_clock.name, canbus1.rx_clock.valueReference) == ('canbus1_Rx_Clock', 1001)
    assert (canbus1.tx_data.name, canbus1.tx_data.valueReference) == ('canbus1_Tx_Data', 1002)
    assert (canbus1.tx_clock.name, canbus1.tx_clock.valueReference) == ('canbus1_Tx_Clock', 1003)

    assert canbus1.has_triggered_tx_clock
    assert not canbus1.has_time_based_tx_clock
    assert canbus1.bus_notifications.name == 'canbus1_BusNotifications'

    assert canbus2.tx_data.valueReference == 2002


def test_fmu_without_manifest(tmp_path):

    filename = create_bus_fmu(tmp_path / 'node.fmu', manifest=None)

    component = read_bus_component('node', filename)

    assert component.manifest is None
    assert component.is_bus_simulation_fmu is False
    assert len(component.bus_terminals) == 1
    assert 'no FMI-LS-BUS manifest' in component.notes[0]


def test_skip_other_terminals(tmp_path):

    extra_terminals = '''    <Terminal name="Powertrain" terminalKind="org.fmi-ls-bus.network-terminal" matchingRule="bus">
      <Terminal name="Frame1" terminalKind="org.fmi-ls-bus.frame-terminal" matchingRule="bus"/>
    </Terminal>
    <Terminal name="flange" terminalKind="mechanical" matchingRule="plug"/>
'''

    filename = create_bus_fmu(tmp_path / 'node.fmu', extra_terminals=extra_terminals)

    component = read_bus_component('node', filename)

    assert [t.name for t in component.bus_terminals] == ['canbus1']
    assert len(component.notes) == 1
    assert '"Powertrain"' in component.notes[0] and 'skipped' in component.notes[0]


def test_invalid_bus_terminals(tmp_path):

    filename = create_bus_fmu(tmp_path / 'missing_member.fmu', omit_members=('Tx_Clock',))

    with pytest.raises(BusTopologyError, match='the member\\(s\\) Tx_Clock are missing'):
        read_bus_component('node', filename)

    filename = create_bus_fmu(tmp_path / 'mime_type.fmu', mime_type='application/octet-stream')

    with pytest.raises(BusTopologyError, match='MIME types'):
        read_bus_component('node', filename)

    filename = create_bus_fmu(tmp_path / 'variable.fmu', extra_terminals='''
    <Terminal name="canbus9" terminalKind="org.fmi-ls-bus.network-terminal" matchingRule="org.fmi-ls-bus.transceiver">
      <TerminalMemberVariable variableKind="signal" variableName="canbus9.Rx_Data" memberName="Rx_Data"/>
    </Terminal>
''')

    with pytest.raises(BusTopologyError, match='"canbus9.Rx_Data" is not defined in the modelDescription.xml'):
        read_bus_component('node', filename)


def test_connect_by_terminal_name(tmp_path, bus):

    ecu1 = create_bus_fmu(tmp_path / 'ecu1.fmu', terminals=['canbus2', 'canbus1'])

    system = connect_bus_system({'bus': bus, 'ecu1': ecu1})

    assert [c.role for c in system.components] == ['Bus Simulation FMU', 'Network FMU']

    assert connections(system) == [('ecu1.canbus2', 'bus.canbus2'), ('ecu1.canbus1', 'bus.canbus1')]

    assert all(c.kind == 'bus-simulation' and c.reason == 'same terminal name' for c in system.connections)

    assert [t.qualified_name for t in system.unconnected_terminals] == ['bus.canbus1_b']
    assert system.warnings == []

    summary = str(system)

    assert 'bus (Bus Simulation FMU): canbus1 [can], canbus1_b [can], canbus2 [can]' in summary
    assert 'ecu1.canbus1 <-> bus.canbus1 (same terminal name)' in summary


def test_connect_multiple_nodes_to_one_network(tmp_path, bus):
    """ Network FMUs whose terminals have the same name are connected to the channels of the network """

    node = create_bus_fmu(tmp_path / 'node.fmu')
    ecu = create_bus_fmu(tmp_path / 'ecu.fmu')

    system = connect_bus_system({'node': node, 'bus': bus, 'ecu': ecu})

    assert connections(system) == [('node.canbus1', 'bus.canbus1'), ('ecu.canbus1', 'bus.canbus1_b')]

    assert system.connections[1].reason == 'next free channel of network "canbus1"'

    # the Network FMUs are connected in the order in which the FMUs are given
    system = connect_bus_system({'ecu': ecu, 'bus': bus, 'node': node})

    assert connections(system) == [('ecu.canbus1', 'bus.canbus1'), ('node.canbus1', 'bus.canbus1_b')]


@pytest.mark.parametrize('channel', ['canbus1_ch2', 'canbus1.2', 'canbus1-b'])
def test_channel_separators(tmp_path, channel):

    bus = create_bus_fmu(tmp_path / 'bus.fmu', terminals=['canbus10', 'canbus1x', channel], is_bus_simulation_fmu=True)
    ecu = create_bus_fmu(tmp_path / 'ecu.fmu')

    system = connect_bus_system({'bus': bus, 'ecu': ecu})

    # canbus10 and canbus1x are different networks
    assert connections(system) == [('ecu.canbus1', f'bus.{channel}')]


def test_no_free_terminal(tmp_path, bus):

    fmus = {'bus': bus}

    for name in ['ecu1', 'ecu2', 'ecu3']:
        fmus[name] = create_bus_fmu(tmp_path / f'{name}.fmu')

    with pytest.raises(BusTopologyError) as context:
        connect_bus_system(fmus)

    message = str(context.value)

    assert 'Cannot connect the Bus Terminal(s) ecu3.canbus1.' in message
    assert 'Free Bus Terminals of the Bus Simulation FMU(s): bus.canbus2.' in message

    system = connect_bus_system(fmus, allow_unconnected=True)

    assert connections(system) == [('ecu1.canbus1', 'bus.canbus1'), ('ecu2.canbus1', 'bus.canbus1_b')]
    assert [t.qualified_name for t in system.unconnected_terminals] == ['bus.canbus2', 'ecu3.canbus1']
    assert system.warnings == ['The Bus Terminal ecu3.canbus1 is not connected.']


def test_explicit_connections(tmp_path):

    bus = create_bus_fmu(tmp_path / 'bus.fmu', terminals=['Node1', 'Node2', 'canbus1'], is_bus_simulation_fmu=True)
    ecu1 = create_bus_fmu(tmp_path / 'ecu1.fmu', terminals=['CanChannel'])
    ecu2 = create_bus_fmu(tmp_path / 'ecu2.fmu', terminals=['CanChannel', 'canbus1'])

    fmus = {'bus': bus, 'ecu1': ecu1, 'ecu2': ecu2}

    with pytest.raises(BusTopologyError, match='Cannot connect the Bus Terminal\\(s\\) ecu1.CanChannel, ecu2.CanChannel'):
        connect_bus_system(fmus)

    system = connect_bus_system(fmus, connections=[
        ('bus.Node2', 'ecu1.CanChannel'),  # the order of the terminals does not matter
        (('ecu2', 'CanChannel'), ('bus', 'Node1')),
    ])

    assert connections(system) == [
        ('ecu1.CanChannel', 'bus.Node2'),
        ('ecu2.CanChannel', 'bus.Node1'),
        ('ecu2.canbus1', 'bus.canbus1'),  # the remaining terminals are connected automatically
    ]

    assert [c.reason for c in system.connections] == ['explicit', 'explicit', 'same terminal name']

    with pytest.raises(BusTopologyError, match='Cannot find the Bus Terminal "ecu1.canbus7"'):
        connect_bus_system(fmus, connections=[('ecu1.canbus7', 'bus.Node1')])

    with pytest.raises(BusTopologyError, match='bus.Node1 is used in more than one connection'):
        connect_bus_system(fmus, connections=[('ecu1.CanChannel', 'bus.Node1'), ('ecu2.CanChannel', 'bus.Node1')])

    with pytest.raises(BusTopologyError, match='both belong to a Bus Simulation FMU'):
        connect_bus_system(fmus, connections=[('bus.Node1', 'bus.Node2')])


def test_explicit_connection_overrides_name_matching(tmp_path, bus):

    ecu1 = create_bus_fmu(tmp_path / 'ecu1.fmu')
    ecu2 = create_bus_fmu(tmp_path / 'ecu2.fmu')

    system = connect_bus_system({'bus': bus, 'ecu1': ecu1, 'ecu2': ecu2}, connections=[('ecu2.canbus1', 'bus.canbus1')])

    assert connections(system) == [('ecu2.canbus1', 'bus.canbus1'), ('ecu1.canbus1', 'bus.canbus1_b')]


def test_direct_connection(tmp_path):

    ecu1 = create_bus_fmu(tmp_path / 'ecu1.fmu', terminals=['canbus1', 'canbus2'])
    ecu2 = create_bus_fmu(tmp_path / 'ecu2.fmu', terminals=['canbus1'])
    ecu3 = create_bus_fmu(tmp_path / 'ecu3.fmu', terminals=['canbus1'])

    with pytest.raises(BusTopologyError, match='Cannot connect the Bus Terminal\\(s\\) ecu1.canbus2. There is no Bus Simulation FMU'):
        connect_bus_system({'ecu1': ecu1, 'ecu2': ecu2})

    system = connect_bus_system({'ecu1': ecu1, 'ecu2': ecu2}, allow_unconnected=True)

    assert connections(system) == [('ecu1.canbus1', 'ecu2.canbus1')]
    assert system.connections[0].kind == 'direct'

    with pytest.raises(BusTopologyError, match='A direct connection is limited to exactly two Network FMUs'):
        connect_bus_system({'ecu1': ecu1, 'ecu2': ecu2, 'ecu3': ecu3}, allow_unconnected=True)


def test_bus_simulation_argument(tmp_path):
    """ The role from the manifest can be overridden """

    # a Bus Simulation FMU without a manifest
    bus = create_bus_fmu(tmp_path / 'bus.fmu', terminals=['canbus1', 'canbus1_b'], manifest=None)
    ecu1 = create_bus_fmu(tmp_path / 'ecu1.fmu')
    ecu2 = create_bus_fmu(tmp_path / 'ecu2.fmu')
    plain = create_bus_fmu(tmp_path / 'plain.fmu', terminals=[], manifest=None)

    fmus = {'bus': bus, 'ecu1': ecu1, 'ecu2': ecu2, 'plain': plain}

    with pytest.raises(BusTopologyError, match='A direct connection is limited to exactly two Network FMUs'):
        connect_bus_system(fmus)

    system = connect_bus_system(fmus, bus_simulation='bus')

    assert connections(system) == [('ecu1.canbus1', 'bus.canbus1'), ('ecu2.canbus1', 'bus.canbus1_b')]
    assert system.component('plain').role == 'no Bus Terminals'

    with pytest.raises(BusTopologyError, match='"plain" cannot be used as Bus Simulation FMU'):
        connect_bus_system(fmus, bus_simulation=['plain'])

    with pytest.raises(KeyError):
        connect_bus_system(fmus, bus_simulation='unknown')


def test_mime_type_compatibility(tmp_path, bus):

    lin = 'application/org.fmi-standard.fmi-ls-bus.lin; version=&quot;{version}&quot;'

    # terminals of a different bus type are not matched by name
    ecu = create_bus_fmu(tmp_path / 'lin.fmu', mime_type=lin)

    assert read_bus_component('ecu', ecu).bus_terminals[0].bus_type == 'lin'

    with pytest.raises(BusTopologyError, match='Cannot connect the Bus Terminal\\(s\\) ecu.canbus1'):
        connect_bus_system({'bus': bus, 'ecu': ecu})

    with pytest.raises(BusTopologyError, match='their bus types differ \\("lin" and "can"\\)'):
        connect_bus_system({'bus': bus, 'ecu': ecu}, connections=[('ecu.canbus1', 'bus.canbus1')])

    # a different pre-release or minor version is reported
    ecu = create_bus_fmu(tmp_path / 'beta.fmu', version='1.0.0-beta.1')

    system = connect_bus_system({'bus': bus, 'ecu': ecu})

    assert connections(system) == [('ecu.canbus1', 'bus.canbus1')]
    assert system.warnings == ['ecu.canbus1 and bus.canbus1 use different versions of the bus MIME type '
                               '("1.0.0-beta.1" and "1.0.0").']

    # a different major version is an error
    ecu = create_bus_fmu(tmp_path / 'v2.fmu', version='2.0.0')

    with pytest.raises(BusTopologyError, match='major versions of their MIME types differ'):
        connect_bus_system({'bus': bus, 'ecu': ecu})


def test_instance_names_from_filenames(tmp_path, bus):

    ecu = create_bus_fmu(tmp_path / 'ecu.fmu')

    # the same FMU can be used for multiple instances
    system = connect_bus_system([bus, ecu, str(ecu)])

    assert [c.name for c in system.components] == ['bus', 'ecu', 'ecu_2']
    assert connections(system) == [('ecu.canbus1', 'bus.canbus1'), ('ecu_2.canbus1', 'bus.canbus1_b')]

    # extracted FMUs
    unzipdir = extract(ecu, unzipdir=tmp_path / 'unzipped' / 'ecu3')

    system = connect_bus_system([bus, unzipdir])

    assert [c.name for c in system.components] == ['bus', 'ecu3']

    with pytest.raises(BusTopologyError, match='must not contain a dot'):
        connect_bus_system({'my.ecu': ecu, 'bus': bus})


def test_validate(tmp_path, bus):

    filename = create_bus_fmu(tmp_path / 'ecu.fmu')
    invalid = tmp_path / 'invalid.fmu'

    # add an element that is not defined in the XML schema
    with zipfile.ZipFile(filename, 'r') as source, zipfile.ZipFile(invalid, 'w') as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'modelDescription.xml':
                data = data.replace(b'canHandleVariableCommunicationStepSize="true"/>',
                                    b'canHandleVariableCommunicationStepSize="true"><Unknown/></CoSimulation>')
            target.writestr(name, data)

    with pytest.raises(Exception, match='Failed to validate modelDescription.xml'):
        connect_bus_system({'bus': bus, 'ecu': invalid})

    system = connect_bus_system({'bus': bus, 'ecu': invalid}, validate=False)

    assert connections(system) == [('ecu.canbus1', 'bus.canbus1')]


def test_fmu_info(tmp_path, bus):

    from fmpy.util import fmu_info

    info = fmu_info(bus)

    assert """
FMI-LS-BUS

  Role               Bus Simulation FMU
  Version            1.0.0
  Bus Terminals      canbus1 (can), canbus1_b (can), canbus2 (can)
""" in info

    info = fmu_info(create_bus_fmu(tmp_path / 'ecu.fmu', manifest=None))

    assert '  Role               Network FMU' in info
    assert '  Note               The FMU has Bus Terminals but no FMI-LS-BUS manifest.' in info

    # FMUs that do not support FMI-LS-BUS
    info = fmu_info(create_bus_fmu(tmp_path / 'plain.fmu', terminals=[], manifest=None))

    assert 'FMI-LS-BUS' not in info

    # invalid Bus Terminals are reported
    info = fmu_info(create_bus_fmu(tmp_path / 'invalid.fmu', omit_members=('Rx_Clock',)))

    assert '  Problem            Bus Terminal "canbus1" of FMU "invalid" is not a valid Bus Terminal' in info
