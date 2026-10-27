"""Exercise the actual installer preflight under Windows PowerShell 5.1."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def interpreter_policy(tmp_path):
    target = tmp_path / "requirements"
    target.mkdir()
    shutil.copy2(
        Path(__file__).resolve().parents[2] / "requirements/python-compatibility.json",
        target / "python-compatibility.json",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows native argument passing")
@pytest.mark.parametrize("explicit_python", [False, True])
def test_installer_python_preflight_in_windows_powershell(tmp_path, explicit_python):
    shell = shutil.which("powershell")
    if not shell or (not explicit_python and not shutil.which("py")):
        pytest.skip("Windows PowerShell and Python launcher required")
    source = (Path(__file__).resolve().parents[2] / "install.ps1").read_text(
        encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    shutil.copy2(
        Path(__file__).resolve().parents[2] / "scripts/python_runtime.ps1",
        tmp_path / "scripts/python_runtime.ps1",
    )
    # Run the installer itself up to the GPU/pip stages, without installing
    # dependencies. This exercises its real native quoting and array splatting.
    preflight = source.split("    # Check model resources", 1)[0]
    preflight += '\n    Write-Output "PREFLIGHT=$version/$architecture"\n} catch { Write-Error $_; exit 1 }\n'
    script = tmp_path / "Python 检查 with spaces.ps1"
    script.write_text(preflight, encoding="utf-8-sig")
    command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if explicit_python:
        command.extend(["-Python", sys.executable])
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace", timeout=30
    )
    assert result.returncode == 0, result.stderr
    if explicit_python:
        expected = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert f"PREFLIGHT={expected}/64" in result.stdout
    else:
        assert any(
            f"PREFLIGHT=3.{minor}/64" in result.stdout for minor in range(10, 15)
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows installer contract")
@pytest.mark.parametrize("option", ["default", "cpu-index", "cpu-device"])
def test_cuda_installer_preflight_rejects_cpu_without_installing(tmp_path, option):
    shell = shutil.which("powershell")
    if not shell:
        pytest.skip("Windows PowerShell required")
    source = (Path(__file__).resolve().parents[2] / "install.ps1").read_text(
        encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    shutil.copy2(
        Path(__file__).resolve().parents[2] / "scripts/python_runtime.ps1",
        tmp_path / "scripts/python_runtime.ps1",
    )
    preflight = source.split("    $components =", 1)[0]
    preflight += (
        '\n    Write-Output "INDEX=$TorchIndex"\n} catch { Write-Error $_; exit 1 }\n'
    )
    script = tmp_path / "CUDA 安装 with spaces.ps1"
    script.write_text(preflight, encoding="utf-8-sig")
    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Python",
        sys.executable,
        "-Component",
        "app",
    ]
    if option == "cpu-index":
        command += ["-TorchIndex", "https://download.pytorch.org/whl/cpu"]
    elif option == "cpu-device":
        command += ["-Device", "cpu"]
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace", timeout=30
    )
    if option == "default":
        assert result.returncode == 0, result.stderr
        assert "INDEX=https://download.pytorch.org/whl/cu128" in result.stdout
    else:
        assert result.returncode != 0
    assert not (tmp_path / ".runtime").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows interpreter selection")
def test_launcher_falls_back_when_preferred_version_is_missing(tmp_path):
    helper = Path(__file__).resolve().parents[2] / "scripts/python_runtime.ps1"
    # Hide the existing app environment and emulate a launcher with only its
    # second candidate available, using the actual test interpreter.
    copied = tmp_path / "scripts/python_runtime.ps1"
    copied.parent.mkdir()
    shutil.copy2(helper, copied)
    interpreter = sys.executable.replace("'", "''")
    script = tmp_path / "fallback.ps1"
    script.write_text(
        f". '{copied}'\n"
        "function py {\n"
        "  if ($args[0] -eq '-3.12') { throw 'Version unavailable' }\n"
        f"  & '{interpreter}' @($args | Select-Object -Skip 1)\n"
        "}\n"
        "$selected = Resolve-ProjectPython\n"
        "Write-Output ('SELECTED=' + $selected.Prefix[0])\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "SELECTED=-3.11" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows interpreter selection")
@pytest.mark.parametrize("reported", ["3.15/64", "3.9/64", "3.11/32"])
def test_explicit_unsupported_python_is_rejected(tmp_path, reported):
    helper = Path(__file__).resolve().parents[2] / "scripts/python_runtime.ps1"
    script = tmp_path / "unsupported.ps1"
    script.write_text(
        f". '{helper}'\n"
        f"function TestInterpreter {{ '{reported}'; $global:LASTEXITCODE=0 }}\n"
        "try { $null = Resolve-ProjectPython TestInterpreter; exit 1 }\n"
        "catch { Write-Output $_.Exception.Message; exit 0 }\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "3.10, 3.11, 3.12, 3.13, 3.14 (64-bit) is required" in result.stdout
