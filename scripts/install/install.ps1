# g2swap install script — Windows (PowerShell 5.1+)
# Usage: .\scripts\install\install.ps1 [-NoAlias] [-NoDaemon] [-Alias NAME] [-Repo <url>]
param(
    [string]$Repo    = "https://github.com/go2digital-org/g2swap",
    [string]$InstallDir = $(if ($PSScriptRoot -and (Test-Path "$PSScriptRoot\..\..\.git")) { (Resolve-Path "$PSScriptRoot\..\..").Path } else { "$HOME\projects\g2swap" }),
    [switch]$NoDaemon,
    [switch]$NoAlias,
    [string]$Alias = ""
)
$ErrorActionPreference = "Stop"

if (-not $NoAlias -and -not $Alias -and [Environment]::UserInteractive) {
    $Alias = Read-Host "  Command name for the dashboard [g2swap]"
}
if (-not $Alias) { $Alias = "g2swap" }
if ($Alias -notmatch '^[A-Za-z][A-Za-z0-9_-]*$') {
    throw "Invalid command name '$Alias': use letters, digits, '-' or '_', starting with a letter."
}

Write-Host "`n  g2swap installer (Windows)`n"

# 1. Install uv if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Installing uv..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:PATH = "$HOME\.local\bin;$HOME\.cargo\bin;$env:PATH"
}
Write-Host "  uv: $(uv --version)"

# 2. Clone or update repo
if (Test-Path "$InstallDir\.git") {
    Write-Host "  Updating existing repo at $InstallDir..."
    git -C $InstallDir pull
} else {
    Write-Host "  Cloning $Repo -> $InstallDir..."
    git clone $Repo $InstallDir
}

# 3. Install g2swap globally
Write-Host "  Installing g2swap..."
uv tool install $InstallDir --reinstall
$env:PATH = "$HOME\.local\bin;$env:PATH"
Write-Host "  g2swap: $(g2swap --version)"

# 4. Auto-daemon via Windows Task Scheduler
if (-not $NoDaemon) {
    # Tasks were named cswap-* before the rename; remove them so only one set runs.
    foreach ($old in "cswap-auto", "cswap-watchdog") {
        Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue
    }
    $cswapBin = (Get-Command g2swap).Source
    $logDir   = "$HOME\.local\share\claude-swap"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $action   = New-ScheduledTaskAction -Execute $cswapBin -Argument "auto"
    $trigger  = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "g2swap-auto" -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName "g2swap-auto" 2>$null
    Write-Host "  Auto-daemon: Task Scheduler job registered (runs at login)"
    $wdAction = New-ScheduledTaskAction -Execute $cswapBin -Argument "watchdog"
    Register-ScheduledTask -TaskName "g2swap-watchdog" -Action $wdAction -Trigger $trigger `
        -Settings $settings -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName "g2swap-watchdog" 2>$null
    Write-Host "  Watchdog: Task Scheduler job registered (runs at login)"
}

# 5. Add the shell function to PowerShell profile
if (-not $NoAlias) {
    if (-not (Test-Path $PROFILE)) { New-Item -Force -Path $PROFILE | Out-Null }
    $marker = "# ${Alias}: Claude Code account manager"
    if (-not (Select-String -Path $PROFILE -Pattern ([regex]::Escape($marker)) -Quiet 2>$null)) {
        Add-Content $PROFILE @"

$marker
function $Alias {
    param([string]`$Cmd, [string[]]`$Rest)
    if (`$Cmd -eq "add-account") {
        `$before = (g2swap status --json | ConvertFrom-Json).active.email
        `$slot   = (g2swap status --json | ConvertFrom-Json).active.number
        Write-Host "`n  Add new Claude account`n  Current: `$before"
        `$ok = Read-Host "  Log out temporarily and add new account? (y/N)"
        if (`$ok -ne "y") { Write-Host "  Cancelled."; return }
        claude auth logout | Out-Null
        claude auth login
        g2swap add
        g2swap switch `$slot | Out-Null
        Write-Host "  Done. Back on `$before."
        return
    }
    if (`$Cmd -eq "smart-priority") {
        `$accounts = (g2swap list --json | ConvertFrom-Json).accounts
        foreach (`$a in `$accounts) {
            `$scoped  = `$a.usage.scoped
            `$fiveH   = `$a.usage.fiveHour
            `$premium = `$scoped | Where-Object { `$_.name -in @("Fable","Opus") }
            if (`$premium -and -not `$fiveH)      { `$plan="Max 20x"; `$pri=1 }
            elseif (`$premium -and `$fiveH)        { `$plan="Max 5x";  `$pri=2 }
            elseif (-not `$premium -and `$fiveH)   { `$plan="Pro";     `$pri=10 }
            else                                   { `$plan="Uncertain";`$pri=3 }
            g2swap priority `$a.number `$pri | Out-Null
            Write-Host "  Slot `$(`$a.number): `$(`$a.email)  =>  `$plan (priority `$pri)"
        }
        g2swap config set autoswitch.strategy priority-consume-first | Out-Null
        g2swap config set autoswitch.weeklyThreshold 97 | Out-Null
        Write-Host "`n  Strategy: priority-consume-first"
        return
    }
    `$before = (g2swap status --json | ConvertFrom-Json).active.email
    if (-not `$Cmd) { g2swap tui } else { g2swap `$Cmd `$Rest }
    `$after = (g2swap status --json | ConvertFrom-Json).active.email
    if (`$before -ne `$after) {
        Write-Host "`n  Switched `$before -> `$after"
        Write-Host "  Reconnecting Remote Control..."
        Start-Process claude -ArgumentList "--dangerously-skip-permissions /remote-control" -WindowStyle Hidden
    }
}
"@
        Write-Host "  Shell function: $Alias added to $PROFILE"
    } else {
        Write-Host "  Shell function: already present (skipped)"
    }
}

Write-Host "`n  Installation complete.`n"
Write-Host "  Open a new terminal, then:"
Write-Host "    $Alias                  -- open TUI"
Write-Host "    $Alias add-account      -- register a new Claude account"
Write-Host "    $Alias smart-priority   -- auto-detect Pro vs Max, set priority"
Write-Host "    $Alias list             -- show all accounts + usage`n"
