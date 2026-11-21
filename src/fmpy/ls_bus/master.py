""" Co-simulation of FMUs that communicate via FMI-LS-BUS Bus Terminals (Network Abstraction / "low cut") """
from __future__ import annotations

import os
import shutil
from collections import deque
from math import isclose
from os import PathLike
from typing import Any, Callable, Iterable, Mapping, Sequence

from attrs import define, field

from .. import extract
from ..fmi3 import FMU3Slave
from ..simulation import Recorder, SimulationResult, apply_start_values, settable_in_instantiated, \
    settable_in_initialization_mode
from . import can
from .topology import BusComponent, BusConnection, BusSystem, BusTerminal, connect_bus_system


@define
class BusTransfer:
    """ The transfer of the value of a Tx_Data variable to the Rx_Data variable of the connected Bus Terminal """

    time: float
    "Simulation time at which the data was delivered to the receiving Bus Terminal"

    source: str
    "Sending Bus Terminal as <instance name>.<terminal name>"

    destination: str
    "Receiving Bus Terminal as <instance name>.<terminal name>"

    data: bytes = field(repr=False)
    "The transferred Bus Operations"

    bus_type: str | None = field(default='can', repr=False)

    tx_time: float | None = field(default=None, repr=False)
    """Simulation time at which the data was sent. It is earlier than the time of the delivery if the
    receiving FMU has a larger step size and was not at a communication point when the data was sent."""

    @property
    def operations(self) -> list[can.Operation]:
        """ The decoded Bus Operations (empty if the bus type is not supported or the data cannot be decoded) """
        if self.bus_type != 'can':
            return []
        try:
            return can.parse_operations(self.data)
        except ValueError:
            return []

    def __str__(self) -> str:
        operations = '; '.join(map(str, self.operations)) or f'{len(self.data)} bytes'
        sent = '' if self.tx_time is None or isclose(self.tx_time, self.time) else f' (sent at {self.tx_time:.6f} s)'
        return f'{self.time:.6f} s  {self.source} -> {self.destination}{sent}  {operations}'


@define(eq=False)
class BusSimulationResult:
    """ Result of simulate_bus_system() """

    system: BusSystem
    "The FMU instances and the connections of their Bus Terminals"

    results: dict[str, SimulationResult] = field(factory=dict, repr=False)
    "Recorded variables as {instance name: structured NumPy array}"

    bus_log: list[BusTransfer] = field(factory=list, repr=False)
    "The transfers between the Bus Terminals in chronological order"

    time: float = 0.0
    "The last communication point"

    terminated_by: str | None = None
    "Name of the FMU instance that requested to terminate the simulation or None"

    drain_steps: int = 0
    "Number of additional steps of the Bus Simulation FMU(s) performed after the stop time to deliver pending data"

    @property
    def connections(self) -> list[BusConnection]:
        return self.system.connections


@define(eq=False)
class _Instance:

    component: BusComponent
    fmu: Any
    event_mode_used: bool
    step_size: float
    time: float = 0.0  # the current communication point
    n_steps: int = 0
    last_sample_time: float = 0.0
    recorder: Recorder | None = None

    @property
    def name(self) -> str:
        return self.component.name


def check_bus_system(system: BusSystem, strict: bool = True) -> None:
    """ Check that the bus system can be simulated by run_bus_simulation()

    Parameters:
        system   the bus system
        strict   raise an exception if an FMU with Bus Terminals does not declare hasEventMode="true"
                 (otherwise a warning is added to the bus system)
    """

    for component in system.components:

        model_description = component.model_description

        if model_description.fmiMajorVersion != 3 or model_description.coSimulation is None:
            raise Exception(f'The FMU "{component.name}" is not an FMI 3.0 Co-Simulation FMU '
                            f'(FMI {model_description.fmiVersion}).')

        if component.bus_terminals and not model_description.coSimulation.hasEventMode:
            message = (f'The FMU "{component.name}" has Bus Terminals but does not declare hasEventMode="true" which is '
                       f'required by FMI-LS-BUS.')
            if strict:
                raise Exception(message + ' Use strict=False to use Event Mode anyway.')
            system.warnings.append(message + ' Event Mode is used anyway.')

    for connection in system.connections:
        for terminal in (connection.a, connection.b):
            if not terminal.has_triggered_tx_clock:
                clock = terminal.tx_clock
                raise NotImplementedError(
                    f'Tx_Clock "{clock.name}" of Bus Terminal {terminal.qualified_name} has causality="{clock.causality}" '
                    f'and intervalVariability="{clock.intervalVariability}". Only triggered output Clocks are supported '
                    f'by the bus master.')


def _same_time(a: float, b: float) -> bool:
    return isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def run_bus_simulation(
        system: BusSystem,
        fmus: Mapping[str, Any],
        start_time: float = 0.0,
        stop_time: float = 1.0,
        step_size: float = 1e-3,
        start_values: Mapping[str, Mapping[str, Any]] | None = None,
        output: Mapping[str, Sequence[str]] | None = None,
        output_interval: float | None = None,
        relative_tolerance: float | None = None,
        set_stop_time: bool = True,
        event_mode_used: Iterable[str] | None = None,
        max_event_iterations: int = 100,
        record_bus_log: bool = True,
        bus_logger: Callable[[BusTransfer], None] | None = None,
        step_finished: Callable[[float], bool] | None = None,
        drain_steps: int = 0,
        step_sizes: Mapping[str, float] | None = None) -> BusSimulationResult:
    """ Initialize and simulate instantiated FMUs of a bus system

    See simulate_bus_system() for the documentation of the parameters.

    Parameters:
        system            the bus system
        fmus              {instance name: FMU instance} of the instantiated FMUs (fmpy.fmi3.FMU3Slave)
        event_mode_used   names of the instances that have been instantiated with eventModeUsed=True
                          (default: all FMUs with Bus Terminals)
    """

    if step_size <= 0:
        raise ValueError("The step_size must be greater than 0.")

    if drain_steps < 0:
        raise ValueError("The number of drain_steps must not be negative.")

    start_values = dict(start_values or {})
    output = output or {}
    step_sizes = dict(step_sizes or {})

    for argument, names in [('start_values', start_values), ('output', output), ('step_sizes', step_sizes)]:
        for name in names:
            if name not in fmus:
                raise Exception(f'The argument "{argument}" contains the unknown FMU instance "{name}". '
                                f'Available instances: {", ".join(fmus)}.')

    for name, value in step_sizes.items():
        if value <= 0:
            raise ValueError(f'The step size of FMU "{name}" must be greater than 0.')

    if event_mode_used is None:
        event_mode_used = [c.name for c in system.components if c.bus_terminals]

    event_mode_used = set(event_mode_used)

    instances = [_Instance(component=c, fmu=fmus[c.name], event_mode_used=c.name in event_mode_used,
                           step_size=step_sizes.get(c.name, step_size), time=start_time, last_sample_time=start_time)
                 for c in system.components]

    by_name = dict((i.name, i) for i in instances)

    event_instances = [i for i in instances if i.event_mode_used]

    # the Bus Simulation FMUs that are stepped after the stop time to deliver pending data
    drain_instances = [i for i in event_instances if i.component.is_bus_simulation_fmu] if drain_steps > 0 else []

    result = BusSimulationResult(system=system, time=start_time)

    n_transfers = [0]  # number of transfers (in a list, so it can be modified by exchange())

    # directed links (source terminal, destination terminal) in the order they are processed:
    # 1. Network FMUs -> Bus Simulation FMU (or other Network FMU), 2. Bus Simulation FMU -> Network FMUs
    links = [(c.a, c.b) for c in system.connections] + [(c.b, c.a) for c in system.connections]

    phases = [
        [(src, dst) for src, dst in links if not src.component.is_bus_simulation_fmu],
        [(src, dst) for src, dst in links if src.component.is_bus_simulation_fmu],
    ]

    # Data that has been sent to a Bus Terminal but not delivered yet as (time, source terminal, data).
    # An FMU only exchanges data at its own communication points. If the FMUs have different step sizes,
    # the data for an FMU that is not at a communication point is delivered at its next communication point.
    queues: dict[BusTerminal, deque] = dict((dst, deque()) for _, dst in links)

    def pending(active: Iterable[_Instance]) -> int:
        """ Number of undelivered transfers for the given FMUs """
        return sum(len(queues[t]) for i in active for t in i.component.bus_terminals if t in queues)

    def exchange(time: float, active: set[str]) -> bool:
        """ Transfer the data between the FMUs with the given names and return True if data has been delivered """

        delivered = False

        for phase in phases:

            # get the values of all senders before setting the receivers
            for src, dst in phase:

                if src.component.name not in active:
                    continue

                fmu = by_name[src.component.name].fmu

                clock_active, = fmu.getClock([src.tx_clock.valueReference])

                if not clock_active:
                    continue

                data, = fmu.getBinary([src.tx_data.valueReference])

                if data:  # the size is zero if there is nothing to transmit
                    queues[dst].append((time, src, bytes(data)))

            # deliver one value per Bus Terminal and super-dense time instant
            for _, dst in phase:

                if dst.component.name not in active or not queues[dst]:
                    continue

                tx_time, src, data = queues[dst].popleft()

                fmu = by_name[dst.component.name].fmu

                # the Clock has to be activated before the clocked variable is set
                fmu.setClock([dst.rx_clock.valueReference], [True])
                fmu.setBinary([dst.rx_data.valueReference], [data])

                delivered = True
                n_transfers[0] += 1

                if record_bus_log or bus_logger is not None:

                    transfer = BusTransfer(time=time, source=src.qualified_name, destination=dst.qualified_name,
                                           data=data, bus_type=src.bus_type, tx_time=tx_time)

                    if record_bus_log:
                        result.bus_log.append(transfer)

                    if bus_logger is not None:
                        bus_logger(transfer)

        return delivered

    def handle_events(time: float, active: list[_Instance]) -> bool:
        """ Iterate in Event Mode until no more data is transferred between the given FMUs

        Returns False if an FMU requested to terminate the simulation.
        """

        names = set(i.name for i in active)

        # delivering the data that is already pending requires one iteration per value
        limit = max_event_iterations + pending(active)

        for _ in range(limit):

            delivered = exchange(time, names)

            update_discrete_states = False

            for instance in active:

                needs_update, terminate_simulation, _, _, _, _ = instance.fmu.updateDiscreteStates()

                if terminate_simulation:
                    result.terminated_by = instance.name
                    return False

                update_discrete_states |= bool(needs_update)

            if not delivered and not update_discrete_states:
                return True

        raise Exception(f"The event iteration at t={time} did not converge after {limit} iterations. "
                        f"The FMUs keep exchanging bus data without advancing the time.")

    def event_mode(time: float, active: list[_Instance]) -> bool:
        """ Handle the events of the given FMUs in Event Mode and return False if an FMU requested to terminate """

        for instance in active:
            instance.fmu.enterEventMode()

        if not handle_events(time, active):
            return False

        for instance in active:
            instance.fmu.enterStepMode()

        return True

    def do_step(instance: _Instance, next_time: float) -> tuple[bool, bool]:
        """ Advance an FMU to its next communication point and return (eventHandlingNeeded, terminateSimulation) """

        event, terminate_simulation, early_return, _ = instance.fmu.doStep(
            currentCommunicationPoint=instance.time, communicationStepSize=next_time - instance.time)

        if early_return:
            raise Exception(f'The FMU "{instance.name}" returned early from doStep() but early return is not allowed.')

        instance.time = next_time
        instance.n_steps += 1

        if terminate_simulation:
            result.terminated_by = instance.name

        return bool(event), bool(terminate_simulation)

    # initialization
    remaining = {}

    for instance in instances:
        remaining[instance.name] = apply_start_values(
            instance.fmu, instance.component.model_description, dict(start_values.get(instance.name, {})),
            settable=settable_in_instantiated)

    for instance in instances:

        instance_stop_time = stop_time

        if instance in drain_instances:
            instance_stop_time += drain_steps * instance.step_size  # the FMU is stepped beyond the stop time

        instance.fmu.enterInitializationMode(tolerance=relative_tolerance, startTime=start_time,
                                             stopTime=instance_stop_time if set_stop_time else None)

    for instance in instances:
        remaining[instance.name] = apply_start_values(
            instance.fmu, instance.component.model_description, remaining[instance.name],
            settable=settable_in_initialization_mode)

    for name, values in remaining.items():
        if values:
            raise Exception(f'The start values for the following variables of FMU "{name}" could not be set: '
                            f'{", ".join(values)}.')

    # tell the Network FMUs if the Bus Simulation FMU provides bus notifications
    for connection in system.connections:

        node, bus = connection.a.bus_notifications, connection.b.bus_notifications

        if connection.kind != 'bus-simulation' or node is None or bus is None:
            continue

        if node.causality != 'parameter' or node.name in start_values.get(connection.a.component.name, {}):
            continue  # not settable or set by the user

        value, = by_name[connection.b.component.name].fmu.getBoolean([bus.valueReference])

        by_name[connection.a.component.name].fmu.setBoolean([node.valueReference], [bool(value)])

    for instance in instances:
        instance.fmu.exitInitializationMode()

    time = start_time

    # the FMUs that use Event Mode are now in Event Mode
    running = handle_events(time, event_instances)

    for instance in event_instances:
        instance.fmu.enterStepMode()

    for instance in instances:
        instance.recorder = Recorder(fmu=instance.fmu, modelDescription=instance.component.model_description,
                                     variableNames=output.get(instance.name), interval=output_interval)
        instance.recorder.sample(time, force=True)

    # simulation loop
    while running:

        # the next communication points of the FMUs that do not exceed the stop time
        due = []

        for instance in instances:

            next_time = start_time + (instance.n_steps + 1) * instance.step_size

            if next_time < stop_time or _same_time(next_time, stop_time):
                due.append((next_time, instance))

        if not due:
            break

        # advance the FMUs whose next communication point is the earliest
        time = min(next_time for next_time, _ in due)

        stepped = [instance for next_time, instance in due if _same_time(next_time, time)]

        event_handling_needed = False

        for instance in stepped:

            event, terminate_simulation = do_step(instance, time)

            if terminate_simulation:
                running = False

            if event and instance.event_mode_used:
                event_handling_needed = True

        # only the FMUs that are at a communication point exchange data
        active = [instance for instance in stepped if instance.event_mode_used]

        if running and (event_handling_needed or pending(active)):
            running = event_mode(time, active)

        for instance in stepped:

            elapsed = time - instance.last_sample_time

            if output_interval is None or elapsed > output_interval or isclose(elapsed, output_interval):
                instance.recorder.sample(time)
                instance.last_sample_time = time

        if step_finished is not None and not step_finished(time):
            running = False

    result.time = time

    # Deliver the data that could not be delivered before the stop time.
    if running and drain_steps > 0 and pending(event_instances):
        # data for FMUs that did not reach another communication point after it was sent
        running = event_mode(time, event_instances)

    # Deliver the data that is still pending in the Bus Simulation FMU(s) (e.g. the frames transmitted in the
    # last step). Only the Bus Simulation FMUs are stepped, so the Network FMUs do not transmit new data.
    while running and drain_instances and result.drain_steps < drain_steps:

        event_handling_needed = False

        for instance in drain_instances:

            event, terminate_simulation = do_step(instance, instance.time + instance.step_size)

            if terminate_simulation:
                running = False

            event_handling_needed |= event

        result.drain_steps += 1

        if not running or not event_handling_needed:
            break  # nothing is pending anymore

        transfers_before = n_transfers[0]

        running = event_mode(max(instance.time for instance in drain_instances), event_instances)

        if n_transfers[0] == transfers_before:
            break  # nothing has been delivered in this step

    for instance in instances:
        result.results[instance.name] = instance.recorder.result()

    return result


def simulate_bus_system(
        fmus: Mapping[str, str | PathLike] | Sequence[str | PathLike],
        connections: Iterable[tuple[str | tuple[str, str], str | tuple[str, str]]] | None = None,
        bus_simulation: str | Iterable[str] | None = None,
        start_time: float = 0.0,
        stop_time: float = 1.0,
        step_size: float = 1e-3,
        start_values: Mapping[str, Mapping[str, Any]] | None = None,
        output: Mapping[str, Sequence[str]] | None = None,
        output_interval: float | None = None,
        relative_tolerance: float | None = None,
        validate: bool = True,
        strict: bool = True,
        allow_unconnected: bool = False,
        debug_logging: bool | Iterable[str] = False,
        logger: Callable | None = None,
        fmi_call_logger: Callable[[str], None] | None = None,
        set_stop_time: bool = True,
        max_event_iterations: int = 100,
        record_bus_log: bool = True,
        bus_logger: Callable[[BusTransfer], None] | None = None,
        step_finished: Callable[[float], bool] | None = None,
        drain_steps: int = 0,
        step_sizes: Mapping[str, float] | None = None) -> BusSimulationResult:
    """ Connect and co-simulate FMUs that communicate via FMI-LS-BUS Bus Terminals

    The Bus Terminals of the Network FMUs (e.g. virtual ECUs) are connected automatically
    to the Bus Terminals of the Bus Simulation FMU based on the terminal names (see connect_bus_system()).
    FMI 3.0 Co-Simulation FMUs without Bus Terminals are simulated alongside.

    In every communication step all FMUs perform a doStep(). If an FMU signals that event handling is
    needed, all FMUs with Bus Terminals enter Event Mode and the values of the Tx_Data variables whose
    Tx_Clock is active are transferred to the Rx_Data variables of the connected Bus Terminals until no
    more data is pending.

    The FMUs can have different communication step sizes (see step_sizes). Every FMU then has its own
    communication points and only exchanges data at these points: data that is sent to an FMU between
    two of its communication points is kept and delivered in the order it was sent when the FMU reaches
    its next communication point.

    Parameters:
        fmus                  {instance name: FMU} or list of FMUs (filenames or directories with extracted
                              FMUs). For a list the instance names are derived from the filenames.
        connections           explicit connections as pairs of "<instance name>.<terminal name>" (optional)
        bus_simulation        instance name(s) of the Bus Simulation FMU(s). If None, the Bus Simulation FMUs
                              are identified by the attribute isBusSimulationFMU of the FMI-LS-BUS manifest.
        start_time            simulation start time
        stop_time             simulation stop time
        step_size             communication step size
        step_sizes            communication step sizes of individual FMUs as {instance name: step size}.
                              FMUs that are not listed use step_size.
        start_values          start values as {instance name: {variable name: value}}
        output                variables to record as {instance name: [variable name]} (default: the outputs)
        output_interval       interval for sampling the recorded variables (default: every communication step)
        relative_tolerance    relative tolerance passed to the FMUs (optional)
        validate              validate the modelDescription.xml and terminalsAndIcons.xml of the FMUs
        strict                raise an exception if an FMU with Bus Terminals does not declare hasEventMode="true"
        allow_unconnected     don't raise an exception if a Bus Terminal of a Network FMU cannot be connected
        debug_logging         enable the debug logging for all FMUs (True) or the given instance names
        logger                callback function to pass to the FMUs for the log messages
        fmi_call_logger       callback function that is called with a message for every FMI call
        set_stop_time         pass the stop time to the FMUs
        max_event_iterations  maximum number of iterations in Event Mode at a communication point
        record_bus_log        record the transfers between the Bus Terminals in the result
        bus_logger            callback function that is called with a BusTransfer for every transfer
        step_finished         callback function that is called with the time after every communication step.
                              The simulation is stopped if it returns False.
        drain_steps           maximum number of additional steps after the stop time in which only the Bus
                              Simulation FMU(s) are stepped to deliver the data that is still pending, e.g. the
                              frames transmitted in the last step. The Network FMUs are not stepped, so they do
                              not transmit new data. Draining ends as soon as a step delivers no data. The stop
                              time passed to the Bus Simulation FMU(s) is extended accordingly. The number of
                              performed steps is returned in BusSimulationResult.drain_steps.

    returns:
        a BusSimulationResult with the connections, the recorded variables and the bus log
    """

    system = connect_bus_system(fmus=fmus, connections=connections, bus_simulation=bus_simulation,
                                validate=validate, allow_unconnected=allow_unconnected)

    check_bus_system(system, strict=strict)

    if isinstance(debug_logging, bool):
        debug_instances = set(c.name for c in system.components) if debug_logging else set()
    else:
        debug_instances = set(debug_logging)

    unzipdirs = []  # temporary directories to remove
    instances = {}
    event_mode_used = set()
    instantiated = False

    try:
        for component in system.components:

            if os.path.isdir(component.filename):
                unzipdir = component.filename
            else:
                # every instance gets its own directory, so the same FMU can be instantiated multiple times
                unzipdir = extract(component.filename)
                unzipdirs.append(unzipdir)

            if fmi_call_logger is None:
                call_logger = None
            else:
                def call_logger(message, name=component.name):
                    fmi_call_logger(f'[{name}] {message}')

            fmu = FMU3Slave(
                guid=component.model_description.guid,
                unzipDirectory=unzipdir,
                modelIdentifier=component.model_description.coSimulation.modelIdentifier,
                instanceName=component.name,
                fmiCallLogger=call_logger,
            )

            use_event_mode = bool(component.bus_terminals)

            fmu.instantiate(visible=False, loggingOn=component.name in debug_instances, eventModeUsed=use_event_mode,
                            earlyReturnAllowed=False, logMessage=logger)

            instances[component.name] = fmu

            if use_event_mode:
                event_mode_used.add(component.name)

        instantiated = True

        return run_bus_simulation(
            system=system,
            fmus=instances,
            start_time=start_time,
            stop_time=stop_time,
            step_size=step_size,
            start_values=start_values,
            output=output,
            output_interval=output_interval,
            relative_tolerance=relative_tolerance,
            set_stop_time=set_stop_time,
            event_mode_used=event_mode_used,
            max_event_iterations=max_event_iterations,
            record_bus_log=record_bus_log,
            bus_logger=bus_logger,
            step_finished=step_finished,
            drain_steps=drain_steps,
            step_sizes=step_sizes,
        )

    finally:

        for fmu in instances.values():

            if instantiated:
                try:
                    fmu.terminate()
                except Exception:
                    pass  # the FMU might not have been initialized or is in an error state

            try:
                fmu.freeInstance()
            except Exception:
                pass

        for unzipdir in unzipdirs:
            shutil.rmtree(unzipdir, ignore_errors=True)
