<#
.SYNOPSIS
Installs Multisim MCP into a frontend or 32-bit worker Python environment.
#>
[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ServerRoot

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python not found: $Python"
}

$Bits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"

& $Python -m pip install --upgrade pip
& $Python -m pip install -e .

Write-Host "Installed multisim-mcp with $Bits-bit Python: $Python"
if ($Bits -ne "32") {
    Write-Host "Install the package in a separate 32-bit Python for the Multisim worker."
    Write-Host "Then set MULTISIM_MCP_WORKER_PYTHON or pass -WorkerPython to run_server.ps1."
}
Write-Host "Install the pinned codec separately when XML conversion is needed:"
Write-Host "  npm install --global electronics-workbench-decoder@0.2.0"
Write-Host "The MCP resolves the npm shim to JavaScript and executes it with node.exe."
Write-Host "You may alternatively point MULTISIM_MCP_EWD/EWE at dist/ewd.js and dist/ewe.js."
