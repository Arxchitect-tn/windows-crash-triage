# windows-crash-triage

A small, dependency-free kit for triaging a Windows PC that keeps crashing (BSODs,
`kernel-power` 41, bugchecks like `0x133` / `0x0A`) — driven from a **Mac over SSH**.
A Tailscale tailnet is the natural transport: the Windows box gets the Tailscale
client, you SSH to its tailnet name, and everything below works without the box
having a public IP or port forward.

## What's in the box

| File | What it does |
|------|--------------|
| `win_events_cron.ps1` | Collector. Pulls Level 1–2 (Critical/Error) events from the System and Application logs for the last N hours and prints them as JSON, newest first. Parameters: `-Hours` (default 48), `-MaxEvents` (default 50). |
| `win_event_watch.py` | Cron watchdog (stdlib only). SCPs the collector to the Windows box, runs it over SSH, parses the JSON, and compares event timestamps against a last-seen state file. Prints `OK` when nothing is new, otherwise a summary of the new events — so a cron job only has to diff stdout to know whether anything changed. |
| `analyze_crashes.ps1` | One-shot triage. Run it at the start of a debugging session: minidump inventory, BugCheck / kernel-power events, error events in a ±window around each known crash time, focused greps for the usual suspects (Parsec virtual display, NVIDIA `nvlddmkm`/`dxgkrnl`, generic driver/PNP errors), installed display drivers, and dump-directory details. |

## Prerequisites

**On the Windows box:**
- [OpenSSH Server](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_configure) (Settings → Optional Features → "OpenSSH Server", or `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`), service set to Automatic.
- Key-based auth: append your Mac's public key to `C:\ProgramData\ssh\administrators_authorized_keys` (you may need to fix its ACLs, per the OpenSSH docs) — or to `~\.ssh\authorized_keys` of the account you log in as.
- PowerShell 5.1+ (ships with Windows 10/11), and a folder for the scripts, e.g. `C:\Users\you\hermes-scripts`.
- If you're behind a NAT/firewall: the [Tailscale](https://tailscale.com) client, logged in to the same tailnet as your Mac.

**On the Mac (or any Linux box):** `ssh` and `scp` (ships with macOS), and an alias for the Windows box in `~/.ssh/config`:

```
Host windows-host
    HostName desktop.tailnet.ts.net   # your Tailscale machine name (or its tailnet IP)
    User you
    IdentitiesOnly yes
```

Test it before anything else: `ssh windows-host "powershell -Command echo hi"`.

## One-shot triage (analyze_crashes.ps1)

Copy it over once and run it:

```sh
scp -O analyze_crashes.ps1 windows-host:'C:\Users\you\hermes-scripts\analyze_crashes.ps1'

# Everything since 30 days ago, plus windowed scans around two known crash times:
ssh windows-host "powershell -ExecutionPolicy Bypass -File C:\Users\you\hermes-scripts\analyze_crashes.ps1 -Since '2026-01-01' -CrashTime @('2026-01-15 03:45','2026-02-02 22:10') -WindowMinutes 15"

# Just the last 7 days, no crash-window pass:
ssh windows-host "powershell -ExecutionPolicy Bypass -File ...\analyze_crashes.ps1 -Since (Get-Date).AddDays(-7)"
```

Parameters:
- `-Since <datetime>` — only events after this are considered (default: 30 days ago).
- `-CrashTime <datetime[]>` — known crash timestamps; the ±window error scans only run when this is non-empty.
- `-WindowMinutes <int>` — window either side of each `-CrashTime` (default 15).
- `-Patterns <string[]>` — regexes for the focused vendor/driver greps (PowerShell `-match`, case-insensitive). Defaults to the Parsec/virtual-display, NVIDIA `nvlddmkm`+`dxgkrnl`, generic driver-failure, and PNP/device-setup sets. Add your own (e.g. a newer driver name) with `-Patterns @('your-driver','nvlddmkm|dxgkrnl')`.

## Watchdog (win_event_watch.py) + cron

The watchdog is the "keep watching after the triage" piece. It re-copies the
collector to the Windows box and runs it every time, so you never have to keep the
remote copy in sync:

```sh
export PATH="$HOME/bin:$PATH"

# One-off run:
python3 win_event_watch.py --host windows-host --remote-script 'C:\Users\you\hermes-scripts\win_events_cron.ps1'

# Same thing via environment (handy in cron):
WIN_HOST=windows-host WIN_REMOTE_SCRIPT='C:\Users\you\hermes-scripts\win_events_cron.ps1' \
    python3 win_event_watch.py
```

All options (defaults in `--help`): `--host` / `WIN_HOST`, `--local-script` /
`WIN_LOCAL_SCRIPT`, `--remote-script` / `WIN_REMOTE_SCRIPT` (reserved
`--remote-python` / `WIN_REMOTE_PYTHON`), `--state-file` / `WIN_STATE_FILE`,
`--hours` / `WIN_HOURS` (48), `--max-events` / `WIN_MAX_EVENTS` (50), `--timeout` /
`WIN_TIMEOUT` (45 s), `--scp-timeout` / `WIN_SCP_TIMEOUT` (15 s).

Cron line (every 4 hours; the state file makes repeats cheap and idempotent):

```
0 */4 * * *  /Users/you/windows-crash-triage/win_event_watch.py --host windows-host >/tmp/win_watch.out 2>&1
```

**Output contract.** The script exits `0` on every path, so it never generates
"exit status" mail noise:

- `OK` — no events newer than the last check (a normal run). Nothing to do.
- `=== N new Windows event ===` followed by a summary block per event — something
  new happened; this *is* the notification payload.
- `WATCHDOG_ERROR: ...` — SCP/SSH/parse problem; the message says what broke, and
  `--state-file` was not touched.

So your cron wrapper ("mail me when stdout isn't exactly `OK`", Hermes cron, or a
plain `if ! grep -qx OK` shell wrapper) only fires when there's a real change — and
the message it gets is already the triage summary.

**State file behaviour.** The newest event timestamp from each successful run is
written to `--state-file` (default `~/.cache/windows-crash-triage/win_event_last.txt`).
The next run reports only events *strictly newer* than that. Deleting the file
makes the next run report whatever is in the current 48 h window. Because it's a
timestamps-only file, the watchdog is stateless across windows.

## JSON shape (what the collector emits)

```json
[
  {
    "Time": "2026-09-21T04:07:11.9385763-04:00",
    "Level": "Error",
    "Id": 1001,
    "Log": "System",
    "Source": "Microsoft-Windows-WER-SystemErrorReporting",
    "Message": "The computer has rebooted from a bugcheck. The bugcheck was: 0x00000133 (0x0000000000000001, ...). A dump was saved in: C:\\Windows\\Minidump\\..."
  }
]
```

`Time` is ISO-8601 round-trip (`ToString("o")`), so the watchdog compares
timestamps lexicographically. With a single matching event `ConvertTo-Json` emits a
bare object instead of an array — the watchdog normalizes that. Messages are
flattened to one line and truncated to 200 chars.

## What to look at first

- **BugCheck events (Event ID 1001, `Microsoft-Windows-WER-SystemErrorReporting`)** —
  gives the stop code (`0x133` DPC watchdog, `0x0A` IRQL, `0x1E`, …) and the dump
  file path. This is the single most important line.
- **`kernel-power` 41** — the machine lost power or reset without a clean shutdown.
  Alone it's a symptom, not a cause: pair it with a preceding 1001 (BSOD → reboot)
  or with nothing (hard power loss / PSU / overheating / RAM).
- **`nvlddmkm` / `dxgkrnl`** — the classic GPU-driver suspects. Recurring
  `nvlddmkm` errors point at the NVIDIA driver or the GPU itself (consider
  DDU + clean driver install); a crash mid-render with these in the window is not
  necessarily a driver bug (see RAM below).
- **Minidump inventory** (`C:\Windows\Minidump\*.dmp`) — how many dumps, and *when*.
  Random-looking individual STOP codes are the classic **RAM-corruption signature**:
  one crash says `0x1E`, the next `0x0A`, the next `0x133`, all in different
  modules. That pattern usually means memory, not the drivers named in each dump.
  Run the Windows Memory Diagnostic or memtest86+ before chasing drivers.
- **PNP / device-setup / driver-service failures** around the crash time — a driver
  that keeps failing to start fronts for a failing device.
- **Parsec / virtual-display events** — if the box runs Parsec with a virtual
  display adapter, its display driver is a prime suspect for display-related
  crashes and is worth updating or toggling off for an A/B test.

## Limitations

- **Minidumps are inventoried, not parsed.** This kit tells you which dumps exist
  and what the event log says about them; getting the faulting module out of a
  dump needs a debugger pass (WinDbg / `!analyze -v`, or a kernel dump + kd).
- **WHEA / `0x133`-style analysis needs a debugger.** The event log alone can't
  rank WHEA error sources or attribute DPC watchdog timeouts to a driver; those
  need the bugcheck dump.
- **The watchdog only sees Level 1–2 events** in the System and Application logs,
  for the configured window. It will not see WHEA (Level 3/4) entries, driver-load
  noise, or `kernel-power` 41 if you raise the level filter. Tweak `-Hours` /
  `-MaxEvents`, and widen the levels in `win_events_cron.ps1`, if your signal lives
  elsewhere.
- Remote paths should stay free of spaces (the SCP/SSH invocation doesn't quote the
  joined command line) — if you must use spaces, put them in the `scp`/`ssh`
  command within `~/.ssh/config` instead.

## License

MIT — see [LICENSE](LICENSE).