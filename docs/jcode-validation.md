# Jcode MCP validation, 2026-09-22

## Scope

A Jcode session continued an earlier Codex task through the same local
`agent-bench` runtime. This check used a synthetic page in real Chromium on an
existing task-owned bench, not the Codex browser extension. It demonstrates the
semantic browser route in Jcode, not native desktop input or every harness.

## Connection recovery

Existing Jcode MCP connections returned an older `bench_list` shape without
workspace metadata. They were preserved because MCP connections can be shared.
A separately named stdio connection to the current `agent-bench-mcp` executable
was opened with `--tools bench_list bench_web`. Its list response included
workspace and actor metadata. No other client or desktop was restarted.

This is a connection-lifetime distinction: editing the runtime files does not
retroactively reload Python modules in an existing MCP process. Reconnect only
a client owned by the task, or use a separate connection or the current CLI.
Actor labels are not exclusive ownership leases.

## Observed workflow

1. Validated workspace 9, agent control, isolated display, and a prepared Chromium
   profile whose `lives_in` matched the task bench.
2. Started a loopback-only fixture with one input, a button, and a result counter.
3. Opened a tab registered to a new mission through Jcode's MCP tools.
4. Observed the accessible controls and filled `Ação pelo Jcode: çãé 日本語 🚀`.
5. Observed again to obtain a fresh button reference, then clicked exactly once.
6. Observed `Confirmado 1: Ação pelo Jcode: çãé 日本語 🚀` through MCP.
7. Independently read the same page through the CLI and asserted the exact result.
8. Saved and inspected a screenshot, closed the owned tab, and stopped the
   fixture. The persisted mission registry had no tabs and the fixture PID was
   absent.

The screenshot and raw observation are retained in the local round's
`retomada-jcode` report directory, outside the repository. No account action,
external submission, authenticated-page content, or live profile is in this doc.

## Regression checks and limits

`python3 -m unittest test_bench_list test_hub_discovery test_hub_clipboard`
passed all 28 tests from `desktop/`. This is targeted regression evidence,
not a new full-suite run.

The prior stability log was reconciled to 346 samples per bench from 05:14 to
10:59 UTC. Both monitored principal cgroups had no failed probes or OOM events
and stable desktop/browser PIDs during that interval. Auxiliary processes and
continuous active workload were not covered. The last recorded agent judgment
preceded the end of collection, so collection is not proof of uninterrupted
autonomous diagnosis through the entire overnight window.

After the successful proof and tab cleanup, the task bench became unavailable.
Its desktop and browser PIDs were absent, and a later list returned only the
default bench. The cause was not established. No bench was reopened or resumed.
The completed proof remains historical evidence, not a claim that those
browsers are currently running.

Native AT-SPI Unicode remains a separate issue: the earlier corrected driver
was validated in an isolated build, but the globally installed driver was not
replaced. This browser fill check does not resolve that native-driver limitation.
