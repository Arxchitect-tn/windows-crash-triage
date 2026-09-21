#!/usr/bin/env python3
"""Cron watchdog for Windows Event Log errors, driven over SSH from a macOS/Linux box.

Copies win_events_cron.ps1 to the Windows host, runs it with PowerShell, parses the
JSON it prints, and compares timestamps against a last-seen state file.

Output contract (cron-friendly - the script exits 0 on every path so a cron job
only has to look at stdout):
  * "OK"                       - no new events since the last check (or nothing to parse)
  * "WATCHDOG_ERROR: ..."      - SCP/SSH/parse failure; the message explains what broke
  * "=== N new Windows events ===" - followed by one summary block per event

State file behaviour: the newest event timestamp of a successful run is persisted,
so the next run reports only events strictly newer than it. Standard library only.

Everything is configurable via CLI flags or environment variables:
  --host             WIN_HOST            SSH host/alias or Tailscale name of the Windows box
  --local-script     WIN_LOCAL_SCRIPT    path to win_events_cron.ps1 on this machine
  --remote-script    WIN_REMOTE_SCRIPT   destination path on the Windows box
  --remote-python    WIN_REMOTE_PYTHON   reserved (unused: the collector runs under PowerShell)
  --state-file       WIN_STATE_FILE      last-seen timestamp file
  --hours            WIN_HOURS           hours of history to fetch (default 48)
  --max-events       WIN_MAX_EVENTS      max events per log (default 50)
  --timeout          WIN_TIMEOUT         ssh timeout in seconds (default 45)
  --scp-timeout      WIN_SCP_TIMEOUT     scp timeout in seconds (default 15)
"""

import argparse
import json
import os
import subprocess
import sys


DEFAULT_HOST = None  # never guess an alias; require --host or WIN_HOST
DEFAULT_LOCAL_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "win_events_cron.ps1"
)
DEFAULT_REMOTE_SCRIPT = r"C:\Users\you\hermes-scripts\win_events_cron.ps1"
DEFAULT_REMOTE_PYTHON = ""  # reserved; the collector is PowerShell-only
DEFAULT_STATE_FILE = os.path.expanduser("~/.cache/windows-crash-triage/win_event_last.txt")
DEFAULT_HOURS = 48
DEFAULT_MAX_EVENTS = 50
DEFAULT_TIMEOUT = 45
DEFAULT_SCP_TIMEOUT = 15


def env_str(name, default):
    return os.environ.get(name, default)


def env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=(
            "Watch Windows System/Application logs over SSH and report Level 1-2 events "
            "newer than the last check (prints OK when there is nothing new)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default=env_str("WIN_HOST", DEFAULT_HOST),
                   help="SSH host/alias or Tailscale name of the Windows box "
                        "(e.g. the alias you configured in ~/.ssh/config).")
    p.add_argument("--local-script", default=env_str("WIN_LOCAL_SCRIPT", DEFAULT_LOCAL_SCRIPT),
                   help="Path to win_events_cron.ps1 on this machine (the file that is scp'ed over).")
    p.add_argument("--remote-script", default=env_str("WIN_REMOTE_SCRIPT", DEFAULT_REMOTE_SCRIPT),
                   help="Destination path for the .ps1 on the Windows box.")
    p.add_argument("--remote-python", default=env_str("WIN_REMOTE_PYTHON", DEFAULT_REMOTE_PYTHON),
                   help="Reserved: path to python.exe on the Windows box if you fork the "
                        "collector to emit its own JSON (not used by the bundled .ps1).")
    p.add_argument("--state-file", default=env_str("WIN_STATE_FILE", DEFAULT_STATE_FILE),
                   help="File holding the last-seen event timestamp (newest per run).")
    p.add_argument("--hours", type=int, default=env_int("WIN_HOURS", DEFAULT_HOURS),
                   help="How many hours of history to fetch on the Windows side.")
    p.add_argument("--max-events", type=int, default=env_int("WIN_MAX_EVENTS", DEFAULT_MAX_EVENTS),
                   help="Max events to fetch per log on the Windows side.")
    p.add_argument("--timeout", type=int, default=env_int("WIN_TIMEOUT", DEFAULT_TIMEOUT),
                   help="Timeout in seconds for the ssh invocation.")
    p.add_argument("--scp-timeout", type=int, default=env_int("WIN_SCP_TIMEOUT", DEFAULT_SCP_TIMEOUT),
                   help="Timeout in seconds for the scp invocation.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not args.host:
        print("WATCHDOG_ERROR: no host given; pass --host or set WIN_HOST "
              "(the ssh alias / Tailscale name of the Windows box)")
        return 0

    if not os.path.exists(args.local_script):
        print(f"WATCHDOG_ERROR: local script not found: {args.local_script}")
        return 0

    # 1) Copy the collector to the Windows box.
    scp = subprocess.run(
        ["scp", "-O", "-q", args.local_script, f'{args.host}:"{args.remote_script}"'],
        capture_output=True, text=True, timeout=args.scp_timeout,
    )
    if scp.returncode != 0:
        print(f"WATCHDOG_ERROR: SCP failed: {scp.stderr.strip()[:200]}")
        return 0

    # 2) Run it over SSH, passing hours/max-events straight through to the .ps1.
    cmd = ["ssh", args.host, "powershell", "-ExecutionPolicy", "Bypass", "-File",
           args.remote_script, "-Hours", str(args.hours), "-MaxEvents", str(args.max_events)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)

    if result.returncode != 0 and result.returncode != 1:
        print(f"WATCHDOG_ERROR: SSH failed (rc={result.returncode}): {result.stderr.strip()[:200]}")
        return 0

    out = result.stdout.strip()
    if not out:
        print("OK")
        return 0

    # Find the JSON array start (skip PowerShell banner / noise before it).
    start = out.find("[")
    if start >= 0:
        end = out.rfind("]")
        if end > start:
            out = out[start:end + 1]

    events = None
    try:
        events = json.loads(out)
    except json.JSONDecodeError:
        pass

    if events is None:
        print("OK")
        return 0
    if isinstance(events, dict):  # ConvertTo-Json emits a bare object for a single event
        events = [events]
    if not events:
        print("OK")
        return 0

    # 3) Compare against the last-seen timestamp.
    last_seen = ""
    if os.path.exists(args.state_file):
        with open(args.state_file) as f:
            last_seen = f.read().strip()

    latest = events[0].get("Time", "")

    if last_seen:
        new_events = [e for e in events if e.get("Time", "") > last_seen]
    else:
        new_events = events

    if not new_events:
        print("OK")
    else:
        plural = "s" if len(new_events) > 1 else ""
        print(f"=== {len(new_events)} new Windows event{plural} ===")
        for e in new_events:
            lvl = e.get("Level", "?")
            t = e.get("Time", "?")[:19]
            log = e.get("Log", "?")
            eid = e.get("Id", "?")
            src = e.get("Source", "?")
            msg = e.get("Message", "")
            if len(msg) > 200:
                msg = msg[:200] + "..."
            print(f"[{lvl}] {t}  Log:{log}  ID:{eid}  {src}")
            print(f"  {msg}")

    # 4) Persist the newest timestamp so the next run only reports newer events.
    if latest:
        state_dir = os.path.dirname(args.state_file) or "."
        os.makedirs(state_dir, exist_ok=True)
        with open(args.state_file, "w") as f:
            f.write(latest)

    return 0


if __name__ == "__main__":
    sys.exit(main())