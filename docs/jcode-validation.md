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
followed by the broader checks below.

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


## Expanded verification and measurable improvement

The complete default suite discovered 345 tests: 344 passed and one opt-in
systemd test was skipped. That remaining test was then run separately with
`AGENT_BENCH_RUN_SYSTEMD_TESTS=1` and passed. Thus all 345 distinct tests passed
across the two commands. The integration test uses real transient non-graphical
systemd units and JSON-only fake drivers. It proves lifecycle isolation, not
real CUA endpoint validation or input. No test units remained afterward.

Two fresh subprocesses of the installed MCP compared full and selected
discovery. The compact UTF-8 JSON tools array fell from 75,942 bytes for 59 tools
to 263 bytes for `bench_list` alone, about 99.65% smaller, while preserving its
exact schema. This is a measured catalog-size improvement, not a model-token
or navigation-latency claim.

The selected real MCP process rejected malformed JSON, invalid parameters, and
an excluded `bench_ensure` call, then successfully processed a valid list call
on the same connection. The excluded bench directory was never created. Both
processes exited successfully. The local evidence contains raw protocol replies
and the runnable verifier, not merely assertions in this document.

| Requirement or risk | Observed check and result | Boundary |
| --- | --- | --- |
| Continue work through Jcode without the Codex extension | MCP open/observe/fill/click, independent CLI assertion, screenshot, and owned-tab cleanup passed | Synthetic page in real Chromium |
| Current runtime discovery and less irrelevant context | Fresh installed MCP returned current schema, selected array 75,942 to 263 bytes | Does not reload other clients |
| Errors must not break connection or create excluded resources | Real subprocess malformed JSON/params and excluded ensure rejected, subsequent list passed, no bench created | Read-only failure cases |
| Preserve neighbors during transport cleanup | Real systemd parent stop removed only its fake driver, neighboring driver still answered, final units absent | Fake drivers, real service manager |
| Browser reference and frame safety | 77 browser tests passed, including stale documents, nested lineage, overlays, reparenting and navigation races | Synthetic CDP fixtures, not all live sites |
| Native isolation and uncertain input | Native, private-environment, uinput and bridge cases passed, including foreign PID refusal, timeouts and no replay | Not a new native Unicode acceptance test |
| Safe packaging and profile retention | 12 installation staging tests and 31 profile tests passed, including real CLI fixture, locks, copy races, corruption and symlink escape | Installer not run over live installation |
| Human control and workspace restrictions | Handoff/recovery/location tests passed, including no human-workspace allocation and foreign browser refusal | No interaction with human desktop |
| Overnight claims match recorded coverage | All 346 samples reconciled, no non-ok probes or OOM in covered cgroups | Gap in active agent judgment remains explicit |

The journal subsequently established an orderly service stop at 12:15:58 UTC,
after the browser proof and tab cleanup. It did not establish who requested the
stop. This narrows the incident from unexplained process absence to a recorded
stop, without authorizing automatic restart.

The local round retains `full-tests.log`, `systemd-integration.log`,
`test-coverage-index.json`, `mcp-protocol-results.json`, and
`bench-stop-journal.log`. The continuation workflow is validated, but the wider
goal of universal autonomous desktop operation remains only partially covered.
