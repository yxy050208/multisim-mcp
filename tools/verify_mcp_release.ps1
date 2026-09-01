[CmdletBinding()]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Resolve-Python {
    param([string]$Requested)

    $candidates = @()
    if ($Requested) {
        $candidates += $Requested
    }
    $candidates += @("python", "py -3.10")

    foreach ($candidate in $candidates) {
        try {
            if ($candidate -eq "py -3.10") {
                & py -3.10 -c "import mcp, sys; print(sys.executable)" *> $null
                if ($LASTEXITCODE -eq 0) { return ,@("py", "-3.10") }
            } else {
                & $candidate -c "import mcp, sys; print(sys.executable)" *> $null
                if ($LASTEXITCODE -eq 0) { return ,@($candidate) }
            }
        } catch {
            continue
        }
    }
    throw "No Python interpreter with mcp installed was found. Use -Python to select one."
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($pythonCommand.Count -eq 1) {
        & $pythonCommand[0] @Arguments
    } else {
        & $pythonCommand[0] $pythonCommand[1] @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Arguments -join ' ')"
    }
}

$pythonCommand = Resolve-Python -Requested $Python
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $root "mcp_server"
Push-Location $root
try {
    Write-Host "[1/8] Version and source checks"
    Invoke-Python -Arguments @("tools/release_audit.py", "--json")
    Invoke-Python -Arguments @("tools/check_deepseek_harness_compat.py", "--json")
    Invoke-Python -Arguments @("tools/check_dsh_plugin_release.py", "--json")

    Write-Host "[2/8] Compile Python sources"
    Invoke-Python -Arguments @("-m", "compileall", "-q", "mcp_server", "tools")

    Write-Host "[3/8] Complete COM-free regression"
    Invoke-Python -Arguments @("-m", "pytest", "-q")

    Write-Host "[4/8] CLI diagnostics"
    Invoke-Python -Arguments @("-m", "multisim_mcp.cli", "--json", "doctor")

    Write-Host "[5/8] Build wheel and sdist"
    Invoke-Python -Arguments @("-m", "build", "mcp_server")

    Write-Host "[6/8] Inspect package contents"
    Invoke-Python -Arguments @("tools/check_mcp_package.py", "--dist", "mcp_server/dist", "--version", "1.2.0")

    Write-Host "[7/8] Git whitespace check"
    & git diff --check
    if ($LASTEXITCODE -ne 0) { throw "git diff --check failed" }

    Write-Host "[8/8] Frontend exclusion check"
    if (Test-Path (Join-Path $root "workbench")) {
        throw "React workbench directory must not be present in the MCP core candidate"
    }
    Write-Host "PASS: MCP Core 1.2.0 candidate is ready for review."
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
