""" Helpers for the FMI-LS-BUS tests: FMU archives that only contain the XML files and fake FMU instances """
from __future__ import annotations

import zipfile
from pathlib import Path

from fmpy.ls_bus import can

CAN_MIME_TYPE = 'application/org.fmi-standard.fmi-ls-bus.can; version=&quot;{version}&quot;'

LS_BUS_DESCRIPTION = ('Layered Standard for the simulation of bus communication on a Physical Signal Abstraction or '
                      'Network Abstraction based level.')


def manifest_xml(is_bus_simulation_fmu: bool = False, version: str = '1.0.0') -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<fmiLayeredStandardManifest
    xmlns:fmi-ls="http://fmi-standard.org/fmi-ls-manifest"
    fmi-ls:fmi-ls-name="org.fmi-standard.fmi-ls-bus"
    fmi-ls:fmi-ls-version="{version}"
    fmi-ls:fmi-ls-description="{LS_BUS_DESCRIPTION}"
    isBusSimulationFMU="{'true' if is_bus_simulation_fmu else 'false'}"/>
'''


def create_bus_fmu(
        filename: Path,
        terminals: list[str] = ('canbus1',),
        is_bus_simulation_fmu: bool = False,
        manifest: str | None | bool = True,
        separator: str = '.',
        mime_type: str = CAN_MIME_TYPE,
        version: str = '1.0.0',
        has_event_mode: bool = True,
        tx_clock: str = 'causality="output" intervalVariability="triggered"',
        bus_notifications: str | None = None,
        extra_terminals: str = '',
        omit_members: tuple[str, ...] = (),
        terminals_path: str = 'terminalsAndIcons/terminalsAndIcons.xml') -> Path:
    """ Create an FMU archive without binaries that defines Bus Terminals of the Network Abstraction

    Parameters:
        filename               filename of the FMU
        terminals              names of the Bus Terminals
        is_bus_simulation_fmu  value of isBusSimulationFMU in the manifest
        manifest               True to add a conformant manifest, None or False to omit it or the XML to add
        separator              separator between terminal name and member name in the variable names
        mime_type              MIME type of the data variables ({version} is replaced)
        version                version in the MIME type
        has_event_mode         value of hasEventMode
        tx_clock               causality and Clock attributes of the Tx_Clock variables
        bus_notifications      causality and variability of a BusNotifications variable in a Configuration Terminal
        extra_terminals        XML of additional <Terminal> elements
        omit_members           member names to omit in the Bus Terminals
        terminals_path         path of the terminalsAndIcons.xml in the archive
    """

    model_name = Path(filename).stem
    mime = mime_type.format(version=version)

    variables = ['    <Float64 name="time" valueReference="0" causality="independent" variability="continuous"/>']
    outputs = []
    terminal_elements = []

    for i, name in enumerate(terminals):

        base = 1000 * (i + 1)
        prefix = name + separator

        variables += [
            f'    <Binary name="{prefix}Rx_Data" valueReference="{base}" causality="input" variability="discrete" clocks="{base + 1}" mimeType="{mime}" maxSize="2048"/>',
            f'    <Clock name="{prefix}Rx_Clock" valueReference="{base + 1}" causality="input" variability="discrete" intervalVariability="triggered"/>',
            f'    <Binary name="{prefix}Tx_Data" valueReference="{base + 2}" causality="output" variability="discrete" clocks="{base + 3}" mimeType="{mime}" maxSize="2048"/>',
            f'    <Clock name="{prefix}Tx_Clock" valueReference="{base + 3}" variability="discrete" {tx_clock}/>',
        ]

        outputs.append(f'    <Output valueReference="{base + 2}"/>')

        if 'causality="output"' in tx_clock:
            outputs.append(f'    <Output valueReference="{base + 3}"/>')

        members = ''.join(
            f'      <TerminalMemberVariable variableKind="signal" variableName="{prefix}{member}" memberName="{member}"/>\n'
            for member in ('Rx_Data', 'Rx_Clock', 'Tx_Data', 'Tx_Clock') if member not in omit_members)

        configuration = ''

        if bus_notifications is not None:
            start = '' if 'calculatedParameter' in bus_notifications else ' start="false"'
            variables.append(f'    <Boolean name="{prefix}BusNotifications" valueReference="{base + 4}" {bus_notifications}{start}/>')
            if 'causality="output"' in bus_notifications:
                outputs.append(f'    <Output valueReference="{base + 4}"/>')
            configuration = (
                '      <Terminal name="Configuration" terminalKind="org.fmi-ls-bus.network-terminal.configuration" matchingRule="bus">\n'
                f'        <TerminalMemberVariable variableKind="signal" variableName="{prefix}BusNotifications" memberName="BusNotifications"/>\n'
                '      </Terminal>\n')

        terminal_elements.append(
            f'    <Terminal name="{name}" terminalKind="org.fmi-ls-bus.network-terminal" matchingRule="org.fmi-ls-bus.transceiver">\n'
            f'{members}{configuration}'
            '    </Terminal>\n')

    model_description = f'''<?xml version="1.0" encoding="UTF-8"?>
<fmiModelDescription fmiVersion="3.0" modelName="{model_name}" instantiationToken="{{{model_name}}}">
  <CoSimulation modelIdentifier="{model_name}" hasEventMode="{'true' if has_event_mode else 'false'}" canHandleVariableCommunicationStepSize="true"/>
  <ModelVariables>
{chr(10).join(variables)}
  </ModelVariables>
  <ModelStructure>
{chr(10).join(outputs)}
  </ModelStructure>
</fmiModelDescription>
'''

    terminals_and_icons = f'''<?xml version="1.0" encoding="UTF-8"?>
<fmiTerminalsAndIcons fmiVersion="3.0">
  <Terminals>
{''.join(terminal_elements)}{extra_terminals}  </Terminals>
</fmiTerminalsAndIcons>
'''

    if manifest is True:
        manifest = manifest_xml(is_bus_simulation_fmu=is_bus_simulation_fmu, version=version)

    with zipfile.ZipFile(filename, 'w') as zf:
        zf.writestr('modelDescription.xml', model_description)
        if terminals or extra_terminals:
            zf.writestr(terminals_path, terminals_and_icons)
        if manifest:
            zf.writestr('extra/org.fmi-standard.fmi-ls-bus/fmi-ls-manifest.xml', manifest)

    return Path(filename)


class FakeFMU:
    """ Base class for fake FMU instances that implements the part of the FMU3Slave API used by the bus master

    The value references follow create_bus_fmu(): terminal i has the base value reference 1000 * (i + 1) and
    Rx_Data = base, Rx_Clock = base + 1, Tx_Data = base + 2, Tx_Clock = base + 3, BusNotifications = base + 4.
    """

    def __init__(self, name: str, n_terminals: int = 1, calls: list | None = None):
        self.name = name
        self.calls = calls if calls is not None else []  # shared log of (instance name, function, arguments)
        self.bases = [1000 * (i + 1) for i in range(n_terminals)]
        self.state = 'instantiated'
        self.time = 0.0
        self.rx_clock = dict((base, False) for base in self.bases)  # activated Rx_Clocks
        self.rx_data = dict((base, b'') for base in self.bases)
        self.tx = dict((base, b'') for base in self.bases)  # pending Tx_Data
        self.tx_reported = dict((base, False) for base in self.bases)  # Tx_Clock activation has been reported
        self.received = dict((base, []) for base in self.bases)  # processed Rx_Data per terminal
        self.booleans = {}
        self.errors = []
        self.terminate_at_step = None
        self.step = 0

    def _log(self, function, *args):
        self.calls.append((self.name, function) + args)

    # state machine

    def enterInitializationMode(self, tolerance=None, startTime=0.0, stopTime=None):
        self._log('enterInitializationMode')
        self.state = 'initialization'
        self.time = startTime
        self.stop_time = stopTime

    def exitInitializationMode(self):
        self._log('exitInitializationMode')
        self.state = 'event'

    def enterEventMode(self):
        self._log('enterEventMode')
        if self.state != 'step':
            self.errors.append('enterEventMode() called in state ' + self.state)
        self.state = 'event'

    def enterStepMode(self):
        self._log('enterStepMode')
        if self.state != 'event':
            self.errors.append('enterStepMode() called in state ' + self.state)
        self.state = 'step'

    def terminate(self):
        self._log('terminate')
        self.state = 'terminated'

    def freeInstance(self):
        self._log('freeInstance')

    def doStep(self, currentCommunicationPoint, communicationStepSize, noSetFMUStatePriorToCurrentPoint=True):
        self._log('doStep', currentCommunicationPoint)
        if self.state != 'step':
            self.errors.append('doStep() called in state ' + self.state)
        self.step += 1
        self.time = currentCommunicationPoint + communicationStepSize
        terminate = self.terminate_at_step == self.step
        event = self.do_step()
        return event, terminate, False, self.time

    def updateDiscreteStates(self):
        self._log('updateDiscreteStates')
        if self.state != 'event':
            self.errors.append('updateDiscreteStates() called in state ' + self.state)
        needs_update = self.update_discrete_states()
        return needs_update, False, False, False, False, 0.0

    # variables

    def getClock(self, vr):
        self._log('getClock', tuple(vr))
        if self.state != 'event':
            self.errors.append('getClock() called in state ' + self.state)
        values = []
        for r in vr:
            base = r - 3
            active = bool(self.tx[base]) and not self.tx_reported[base]
            if active:
                self.tx_reported[base] = True  # an activation is only reported once
            values.append(active)
        return values

    def setClock(self, vr, values):
        self._log('setClock', tuple(vr), tuple(values))
        for r, value in zip(vr, values):
            self.rx_clock[r - 1] = value

    def getBinary(self, vr, nValues=None):
        self._log('getBinary', tuple(vr))
        return [self.tx[r - 2] or None for r in vr]

    def setBinary(self, vr, values):
        self._log('setBinary', tuple(vr), tuple(values))
        for r, value in zip(vr, values):
            if not self.rx_clock[r]:
                self.errors.append('Rx_Data set while Rx_Clock is not active')
            self.rx_data[r] = value

    def getBoolean(self, vr, nValues=None):
        self._log('getBoolean', tuple(vr))
        return [self.booleans.get(r, False) for r in vr]

    def setBoolean(self, vr, values):
        self._log('setBoolean', tuple(vr), tuple(values))
        for r, value in zip(vr, values):
            self.booleans[r] = value

    # behavior

    def do_step(self) -> bool:
        """ Advance the time and return True if event handling is needed """
        return False

    def process(self, base: int, data: bytes) -> None:
        """ Process the Rx_Data received on a terminal """

    def update_discrete_states(self) -> bool:
        """ Process the received data, clear the sent data and return True if another event iteration is needed """

        for base in self.bases:

            if self.tx_reported[base]:  # the data has been fetched by the importer
                self.tx[base] = b''
                self.tx_reported[base] = False

        for base in self.bases:

            if self.rx_clock[base]:
                self.rx_clock[base] = False
                self.received[base].append(self.rx_data[base])
                self.process(base, self.rx_data[base])
                self.rx_data[base] = b''

        return False


class FakeNode(FakeFMU):
    """ A Network FMU that transmits the given data in the given communication steps """

    def __init__(self, name, schedule=None, **kwargs):
        """
        Parameters:
            schedule   {step: data} with the Bus Operations to transmit on the first terminal in step 1, 2, ...
        """
        super().__init__(name, **kwargs)
        self.schedule = schedule or {}

    def do_step(self):
        data = self.schedule.get(self.step)
        if data is None:
            return False
        self.tx[self.bases[0]] = data
        return True  # signal the pending Tx data with eventHandlingNeeded


class FakeBusSimulation(FakeFMU):
    """ A Bus Simulation FMU that forwards the transmit operations to the other terminals and confirms them """

    def __init__(self, name, n_terminals=2, delayed=False, **kwargs):
        """
        Parameters:
            delayed   False: the operations are forwarded at the same communication point
                      True: the operations are forwarded at the end of the next communication step
        """
        super().__init__(name, n_terminals=n_terminals, **kwargs)
        self.delayed = delayed
        self.queue = dict((base, b'') for base in self.bases)  # operations to forward in the next step
        self.produced = False

    def process(self, base, data):
        target = self.queue if self.delayed else self.tx
        for operation in can.parse_operations(data):
            if operation.code not in {can.CAN_TRANSMIT, can.CAN_FD_TRANSMIT}:
                continue
            target[base] += can.create_confirm(operation.arguments['id'])
            for other in self.bases:
                if other != base:
                    target[other] += operation.raw
            self.produced = not self.delayed

    def do_step(self):
        pending = False
        for base in self.bases:
            if self.queue[base]:
                self.tx[base] += self.queue[base]
                self.queue[base] = b''
                pending = True
        return pending

    def update_discrete_states(self):
        super().update_discrete_states()
        # the forwarded operations are provided in the next super-dense time instant
        produced, self.produced = self.produced, False
        return produced
