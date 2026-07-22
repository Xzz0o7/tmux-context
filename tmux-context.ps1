[CmdletBinding()]
param(
    [string]$Distribution,
    [string]$WslPath,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$ErrorActionPreference = "Stop"
$wslExe = (Get-Command wsl.exe -ErrorAction Stop).Source

if (-not $Distribution) {
    $distributions = & $wslExe -l -q | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if (-not $distributions) {
        throw "No WSL distribution is installed. Install WSL2 before running tmux-context."
    }
    $Distribution = $distributions[0]
}

if (-not $WslPath) {
    if ($PSScriptRoot -match '^\\\\wsl(?:\.localhost)?\\([^\\]+)\\(.+)$') {
        if (-not $PSBoundParameters.ContainsKey("Distribution")) {
            $Distribution = $Matches[1]
        }
        $WslPath = "/" + ($Matches[2] -replace "\\", "/")
    } else {
        $WslPath = (& $wslExe -d $Distribution -- wslpath -u $PSScriptRoot).Trim()
    }
}

if (-not $WslPath) {
    throw "Cannot resolve the repository path in WSL. Pass -WslPath explicitly."
}

$wslArgs = @(
    "-d", $Distribution,
    "--exec", "bash", "-lc",
    'cd "$1" && shift && exec ./tmux-context "$@"',
    "tmux-context", $WslPath
) + $CommandArgs

if ($DryRun) {
    Write-Output ("WSL distribution: {0}" -f $Distribution)
    Write-Output ("WSL repository: {0}" -f $WslPath)
    Write-Output ("tmux-context arguments: {0}" -f ($CommandArgs -join " "))
    return
}

& $wslExe @wslArgs
exit $LASTEXITCODE
