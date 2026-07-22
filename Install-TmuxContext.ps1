[CmdletBinding()]
param(
    [string]$Distribution,
    [string]$WslPath,
    [switch]$SkipWindowsTerminal
)

$ErrorActionPreference = "Stop"
$bridge = Join-Path $PSScriptRoot "tmux-context.ps1"
$bridgeArgs = @()
if ($Distribution) {
    $bridgeArgs += "-Distribution", $Distribution
}
if ($WslPath) {
    $bridgeArgs += "-WslPath", $WslPath
}

& $bridge @bridgeArgs "install"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not $SkipWindowsTerminal) {
    & $bridge @bridgeArgs "enable-windows-title"
    exit $LASTEXITCODE
}
