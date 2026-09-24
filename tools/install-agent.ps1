param(
  [ValidateSet('qwen','chatgpt','clawcode','workbody','deepseek','all')]
  [string]$Client = 'all',
  [string]$Python32 = $env:MULTISIM_MCP_PYTHON,
  [string]$Output = (Join-Path (Get-Location) 'generated-config')
)
$ErrorActionPreference = 'Stop'
if (-not $Python32) { throw 'Provide -Python32 pointing to a 32-bit Python with pywin32 and Multisim access.' }
$python = (Resolve-Path -LiteralPath $Python32).Path
$bits = & $python -c 'import struct; print(struct.calcsize("P") * 8)'
if ($LASTEXITCODE -ne 0 -or $bits.Trim() -ne '32') { throw "Python32 must be 32-bit; detected $bits" }
$clients = if ($Client -eq 'all') { @('qwen','chatgpt','clawcode','workbody','deepseek') } else { @($Client) }
New-Item -ItemType Directory -Force -Path $Output | Out-Null
foreach ($name in $clients) {
  $target = Join-Path $Output "$name.json"
  & $python (Join-Path $PSScriptRoot 'generate_agent_config.py') --client $name --python32 $python --output $target
  if ($LASTEXITCODE -ne 0) { throw "Failed to generate $name configuration" }
}
Write-Host "Generated MCP configurations in $((Resolve-Path $Output).Path)"
Write-Host 'Restart the selected Agent and run runtime_status before the first experiment.'
