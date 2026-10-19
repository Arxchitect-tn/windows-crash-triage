# windows-crash-triage

A small, dependency-free kit for triaging a Windows PC that keeps crashing —
BSODs, `kernel-power` 41 events, bugchecks such as `0x133` or `0x0A` — driven from a
Mac or Linux box over SSH. A Tailscale tailnet is a convenient transport: install the
Tailscale client on the Windows box, SSH to its tailnet name, and nothing needs a
public IP or a port forward.

The kit reads evidence. It queries the System and Application event logs, lists
minidumps, and checks installed display drivers. It never deletes dumps, changes
drivers, or reboots anything; the only file it writes on the Windows side is its own
collector script, at the path you choose.

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [One-shot triage](#one-shot-triage)
- [Watchdog and cron](#watchdog-and-cron)
- [Reading the output](#reading-the-output)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

## What it does

| File | Purpose |
|------|---------|
| `win_events_cron.ps1` | Collector. Reads Level 1–2 (Critical/Error) events from the System and Application logs for the last N hours and prints them as a JSON array, newest first. Parameters: `-Hours` (default 48), `-MaxEvents` (default 50 per log). |
| `win_event_watch.py` | Watchdog (Python standard library only). Copies the collector to the Windows box, runs it over SSH, parses the JSON, and compares event timestamps against a last-seen state file. Prints `OK` when nothing is new, otherwise a summary of the new events — so a cron job only has to look at stdout to know whether anything changed. |
| `analyze_crashes.ps1` | One-shot triage for the start of a debugging session: minidump inventory, bugcheck and `kernel-power` events, error events in a ±window around each known crash time, focused greps for common suspects (Parsec virtual display, NVIDIA `nvlddmkm`/`dxgkrnl`, driver and PNP failures), installed display drivers, and dump-directory details. |

## Requirements

**On the Windows box**

- [OpenSSH Server](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_configure) — Settings → Optional Features → "OpenSSH Server", or `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`. Set the `sshd` service to Automatic.
- Key-based auth. Append your Mac's public key to `C:\ProgramData\ssh\administrators_authorized_keys` for an admin account (fix its ACLs per the OpenSSH docs), or to `~\.ssh\authorized_keys` for a standard account.
- **Windows PowerShell 5.1** (`powershell.exe`, ships with Windows 10/11). The triage script uses `Get-WmiObject`, which PowerShell 7 does not provide.
- A directory for the scripts, e.g. `C:\Users\you\hermes-scripts`.
- Optional, if the box is behind NAT: the [Tailscale](https://tailscale.com) client on the same tailnet as your Mac.

**On the Mac or Linux box**

- `ssh` and `scp` (both ship with macOS).
- Python 3.7+ for the watchdog (standard library only — nothing to install).
- An SSH alias in `~/.ssh/config`:

```
Host windows-host
    HostName desktop.tailnet.ts.net   # Tailscale machine name or tailnet IP
    User you
    IdentitiesOnly yes
```

Check connectivity before anything else:

```sh
ssh windows-host 'powershell -NoProfile -Command "echo hi"'
```

## Quick start

```sh
# 1. Copy the scripts once (space-free destination paths; see Limitations).
scp -O analyze_crashes.ps1  windows-host:'C:\Users\you\hermes-scripts\analyze_crashes.ps1'
scp -O win_events_cron.ps1  windows-host:'C:\Users\you\hermes-scripts\win_events_cron.ps1'

# 2. Triage since 1 Jan, with a ±15 min error scan around one known crash.
ssh windows-host 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\you\hermes-scripts\analyze_crashes.ps1 -Since 2026-01-01 -CrashTime 2026-01-15T03:45:00 -WindowMinutes 15'

# 3. Then leave the watchdog running (first run reports the whole 48 h window).
python3 win_event_watch.py --host windows-host \
    --remote-script 'C:\Users\you\hermes-scripts\win_events_cron.ps1'
```

Step 2 uses an example crash time — substitute a real one. Without `-CrashTime` the
windowed error scans are skipped; see [One-shot triage](#one-shot-triage) for passing
dates and arrays.

## How it works

```
Mac / Linux                                Windows box
────────────                               ───────────
win_event_watch.py
  │  1. scp win_events_cron.ps1  ─────────▶  C:\Users\you\hermes-scripts\
  │  2. ssh powershell -File ... -Hours N -MaxEvents M
  │                                          Get-WinEvent: System + Application,
  │                                          Level 1–2 only, last N hours
  │  3. ◀──────────────── JSON array, newest first
  │  4. read  ~/.cache/windows-crash-triage/win_event_last.txt
  │  5. print OK  |  summary of events newer than that timestamp
  └─ 6. write the newest Time back to the state file
```

The collector is re-copied and re-run on every watchdog invocation, so the remote
copy never drifts from the one in this repository. The state file holds nothing but a
timestamp, so each run is independent of the previous window: deleting it makes the
next run report whatever falls inside the current `-Hours` window.

## One-shot triage

`analyze_crashes.ps1` prints seven blocks in a fixed order: dump files, bugcheck
events, errors around known crash times, pattern greps, installed display drivers,
application-log errors around crash times, and dump-directory details. Each block is
best-effort — a missing `C:\Windows\Minidump` directory or an empty log yields an
empty section, not a failure.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `-Since <datetime>` | 30 days ago | Only events after this time are considered. |
| `-CrashTime <datetime[]>` | empty | Known crash timestamps. The ±window error scans run only when this is non-empty. |
| `-WindowMinutes <int>` | `15` | Minutes either side of each `-CrashTime`. |
| `-Patterns <string[]>` | Parsec/virtual-display, NVIDIA `nvlddmkm`/`dxgkrnl`, generic driver failures, PNP/device-setup | Regexes (PowerShell `-match`, case-insensitive) for the focused event greps. |

From the Mac, scalar arguments only — `-File` passes them as literal strings:

```sh
ssh windows-host 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\you\hermes-scripts\analyze_crashes.ps1 -Since 2026-01-01 -WindowMinutes 15'
```

For date expressions and arrays, run it in PowerShell on the box, where normal
PowerShell syntax applies:

```powershell
& C:\Users\you\hermes-scripts\analyze_crashes.ps1 -Since (Get-Date).AddDays(-7) `
    -CrashTime @('2026-01-15 03:45','2026-02-02 22:10') `
    -Patterns @('your-driver','nvlddmkm|dxgkrnl')
```

`-File` does not evaluate expressions or `@()` literals, so `-Since (Get-Date).AddDays(-7)`
and `-CrashTime @(...)` fail to bind when passed that way. Scalar values such as
`-Since 2026-01-01` bind fine because `[datetime]` converts the string.

Two details worth knowing before you read the output:

- The script fetches the newest 5,000 System events (10,000 for the pattern greps)
  and 5,000 Application events, then filters by `-Since` in PowerShell. On a noisy
  machine, events older than those caps fall outside the window even if `-Since`
  reaches further back.
- `-Since` applies to the event blocks only. The dump listing and display-driver
  inventory always reflect the machine's current state.

## Watchdog and cron

```sh
# One-off run:
python3 win_event_watch.py --host windows-host \
    --remote-script 'C:\Users\you\hermes-scripts\win_events_cron.ps1'

# Configuration by environment, handy in cron:
WIN_HOST=windows-host \
WIN_REMOTE_SCRIPT='C:\Users\you\hermes-scripts\win_events_cron.ps1' \
    python3 win_event_watch.py
```

| Flag | Environment | Default | Meaning |
|------|-------------|---------|---------|
| `--host` | `WIN_HOST` | none (required) | SSH alias or Tailscale name of the Windows box. |
| `--local-script` | `WIN_LOCAL_SCRIPT` | `win_events_cron.ps1` next to the watchdog | File that gets copied over. |
| `--remote-script` | `WIN_REMOTE_SCRIPT` | `C:\Users\you\hermes-scripts\win_events_cron.ps1` | Destination path on the Windows box. |
| `--state-file` | `WIN_STATE_FILE` | `~/.cache/windows-crash-triage/win_event_last.txt` | Last-seen timestamp. |
| `--hours` | `WIN_HOURS` | `48` | Hours of history fetched on the Windows side. |
| `--max-events` | `WIN_MAX_EVENTS` | `50` | Maximum events fetched per log. |
| `--timeout` | `WIN_TIMEOUT` | `45` | Seconds allowed for the SSH call. |
| `--scp-timeout` | `WIN_SCP_TIMEOUT` | `15` | Seconds allowed for the SCP call. |
| `--remote-python` | `WIN_REMOTE_PYTHON` | empty | Accepted but unused. Reserved for a forked collector that emits its own JSON. |

Cron line, every four hours. Invoke the script through `python3` — the file in the
repository is not marked executable, and cron's `PATH` is minimal:

```
0 */4 * * *  python3 /Users/you/windows-crash-triage/win_event_watch.py --host windows-host >/tmp/win_watch.out 2>&1
```

**Output contract.** stdout is the whole interface:

- `OK` — no events newer than the last check, an empty result, or output that was not
  valid JSON (see Limitations).
- `=== N new Windows events ===` — one summary block per event follows; this is the
  notification payload. The singular form is `=== 1 new Windows event ===`.
- `WATCHDOG_ERROR: ...` — SCP or SSH failed, or no host or local script was given.
  The message names the problem, and the state file is left untouched.

Exit status is `0` on every path the script handles itself, so cron produces no
"exit status" mail. Two paths are unhandled: an SSH/SCP **timeout** and a missing
`scp`/`ssh` binary both raise an uncaught exception, print a traceback, and exit
non-zero. Size `--timeout` for a slow link if you see that.

A wrapper that only speaks up when something changed is a shell conditional on the
output — `if ! grep -qx OK; then ...; fi` — or any cron that mails on stdout.

**State file.** A successful run writes the newest event timestamp it saw, so the next
run reports only events strictly newer than that. With no state file, the first run
reports everything in the current `-Hours` window — expect a burst, then quiet.

## Reading the output

These are starting hypotheses, not verdicts. Confirm with dump analysis before
changing hardware or drivers.

- **BugCheck events (ID 1001, `Microsoft-Windows-WER-SystemErrorReporting`)** — the
  stop code (`0x133` DPC watchdog, `0x0A` IRQL, `0x1E`, …) and the dump path. Usually
  the most informative single line in the log.
- **`kernel-power` 41** — the machine reset without a clean shutdown. On its own it is
  a symptom: pair it with a preceding 1001 (bugcheck → reboot) or with nothing at all
  (hard power loss, PSU, overheating, memory).
- **`nvlddmkm` / `dxgkrnl`** — the usual GPU-driver suspects. Recurring `nvlddmkm`
  errors point at the NVIDIA driver or the card (consider DDU and a clean install).
  A crash mid-render with these in the window is not automatically a driver bug.
- **Minidump inventory** (`C:\Windows\Minidump\*.dmp`) — how many, and when.
  Different stop codes in different modules across successive dumps
  (`0x1E`, then `0x0A`, then `0x133`) is a memory-corruption pattern; test the RAM
  (Windows Memory Diagnostic or memtest86+) before chasing the modules named in each
  dump.
- **PNP / device-setup / driver-service failures** near the crash — a driver that
  repeatedly fails to start points at a failing device.
- **Parsec / virtual-display events** — where a virtual display adapter is installed,
  its driver is a reasonable suspect for display-related crashes; update it, or
  disable it for an A/B test.

## Limitations

- **Minidumps are inventoried, not parsed.** The kit reports which dumps exist and
  what the event log says about them. Extracting the faulting module needs a debugger
  (WinDbg `!analyze -v`, or a kernel dump with `kd`).
- **WHEA and DPC-watchdog attribution need a debugger.** The event log cannot rank
  WHEA error sources or tie a `0x133` timeout to a specific driver.
- **Only Level 1–2 events are collected**, from the System and Application logs, for
  the configured window. WHEA entries (Level 3/4) and driver-load noise are not
  included. Widen the levels in `win_events_cron.ps1` if your signal lives there.
- **Unparseable collector output is reported as `OK`.** If PowerShell fails in a way
  that still exits 0 and prints no JSON, the watchdog stays quiet and does not advance
  the state file. Check the collector by hand if you expect events and see `OK`.
- **The event window is capped.** Both scripts fetch a fixed number of the newest
  records before filtering, so a very noisy log or a `-Since` far in the past can miss
  older entries.
- **Gaps longer than `-Hours` are invisible.** The watchdog reports only what the
  collector returns, so events from a period when the Mac was offline and longer than
  48 hours old are never seen.
- **Keep remote paths free of spaces.** The SCP argument is quoted, but the `ssh`
  invocation passes the remote script path as an unquoted argument.
- **Read-only by design.** Nothing here repairs a machine, and no output should be
  read as a root-cause determination on its own.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
constraints this kit is built to (standard library only, Windows PowerShell 5.1,
evidence over inference) and the checks to run before opening a PR.

## License

MIT — see [LICENSE](LICENSE).
