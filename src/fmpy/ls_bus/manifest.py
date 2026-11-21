""" Reader for the manifest of the FMI Layered Standard for Network Communication (FMI-LS-BUS) """
from __future__ import annotations

from os import PathLike
from typing import IO

from attrs import define, field


LS_BUS_NAME = 'org.fmi-standard.fmi-ls-bus'
"Name of the layered standard in reverse domain name notation"

LS_BUS_MANIFEST_PATH = 'extra/org.fmi-standard.fmi-ls-bus/fmi-ls-manifest.xml'
"Path of the manifest inside the FMU"

LS_MANIFEST_NAMESPACE = 'http://fmi-standard.org/fmi-ls-manifest'
"XML namespace of the fmi-ls-* attributes"


@define(eq=False)
class LsBusManifest:
    """ Content of the fmi-ls-manifest.xml of an FMI-LS-BUS FMU """

    name: str | None = None
    "fmi-ls-name"

    version: str | None = None
    "fmi-ls-version"

    description: str | None = field(default=None, repr=False)
    "fmi-ls-description"

    is_bus_simulation_fmu: bool = False
    "True if the FMU is a Bus Simulation FMU, False if it is a Network FMU"

    problems: list[str] = field(factory=list, repr=False)
    "Deviations from the manifest format defined by the layered standard"


def read_ls_bus_manifest(filename: str | PathLike | IO) -> LsBusManifest | None:
    """ Read the FMI-LS-BUS manifest from an FMU without extracting it

    The reader is tolerant: deviations from the format defined by the layered standard are
    reported in LsBusManifest.problems instead of raising an exception.

    Parameters:
        filename   filename of the FMU, directory with extracted FMU or file like object

    returns:
        manifest   an LsBusManifest or None if the FMU does not contain an FMI-LS-BUS manifest
    """

    import os
    import zipfile
    from lxml import etree

    if isinstance(filename, (str, PathLike)) and os.path.isdir(filename):  # extracted FMU
        xml_file = os.path.join(filename, LS_BUS_MANIFEST_PATH)
        if not os.path.isfile(xml_file):
            return None
        with open(xml_file, 'rb') as f:
            xml = f.read()
    else:  # FMU as path or file like object
        with zipfile.ZipFile(filename, 'r') as zf:
            if LS_BUS_MANIFEST_PATH not in zf.namelist():
                return None
            xml = zf.read(LS_BUS_MANIFEST_PATH)

    manifest = LsBusManifest()

    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as e:
        manifest.problems.append(f"The manifest is not well-formed XML: {e}")
        return manifest

    def ls_attribute(name: str) -> str | None:
        # the fmi-ls-* attributes are namespace qualified, but tolerate unqualified attributes
        value = root.get(f'{{{LS_MANIFEST_NAMESPACE}}}{name}')
        return value if value is not None else root.get(name)

    manifest.name = ls_attribute('fmi-ls-name')
    manifest.version = ls_attribute('fmi-ls-version')
    manifest.description = ls_attribute('fmi-ls-description')

    tag = etree.QName(root).localname

    if tag != 'fmiLayeredStandardManifest' or etree.QName(root).namespace is not None:
        manifest.problems.append(f'The root element is "{root.tag}" but should be "fmiLayeredStandardManifest".')

    if manifest.name is None:
        manifest.problems.append('The attribute "fmi-ls-name" is missing.')
    elif manifest.name != LS_BUS_NAME:
        manifest.problems.append(f'The attribute "fmi-ls-name" is "{manifest.name}" but should be "{LS_BUS_NAME}".')

    if manifest.version is None:
        manifest.problems.append('The attribute "fmi-ls-version" is missing.')

    is_bus_simulation_fmu = root.get('isBusSimulationFMU')

    if is_bus_simulation_fmu is None:
        # non-standard manifests might define the attribute on a nested element
        for element in root.iter():
            if 'isBusSimulationFMU' in element.attrib:
                is_bus_simulation_fmu = element.get('isBusSimulationFMU')
                manifest.problems.append('The attribute "isBusSimulationFMU" is not defined on the root element.')
                break

    manifest.is_bus_simulation_fmu = is_bus_simulation_fmu in {'true', '1'}

    return manifest
