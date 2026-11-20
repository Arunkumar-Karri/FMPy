""" Discovery and automatic connection of FMI-LS-BUS Bus Terminals (Network Abstraction / "low cut") """
from __future__ import annotations

import os
import re
from os import PathLike
from typing import Iterable, Literal, Mapping, Sequence

from attrs import define, field

from ..model_description import ModelDescription, ModelVariable, Terminal, read_model_description, \
    read_terminals_and_icons
from .manifest import LsBusManifest, read_ls_bus_manifest


NETWORK_TERMINAL_KIND = 'org.fmi-ls-bus.network-terminal'
"terminalKind of a Bus Terminal"

CONFIGURATION_TERMINAL_KIND = 'org.fmi-ls-bus.network-terminal.configuration'
"terminalKind of the Configuration Terminal nested in a Bus Terminal"

TRANSCEIVER_MATCHING_RULE = 'org.fmi-ls-bus.transceiver'
"matchingRule of a Bus Terminal of the Network Abstraction"

MIME_TYPE_PREFIX = 'application/org.fmi-standard.fmi-ls-bus.'
"Prefix of the MIME types of the Rx_Data and Tx_Data variables"

CHANNEL_SEPARATORS = ('_', '.', '-')
"Separators between the network name and the channel suffix in the terminal names of a Bus Simulation FMU"

MEMBER_NAMES = ('Rx_Data', 'Rx_Clock', 'Tx_Data', 'Tx_Clock')

TIME_BASED_INTERVAL_VARIABILITIES = {'constant', 'fixed', 'tunable', 'changing', 'countdown'}


class BusTopologyError(Exception):
    """ Raised if the Bus Terminals of a set of FMUs cannot be connected """


@define(eq=False)
class BusTerminal:
    """ A Bus Terminal of the Network Abstraction with its resolved member variables """

    component: BusComponent = field(repr=False)
    "The FMU instance the terminal belongs to"

    name: str
    "Name of the terminal (should be the network name)"

    rx_data: ModelVariable = field(repr=False)
    rx_clock: ModelVariable = field(repr=False)
    tx_data: ModelVariable = field(repr=False)
    tx_clock: ModelVariable = field(repr=False)

    bus_type: str | None = None
    "Bus type from the MIME type of the data variables, e.g. 'can' (covers CAN, CAN FD and CAN XL)"

    version: str | None = field(default=None, repr=False)
    "Version from the MIME type of the data variables"

    configuration: dict[str, ModelVariable] = field(factory=dict, repr=False)
    "Member variables of the nested Configuration Terminal by memberName"

    terminal: Terminal | None = field(default=None, repr=False)
    "The terminal as defined in the terminalsAndIcons.xml"

    @property
    def qualified_name(self) -> str:
        """ <instance name>.<terminal name> """
        return f'{self.component.name}.{self.name}'

    @property
    def has_triggered_tx_clock(self) -> bool:
        """ True if Tx_Clock is a triggered output Clock that is activated by the FMU """
        return self.tx_clock.causality == 'output' and self.tx_clock.intervalVariability == 'triggered'

    @property
    def has_time_based_tx_clock(self) -> bool:
        """ True if Tx_Clock is a time-based Clock that has to be activated by the importer """
        return self.tx_clock.causality == 'input' and self.tx_clock.intervalVariability in TIME_BASED_INTERVAL_VARIABILITIES

    @property
    def bus_notifications(self) -> ModelVariable | None:
        """ The BusNotifications variable of the Configuration Terminal or None if it is not defined """
        for member_name in ('BusNotifications', 'BusNotification'):  # the example of v1.0.0 uses the singular
            if member_name in self.configuration:
                return self.configuration[member_name]
        return None

    def __str__(self) -> str:
        return self.qualified_name


@define(eq=False)
class BusComponent:
    """ An FMU instance in a bus system """

    name: str
    "Instance name"

    filename: str | PathLike = field(repr=False)
    "Filename of the FMU or directory with the extracted FMU"

    model_description: ModelDescription = field(repr=False)

    manifest: LsBusManifest | None = field(default=None, repr=False)
    "The FMI-LS-BUS manifest or None if the FMU does not provide one"

    is_bus_simulation_fmu: bool = False
    "True for a Bus Simulation FMU, False for a Network FMU (or an FMU without Bus Terminals)"

    bus_terminals: list[BusTerminal] = field(factory=list, repr=False)
    "Bus Terminals of the Network Abstraction"

    notes: list[str] = field(factory=list, repr=False)
    "Remarks on the FMU, e.g. terminals that were skipped"

    @property
    def role(self) -> str:
        if not self.bus_terminals:
            return 'no Bus Terminals'
        return 'Bus Simulation FMU' if self.is_bus_simulation_fmu else 'Network FMU'


@define(eq=False)
class BusConnection:
    """ A connection of two Bus Terminals

    Tx_Data and Tx_Clock of each terminal are connected to Rx_Data and Rx_Clock of the other terminal.
    """

    a: BusTerminal
    "Bus Terminal of a Network FMU"

    b: BusTerminal
    "Bus Terminal of the Bus Simulation FMU (or of the second Network FMU in a direct connection)"

    kind: Literal['bus-simulation', 'direct'] = 'bus-simulation'

    reason: str = field(default='', repr=False)
    "Why the terminals were connected"

    def __str__(self) -> str:
        return f'{self.a.qualified_name} <-> {self.b.qualified_name} ({self.reason})'


@define(eq=False)
class BusSystem:
    """ A set of FMU instances and the connections of their Bus Terminals """

    components: list[BusComponent] = field(factory=list)
    connections: list[BusConnection] = field(factory=list)

    unconnected_terminals: list[BusTerminal] = field(factory=list, repr=False)
    "Bus Terminals that are not part of a connection"

    warnings: list[str] = field(factory=list, repr=False)

    def component(self, name: str) -> BusComponent:
        """ Get an FMU instance by name """
        for component in self.components:
            if component.name == name:
                return component
        raise KeyError(f'The bus system has no FMU instance "{name}".')

    def __str__(self) -> str:

        lines = ['FMUs']

        for component in self.components:
            terminals = ', '.join(f'{t.name} [{t.bus_type}]' for t in component.bus_terminals)
            lines.append(f'  {component.name} ({component.role}){": " + terminals if terminals else ""}')
            for note in component.notes:
                lines.append(f'    note: {note}')

        lines.append('Connections')

        for connection in self.connections:
            lines.append(f'  {connection}')

        if not self.connections:
            lines.append('  none')

        if self.unconnected_terminals:
            lines.append('Unconnected Bus Terminals')
            for terminal in self.unconnected_terminals:
                lines.append(f'  {terminal.qualified_name}')

        if self.warnings:
            lines.append('Warnings')
            for warning in self.warnings:
                lines.append(f'  {warning}')

        return '\n'.join(lines)


def _parse_mime_type(mime_type: str | None) -> tuple[str | None, str | None]:
    """ Get the bus type and version from the MIME type of a data variable """

    if mime_type is None:
        return None, None

    essence, _, parameters = mime_type.partition(';')
    essence = essence.strip().lower()

    if not essence.startswith(MIME_TYPE_PREFIX):
        return None, None

    bus_type = essence[len(MIME_TYPE_PREFIX):]

    match = re.search(r'version\s*=\s*"?([^";\s]+)"?', parameters)

    return bus_type, match.group(1) if match else None


def _major_version(version: str | None) -> str | None:
    return None if version is None else version.split('.')[0]


def _resolve_bus_terminal(component: BusComponent, terminal: Terminal) -> BusTerminal:
    """ Resolve and check the member variables of a Bus Terminal """

    variables = dict((v.name, v) for v in component.model_description.modelVariables)

    def problem(message: str) -> BusTopologyError:
        return BusTopologyError(f'Bus Terminal "{terminal.name}" of FMU "{component.name}" is not a valid Bus Terminal '
                                f'of the FMI-LS-BUS Network Abstraction: {message}')

    def resolve(t: Terminal) -> dict[str, ModelVariable]:
        members = {}
        for member in t.memberVariables:
            if member.variableName not in variables:
                raise problem(f'the member variable "{member.variableName}" is not defined in the modelDescription.xml.')
            members[member.memberName] = variables[member.variableName]
        return members

    members = resolve(terminal)

    missing = [name for name in MEMBER_NAMES if name not in members]

    if missing:
        raise problem(f"the member(s) {', '.join(missing)} are missing.")

    rx_data, rx_clock, tx_data, tx_clock = [members[name] for name in MEMBER_NAMES]

    for name, variable, type, causalities in [
        ('Rx_Data', rx_data, 'Binary', {'input'}),
        ('Rx_Clock', rx_clock, 'Clock', {'input'}),
        ('Tx_Data', tx_data, 'Binary', {'output'}),
        ('Tx_Clock', tx_clock, 'Clock', {'input', 'output'}),
    ]:
        if variable.type != type:
            raise problem(f'{name} ("{variable.name}") must be of type {type} but is of type {variable.type}.')
        if variable.causality not in causalities:
            raise problem(f'{name} ("{variable.name}") must have causality {" or ".join(sorted(causalities))} '
                          f'but has causality "{variable.causality}".')

    rx_bus_type, rx_version = _parse_mime_type(rx_data.mimeType)
    tx_bus_type, tx_version = _parse_mime_type(tx_data.mimeType)

    if rx_bus_type is None or tx_bus_type is None:
        raise problem(f'the MIME types of Rx_Data ("{rx_data.mimeType}") and Tx_Data ("{tx_data.mimeType}") '
                      f'must start with "{MIME_TYPE_PREFIX}".')

    if rx_bus_type != tx_bus_type:
        raise problem(f'Rx_Data and Tx_Data have different bus types ("{rx_bus_type}" and "{tx_bus_type}").')

    bus_terminal = BusTerminal(
        component=component,
        name=terminal.name,
        rx_data=rx_data,
        rx_clock=rx_clock,
        tx_data=tx_data,
        tx_clock=tx_clock,
        bus_type=rx_bus_type,
        version=tx_version or rx_version,
        terminal=terminal,
    )

    for nested in terminal.terminals:
        if nested.terminalKind == CONFIGURATION_TERMINAL_KIND:
            bus_terminal.configuration.update(resolve(nested))

    return bus_terminal


def read_bus_component(name: str, filename: str | PathLike, validate: bool = True,
                       model_description: ModelDescription | None = None) -> BusComponent:
    """ Read the FMI-LS-BUS relevant information of an FMU

    Parameters:
        name               instance name of the FMU
        filename           filename of the FMU or directory with the extracted FMU
        validate           validate the modelDescription.xml and terminalsAndIcons.xml
        model_description  the model description (if it has already been read)

    returns:
        a BusComponent with the role of the FMU and its Bus Terminals
    """

    if model_description is None:
        model_description = read_model_description(filename, validate=validate)

    manifest = read_ls_bus_manifest(filename)

    component = BusComponent(
        name=name,
        filename=filename,
        model_description=model_description,
        manifest=manifest,
        is_bus_simulation_fmu=manifest is not None and manifest.is_bus_simulation_fmu,
    )

    terminals = read_terminals_and_icons(filename, validate=validate)

    for terminal in terminals:

        if terminal.terminalKind != NETWORK_TERMINAL_KIND:
            continue  # not a Bus Terminal

        if terminal.matchingRule != TRANSCEIVER_MATCHING_RULE:
            component.notes.append(f'Bus Terminal "{terminal.name}" (matchingRule="{terminal.matchingRule}") is not a '
                                   f'terminal of the Network Abstraction and was skipped.')
            continue

        component.bus_terminals.append(_resolve_bus_terminal(component, terminal))

    if component.bus_terminals:
        if manifest is None:
            component.notes.append('The FMU has Bus Terminals but no FMI-LS-BUS manifest. It is treated as a Network FMU.')
        else:
            for p in manifest.problems:
                component.notes.append(f'FMI-LS-BUS manifest: {p}')

    return component


def _unique_names(filenames: Iterable[str | PathLike]) -> dict[str, str | PathLike]:
    """ Derive unique instance names from the filenames of the FMUs """

    fmus = {}

    for filename in filenames:

        stem, _ = os.path.splitext(os.path.basename(os.path.normpath(str(filename))))

        name = stem
        i = 2

        while name in fmus:
            name = f'{stem}_{i}'
            i += 1

        fmus[name] = filename

    return fmus


def _find_terminal(components: list[BusComponent], specifier: str | tuple[str, str]) -> BusTerminal:
    """ Find a Bus Terminal by "<instance name>.<terminal name>" or (<instance name>, <terminal name>) """

    candidates = []

    if isinstance(specifier, str):
        for component in components:
            if specifier.startswith(component.name + '.'):
                candidates.append((component, specifier[len(component.name) + 1:]))
    else:
        instance_name, terminal_name = specifier
        candidates = [(component, terminal_name) for component in components if component.name == instance_name]

    for component, terminal_name in candidates:
        for terminal in component.bus_terminals:
            if terminal.name == terminal_name:
                return terminal

    available = ', '.join(t.qualified_name for c in components for t in c.bus_terminals)

    raise BusTopologyError(f'Cannot find the Bus Terminal "{specifier}". Available Bus Terminals: {available or "none"}.')


def _check_connection(connection: BusConnection, warnings: list[str]) -> None:
    """ Check that the two terminals of a connection are compatible """

    a, b = connection.a, connection.b

    if a.bus_type != b.bus_type:
        raise BusTopologyError(f'Cannot connect {a.qualified_name} and {b.qualified_name} because their bus types '
                               f'differ ("{a.bus_type}" and "{b.bus_type}").')

    if a.version != b.version:
        if _major_version(a.version) != _major_version(b.version):
            raise BusTopologyError(f'Cannot connect {a.qualified_name} and {b.qualified_name} because the major versions '
                                   f'of their MIME types differ ("{a.version}" and "{b.version}").')
        warnings.append(f'{a.qualified_name} and {b.qualified_name} use different versions of the bus MIME type '
                        f'("{a.version}" and "{b.version}").')


def connect_bus_system(
        fmus: Mapping[str, str | PathLike] | Sequence[str | PathLike],
        connections: Iterable[tuple[str | tuple[str, str], str | tuple[str, str]]] | None = None,
        bus_simulation: str | Iterable[str] | None = None,
        validate: bool = True,
        allow_unconnected: bool = False,
        model_descriptions: Mapping[str, ModelDescription] | None = None) -> BusSystem:
    """ Discover the Bus Terminals of a set of FMUs and connect them automatically

    The Bus Terminals of the Network FMUs (e.g. virtual ECUs) are connected to the
    Bus Terminals of the Bus Simulation FMU based on the terminal names:

    1. the connections given explicitly are created first
    2. a Bus Terminal "X" of a Network FMU is connected to the first free Bus Terminal of a
       Bus Simulation FMU with the same name "X"
    3. if no such terminal is available, it is connected to the first free Bus Terminal of a
       Bus Simulation FMU whose name starts with "X" followed by a separator ("_", "." or "-"),
       e.g. "X_b" (a further channel of the same network)

    Network FMUs are processed in the order in which the FMUs are given.

    Without a Bus Simulation FMU, two Bus Terminals with the same name are connected directly
    (the layered standard limits direct communication to exactly two Network FMUs).

    Parameters:
        fmus                {instance name: FMU} or list of FMUs (filenames or directories with extracted
                            FMUs). For a list the instance names are derived from the filenames.
        connections         explicit connections as pairs of "<instance name>.<terminal name>"
        bus_simulation      instance name(s) of the Bus Simulation FMU(s). If None, the Bus Simulation FMUs
                            are identified by the attribute isBusSimulationFMU of the FMI-LS-BUS manifest.
        validate            validate the modelDescription.xml and terminalsAndIcons.xml
        allow_unconnected   don't raise an exception if a Bus Terminal of a Network FMU cannot be connected
        model_descriptions  {instance name: ModelDescription} for model descriptions that have already been read

    returns:
        a BusSystem with the FMU instances and the connections of their Bus Terminals
    """

    if not isinstance(fmus, Mapping):
        fmus = _unique_names(fmus)

    model_descriptions = model_descriptions or {}

    system = BusSystem()

    for name, filename in fmus.items():
        if '.' in name:
            raise BusTopologyError(f'The instance name "{name}" must not contain a dot.')
        system.components.append(read_bus_component(name, filename, validate=validate,
                                                    model_description=model_descriptions.get(name)))

    components = system.components

    # roles
    if bus_simulation is not None:

        names = {bus_simulation} if isinstance(bus_simulation, str) else set(bus_simulation)

        for name in names:
            if not system.component(name).bus_terminals:
                raise BusTopologyError(f'The FMU "{name}" cannot be used as Bus Simulation FMU because it has no Bus '
                                       f'Terminals of the Network Abstraction.')

        for component in components:
            component.is_bus_simulation_fmu = component.name in names

    used: set[BusTerminal] = set()

    def connect(a: BusTerminal, b: BusTerminal, reason: str) -> None:

        for terminal in (a, b):
            if terminal in used:
                raise BusTopologyError(f'The Bus Terminal {terminal.qualified_name} is used in more than one connection.')

        if a is b:
            raise BusTopologyError(f'The Bus Terminal {a.qualified_name} cannot be connected to itself.')

        if a.component.is_bus_simulation_fmu and b.component.is_bus_simulation_fmu:
            raise BusTopologyError(f'Cannot connect {a.qualified_name} and {b.qualified_name} because both belong to a '
                                   f'Bus Simulation FMU.')

        if a.component.is_bus_simulation_fmu:
            a, b = b, a  # the terminal of the Network FMU comes first

        connection = BusConnection(a=a, b=b, kind='bus-simulation' if b.component.is_bus_simulation_fmu else 'direct',
                                   reason=reason)

        _check_connection(connection, system.warnings)

        used.update((a, b))

        system.connections.append(connection)

    # 1. explicit connections
    for a, b in connections or []:
        connect(_find_terminal(components, a), _find_terminal(components, b), 'explicit')

    node_terminals = [t for c in components if not c.is_bus_simulation_fmu for t in c.bus_terminals]
    bus_simulation_terminals = [t for c in components if c.is_bus_simulation_fmu for t in c.bus_terminals]

    unmatched = []

    if bus_simulation_terminals:

        # 2. connect the Network FMUs to the Bus Simulation FMU(s)
        for terminal in node_terminals:

            if terminal in used:
                continue

            free = [t for t in bus_simulation_terminals if t not in used and t.bus_type == terminal.bus_type]

            same_name = [t for t in free if t.name == terminal.name]

            channels = [t for t in free if any(t.name.startswith(terminal.name + s) for s in CHANNEL_SEPARATORS)]

            if same_name:
                connect(terminal, same_name[0], 'same terminal name')
            elif channels:
                connect(terminal, channels[0], f'next free channel of network "{terminal.name}"')
            else:
                unmatched.append(terminal)

    else:

        # 3. direct connection of two Network FMUs
        by_name: dict[str, list[BusTerminal]] = {}

        for terminal in node_terminals:
            if terminal not in used:
                by_name.setdefault(terminal.name, []).append(terminal)

        for name, terminals in by_name.items():
            if len(terminals) == 2 and terminals[0].component is not terminals[1].component:
                connect(terminals[0], terminals[1], 'same terminal name, direct connection without Bus Simulation FMU')
            elif len(terminals) > 2:
                raise BusTopologyError(
                    f'{len(terminals)} Bus Terminals are named "{name}" ({", ".join(t.qualified_name for t in terminals)}) '
                    f'but there is no Bus Simulation FMU. A direct connection is limited to exactly two Network FMUs. '
                    f'Add a Bus Simulation FMU or connect the terminals explicitly.')
            else:
                unmatched += terminals

    system.unconnected_terminals = [t for c in components for t in c.bus_terminals if t not in used]

    if unmatched and not allow_unconnected:

        free = [t.qualified_name for t in bus_simulation_terminals if t not in used]

        if bus_simulation_terminals:
            hint = (f'Free Bus Terminals of the Bus Simulation FMU(s): {", ".join(free) or "none"}. A terminal "X" is '
                    f'connected to a free terminal named "X" or "X" followed by "_", "." or "-" and a suffix.')
        else:
            hint = 'There is no Bus Simulation FMU and no second Network FMU with a Bus Terminal of the same name.'

        raise BusTopologyError(
            f'Cannot connect the Bus Terminal(s) {", ".join(t.qualified_name for t in unmatched)}. {hint} '
            f'Use the argument "connections" to connect terminals explicitly or allow_unconnected=True to leave them '
            f'unconnected.')

    for terminal in unmatched:
        system.warnings.append(f'The Bus Terminal {terminal.qualified_name} is not connected.')

    return system
