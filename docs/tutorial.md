## Graphical User Interface

You can start the FMPy GUI with `python -m fmpy.gui`

![FMPy GUI](Rectifier_GUI.png)

## Python

To follow this example download `Rectifier.fmu` for your platform by clicking on the respective link:
[Linux](https://github.com/modelica/fmi-cross-check/raw/master/fmus/2.0/cs/linux64/MapleSim/2016.2/Rectifier/Rectifier.fmu),
[macOS](https://github.com/modelica/fmi-cross-check/raw/master/fmus/2.0/cs/darwin64/MapleSim/2016.2/Rectifier/Rectifier.fmu),
[Windows (32-bit)](https://github.com/modelica/fmi-cross-check/raw/master/fmus/2.0/cs/win32/MapleSim/2016.2/Rectifier/Rectifier.fmu),
[Windows (64-bit)](https://github.com/modelica/fmi-cross-check/raw/master/fmus/2.0/cs/win64/MapleSim/2016.2/Rectifier/Rectifier.fmu).
Change to the folder where you've saved the FMU and open a Python prompt.

```
>>> from fmpy import *
>>> fmu = 'Rectifier.fmu'
>>> dump(fmu)  # get information

Model Info

  FMI Version       2.0
  FMI Type          Co-Simulation
  Model Name        Rectifier
  Description       Model Rectifier
  Platforms         win64
  Continuous States 4
  Event Indicators  6
  Variables         63
  Generation Tool   MapleSim (1196527/1196706/1196706)
  Generation Date   2017-01-19T18:42:46Z

Default Experiment

  Stop Time         0.1
  Step Size         1e-07

Variables (input, output)

Name                Causality              Start Value  Unit     Description
outputs             output                              V        Rectifier1.Capacitor1.v
>>> result = simulate_fmu(fmu)         # simulate the FMU
>>> from fmpy.util import plot_result  # import the plot function
>>> plot_result(result)                # plot the result
```

![Rectifier Result](Rectifier_result.png)

## Command Line Interface

To get information about an FMU directly from the command line change to the folder where you've saved the
FMU and enter

```bash
fmpy info Rectifier.fmu
```

Simulate the FMU and plot the results

```bash
fmpy simulate Rectifier.fmu --show-plot
```

Get more information about the available options

```bash
fmpy --help
```

## Advanced Usage

To learn more about how to use FMPy in you own scripts take a look at the
[coupled_clutches.py](https://github.com/CATIA-Systems/FMPy/blob/master/fmpy/examples/coupled_clutches.py),
[custom_input.py](https://github.com/CATIA-Systems/FMPy/blob/master/fmpy/examples/custom_input.py) and
[parameter_variation.py](https://github.com/CATIA-Systems/FMPy/blob/master/fmpy/examples/parameter_variation.py) examples.

## Network Communication (FMI-LS-BUS)

FMPy can connect and co-simulate FMI 3.0 FMUs that communicate via the Network Abstraction ("low cut")
of the [FMI Layered Standard for Network Communication](https://github.com/modelica/fmi-ls-bus) (FMI-LS-BUS),
e.g. for CAN and CAN FD. The Bus Terminals of the Network FMUs (e.g. virtual ECUs) are connected
automatically to the Bus Terminals of the Bus Simulation FMU.

```python
from fmpy.ls_bus import simulate_bus_system

result = simulate_bus_system(
    fmus={'bus': 'BusSimulation.fmu', 'ecu1': 'ECU1.fmu', 'ecu2': 'ECU2.fmu'},
    stop_time=1.0,
    step_size=1e-3,
)

for connection in result.connections:
    print(connection)  # e.g. ecu1.canbus1 <-> bus.canbus1 (same terminal name)

for transfer in result.bus_log:
    print(transfer)    # e.g. 0.010000 s  ecu1.canbus1 -> bus.canbus1  CAN Transmit(id=0x100, ...)
```

**Roles**. The Bus Simulation FMU is identified by `isBusSimulationFMU="true"` in its manifest
`extra/org.fmi-standard.fmi-ls-bus/fmi-ls-manifest.xml`. All other FMUs with Bus Terminals are Network FMUs.
Use the argument `bus_simulation` to override the roles. FMUs without Bus Terminals are simulated alongside.

**Connections**. The layered standard defines how the members of two Bus Terminals are connected
(`Tx_Data` to `Rx_Data` and `Tx_Clock` to `Rx_Clock`) but not which terminals belong together.
FMPy connects the terminals by name:

1. the connections given with the argument `connections` are created first,
   e.g. `connections=[('ecu1.CanChannel', 'bus.Node1')]`
2. a Bus Terminal `canbus1` of a Network FMU is connected to the first free Bus Terminal `canbus1`
   of the Bus Simulation FMU
3. if this terminal is already used (several Network FMUs on the same network), it is connected to the first
   free Bus Terminal whose name starts with `canbus1` followed by `_`, `.` or `-`, e.g. `canbus1_b`

The Network FMUs are processed in the order in which the FMUs are given. Without a Bus Simulation FMU two
Bus Terminals with the same name are connected directly. Use `fmpy.ls_bus.connect_bus_system()` to get the
connections without simulating the FMUs and `fmpy info` to list the Bus Terminals of an FMU.

**Step sizes**. All FMUs are advanced with `step_size` unless an individual step size is given with
`step_sizes`, e.g. `step_sizes={'ecu2': 0.01}`. Every FMU then has its own communication points and only
exchanges data at these points: data that is sent to an FMU between two of its communication points is
delivered, in the order it was sent, when the FMU reaches its next communication point
(`BusTransfer.tx_time` is the time the data was sent, `BusTransfer.time` the time it was delivered).

**Pending data**. A Bus Simulation FMU may deliver a frame one or more communication steps after it was
transmitted, so the frames of the last steps are still pending at the stop time. With `drain_steps=N`
only the Bus Simulation FMU is stepped up to `N` more times after the stop time until no more data is
delivered. The Network FMUs are not stepped while draining and do not transmit new frames.

**Limitations**. Only triggered output Clocks are supported for `Tx_Clock`, i.e. the FMUs signal pending
data by returning `eventHandlingNeeded` from `fmi3DoStep()`. Time-based Clocks (e.g. `countdown`) and the
Physical Signal Abstraction ("high cut") are not supported yet.

See the example `fmpy/examples/ls_bus_can.py`, which can also be run from the command line:

```bash
python -m fmpy.examples.ls_bus_can BusSimulation.fmu ECU1.fmu ECU2.fmu --stop-time 1 --step-size 1e-3
```

## Debugging C code FMUs

FMPy can generate [CMake](https://cmake.org/) projects for C code FMUs that allow you to conveniently build and debug FMUs in your favorite IDE. To debug an FMU using Visual Studio Solution follow these steps:

- Open the FMU in the FMPy GUI, click `Help > Create CMake Project...` and select the directory to save the project files

- Open the CMake GUI and select the source and build directories (you can set both to the above directory)

- Click `Configure` and select the generator (e.g. `Visual Studio 14 2015 Win64` to create a Visual Studio 2015 solution for 64-bit Windows)

- Click `Generate` to create the Visual Studio solution

- Click `Open Project` to open the solution

- In Visual Studio select `Build > Build Solution` to build the debug FMU

- Open the FMU that has been created in the project directory in the FMPy GUI

- In Visual Studio select `Debug > Attach to Process...`, select the Python process that runs the FMPy GUI and click `Attach`

- Set a breakpoint

- Run the simulation in the FMPy GUI and start debugging
