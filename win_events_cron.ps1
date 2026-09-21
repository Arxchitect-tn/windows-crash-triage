# win_events_cron.ps1 - collect recent Level 1-2 (Error/Critical) events from the
# System and Application logs and emit them as JSON, newest first.
#
# Usage:
#   .\win_events_cron.ps1 -Hours 48 -MaxEvents 50
#
# Output: a JSON array (newest first) on stdout. Each entry:
#   { "Time": ISO-8601 round-trip timestamp, "Level": ..., "Id": ...,
#     "Log": "System"|"Application", "Source": ..., "Message": truncated to 200 chars }
#
# Notes:
#   - With a single matching event ConvertTo-Json emits a single object, not an
#     array - the watchdog handles both.
#   - Messages are flattened to one line and truncated to 200 chars.

param(
    [int]$Hours = 48,
    [int]$MaxEvents = 50
)

$startTime = (Get-Date).AddHours(-$Hours)
$events = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$startTime; Level=1,2} -MaxEvents $MaxEvents -ErrorAction SilentlyContinue
$appEvents = Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$startTime; Level=1,2} -MaxEvents $MaxEvents -ErrorAction SilentlyContinue

$all = @($events; $appEvents) | Sort-Object TimeCreated -Descending
$all | ForEach-Object {
    $msg = [string]($_.Message -replace "`n", " " -replace "`r", "")
    if ($msg.Length -gt 200) { $msg = $msg.Substring(0, 200) }
    [PSCustomObject]@{
        Time    = $_.TimeCreated.ToString("o")
        Level   = $_.LevelDisplayName
        Id      = $_.Id
        Log     = $_.LogName
        Source  = $_.ProviderName
        Message = $msg
    }
} | ConvertTo-Json