# Contributing

Windows evidence collection over SSH using Python and PowerShell.

[Project overview](README.md)

## Proposing a change

For a bug report, include the expected and actual behavior, the relevant command,
runtime or OS versions, and a small sanitized reproduction. For larger changes,
open an issue first to discuss the approach.

Keep pull requests focused. Explain observable behavior changes, update the README
when instructions or defaults change, and state exactly what you tested and what
remains unverified. Preserve existing interfaces unless the change explicitly
addresses them. There is currently no automated test suite in this repository.

## Local checks

```sh
python3 -c "import ast, pathlib; ast.parse(pathlib.Path('win_event_watch.py').read_text())"
python3 win_event_watch.py --help
```

The commands above do not contact a Windows host. On Windows, parse-check both
PowerShell scripts with `System.Management.Automation.Language.Parser` before
running them on an authorized test machine. For watchdog changes, check array
and single-object JSON, empty and malformed output, repeat-run state handling,
and SSH/SCP failures. Use a disposable state-file path and sanitized fixtures.

## Sensitive information

Never publish API keys, SSH private keys, credentials, private hostnames, or raw
production diagnostics. Reports can reveal usernames, paths, addresses, model
names, and service details. Redact them before sharing. Do not attach crash dumps
or full event-log exports to a public issue.

If a report would disclose an exploitable vulnerability or a secret, do not post
the sensitive details publicly. Establish a private reporting channel with the
maintainer first; no response-time commitment is implied here.

## License

This repository includes an [MIT license](LICENSE).
