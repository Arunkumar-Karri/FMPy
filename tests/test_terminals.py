import zipfile

import pytest

from fmpy import extract, read_model_description
from fmpy.model_description import read_terminals_and_icons
from ls_bus_utils import create_bus_fmu


TERMINALS_AND_ICONS = '''<?xml version="1.0" encoding="UTF-8"?>
<fmiTerminalsAndIcons fmiVersion="3.0">
  <Terminals>
    <Terminal name="Powertrain" terminalKind="org.fmi-ls-bus.network-terminal" matchingRule="org.fmi-ls-bus.transceiver" description="Powertrain CAN">
      <TerminalMemberVariable variableKind="signal" variableName="Powertrain.Rx_Clock" memberName="Rx_Clock"/>
      <TerminalMemberVariable variableKind="signal" variableName="Powertrain.Rx_Data" memberName="Rx_Data"/>
      <Terminal name="Configuration" terminalKind="org.fmi-ls-bus.network-terminal.configuration" matchingRule="bus">
        <TerminalMemberVariable variableKind="signal" variableName="BusNotifications" memberName="BusNotifications"/>
      </Terminal>
    </Terminal>
    <Terminal name="fluid" matchingRule="plug">
      <TerminalStreamMemberVariable inStreamMemberName="h_in" outStreamMemberName="h_out" inStreamVariableName="a.h_in" outStreamVariableName="a.h_out"/>
    </Terminal>
  </Terminals>
</fmiTerminalsAndIcons>
'''


def test_read_terminals(tmp_path):

    xml_file = tmp_path / 'terminalsAndIcons.xml'
    xml_file.write_text(TERMINALS_AND_ICONS)

    powertrain, fluid = read_terminals_and_icons(str(xml_file))

    assert powertrain.name == 'Powertrain'
    assert powertrain.terminalKind == 'org.fmi-ls-bus.network-terminal'
    assert powertrain.matchingRule == 'org.fmi-ls-bus.transceiver'
    assert powertrain.description == 'Powertrain CAN'

    assert [(m.memberName, m.variableName, m.variableKind) for m in powertrain.memberVariables] == [
        ('Rx_Clock', 'Powertrain.Rx_Clock', 'signal'),
        ('Rx_Data', 'Powertrain.Rx_Data', 'signal'),
    ]

    configuration, = powertrain.terminals

    assert configuration.name == 'Configuration'
    assert configuration.terminalKind == 'org.fmi-ls-bus.network-terminal.configuration'
    assert configuration.memberVariables[0].memberName == 'BusNotifications'

    assert fluid.terminalKind is None
    assert fluid.streamMemberVariables[0].inStreamVariableName == 'a.h_in'
    assert fluid.streamMemberVariables[0].outStreamMemberName == 'h_out'


@pytest.mark.parametrize('path', ['terminalsAndIcons/terminalsAndIcons.xml', 'icons/terminalsAndIcons.xml'])
def test_read_terminals_from_fmu(tmp_path, path):

    filename = create_bus_fmu(tmp_path / 'Node.fmu', terminals=['canbus1', 'canbus2'], terminals_path=path)

    # FMU archive
    assert [t.name for t in read_terminals_and_icons(filename)] == ['canbus1', 'canbus2']

    # file like object
    with open(filename, 'rb') as file:
        assert [t.name for t in read_terminals_and_icons(file)] == ['canbus1', 'canbus2']

    # extracted FMU
    unzipdir = extract(filename, unzipdir=tmp_path / 'unzipped')
    assert [t.name for t in read_terminals_and_icons(unzipdir)] == ['canbus1', 'canbus2']


def test_fmu_without_terminals(tmp_path):

    filename = create_bus_fmu(tmp_path / 'Plain.fmu', terminals=[], manifest=None)

    assert read_terminals_and_icons(filename) == []


def test_validate_terminals(tmp_path):

    filename = tmp_path / 'Invalid.fmu'

    with zipfile.ZipFile(filename, 'w') as zf:
        zf.writestr('terminalsAndIcons/terminalsAndIcons.xml',
                    '<fmiTerminalsAndIcons fmiVersion="3.0"><Terminals><Terminal name="t"/></Terminals></fmiTerminalsAndIcons>')

    with pytest.raises(Exception, match="Failed to validate terminalsAndIcons.xml"):
        read_terminals_and_icons(filename)

    terminal, = read_terminals_and_icons(filename, validate=False)

    assert terminal.name == 't'
    assert terminal.matchingRule is None


def test_binary_and_clock_attributes(tmp_path):

    filename = create_bus_fmu(tmp_path / 'Node.fmu')

    # clocked Binary inputs do not require a start value
    model_description = read_model_description(filename, validate=True)

    variables = dict((v.name, v) for v in model_description.modelVariables)

    rx_data = variables['canbus1.Rx_Data']

    assert rx_data.mimeType == 'application/org.fmi-standard.fmi-ls-bus.can; version="1.0.0"'
    assert rx_data.maxSize == 2048
    assert rx_data.clocks == [variables['canbus1.Rx_Clock']]

    tx_clock = variables['canbus1.Tx_Clock']

    assert tx_clock.causality == 'output'
    assert tx_clock.intervalVariability == 'triggered'
    assert tx_clock.mimeType is None


def test_clock_attributes_from_declared_type(tmp_path):

    xml_file = tmp_path / 'modelDescription.xml'

    xml_file.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<fmiModelDescription fmiVersion="3.0" modelName="Clocks" instantiationToken="{Clocks}">
  <CoSimulation modelIdentifier="Clocks" hasEventMode="true"/>
  <TypeDefinitions>
    <BinaryType name="CanData" mimeType="application/org.fmi-standard.fmi-ls-bus.can; version=&quot;1.0.0&quot;" maxSize="512"/>
    <ClockType name="Periodic" intervalVariability="constant" intervalDecimal="0.01" supportsFraction="true" resolution="1000" intervalCounter="10" priority="2"/>
  </TypeDefinitions>
  <ModelVariables>
    <Float64 name="time" valueReference="0" causality="independent" variability="continuous"/>
    <Clock name="c1" valueReference="1" causality="input" intervalVariability="constant" declaredType="Periodic"/>
    <Clock name="c2" valueReference="2" causality="input" intervalVariability="countdown" canBeDeactivated="true"/>
    <Binary name="b1" valueReference="3" causality="input" clocks="1" declaredType="CanData"/>
    <Binary name="b2" valueReference="4" causality="input" clocks="2" declaredType="CanData" maxSize="64"/>
    <Binary name="b3" valueReference="5"/>
  </ModelVariables>
  <ModelStructure/>
</fmiModelDescription>
''')

    model_description = read_model_description(str(xml_file))

    c1, c2, b1, b2, b3 = model_description.modelVariables[1:]

    assert c1.declaredType.name == 'Periodic'
    assert c1.intervalVariability == 'constant'
    assert c1.intervalDecimal == pytest.approx(0.01)
    assert c1.shiftDecimal == 0.0
    assert c1.supportsFraction is True
    assert c1.resolution == 1000
    assert c1.intervalCounter == 10
    assert c1.shiftCounter == 0
    assert c1.priority == 2
    assert c1.canBeDeactivated is False

    assert c2.intervalVariability == 'countdown'
    assert c2.canBeDeactivated is True
    assert c2.intervalDecimal is None

    assert b1.mimeType == 'application/org.fmi-standard.fmi-ls-bus.can; version="1.0.0"'
    assert b1.maxSize == 512
    assert b2.maxSize == 64

    assert b3.mimeType == 'application/octet-stream'
    assert b3.maxSize is None
