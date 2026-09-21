# analyze_crashes.ps1 - one-shot triage of Windows crash / BSOD evidence.
#
# Run it on the Windows box (over SSH from the Mac, or directly in PowerShell):
#   ssh windows-host "powershell -ExecutionPolicy Bypass -File C:\Users\you\hermes-scripts\analyze_crashes.ps1"
#   ssh windows-host "powershell -ExecutionPolicy Bypass -File ...\analyze_crashes.ps1 -Since '2026-01-01' -CrashTime @('2026-01-15 03:45','2026-02-02 22:10') -WindowMinutes 15"
#
# Parameterization:
#   -Since          [datetime]    only look at events after this (default: 30 days ago)
#   -CrashTime      [datetime[]]  known crash timestamps; windowed error scans around
#                                 each are only performed when this is non-empty
#   -WindowMinutes  [int]         +/- window around each -CrashTime (default 15)
#   -Patterns       [string[]]    regexes (PowerShell -match, case-insensitive) used
#                                 for the focused vendor/driver event greps

param(
    [datetime]$Since = (Get-Date).AddDays(-30),
    [datetime[]]$CrashTime = @(),
    [int]$WindowMinutes = 15,
    [string[]]$Patterns = @(
        "Parsec|parsec|parsecd|VDD|vdd|Virtual Display",
        "nvlddmkm|NVIDIA|dxgkrnl|dxgmms",
        "driver|Driver|failed|Failed|error|Error|crash|Crash",
        "PNP|DriverFrameworks|DeviceSetup|DispDrv"
    )
)

Write-Host "=== CRASH DUMP FILES ==="
Get-ChildItem C:\Windows\Minidump\*.dmp -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ("  " + $_.Name + " - " + $_.Length + " bytes, LastWrite: " + $_.LastWriteTime) }

Write-Host ""
Write-Host ("=== BUGCHECK EVENTS (ID 1001 - BugCheck, ID 41 - Kernel-Power, ID 161 - BugCheckRecovery) since " + $Since + " ===")
$events = Get-WinEvent -LogName System -MaxEvents 5000 -ErrorAction SilentlyContinue
$bugchecks = $events | Where-Object { ($_.Id -eq 1001 -or $_.Id -eq 41 -or $_.Id -eq 161) -and $_.TimeCreated -gt $Since }
$bugchecks | Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,Message | Format-Table -AutoSize -Wrap

if ($CrashTime.Count -gt 0) {
    Write-Host ""
    Write-Host ("=== ERROR EVENTS AROUND CRASH TIMES (+/- " + $WindowMinutes + " min) ===")
    foreach ($ct in $CrashTime) {
        Write-Host ""
        Write-Host ("--- Errors around " + $ct.ToString("yyyy-MM-dd HH:mm") + " ---")
        $windowStart = $ct.AddMinutes(-$WindowMinutes)
        $windowEnd = $ct.AddMinutes($WindowMinutes)
        $nearby = $events | Where-Object { $_.TimeCreated -ge $windowStart -and $_.TimeCreated -le $windowEnd -and $_.LevelDisplayName -eq "Error" }
        $nearby | Select-Object TimeCreated,Id,ProviderName,Message | Format-Table -AutoSize -Wrap
    }
} else {
    Write-Host ""
    Write-Host "=== ERROR EVENTS AROUND CRASH TIMES === (no -CrashTime given; windowed analysis skipped)"
}

Write-Host ""
Write-Host ("=== FOCUSED VENDOR / DRIVER EVENT GREPS (since " + $Since + ") ===")
$allEvents = Get-WinEvent -LogName System -MaxEvents 10000 -ErrorAction SilentlyContinue
foreach ($pat in $Patterns) {
    Write-Host ""
    Write-Host ("--- Pattern: " + $pat + " ---")
    $allEvents | Where-Object { $_.Message -match $pat -and $_.TimeCreated -gt $Since } |
        Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,Message | Format-Table -AutoSize -Wrap
}

Write-Host ""
Write-Host "=== INSTALLED DISPLAY DRIVERS ==="
Get-WmiObject Win32_PnPSignedDriver | Where-Object { $_.DeviceName -match "Display|NVIDIA|Parsec|Virtual" } | Select-Object DeviceName,DriverVersion,DriverDate | Format-Table -AutoSize

if ($CrashTime.Count -gt 0) {
    Write-Host ""
    Write-Host ("=== APPLICATION EVENT LOG (ERRORS AROUND CRASH TIMES, +/- " + $WindowMinutes + " min) ===")
    $appEvents = Get-WinEvent -LogName Application -MaxEvents 5000 -ErrorAction SilentlyContinue
    foreach ($ct in $CrashTime) {
        Write-Host ""
        Write-Host ("--- App errors around " + $ct.ToString("yyyy-MM-dd HH:mm") + " ---")
        $windowStart = $ct.AddMinutes(-$WindowMinutes)
        $windowEnd = $ct.AddMinutes($WindowMinutes)
        $appEvents | Where-Object { $_.TimeCreated -ge $windowStart -and $_.TimeCreated -le $windowEnd -and $_.LevelDisplayName -eq "Error" } | Select-Object TimeCreated,Id,ProviderName,Message -First 5 | Format-Table -AutoSize -Wrap
    }
} else {
    Write-Host ""
    Write-Host "=== APPLICATION EVENT LOG (ERRORS AROUND CRASH TIMES) === (no -CrashTime given; skipping)"
}

Write-Host ""
Write-Host "=== DUMP DIRECTORY DETAILS ==="
Get-ChildItem C:\Windows\Minidump\*.dmp -ErrorAction SilentlyContinue | Select-Object Name,Length,CreationTime,LastWriteTime | Format-Table -AutoSize