"""Thin COM wrapper around Multisim's Automation API.

The Multisim COM server is 32-bit, so this module must run inside a 32-bit
Python interpreter.
"""

from __future__ import annotations

import math
import os
import platform
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import pythoncom as _pythoncom
    from win32com import client as _win32_client
except ImportError:
    _pythoncom = None
    _win32_client = None

pythoncom: Any = _pythoncom
win32_client: Any = _win32_client

from multisim_mcp import __version__
from multisim_mcp.safety import NPX_DOWNLOAD_ENV, env_flag


PROG_ID = "MultisimInterface.MultisimApp"
CODEC_PACKAGE = "electronics-workbench-decoder@0.2.0"


def runtime_diagnostics() -> dict:
    """Return actionable runtime details without starting Multisim."""
    bits = struct.calcsize("P") * 8
    windows = os.name == "nt"
    pywin32_available = pythoncom is not None and win32_client is not None
    runtime_compatible = windows and bits == 32 and pywin32_available
    return {
        "platform": platform.platform(),
        "windows": windows,
        "python": sys.version.split()[0],
        "multisim_mcp": __version__,
        "python_executable": sys.executable,
        "python_bits": bits,
        "required_python_bits": 32,
        "pywin32_available": pywin32_available,
        "prog_id": PROG_ID,
        "runtime_compatible": runtime_compatible,
        "runtime_mode": "automation" if runtime_compatible else "introspection-only",
        "runtime_message": (
            "Multisim automation is available."
            if runtime_compatible
            else "MCP introspection is available, but Multisim automation requires "
            "32-bit Python with pywin32 on Windows and a licensed Multisim installation."
        ),
    }


def require_compatible_runtime() -> None:
    """Fail before COM activation with a useful installation diagnostic."""
    info = runtime_diagnostics()
    if not info["windows"]:
        raise RuntimeError("Multisim automation requires Windows")
    if info["python_bits"] != 32:
        raise RuntimeError(
            "Multisim automation requires a 32-bit Python interpreter; "
            f"current interpreter is {info['python_bits']}-bit: {info['python_executable']}"
        )
    if not info["pywin32_available"]:
        raise RuntimeError(
            "Multisim automation requires pywin32; reinstall multisim-mcp in the "
            "32-bit Windows Python environment"
        )


def clean_error(text: str) -> str:
    """Multisim returns UTF-8 bytes as BSTR code points; normalize them."""
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8", errors="replace")
    except Exception:
        return text


def bstr_array(values: Iterable[str]) -> Any:
    require_compatible_runtime()
    return win32_client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_BSTR, list(values)
    )


def r8_array(values: Iterable[float]) -> Any:
    require_compatible_runtime()
    return win32_client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8, list(values)
    )


class MultisimClient:
    def __init__(self) -> None:
        self._app: Any = None
        self._circuit: Any = None

    def _ensure_com(self) -> None:
        require_compatible_runtime()
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

    def _ensure_app(self) -> Any:
        require_compatible_runtime()
        self._ensure_com()
        if self._app is None:
            try:
                self._app = win32_client.gencache.EnsureDispatch(PROG_ID)
            except Exception as exc:
                raise RuntimeError(
                    "Could not activate the Multisim Automation API. Verify that "
                    "Multisim is installed, licensed, and registered for this user. "
                    f"COM ProgID: {PROG_ID}. Original error: {exc}"
                ) from exc
        return self._app

    def _connect_app(self) -> Any:
        app = self._ensure_app()
        if not bool(app.IsConnected):
            app.Connect()
        return app

    @property
    def circuit(self) -> Any:
        if self._circuit is None:
            raise RuntimeError("No circuit is open")
        return self._circuit

    def connect(self) -> dict:
        app = self._connect_app()
        return {
            "connected": bool(app.IsConnected),
            "version": str(app.VersionInfo),
            "path": str(app.Path),
        }

    def disconnect(self) -> dict:
        if self._app is not None:
            try:
                self._app.Disconnect()
            finally:
                self._app = None
                self._circuit = None
        return {"connected": False}

    def open_circuit(self, path: str) -> dict:
        self._connect_app()
        self._circuit = self._app.OpenFile(os.path.abspath(path))
        return self.circuit_info()

    def new_circuit(self) -> dict:
        self._connect_app()
        self._circuit = self._app.NewFile()
        return self.circuit_info()

    def circuit_info(self) -> dict:
        circuit = self.circuit
        return {
            "name": str(circuit.CircuitName),
            "file": str(circuit.FileName),
            "state": int(circuit.SimulationState),
            "last_error": clean_error(str(circuit.LastErrorMessage)),
        }

    def enum_components(self, component_type: int = 0) -> list:
        return list(self.circuit.EnumComponents(component_type) or ())

    def enum_inputs(self, input_type: int = 0) -> list:
        return list(self.circuit.EnumInputs(input_type) or ())

    def enum_outputs(self, output_type: int = 0) -> list:
        return list(self.circuit.EnumOutputs(output_type) or ())

    def set_output_request(
        self,
        output_name: str,
        method: int = 0,
        sample_rate: float = 1_000_000.0,
        num_samples: int = 1_000,
        repeat_flag: bool = False,
    ) -> dict:
        self.circuit.SetOutputRequest(
            output_name, method, sample_rate, num_samples, bool(repeat_flag)
        )
        return {"requested": output_name}

    def clear_output_request(self, output_name: str) -> dict:
        try:
            self.circuit.ClearOutputRequest(output_name)
        except Exception as exc:
            return {
                "cleared": False,
                "output": output_name,
                "warning": clean_error(str(exc)),
            }
        return {"cleared": True, "output": output_name}

    def _rows_to_dict(self, rows: Any, max_points: int = 2000) -> dict:
        if not isinstance(rows, (tuple, list)) or not rows:
            return {"rows": [], "shape": [0, 0]}
        normalized = [list(row) for row in rows]
        n_cols = len(normalized[0]) if normalized else 0
        n_points = len(normalized[0]) if normalized and n_cols else 0
        step = max(1, math.ceil(n_points / max(max_points, 1)))
        sampled = [row[::step] for row in normalized]
        return {
            "rows": sampled,
            "shape": [len(normalized), n_cols],
            "n_points": n_points,
            "sampled_points": len(sampled[0]) if sampled else 0,
        }

    def get_output_data(self, output_name: str, max_points: int = 2000) -> dict:
        circuit = self.circuit
        value = circuit.GetOutputData(
            output_name, pythoncom.Missing, pythoncom.Missing
        )
        rows, method = value
        result = self._rows_to_dict(rows, max_points)
        result["method"] = int(method)
        result["output"] = output_name
        return result

    def wait_ready(self, output_name: str, seconds: float = 30.0) -> bool:
        if seconds <= 0:
            raise ValueError("timeout must be greater than zero")
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                if bool(self.circuit.OutputReady(output_name)):
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        return False

    def _collect_analysis_outputs(
        self,
        analysis: str,
        output_names: list[str],
        timeout: float,
        max_points: int,
    ) -> dict:
        if not output_names:
            raise ValueError("output_names must contain at least one output")
        if max_points <= 0:
            raise ValueError("max_points must be greater than zero")

        deadline = time.time() + timeout
        pending = list(dict.fromkeys(output_names))
        ready_names: list[str] = []
        while pending and time.time() < deadline:
            for name in pending[:]:
                try:
                    if bool(self.circuit.OutputReady(name)):
                        ready_names.append(name)
                        pending.remove(name)
                except Exception:
                    continue
            if pending:
                time.sleep(0.05)

        result: dict[str, Any] = {
            "ready": not pending,
            "analysis": analysis,
            "requested_outputs": output_names,
            "ready_outputs": ready_names,
        }
        if pending:
            stop_succeeded = False
            try:
                self.circuit.StopSimulation()
                stop_succeeded = True
            except Exception:
                pass
            result["missing_outputs"] = pending
            result["timed_out"] = True
            result["stop_succeeded"] = stop_succeeded
            result["last_error"] = clean_error(str(self.circuit.LastErrorMessage))
            return result

        output_results = {
            name: self.get_output_data(name, max_points) for name in output_names
        }
        if len(output_names) == 1:
            result.update(output_results[output_names[0]])
        else:
            result["results"] = output_results
        return result

    def run_transient(
        self,
        output_name: str,
        sample_rate: float = 1_000_000.0,
        num_samples: int = 1_000,
        duration: float = 0.001,
        repeat_flag: bool = False,
        timeout: float = 30.0,
        max_points: int = 2000,
    ) -> dict:
        circuit = self.circuit
        self.clear_output_request(output_name)
        try:
            circuit.StopSimulation()
        except Exception:
            pass
        circuit.SetOutputRequest(
            output_name, 0, sample_rate, num_samples, bool(repeat_flag)
        )
        circuit.RunSimulation(duration, False)
        ready = self.wait_ready(output_name, timeout)
        result = {
            "ready": ready,
            "state": int(circuit.SimulationState),
        }
        if ready:
            data = self.get_output_data(output_name, max_points)
            result.update(data)
        else:
            stop_succeeded = False
            try:
                circuit.StopSimulation()
                stop_succeeded = True
            except Exception:
                pass
            result["timed_out"] = True
            result["stop_succeeded"] = stop_succeeded
            result["last_error"] = clean_error(str(circuit.LastErrorMessage))
        return result

    def run_dc_operating_point(
        self, output_names: list[str], timeout: float = 30.0, max_points: int = 200
    ) -> dict:
        if not output_names:
            raise ValueError("output_names must contain at least one output")
        circuit = self.circuit
        circuit.DoDCOperatingPoint(bstr_array(output_names))
        return self._collect_analysis_outputs("dc", output_names, timeout, max_points)

    def run_ac_sweep(
        self,
        output_names: list[str],
        sweep_type: int = 0,
        num_points: int = 10,
        start_frequency: float = 100.0,
        stop_frequency: float = 1_000_000.0,
        timeout: float = 60.0,
        max_points: int = 2000,
    ) -> dict:
        if not output_names:
            raise ValueError("output_names must contain at least one output")
        circuit = self.circuit
        circuit.DoACSweep(
            sweep_type,
            num_points,
            start_frequency,
            stop_frequency,
            bstr_array(output_names),
        )
        return self._collect_analysis_outputs(
            "ac_sweep", output_names, timeout, max_points
        )

    def run_ac_single_frequency(
        self,
        output_names: list[str],
        frequency: float = 1000.0,
        timeout: float = 30.0,
        max_points: int = 200,
    ) -> dict:
        if not output_names:
            raise ValueError("output_names must contain at least one output")
        circuit = self.circuit
        circuit.DoACSingleFrequency(frequency, bstr_array(output_names))
        return self._collect_analysis_outputs(
            "ac_single", output_names, timeout, max_points
        )

    def set_input_data_sampled(
        self,
        input_name: str,
        sample_rate: float,
        values: list[float],
        repeat_flag: bool = False,
    ) -> dict:
        self.circuit.SetInputDataSampled(
            input_name, sample_rate, r8_array(values), bool(repeat_flag)
        )
        return {"input": input_name, "samples": len(values)}

    def set_input_data_raw(
        self,
        input_name: str,
        times: list[float],
        values: list[float],
        repeat_flag: bool = False,
    ) -> dict:
        if len(times) != len(values):
            raise ValueError("times and values must have the same length")
        if not times:
            raise ValueError("times and values must not be empty")
        self.circuit.SetInputDataRaw(
            input_name, r8_array([times, values]), bool(repeat_flag)
        )
        return {"input": input_name, "samples": len(values)}

    def clear_input_data(self, input_name: str) -> dict:
        try:
            self.circuit.ClearInputData(input_name)
        except Exception as exc:
            return {
                "cleared": False,
                "input": input_name,
                "warning": clean_error(str(exc)),
            }
        return {"cleared": True, "input": input_name}

    def stop_simulation(self) -> dict:
        try:
            self.circuit.StopSimulation()
        except Exception as exc:
            return {
                "stopped": False,
                "state": int(self.circuit.SimulationState),
                "warning": clean_error(str(exc)),
            }
        return {"stopped": True, "state": int(self.circuit.SimulationState)}

    def save_circuit(self, path: Optional[str] = None) -> str:
        if path:
            return str(self.circuit.SaveAs(os.path.abspath(path)))
        return str(self.circuit.Save())

    def get_circuit_image(self, path: str, image_format: int = 2) -> str:
        return str(self.circuit.GetCircuitImage(image_format, os.path.abspath(path)))

    def report_netlist(self, path: str, probes_flag: bool = False, fmt: int = 0) -> str:
        return str(self.circuit.ReportNetlist(bool(probes_flag), fmt, os.path.abspath(path)))

    def report_bom(self, path: str, real_flag: bool = False, fmt: int = 0) -> str:
        return str(self.circuit.ReportBOM(bool(real_flag), fmt, os.path.abspath(path)))

    def generate_report(
        self,
        output_path: str,
        title: str = "",
        analyses: Optional[list[dict]] = None,
        include_netlist: bool = False,
        include_bom: bool = False,
        include_image: bool = False,
    ) -> dict:
        """Write a Markdown report with circuit, exports, and analysis summaries."""
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        info = self.circuit_info()
        lines: list[str] = []
        lines.append(f"# {title or info['name']}")
        lines.append("")
        lines.append("## Circuit")
        lines.append("")
        lines.append(f"- Name: {info['name']}")
        lines.append(f"- File: {info['file']}")
        lines.append(f"- Simulation state: {info['state']}")
        lines.append("")

        components = self.enum_components(0)
        inputs = self.enum_inputs(0)
        outputs = self.enum_outputs(0)
        if components:
            lines.append("## Components")
            lines.append("")
            lines.append(", ".join(components))
            lines.append("")
        if inputs:
            lines.append("## Simulation Inputs")
            lines.append("")
            lines.append(", ".join(inputs))
            lines.append("")
        if outputs:
            lines.append("## Simulation Outputs")
            lines.append("")
            lines.append(", ".join(outputs))
            lines.append("")

        attached: list[str] = []
        if include_netlist:
            netlist_path = os.path.join(os.path.dirname(output_path), "circuit.netlist")
            self.report_netlist(netlist_path)
            attached.append(f"- Netlist: `{os.path.basename(netlist_path)}`")
        if include_bom:
            bom_path = os.path.join(os.path.dirname(output_path), "circuit.bom")
            self.report_bom(bom_path)
            attached.append(f"- BOM: `{os.path.basename(bom_path)}`")
        if include_image:
            image_path = os.path.join(os.path.dirname(output_path), "circuit.png")
            self.get_circuit_image(image_path, 2)
            attached.append(
                f"- Schematic: ![schematic]({os.path.basename(image_path)})"
            )
        if attached:
            lines.append("## Exports")
            lines.append("")
            lines.extend(attached)
            lines.append("")

        for analysis in analyses or []:
            name = analysis.get("name") or analysis.get("analysis") or "Analysis"
            lines.append(f"## {name}")
            lines.append("")
            ready = analysis.get("ready")
            lines.append(f"- Ready: {ready}")
            if "output" in analysis:
                lines.append(f"- Output: {analysis['output']}")
            if "shape" in analysis:
                lines.append(f"- Shape: {analysis['shape']}")
            lines.append("")
            rows = analysis.get("rows") or []
            if rows:
                max_points = 12
                max_signals = 4
                lines.append("| signal | points | first values |")
                lines.append("| --- | --- | --- |")
                for signal_idx, row in enumerate(rows[:max_signals]):
                    values = row[:max_points]
                    rendered = ", ".join(
                        f"{value:.6g}" if isinstance(value, (int, float)) else str(value)
                        for value in values
                    )
                    lines.append(f"| {signal_idx} | {len(row)} | {rendered} |")
                lines.append("")
            else:
                lines.append("No rows returned.")
                lines.append("")

        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return {
            "path": output_path,
            "size": os.path.getsize(output_path),
            "analyses": len(analyses or []),
        }

    def do_command_line(self, command_file: str, log_file: str) -> str:
        return str(self.circuit.DoCommandLine(
            os.path.abspath(command_file), os.path.abspath(log_file)
        ))

    def run_command_file(
        self, command_file: str, log_file: str, timeout: float = 60.0
    ) -> dict:
        """Run a Nutmeg command file and wait until the engine goes idle."""
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        command_file = os.path.abspath(command_file)
        log_file = os.path.abspath(log_file)
        started_at = time.monotonic()
        self.circuit.DoCommandLine(command_file, log_file)
        state = int(self.circuit.SimulationState)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.25)
            state = int(self.circuit.SimulationState)
            if state == 0:
                break
        timed_out = state != 0
        if timed_out:
            try:
                self.circuit.StopSimulation()
                state = int(self.circuit.SimulationState)
            except Exception:
                pass
        result = {
            "command_file": command_file,
            "log_file": log_file,
            "state": state,
            "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "last_error": clean_error(str(self.circuit.LastErrorMessage)),
            "log_exists": os.path.exists(log_file),
        }
        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
                result["log"] = fh.read()
        return result

    def get_rlc_value(self, component_name: str) -> dict:
        """Read an R/L/C component value through the RLCValue property."""
        value = float(self.circuit.RLCValue(component_name))
        return {"component": component_name, "value": value}

    def set_rlc_value(self, component_name: str, value: float) -> dict:
        """Write an R/L/C component value through the SetRLCValue property."""
        self.circuit.SetRLCValue(component_name, float(value))
        return {"component": component_name, "value": float(value)}


class Ms14Codec:
    """Small wrapper around ewd/ewe for .ms14 XML round-trips."""

    @staticmethod
    def _node_script_command(script: str) -> list[str]:
        node = shutil.which("node")
        if not node or Path(node).suffix.lower() in {".cmd", ".bat"}:
            raise RuntimeError("A real node.exe executable is required to run the codec safely")
        return [node, os.path.abspath(script)]

    @classmethod
    def _command_from_path(cls, tool: str, path: str) -> list[str]:
        """Resolve npm shims to JavaScript and never pass user paths to cmd.exe."""
        path = os.path.abspath(path)
        suffix = Path(path).suffix.lower()
        if suffix in {".js", ".cjs", ".mjs"}:
            return cls._node_script_command(path)
        if suffix not in {".cmd", ".bat"}:
            return [path]

        shim_dir = Path(path).parent
        candidates = (
            shim_dir / "node_modules" / "electronics-workbench-decoder" / "dist" / f"{tool}.js",
            shim_dir.parent / "electronics-workbench-decoder" / "dist" / f"{tool}.js",
        )
        for candidate in candidates:
            if candidate.is_file():
                return cls._node_script_command(str(candidate))
        raise RuntimeError(
            f"Refusing to invoke the unsafe batch shim {path}. Set "
            f"MULTISIM_MCP_{tool.upper()} to the package's dist/{tool}.js file."
        )

    @classmethod
    def _base_cmd(cls, tool: str) -> list[str]:
        override = os.environ.get(f"MULTISIM_MCP_{tool.upper()}")
        if override:
            override = os.path.abspath(os.path.expandvars(override))
            if not os.path.isfile(override):
                raise RuntimeError(f"Configured {tool} executable does not exist: {override}")
            return cls._command_from_path(tool, override)
        exe = shutil.which(tool)
        if exe:
            return cls._command_from_path(tool, exe)
        npx = shutil.which("npx")
        if npx and env_flag(NPX_DOWNLOAD_ENV):
            if os.name == "nt":
                raise RuntimeError(
                    "The runtime npx fallback is disabled on Windows because npm batch "
                    "shims are not a safe boundary for caller-controlled paths. Install "
                    f"{CODEC_PACKAGE} once and set MULTISIM_MCP_{tool.upper()} to dist/{tool}.js."
                )
            return [npx, "--yes", "-p", CODEC_PACKAGE, tool]
        raise RuntimeError(
            f"{tool} was not found. Install {CODEC_PACKAGE} explicitly and put "
            f"{tool} on PATH, set MULTISIM_MCP_{tool.upper()}, or explicitly opt "
            f"in to the pinned npx fallback with {NPX_DOWNLOAD_ENV}=1"
        )

    def decode(self, source: str, output_xml: Optional[str] = None) -> dict:
        source = os.path.abspath(source)
        proc = subprocess.run(
            [*self._base_cmd("ewd"), source],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "ewd failed")
        generated = source + ".xml"
        target = os.path.abspath(output_xml) if output_xml else generated
        if os.path.abspath(target) != os.path.abspath(generated):
            shutil.copy2(generated, target)
        return {"xml": target, "size": os.path.getsize(target)}

    def encode(self, source_xml: str, output_ms14: Optional[str] = None) -> dict:
        source_xml = os.path.abspath(source_xml)
        output_ms14 = output_ms14 or source_xml.removesuffix(".xml")
        proc = subprocess.run(
            [
                *self._base_cmd("ewe"),
                "--format",
                "multisim",
                "--output",
                output_ms14,
                source_xml,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "ewe failed")
        return {"ms14": output_ms14, "size": os.path.getsize(output_ms14)}
