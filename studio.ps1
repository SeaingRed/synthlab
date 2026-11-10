<#
.SYNOPSIS
  Show, stop and (re)start synthlab studio servers.

.USAGE
  .\studio.ps1            # stop this checkout's studios (any port), then start (foreground, Ctrl+C to stop)
  .\studio.ps1 status     # every running studio, any checkout: port, URL, health, pids
  .\studio.ps1 stop       # stop this checkout's studios, plus whatever holds the port
  .\studio.ps1 stop -All  # ...and every other checkout's too
  .\studio.ps1 -Port 8431 # use another port
  .\studio.ps1 -Share     # bind all interfaces so other devices on the LAN can connect

  One studio is a chain of processes: uv run -> synthlab-studio.exe -> the
  venv's python.exe redirector -> the real python.exe that listens. They are
  found by that chain, not by port, so a studio on another port, or one still
  starting (or hung) before it binds, is still seen and stopped whole.
  Other checkouts' studios are left alone unless -All: one may be in use.

  If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\studio.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "restart",
    [int]$Port = 8420,
    [switch]$Share,
    [switch]$All
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Root = (Resolve-Path $PSScriptRoot).Path.TrimEnd("\")

# A listening socket: no remote end, owned by a process that is alive. Not
# `-State Listen` (seen on Windows 11 26220): a live server's listener was
# reported with State 0, and a server just stopped left a "Listen" row under
# its dead pid (later pid 0) that never blocks a new bind — the restart threw
# "still in use" and `status` showed a running studio as having no port.
function Test-Listener($c) {
    $c.RemotePort -eq 0 -and $c.OwningProcess -ne 0 -and $c.State -notin @("TimeWait", "CloseWait", "Closing", "LastAck", "FinWait1", "FinWait2") -and
    (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue)
}
function Get-Listeners {
    @(Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { Test-Listener $_ })
}

# one port's listeners: asked of that port alone (~0.2 s), not read out of
# every connection on the machine with a process lookup each (~0.9 s with
# 445 rows) — the stop's wait loops ask it every few hundred ms
function Get-PortPids([int]$p) {
    @(Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue | Where-Object { Test-Listener $_ } |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

# Every studio process tree: @{Root; Pids; Checkout; Port; Started}
function Get-Studios {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='uv.exe' OR Name='python.exe' OR Name='synthlab-studio.exe'" |
        Where-Object { $_.CommandLine -match 'synthlab-studio(\.exe)?"?(\s|$)' })
    if (-not $procs) { return @() }
    $byId = @{}; foreach ($p in $procs) { $byId[[int]$p.ProcessId] = $p }
    $listen = @{}
    foreach ($c in @(Get-Listeners)) {
        if ($byId.ContainsKey([int]$c.OwningProcess)) { $listen[[int]$c.OwningProcess] = $c.LocalPort }
    }
    foreach ($r in $procs | Where-Object { -not $byId.ContainsKey([int]$_.ParentProcessId) }) {
        $tree = @($r); $frontier = @([int]$r.ProcessId)
        while ($frontier) {
            $kids = @($procs | Where-Object { $frontier -contains [int]$_.ParentProcessId })
            $tree += $kids; $frontier = @($kids | ForEach-Object { [int]$_.ProcessId })
        }
        $checkout = $null
        foreach ($p in $tree) {
            if ($p.CommandLine -match '"?([A-Za-z]:\\[^"]+?)\\\.venv\\Scripts\\') { $checkout = $Matches[1]; break }
        }
        $port = ($tree | ForEach-Object { $listen[[int]$_.ProcessId] } | Where-Object { $_ } | Select-Object -First 1)
        [pscustomobject]@{
            Root     = [int]$r.ProcessId
            Pids     = @($tree | ForEach-Object { [int]$_.ProcessId })
            Checkout = $checkout
            Mine     = $checkout -and ($checkout.TrimEnd("\") -ieq $Root)
            Port     = $port
            Started  = $r.CreationDate
        }
    }
}

function Get-Health([object]$port) {
    if (-not $port) { return "no port yet (starting, or hung)" }
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { "ok" } else { "HTTP $($r.StatusCode)" }
    } catch { "not responding" }
}

function Show-Status {
    $studios = @(Get-Studios)
    if (-not $studios) { Write-Host "no studio running"; return }
    $studios | ForEach-Object {
        $where = if ($_.Mine) { "this checkout" } elseif ($_.Checkout) {
            $_.Checkout.Replace("$Root\", "") } else { "?" }
        [pscustomobject]@{
            Checkout = $where
            Port     = $_.Port
            URL      = if ($_.Port) { "http://127.0.0.1:$($_.Port)" } else { "" }
            Health   = Get-Health $_.Port
            Started  = $_.Started.ToString("HH:mm:ss")
            Pids     = ($_.Pids -join " > ")
        }
    } | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
    $foreign = @(Get-PortPids $Port | Where-Object { $p = $_; -not ($studios | Where-Object { $_.Pids -contains $p }) })
    foreach ($p in $foreign) {
        $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
        Write-Host "port $Port is held by a non-studio process: pid $p $($proc.ProcessName)"
    }
}

function Stop-Tree([int]$rootPid, [string]$why) {
    Write-Host "stopping $why (pid $rootPid and its children)"
    & taskkill.exe /PID $rootPid /T /F 2>&1 | Out-Null
}

function Stop-Studio([switch]$FreePort) {
    $studios = @(Get-Studios)
    $targets = @($studios | Where-Object { $_.Mine -or $All })
    # whatever holds the port blocks the START, whoever it belongs to — so a
    # restart frees it. A bare `stop` does not: run from a second checkout it
    # would otherwise take another checkout's studio on 8420 down with it.
    $holders = if ($FreePort) { Get-PortPids $Port } else { @() }
    foreach ($p in $holders) {
        $owner = $studios | Where-Object { $_.Pids -contains $p } | Select-Object -First 1
        if ($owner -and -not ($targets | Where-Object { $_.Root -eq $owner.Root })) {
            Write-Warning "port $Port belongs to a studio from $($owner.Checkout); stopping it to free the port"
            $targets += $owner
        } elseif (-not $owner) {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            if (-not $proc) { Write-Host "port $Port is still listed under pid $p, which has already exited"; continue }
            Stop-Tree $p "non-studio process $($proc.ProcessName) on port $Port"
        }
    }
    foreach ($s in $targets) {
        $label = if ($s.Port) { "studio on port $($s.Port)" } else { "studio with no port yet" }
        Stop-Tree $s.Root $label
    }
    $others = @($studios | Where-Object { $t = $_; -not ($targets | Where-Object { $_.Root -eq $t.Root }) })
    foreach ($o in $others) { Write-Host "left running: studio from $($o.Checkout) on port $($o.Port) (use -All to stop it)" }
    if (-not $targets -and -not $holders) { Write-Host "nothing to stop" }
    # wait for the processes and, when the port was to be freed, the socket
    # (TIME_WAIT is fine; LISTEN is not)
    $gone = { (-not $FreePort -or -not (Get-PortPids $Port)) -and -not ($targets | ForEach-Object { $_.Pids } |
        Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }) }
    for ($i = 0; $i -lt 40 -and -not (& $gone); $i++) { Start-Sleep -Milliseconds 250 }
    # a server that has already exited (a Ctrl+C just before) can stay listed
    # as LISTEN under its dead pid for a while: wait it out, up to 30 s more,
    # killing again whatever is still alive and holding it (a 10 s wait was
    # not enough: it threw with the holder's name blank)
    for ($i = 0; $i -lt 60 -and -not (& $gone); $i++) {
        if ($i % 8 -eq 0) {
            $held = @(Get-PortPids $Port)
            $alive = @($held | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
            foreach ($p in $alive) { & taskkill.exe /PID $p /T /F 2>&1 | Out-Null }
            Write-Host "waiting for port $Port to be released (pid $($held -join ', ')$(if (-not $alive) { ', already exited' }))..."
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not (& $gone)) {
        $held = @(Get-PortPids $Port | ForEach-Object { $n = (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName; "pid $_ $(if ($n) { $n } else { '(exited)' })" })
        throw "port $Port is still in use after stop ($($held -join '; ')) — wait a moment and run .\studio.ps1 again"
    }
}

function Start-Studio {
    if (Get-PortPids $Port) { throw "port $Port already in use — run '.\studio.ps1 stop' first" }
    if ($env:VIRTUAL_ENV) {
        Write-Warning "VIRTUAL_ENV=$($env:VIRTUAL_ENV) is set; uv ignores it and uses .venv. Fine."
    }
    if ($Share) {
        Write-Host "starting studio on port $Port  (shared on the LAN, Ctrl+C to stop)"
        Write-Host "shared mode is for trusted networks; the token link is printed by the server"
        Write-Host "if other devices can't connect, allow the port once (admin), for this LAN only:"
        Write-Host "  New-NetFirewallRule -DisplayName 'synthlab studio' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private -RemoteAddress LocalSubnet"
        uv run synthlab-studio --port $Port --share
        return
    }
    Write-Host "starting studio on http://127.0.0.1:$Port  (Ctrl+C to stop)"
    uv run synthlab-studio --port $Port
}

switch ($Action) {
    "stop"    { Stop-Studio }
    "status"  { Show-Status }
    "start"   { Start-Studio }
    "restart" {
        $clock = [Diagnostics.Stopwatch]::StartNew()
        Stop-Studio -FreePort
        Write-Host ("stopped in {0:N1} s" -f $clock.Elapsed.TotalSeconds)
        Start-Studio
    }
}
