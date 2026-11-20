""" Support for the FMI Layered Standard for Network Communication (FMI-LS-BUS)

Connect and co-simulate Network FMUs (e.g. virtual ECUs) and a Bus Simulation FMU
that communicate via the Bus Terminals of the Network Abstraction ("low cut"), e.g. for CAN and CAN FD:

    from fmpy.ls_bus import simulate_bus_system

    result = simulate_bus_system(
        fmus={'bus': 'BusSimulation.fmu', 'ecu1': 'ECU1.fmu', 'ecu2': 'ECU2.fmu'},
        stop_time=1.0,
        step_size=1e-3,
    )

    for connection in result.connections:
        print(connection)

    for transfer in result.bus_log:
        print(transfer)
"""

from .manifest import LsBusManifest, read_ls_bus_manifest
from .topology import BusComponent, BusConnection, BusSystem, BusTerminal, BusTopologyError, connect_bus_system, \
    read_bus_component
from .master import BusSimulationResult, BusTransfer, check_bus_system, run_bus_simulation, simulate_bus_system

__all__ = [
    'BusComponent',
    'BusConnection',
    'BusSimulationResult',
    'BusSystem',
    'BusTerminal',
    'BusTopologyError',
    'BusTransfer',
    'LsBusManifest',
    'check_bus_system',
    'connect_bus_system',
    'read_bus_component',
    'read_ls_bus_manifest',
    'run_bus_simulation',
    'simulate_bus_system',
]
