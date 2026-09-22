# Native browser validation, 2026-09-22

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
| Real Google entry | Open `https://accounts.google.com/` in the native browser, observe via AT-SPI | Reached `Choose an account`. No matching `secure` error text on that initial screen. No account selected, password submitted, MFA attempted or login claimed |
| Preserve existing debugging sessions | Native status on existing `padrao` | Refused with `browser_not_native`, exit 1, session preserved |
| Missing identity must fail closed | Native status on absent profile | `profile_missing`, exit 1, no session directory created |
| Do not trust dispatch as effect | CUA `type_text` on Wikipedia | Returned success but field stayed empty. Reconciled before retry. Verified bench clipboard/X11 path succeeded instead |

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
login to an affected service in this conventional browser, using the authorized
account and pausing for personal verification when requested. No repeated login
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

The Google and Wikipedia tabs were closed after inspection. The test browser
was closed cooperatively and its dedicated MCP client disconnected. Only the
owned test bench and localhost fixture server were stopped. The prepared
`browser-normal-922` profile is retained with the test localStorage value, no
unsaved form or uncertain external submission. To continue the real login
acceptance, reopen this named bench through the native launcher and use its
freshly observed window identifiers. Other active benches were not restarted.
