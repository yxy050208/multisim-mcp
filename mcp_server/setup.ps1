<#
.SYNOPSIS
Installs the Multisim MCP package into a 32-bit Python interpreter.
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
if ($Bits -ne "32") {
    throw "Multisim's COM server requires 32-bit Python; selected interpreter is $Bits-bit."
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -e .

Write-Host "Installed multisim-mcp with $Python"
Write-Host "Install the pinned codec separately when XML conversion is needed:"
Write-Host "  npm install --global electronics-workbench-decoder@0.2.0"
Write-Host "The MCP resolves the npm shim to JavaScript and executes it with node.exe."
Write-Host "You may alternatively point MULTISIM_MCP_EWD/EWE at dist/ewd.js and dist/ewe.js."
