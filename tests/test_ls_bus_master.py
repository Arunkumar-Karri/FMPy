import os

import pytest

from fmpy.ls_bus import can, check_bus_system, connect_bus_system, run_bus_simulation, simulate_bus_system

from ls_bus_utils import FakeBusSimulation, FakeFMU, FakeNode, create_bus_fmu


FRAME_A = can.create_can_transmit(id=0x100, data=bytes([1, 2, 0, 4]))
FRAME_B = can.create_can_fd_transmit(id=0x200, data=bytes(16), brs=True)


@pytest.fixture
def bus_system(tmp_path):
    """ A Bus Simulation FMU and two Network FMUs whose Bus Terminals have the same name """
    return connect_bus_system({
        'bus': create_bus_fmu(tmp_path / 'bus.fmu', terminals=['canbus1', 'canbus1_b'], is_bus_simulation_fmu=True),
        'ecu1': create_bus_fmu(tmp_path / 'ecu1.fmu'),
        'ecu2': create_bus_fmu(tmp_path / 'ecu2.fmu'),
    })


@pytest.fixture
def direct_system(tmp_path):
    """ Two directly connected Network FMUs """
    return connect_bus_system({
        'ecu1': create_bus_fmu(tmp_path / 'ecu1.fmu'),
        'ecu2': create_bus_fmu(tmp_path / 'ecu2.fmu'),
    })


def transfers(result):
    return [(t.time, t.source, t.destination, t.data) for t in result.bus_log]


def test_bus_simulation(bus_system):

    calls = []

    bus = FakeBusSimulation('bus', calls=calls)
    ecu1 = FakeNode('ecu1', schedule={2: FRAME_A}, calls=calls)
    ecu2 = FakeNode('ecu2', schedule={2: FRAME_B, 4: FRAME_B}, calls=calls)

    fmus = {'bus': bus, 'ecu1': ecu1, 'ecu2': ecu2}

    result = run_bus_simulation(bus_system, fmus, stop_time=0.5, step_size=0.1)

    for fmu in fmus.values():
        assert fmu.errors == []
        assert fmu.state == 'step'

    assert result.time == pytest.approx(0.5)
    assert result.terminated_by is None
    assert result.connections == bus_system.connections

    confirm_a = can.create_confirm(0x100)
    confirm_b = can.create_confirm(0x200)

    assert transfers(result) == [
        # 1. Network FMUs -> Bus Simulation FMU
        (pytest.approx(0.2), 'ecu1.canbus1', 'bus.canbus1', FRAME_A),
        (pytest.approx(0.2), 'ecu2.canbus1', 'bus.canbus1_b', FRAME_B),
        # 2. Bus Simulation FMU -> Network FMUs (in the next super-dense time instant)
        (pytest.approx(0.2), 'bus.canbus1', 'ecu1.canbus1', confirm_a + FRAME_B),
        (pytest.approx(0.2), 'bus.canbus1_b', 'ecu2.canbus1', FRAME_A + confirm_b),
        (pytest.approx(0.4), 'ecu2.canbus1', 'bus.canbus1_b', FRAME_B),
        (pytest.approx(0.4), 'bus.canbus1', 'ecu1.canbus1', FRAME_B),
        (pytest.approx(0.4), 'bus.canbus1_b', 'ecu2.canbus1', confirm_b),
    ]

    # the data is transferred unchanged (including zero bytes)
    assert ecu1.received[1000] == [confirm_a + FRAME_B, FRAME_B]
    assert ecu2.received[1000] == [FRAME_A + confirm_b, confirm_b]
    assert bus.received[1000] == [FRAME_A]
    assert bus.received[2000] == [FRAME_B, FRAME_B]

    # the bus log decodes the operations
    assert [o.name for o in result.bus_log[2].operations] == ['Confirm', 'CAN FD Transmit']
    assert str(result.bus_log[0]) == ('0.200000 s  ecu1.canbus1 -> bus.canbus1  '
                                      'CAN Transmit(id=0x100, ide=False, rtr=False, data=[01 02 00 04])')

    # all FMUs perform every step and are sampled at every communication point
    assert [c[2] for c in calls if c[:2] == ('ecu1', 'doStep')] == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])
    assert list(result.results['ecu1']['time']) == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    assert set(result.results) == {'bus', 'ecu1', 'ecu2'}


def test_call_sequence(bus_system):

    calls = []

    fmus = {
        'bus': FakeBusSimulation('bus', calls=calls),
        'ecu1': FakeNode('ecu1', schedule={1: FRAME_A}, calls=calls),
        'ecu2': FakeNode('ecu2', calls=calls),
    }

    run_bus_simulation(bus_system, fmus, stop_time=0.1, step_size=0.1, start_time=0.0)

    names = ['bus', 'ecu1', 'ecu2']

    def all_fmus(function, *args):
        return [(name, function) + args for name in names]

    initialization = (
        all_fmus('enterInitializationMode')
        + all_fmus('exitInitializationMode')
        # initial event iteration: no Tx_Clock is active
        + [('ecu1', 'getClock', (1003,)), ('ecu2', 'getClock', (1003,)),
           ('bus', 'getClock', (1003,)), ('bus', 'getClock', (2003,))]
        + all_fmus('updateDiscreteStates')
        + all_fmus('enterStepMode')
    )

    assert calls[:len(initialization)] == initialization

    step = [c for c in calls[len(initialization):] if c[1] not in {'getBinary', 'getClock'} or c[0] == 'ecu1']

    # drop the sampling of the outputs after the step
    step = step[:step.index(('bus', 'enterStepMode')) + 3]

    assert step == (
        all_fmus('doStep', 0.0)
        + all_fmus('enterEventMode')
        + [
            # 1st iteration: the Tx_Clock is queried before the data
            ('ecu1', 'getClock', (1003,)),
            ('ecu1', 'getBinary', (1002,)),
            # the Rx_Clock is activated before the clocked data is set
            ('bus', 'setClock', (1001,), (True,)),
            ('bus', 'setBinary', (1000,), (FRAME_A,)),
        ]
        + all_fmus('updateDiscreteStates')
        + [
            # 2nd iteration: the Bus Simulation FMU confirms and forwards the frame
            ('ecu1', 'getClock', (1003,)),
            ('ecu1', 'setClock', (1001,), (True,)),
            ('ecu1', 'setBinary', (1000,), (can.create_confirm(0x100),)),
            ('ecu2', 'setClock', (1001,), (True,)),
            ('ecu2', 'setBinary', (1000,), (FRAME_A,)),
        ]
        + all_fmus('updateDiscreteStates')
        # 3rd iteration: nothing to transfer
        + [('ecu1', 'getClock', (1003,))]
        + all_fmus('updateDiscreteStates')
        + all_fmus('enterStepMode')
    )

    for fmu in fmus.values():
        assert fmu.errors == []


def test_direct_connection(direct_system):

    calls = []

    ecu1 = FakeNode('ecu1', schedule={1: FRAME_A, 2: FRAME_A}, calls=calls)
    ecu2 = FakeNode('ecu2', schedule={1: FRAME_B}, calls=calls)

    result = run_bus_simulation(direct_system, {'ecu1': ecu1, 'ecu2': ecu2}, stop_time=0.2, step_size=0.1)

    assert transfers(result) == [
        (pytest.approx(0.1), 'ecu1.canbus1', 'ecu2.canbus1', FRAME_A),
        (pytest.approx(0.1), 'ecu2.canbus1', 'ecu1.canbus1', FRAME_B),
        (pytest.approx(0.2), 'ecu1.canbus1', 'ecu2.canbus1', FRAME_A),
    ]

    assert ecu1.received[1000] == [FRAME_B]
    assert ecu2.received[1000] == [FRAME_A, FRAME_A]
    assert ecu1.errors == ecu2.errors == []

    # the Tx_Data of both FMUs is retrieved before the Rx_Data is set
    exchange = [c[:2] for c in calls if c[1] in {'getBinary', 'setBinary'}][:4]

    assert exchange == [('ecu1', 'getBinary'), ('ecu2', 'getBinary'), ('ecu2', 'setBinary'), ('ecu1', 'setBinary')]


def test_no_event_handling_needed(bus_system):
    """ The FMUs only enter Event Mode if an FMU signals that event handling is needed """

    calls = []

    fmus = {name: FakeNode(name, calls=calls) for name in ['ecu1', 'ecu2']}
    fmus['bus'] = FakeBusSimulation('bus', calls=calls)

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1)

    assert result.bus_log == []
    assert not any(c[1] == 'enterEventMode' for c in calls)
    assert len([c for c in calls if c[1] == 'doStep']) == 9


def test_empty_tx_data(direct_system):
    """ Nothing is transferred if the Tx_Clock is active but the size of Tx_Data is zero """

    class EmptyTx(FakeNode):

        def getClock(self, vr):
            super().getClock(vr)
            return [True]

        def getBinary(self, vr, nValues=None):
            return [None]

    calls = []

    ecu1 = EmptyTx('ecu1', schedule={1: FRAME_A}, calls=calls)
    ecu2 = FakeNode('ecu2', calls=calls)

    result = run_bus_simulation(direct_system, {'ecu1': ecu1, 'ecu2': ecu2}, stop_time=0.1, step_size=0.1)

    assert result.bus_log == []
    assert ecu2.received[1000] == []
    assert not any(c[1] in {'setClock', 'setBinary'} for c in calls)


def test_terminate(bus_system):

    ecu2 = FakeNode('ecu2', schedule={1: FRAME_B})
    ecu2.terminate_at_step = 2

    fmus = {'bus': FakeBusSimulation('bus'), 'ecu1': FakeNode('ecu1'), 'ecu2': ecu2}

    result = run_bus_simulation(bus_system, fmus, stop_time=1.0, step_size=0.1)

    assert result.terminated_by == 'ecu2'
    assert result.time == pytest.approx(0.2)
    assert fmus['ecu1'].step == 2
    assert list(result.results['bus']['time']) == pytest.approx([0.0, 0.1, 0.2])


def test_terminate_in_event_mode(direct_system):

    class Terminating(FakeNode):

        def updateDiscreteStates(self):
            super().updateDiscreteStates()
            return False, self.step == 3, False, False, False, 0.0

    fmus = {'ecu1': FakeNode('ecu1', schedule={3: FRAME_A}), 'ecu2': Terminating('ecu2')}

    result = run_bus_simulation(direct_system, fmus, stop_time=1.0, step_size=0.1)

    assert result.terminated_by == 'ecu2'
    assert result.time == pytest.approx(0.3)


def test_event_iteration_does_not_converge(direct_system):

    class Babbling(FakeNode):

        def update_discrete_states(self):
            super().update_discrete_states()
            self.tx[1000] = FRAME_A  # always something to transmit
            return False

    fmus = {'ecu1': Babbling('ecu1', schedule={1: FRAME_A}), 'ecu2': FakeNode('ecu2')}

    with pytest.raises(Exception, match='did not converge after 5 iterations'):
        run_bus_simulation(direct_system, fmus, stop_time=1.0, step_size=0.1, max_event_iterations=5)


def test_bus_notifications(tmp_path):

    fmus = {
        'bus': create_bus_fmu(tmp_path / 'bus.fmu', terminals=['canbus1', 'canbus1_b'], is_bus_simulation_fmu=True,
                              bus_notifications='causality="calculatedParameter" variability="fixed"'),
        'ecu1': create_bus_fmu(tmp_path / 'ecu1.fmu', bus_notifications='causality="parameter" variability="fixed"'),
        'ecu2': create_bus_fmu(tmp_path / 'ecu2.fmu', bus_notifications='causality="parameter" variability="fixed"'),
        'ecu3': create_bus_fmu(tmp_path / 'ecu3.fmu', terminals=[]),
    }

    system = connect_bus_system(fmus)

    bus = FakeBusSimulation('bus')
    bus.booleans = {1004: True, 2004: True}  # the Bus Simulation FMU provides bus notifications

    instances = {'bus': bus, 'ecu1': FakeNode('ecu1'), 'ecu2': FakeNode('ecu2'), 'ecu3': FakeNode('ecu3')}

    run_bus_simulation(system, instances, stop_time=0.1, step_size=0.1,
                       start_values={'ecu2': {'canbus1.BusNotifications': False}})

    # the value of the Bus Simulation FMU is applied to the Network FMU...
    assert instances['ecu1'].booleans == {1004: True}

    # ...unless it has been set by the user
    assert instances['ecu2'].booleans == {1004: False}

    # FMUs without Bus Terminals do not use Event Mode
    assert instances['ecu3'].state == 'event'
    assert instances['ecu3'].step == 1
    assert not any(c[1] in {'enterStepMode', 'updateDiscreteStates'} for c in instances['ecu3'].calls)


def test_arguments(bus_system):

    def create_fmus():
        return {'bus': FakeBusSimulation('bus'), 'ecu1': FakeNode('ecu1', schedule={1: FRAME_A}), 'ecu2': FakeNode('ecu2')}

    with pytest.raises(Exception, match='"start_values" contains the unknown FMU instance "ecu9"'):
        run_bus_simulation(bus_system, create_fmus(), start_values={'ecu9': {}})

    with pytest.raises(Exception, match='start values for the following variables of FMU "ecu1" could not be set: x'):
        run_bus_simulation(bus_system, create_fmus(), start_values={'ecu1': {'x': 1}})

    with pytest.raises(ValueError, match='step_size must be greater than 0'):
        run_bus_simulation(bus_system, create_fmus(), step_size=0)

    # stop the simulation with the step_finished callback
    result = run_bus_simulation(bus_system, create_fmus(), stop_time=1.0, step_size=0.1, step_finished=lambda time: time < 0.25)

    assert result.time == pytest.approx(0.3)

    # sample the outputs with a larger interval and don't record the bus log
    logged = []

    result = run_bus_simulation(bus_system, create_fmus(), stop_time=1.0, step_size=0.1, output_interval=0.5,
                                output={'ecu1': []}, record_bus_log=False, bus_logger=logged.append)

    assert list(result.results['ecu1']['time']) == pytest.approx([0.0, 0.5, 1.0])
    assert result.results['ecu1'].dtype.names == ('time',)
    assert result.bus_log == []
    assert [t.source for t in logged] == ['ecu1.canbus1', 'bus.canbus1', 'bus.canbus1_b']

    # the stop time is not exceeded if it is not a multiple of the step size
    result = run_bus_simulation(bus_system, create_fmus(), stop_time=0.25, step_size=0.1)

    assert result.time == pytest.approx(0.2)


def test_check_bus_system(tmp_path):

    bus = create_bus_fmu(tmp_path / 'bus.fmu', is_bus_simulation_fmu=True)

    # hasEventMode is required for FMUs with Bus Terminals
    system = connect_bus_system({'bus': bus, 'ecu': create_bus_fmu(tmp_path / 'no_event_mode.fmu', has_event_mode=False)})

    with pytest.raises(Exception, match='The FMU "ecu" has Bus Terminals but does not declare hasEventMode="true"'):
        check_bus_system(system)

    check_bus_system(system, strict=False)

    assert len(system.warnings) == 1 and 'Event Mode is used anyway' in system.warnings[0]

    # time-based Tx_Clocks are not supported yet
    ecu = create_bus_fmu(tmp_path / 'countdown.fmu', tx_clock='causality="input" intervalVariability="countdown"')

    system = connect_bus_system({'bus': bus, 'ecu': ecu})

    assert system.connections[0].a.has_time_based_tx_clock

    with pytest.raises(NotImplementedError, match='Tx_Clock "canbus1.Tx_Clock" of Bus Terminal ecu.canbus1 has '
                                                  'causality="input" and intervalVariability="countdown"'):
        check_bus_system(system)


def test_simulate_bus_system_without_binaries(tmp_path):
    """ The FMUs are connected and checked before they are instantiated """

    fmus = {
        'bus': create_bus_fmu(tmp_path / 'bus.fmu', is_bus_simulation_fmu=True),
        'ecu': create_bus_fmu(tmp_path / 'ecu.fmu', has_event_mode=False),
    }

    with pytest.raises(Exception, match='does not declare hasEventMode="true"'):
        simulate_bus_system(fmus)

    with pytest.raises(Exception, match='Cannot find shared library'):
        simulate_bus_system(fmus, strict=False)


@pytest.mark.skipif('FMPY_LS_BUS_FMU_DIR' not in os.environ, reason="Environment variable FMPY_LS_BUS_FMU_DIR must be set.")
def test_simulate_bus_system():
    """ Simulate the FMUs in the directory FMPY_LS_BUS_FMU_DIR

    The directory must contain a Bus Simulation FMU and Network FMUs whose Bus Terminals can be connected
    automatically and that transmit frames. The current working directory is changed to this directory
    for FMUs that load their configuration relative to the working directory.
    """

    fmu_dir = os.environ['FMPY_LS_BUS_FMU_DIR']

    fmus = sorted(os.path.join(fmu_dir, f) for f in os.listdir(fmu_dir) if f.endswith('.fmu'))

    work_dir = os.getcwd()

    os.chdir(fmu_dir)

    try:
        result = simulate_bus_system(fmus, stop_time=float(os.environ.get('FMPY_LS_BUS_STOP_TIME', '0.5')),
                                     step_size=float(os.environ.get('FMPY_LS_BUS_STEP_SIZE', '0.005')))
    finally:
        os.chdir(work_dir)

    print(result.system)

    assert result.terminated_by is None
    assert len(result.connections) > 0

    received = set(t.destination for t in result.bus_log)

    # every connected Network FMU receives data from the bus
    for connection in result.connections:
        assert connection.a.qualified_name in received


def test_drain(bus_system, direct_system):
    """ The data that is pending in the Bus Simulation FMU at the stop time is delivered in additional steps """

    confirm = can.create_confirm(0x100)

    def create_fmus():
        return {
            'bus': FakeBusSimulation('bus', delayed=True),  # forwards the frames one step later
            'ecu1': FakeNode('ecu1', schedule={1: FRAME_A, 3: FRAME_A}),
            'ecu2': FakeNode('ecu2'),
        }

    # without draining the frame transmitted in the last step is not delivered
    fmus = create_fmus()

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1)

    assert result.drain_steps == 0
    assert fmus['ecu2'].received[1000] == [FRAME_A]
    assert fmus['bus'].stop_time == pytest.approx(0.3)

    # with draining
    fmus = create_fmus()

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1, drain_steps=16)

    # one step to deliver the pending data and one step without data
    assert result.drain_steps == 2
    assert result.time == pytest.approx(0.3)
    assert result.terminated_by is None

    assert fmus['ecu2'].received[1000] == [FRAME_A, FRAME_A]
    assert fmus['ecu1'].received[1000] == [confirm, confirm]

    assert transfers(result)[-2:] == [
        (pytest.approx(0.4), 'bus.canbus1', 'ecu1.canbus1', confirm),
        (pytest.approx(0.4), 'bus.canbus1_b', 'ecu2.canbus1', FRAME_A),
    ]

    # only the Bus Simulation FMU is stepped beyond the stop time...
    assert fmus['bus'].step == 5
    assert fmus['ecu1'].step == fmus['ecu2'].step == 3

    # ...and gets an extended stop time
    assert fmus['bus'].stop_time == pytest.approx(0.3 + 16 * 0.1)
    assert fmus['ecu1'].stop_time == fmus['ecu2'].stop_time == pytest.approx(0.3)

    for fmu in fmus.values():
        assert fmu.errors == []
        assert fmu.state == 'step'

    # the variables are not sampled while draining
    assert list(result.results['bus']['time']) == pytest.approx([0.0, 0.1, 0.2, 0.3])

    # the number of drain steps is limited
    fmus = create_fmus()

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1, drain_steps=1)

    assert result.drain_steps == 1
    assert fmus['ecu2'].received[1000] == [FRAME_A, FRAME_A]

    # nothing is drained if the simulation was stopped before the stop time...
    fmus = create_fmus()

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1, drain_steps=16,
                                step_finished=lambda time: time < 0.05)

    assert result.time == pytest.approx(0.1)
    assert result.drain_steps == 0
    assert fmus['bus'].step == 1

    # ...or if there is no Bus Simulation FMU
    fmus = {'ecu1': FakeNode('ecu1', schedule={1: FRAME_A}), 'ecu2': FakeNode('ecu2')}

    result = run_bus_simulation(direct_system, fmus, stop_time=0.1, step_size=0.1, drain_steps=16)

    assert result.drain_steps == 0
    assert fmus['ecu1'].stop_time == pytest.approx(0.1)

    with pytest.raises(ValueError, match='drain_steps must not be negative'):
        run_bus_simulation(bus_system, create_fmus(), drain_steps=-1)


def test_step_sizes(bus_system):
    """ FMUs with different step sizes only exchange data at their own communication points """

    f1, f2, f3 = [can.create_can_transmit(id=i, data=bytes([i])) for i in (1, 2, 3)]

    calls = []

    fmus = {
        'bus': FakeBusSimulation('bus', calls=calls),
        'ecu1': FakeNode('ecu1', schedule={1: f1, 2: f2, 3: f3}, calls=calls),
        'ecu2': FakeNode('ecu2', calls=calls),
    }

    result = run_bus_simulation(bus_system, fmus, stop_time=0.4, step_size=0.1, step_sizes={'ecu2': 0.2})

    for fmu in fmus.values():
        assert fmu.errors == []
        assert fmu.state == 'step'

    assert result.time == pytest.approx(0.4)

    # every FMU is advanced with its own step size
    assert [c[2] for c in calls if c[:2] == ('ecu1', 'doStep')] == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert [c[2] for c in calls if c[:2] == ('ecu2', 'doStep')] == pytest.approx([0.0, 0.2])
    assert fmus['bus'].step == 4

    assert list(result.results['ecu1']['time']) == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])
    assert list(result.results['ecu2']['time']) == pytest.approx([0.0, 0.2, 0.4])

    # ecu2 only enters Event Mode at its own communication points
    assert len([c for c in calls if c[:2] == ('ecu2', 'enterEventMode')]) == 2
    assert len([c for c in calls if c[:2] == ('ecu1', 'enterEventMode')]) == 4

    # the frames are forwarded to the Bus Simulation FMU and confirmed immediately...
    assert fmus['bus'].received[1000] == [f1, f2, f3]
    assert fmus['ecu1'].received[1000] == [can.create_confirm(i) for i in (1, 2, 3)]

    # ...and delivered to ecu2 in the order they were sent when it reaches its next communication point
    assert fmus['ecu2'].received[1000] == [f1, f2, f3]

    to_ecu2 = [t for t in result.bus_log if t.destination == 'ecu2.canbus1']

    assert [(t.time, t.tx_time, t.data) for t in to_ecu2] == [
        (pytest.approx(0.2), pytest.approx(0.1), f1),
        (pytest.approx(0.2), pytest.approx(0.2), f2),
        (pytest.approx(0.4), pytest.approx(0.3), f3),  # delivered although no FMU signaled an event at 0.4
    ]

    assert str(to_ecu2[0]).startswith('0.200000 s  bus.canbus1_b -> ecu2.canbus1 (sent at 0.100000 s)  CAN Transmit')
    assert str(to_ecu2[1]).startswith('0.200000 s  bus.canbus1_b -> ecu2.canbus1  CAN Transmit')


def test_step_sizes_pending_data(bus_system):
    """ Data for an FMU that does not reach another communication point is delivered when draining """

    f1, f2, f3 = [can.create_can_transmit(id=i, data=bytes([i])) for i in (1, 2, 3)]

    def create_fmus():
        return {
            'bus': FakeBusSimulation('bus'),
            'ecu1': FakeNode('ecu1', schedule={1: f1, 2: f2, 3: f3}),
            'ecu2': FakeNode('ecu2'),
        }

    # the last communication point of ecu2 is 0.2
    fmus = create_fmus()

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1, step_sizes={'ecu2': 0.2})

    assert result.time == pytest.approx(0.3)
    assert fmus['ecu2'].step == 1
    assert fmus['ecu2'].received[1000] == [f1, f2]

    fmus = create_fmus()

    result = run_bus_simulation(bus_system, fmus, stop_time=0.3, step_size=0.1, step_sizes={'ecu2': 0.2}, drain_steps=4)

    assert fmus['ecu2'].step == 1
    assert fmus['ecu2'].received[1000] == [f1, f2, f3]
    assert result.drain_steps == 1  # the Bus Simulation FMU has no more data after the first drain step

    for fmu in fmus.values():
        assert fmu.errors == []
        assert fmu.state == 'step'


def test_step_sizes_backlog(direct_system):
    """ Delivering the data that accumulated for a slow FMU is not limited by max_event_iterations """

    frames = [can.create_can_transmit(id=i, data=bytes([i])) for i in range(1, 11)]

    fmus = {
        'ecu1': FakeNode('ecu1', schedule=dict((i + 1, frame) for i, frame in enumerate(frames))),
        'ecu2': FakeNode('ecu2'),
    }

    result = run_bus_simulation(direct_system, fmus, stop_time=1.0, step_size=0.1, step_sizes={'ecu2': 1.0},
                                max_event_iterations=3)

    assert fmus['ecu1'].step == 10
    assert fmus['ecu2'].step == 1
    assert fmus['ecu2'].received[1000] == frames
    assert [t.tx_time for t in result.bus_log] == pytest.approx([0.1 * i for i in range(1, 11)])
    assert all(t.time == pytest.approx(1.0) for t in result.bus_log)
    assert fmus['ecu1'].errors == fmus['ecu2'].errors == []


def test_step_sizes_arguments(bus_system):

    def create_fmus():
        return {'bus': FakeBusSimulation('bus'), 'ecu1': FakeNode('ecu1', schedule={1: FRAME_A}), 'ecu2': FakeNode('ecu2')}

    with pytest.raises(Exception, match='"step_sizes" contains the unknown FMU instance "ecu9"'):
        run_bus_simulation(bus_system, create_fmus(), step_sizes={'ecu9': 0.1})

    with pytest.raises(ValueError, match='The step size of FMU "ecu1" must be greater than 0'):
        run_bus_simulation(bus_system, create_fmus(), step_sizes={'ecu1': 0.0})

    # step sizes that are not multiples of each other
    calls = []

    fmus = {'bus': FakeBusSimulation('bus', calls=calls), 'ecu1': FakeNode('ecu1', schedule={1: FRAME_A}, calls=calls),
            'ecu2': FakeNode('ecu2', calls=calls)}

    result = run_bus_simulation(bus_system, fmus, stop_time=0.6, step_size=0.1, step_sizes={'ecu2': 0.15, 'bus': 0.05})

    assert result.time == pytest.approx(0.6)
    assert [c[2] for c in calls if c[:2] == ('ecu2', 'doStep')] == pytest.approx([0.0, 0.15, 0.3, 0.45])
    assert fmus['ecu1'].step == 6
    assert fmus['bus'].step == 12
    assert fmus['ecu2'].received[1000] == [FRAME_A]

    for fmu in fmus.values():
        assert fmu.errors == []

    # the Bus Simulation FMU is drained with its own step size
    fmus = {'bus': FakeBusSimulation('bus', delayed=True), 'ecu1': FakeNode('ecu1', schedule={4: FRAME_A}),
            'ecu2': FakeNode('ecu2')}

    result = run_bus_simulation(bus_system, fmus, stop_time=0.4, step_size=0.1, step_sizes={'bus': 0.05}, drain_steps=8)

    assert fmus['ecu2'].received[1000] == [FRAME_A]
    assert result.bus_log[-1].time == pytest.approx(0.45)
    assert fmus['bus'].stop_time == pytest.approx(0.4 + 8 * 0.05)
