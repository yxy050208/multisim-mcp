<#
.SYNOPSIS
Runs the Multisim MCP stdio server with a 32-bit Python interpreter.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ServerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ServerRoot

$LocalPython = Join-Path (Split-Path -Parent $ServerRoot) "tools\python32\python.exe"
if (Test-Path -LiteralPath $LocalPython) {
    $Python = $LocalPython
} else {
    $Python = "python"
}

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python not found. Set a 32-bit Python interpreter on PATH or place one at $LocalPython"
}

$Bits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
if ($Bits -ne "32") {
    throw "Multisim's COM server is 32-bit; a 32-bit Python interpreter is required."
}

& $Python -m multisim_mcp.server
