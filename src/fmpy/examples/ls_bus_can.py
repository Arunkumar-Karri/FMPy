""" This example demonstrates how to connect and co-simulate FMUs that communicate via CAN or CAN FD
 using the FMI Layered Standard for Network Communication (FMI-LS-BUS).

 The Bus Terminals of the Network FMUs (e.g. virtual ECUs) are connected automatically
 to the Bus Terminals of the Bus Simulation FMU based on the names of the terminals.

 Usage:

   python -m fmpy.examples.ls_bus_can BusSimulation.fmu ECU1.fmu ECU2.fmu --stop-time 1 --step-size 1e-3
 """

from fmpy.ls_bus import connect_bus_system, simulate_bus_system


def simulate_can_bus(fmus, stop_time=1.0, step_size=1e-3, connections=None, start_values=None, show_bus_log=True,
                     drain_steps=0, step_sizes=None):
    """ Connect and simulate Network FMUs and a Bus Simulation FMU

    Parameters:
        fmus          {instance name: filename} or list of filenames of the FMUs
        stop_time     simulation stop time
        step_size     communication step size
        connections   explicit connections as pairs of "<instance name>.<terminal name>" (optional)
        start_values  start values as {instance name: {variable name: value}} (optional)
        show_bus_log  print the Bus Operations that are exchanged between the FMUs
        drain_steps   maximum number of steps of the Bus Simulation FMU after the stop time to deliver pending data
        step_sizes    communication step sizes of individual FMUs as {instance name: step size} (optional)

    Returns:
        a BusSimulationResult
    """

    # discover the Bus Terminals and connect them (without simulating) to review the connections
    system = connect_bus_system(fmus, connections=connections)

    print(system)

    # callback that is called whenever the data of a Bus Terminal is transferred
    def print_transfer(transfer):
        print(transfer)

    result = simulate_bus_system(
        fmus,
        connections=connections,
        start_values=start_values,
        stop_time=stop_time,
        step_size=step_size,
        bus_logger=print_transfer if show_bus_log else None,
        drain_steps=drain_steps,
        step_sizes=step_sizes,
    )

    # count the operations received by each Bus Terminal
    received = {}

    for transfer in result.bus_log:
        for operation in transfer.operations:
            key = (transfer.destination, operation.name)
            received[key] = received.get(key, 0) + 1

    print()
    print("Received Bus Operations")

    for (terminal, operation), count in sorted(received.items()):
        print(f"  {terminal:30} {operation:16} {count}")

    return result


if __name__ == '__main__':

    import argparse

    parser = argparse.ArgumentParser(description="Connect and simulate FMI-LS-BUS FMUs")

    parser.add_argument('fmus', nargs='+', help="filenames of the Bus Simulation FMU and the Network FMUs")
    parser.add_argument('--stop-time', type=float, default=1.0, help="simulation stop time")
    parser.add_argument('--step-size', type=float, default=1e-3, help="communication step size")
    parser.add_argument('--connect', action='append', default=[], metavar='INSTANCE.TERMINAL=INSTANCE.TERMINAL',
                        help="connect two Bus Terminals explicitly")
    parser.add_argument('--fmu-step-size', action='append', default=[], metavar='INSTANCE=STEP_SIZE',
                        help="communication step size of an individual FMU (the instance name is the filename without extension)")
    parser.add_argument('--drain-steps', type=int, default=0,
                        help="maximum number of steps of the Bus Simulation FMU after the stop time to deliver pending data")
    parser.add_argument('--hide-bus-log', action='store_true', help="don't print the exchanged Bus Operations")

    args = parser.parse_args()

    simulate_can_bus(
        fmus=args.fmus,
        stop_time=args.stop_time,
        step_size=args.step_size,
        connections=[tuple(c.split('=', 1)) for c in args.connect],
        show_bus_log=not args.hide_bus_log,
        drain_steps=args.drain_steps,
        step_sizes=dict((name, float(value)) for name, value in (s.split('=', 1) for s in args.fmu_step_size)),
    )
