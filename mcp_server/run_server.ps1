<#
.SYNOPSIS
Runs the Multisim MCP stdio server with an optional separate 32-bit worker.
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$WorkerPython = ""
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ServerRoot

$LocalPython = Join-Path (Split-Path -Parent $ServerRoot) "tools\python32\python.exe"
if (-not $Python) {
    if (Test-Path -LiteralPath $LocalPython) {
        $Python = $LocalPython
    } else {
        $Python = "python"
    }
}

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "MCP frontend Python not found: $Python"
}

$Bits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
if ($WorkerPython) {
    if (-not (Get-Command $WorkerPython -ErrorAction SilentlyContinue)) {
        throw "Multisim worker Python not found: $WorkerPython"
    }
    $WorkerBits = & $WorkerPython -c "import struct; print(struct.calcsize('P') * 8)"
    if ($WorkerBits -ne "32") {
        throw "Multisim worker Python must be 32-bit; selected interpreter is $WorkerBits-bit."
    }
    $env:MULTISIM_MCP_WORKER_PYTHON = $WorkerPython
} elseif ($Bits -ne "32") {
    [Console]::Error.WriteLine(
        "Using a $Bits-bit MCP frontend; the server will auto-discover a 32-bit worker."
    )
}

& $Python -m multisim_mcp.server
