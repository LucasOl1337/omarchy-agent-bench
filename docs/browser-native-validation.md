# Native browser validation, 2026-09-22

**Latest state, 13:26 UTC:** the native browser was reopened for the real login
acceptance path. Google's account chooser contains multiple remembered
identities, including multiple entries for the user, all signed out. No identity
was guessed or selected and no credentials were entered. The bench remains open
in workspace 10 with only the pending Google login tab. The human must select
the intended account and authenticate before this acceptance test can finish.
This is a concrete blocker, not evidence that removing CDP fixed authentication.

## Outcome and scope

An opt-in conventional Chromium launcher is implemented and installed on the
local workstation as `agent-bench-native`. The original CDP launcher and active
browsers were left unchanged. Native reading, input and a public search workflow
worked without a debugging endpoint. A complete authenticated login was **not**
performed, so this is not proof that Google's insecure-browser rejection is
resolved for an account or that all sites accept the browser.

## Real interfaces exercised

Test bench: `browser-normal-922`, workspace 10, private X11 display `:84`.
The profile was prepared once through the existing `agent-bench-profile`
command from the official offline seed. No live profile was copied or replaced.
A new dedicated `agent-bench mcp browser-normal-922` client used the current
isolation guards. All input and captures stayed inside this bench.

| Requirement | Check | Observed result |
| --- | --- | --- |
| Conventional browser without CDP | Installed CLI open/status, PID executable/flags/cgroup, profile endpoint existence | `/usr/lib/chromium/chromium`, bench singleton/cgroup confirmed, no `DevToolsActivePort`, no remote-debugging or WebDriver flags |
| No JS signal falsification | Local diagnostic page reads `navigator.webdriver` itself | `false`, with no property override, injected stealth script or changed user agent |
| Read quickly without DOM/CDP | MCP `get_window_state`, real Chromium AT-SPI | Local page reads 61–76 ms. Wikipedia portal about 146–171 ms in measured sequence, one tool call 354 ms. Article read 321 ms. Timings are observed samples, not a latency SLA |
| Native click and Unicode input | Current AT-SPI element tokens, native click, bench clipboard readback and Ctrl+V | Local output exactly `Persistido: Ação rápida: São Paulo ✓ café 123`. Clicks 154–158 ms, paste 23 ms in measured sequence |
| Real public page workflow | Wikipedia portal, field focus via observed AT-SPI bounds, native clipboard/keyboard, exact field readback, Return | `Browser automation` searched and navigated to `Headless browser - Wikipedia`, with the redirect link observed in the article tree. Focus/replace command measured 231 ms |
| Persist after browser restart | Cooperative Alt+F4, status closed, same-profile open, rediscovered new PID/window, AT-SPI readback | Exact localStorage test string survived Chromium restart. This is storage persistence, not authenticated-session persistence |
| Real Google entry | Open `https://accounts.google.com/` in the native browser, observe via AT-SPI | Reached `Choose an account`. Remembered identities were all signed out and the intended account was ambiguous. No matching `secure` error text on that initial screen. No account selected, password submitted, MFA attempted or login claimed |
| Preserve existing debugging sessions | Native status on existing `padrao` | Refused with `browser_not_native`, exit 1, session preserved |
| Missing identity must fail closed | Native status on absent profile | `profile_missing`, exit 1, no session directory created |
| Do not trust dispatch as effect | CUA `type_text` on Wikipedia | Returned success but field stayed empty. Reconciled before retry. Verified bench clipboard/X11 path succeeded instead |
| Package new command | Real `install.sh --no-enable --no-skills --no-hypr` in a temporary HOME and prefix | Installer exit 0, executable symlink resolved to installed CLI, `--help` exit 0. Installed `status` refused missing profile with JSON/exit 1 and created no profile. No live units or human configuration touched |
| Reuse without duplicate page | Installed `open browser-normal-922` without URL, before/after status | Same PID, `action:reused`, `url_delivery:not_requested`. Pending Google tab preserved |
| Reject unsafe public input | Installed CLI with option-like URL, JavaScript URL, credential-bearing URL and absent profile | All refused with structured JSON/exit 1 before navigation. Runtime module still byte-identical to source |

## Public contract and regression mapping

The changed interfaces are the new CLI/module, its installer symlink and the
operation guides. Existing CDP/MCP interfaces were not changed.

| Contract | Named unit checks in `test_browser_native.py` | Additional observed evidence |
| --- | --- | --- |
| `--help` / `status` are read-only and report explicit capabilities | `test_help_does_not_start_inspect_or_mutate`, `test_closed_status_is_read_only_and_never_probes_cdp`, `test_status_offline_does_not_create_runtime`, `test_cli_json_and_nonzero_errors` | Installed CLI help/status, live identity, closed identity and absent profile exercised |
| Existing profile/binary identity only, no fallback | `test_missing_default_never_starts_or_creates_profile`, `test_linked_default_refused`, `test_invalid_explicit_binary_has_no_fallback`, `test_elf_wrapper_is_rejected_even_with_fake_resources` | Official seed prepared once, direct packaged executable observed, existing CDP browser refused |
| PID, cgroup, display and argv remain bound to bench | `test_external_and_other_bench_process_refused_before_start`, `test_explicit_display_overrides_refused_even_with_valid_or_erased_env`, `test_wrong_profile_and_duplicate_flags_refused`, `test_process_start_time_change_refused` | Real singleton/cgroup and MCP window discovered in workspace 10, display :84 |
| Erased environment is disclosed, not fabricated | `test_erased_environment_reports_inferred_display_not_verified_env`, `test_partial_environment_cannot_claim_erased_environment_fallback`, `test_verified_environment_report_is_distinct` | Real Chromium cleared environ, CLI reports inferred source and `env_verified:false` |
| `open` delivers once and preserves uncertainty | `test_initial_launch_url_exactly_once_no_extra_blank`, `test_existing_native_without_url_never_launches_again`, `test_launch_timeout_never_relaunches_or_removes_artifacts`, `test_lost_launch_reply_never_retries`, `test_unsafe_flags_observed_after_dispatch_report_url_uncertainty` | Initial launch, navigation via singleton, same-PID no-op and cooperative restart exercised |
| Human control and concurrent mutation are guarded | `test_human_control_blocks_start_and_launch_but_not_status`, `test_control_gate_rechecked_after_start`, `test_server_human_state_blocks_mutation`, `test_mutations_serialized_without_wait_or_second_launch` | Existing control mechanism used for every real input. A real human takeover was not simulated or overridden |
| Invalid URL / system startup errors return JSON without fallback | `test_invalid_urls_and_timeout_do_not_start`, `test_cli_start_subprocess_failure_is_json_without_launch` | Real negative CLI calls returned expected JSON/exit 1 |
| Fast reading/input guide is usable and truthful | No guide claim relies only on unit tests | Wikipedia workflow passed through public interfaces. Failed `type_text` was documented, not presented as fixed. Google login remains blocked at identity selection |

Whole-result recheck after the login follow-up: 392 tests executed, one skipped,
suite successful in 6.641 seconds. The real installer and installed-CLI checks
above were added to cover packaging and no-op/negative integration boundaries.
They do not establish a successful login or a before/after improvement in a
service's authentication acceptance rate.

## Defects found and corrected during validation

- Scanner initially parsed agent prompts containing `--user-data-dir` as browser
  arguments. It now checks the executable before parsing potential owners.
- Real Chromium erased its `/proc/PID/environ` to all NUL bytes. Identity now
  reports `env_verified:false` and `display_source:bench_status/cgroup` for that
  specific case, while retaining singleton, executable, flags and cgroup checks.
  Actual window discovery separately confirmed the browser on the bench display.
- Independent review found `--display` could override the inherited display.
  It is now refused, including single-dash and separate-value forms.
  A divergent `--ozone-platform-hint` is also refused.
- The native typing API's success response was not sufficient. The operating
  guide now prefers bench clipboard plus verified native focus/input.
- A failed systemd startup could escape the CLI as a traceback. It now returns
  structured JSON with exit 1 and does not dispatch a browser launch.

## Automated checks

Baseline project suite: 345 tests, one skip, passed before the feature.
Final project suite: 392 tests, one skip, passed in 6.524 seconds, including
47 new native-browser tests. The installed module matches the tested source.
The exact staged commit snapshot also passed its 100 tests independently of
uncommitted work. This copied-source check is supplementary packaging evidence,
not a replacement for the real workstation/public-page checks.
Tests exercise CLI validation, missing/redirected profiles, browser outside the
bench, unsafe/debugging/display flags, PID changes, single-NUL command lines,
cleared/incorrect environments, lock ambiguity, launch timeouts, lost replies,
control ownership, and at-most-once URL dispatch. Unit tests are supplementary,
not substitutes for the public-page and native-process checks above.

## Limits and next action

The remaining acceptance step for the original login complaint is one controlled
login to an affected service in this conventional browser. The Google chooser
does not identify a unique authorized account for this test, so the human must
select the intended identity and complete personal verification. No repeated login
attempts were made and no credentials were read from profiles. The existing
`password-store=basic` convention is retained, not repaired or migrated.

AT-SPI trees can be incomplete. A small node limit initially omitted the page
below Chromium's toolbar. Increase the bound based on truncation, or use a
screenshot. Query filtering reduces returned text, not necessarily traversal
work. Use current tokens or observed bounds and verify every consequential
input. No stealth plugin or Codex extension was installed.

Screenshots and full diagnostic transcripts stay in the private Jcode scratch
directory, not in git. Only public descriptions and non-secret test values are
recorded here. The fixture is synthetic evidence for Unicode and storage.
Wikipedia is the representative end-user workflow. Google login remains only
an initial-screen check, not completed authentication.

## Cleanup and checkpoint

At the end of the first validation, Google and Wikipedia tabs were closed. The test browser
was closed cooperatively and its dedicated MCP client disconnected. Only the
owned test bench and localhost fixture server were stopped. The prepared
`browser-normal-922` profile is retained with the test localStorage value, no
unsaved form or uncertain external submission. To continue the real login
acceptance, use freshly observed window identifiers. Other active benches were
not restarted.

The subsequent login-acceptance follow-up reopened the same native browser.
It now remains on the pending account chooser in workspace 10. No test fixture
tabs remain, and the fixture server and temporary MCP clients are stopped.
The private checkpoint is `native-login-checkpoint.json` under this bench's
session directory. It records only the phase, ambiguity and next action, not
email addresses, passwords or tokens. Full private chooser observations were
removed after recording this content-minimized checkpoint.
