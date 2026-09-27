# Conventional Chromium with native controls

Use this opt-in route when a task needs an ordinary persistent Chromium session
without a DevTools endpoint. It keeps the existing CDP workflow unchanged. It is
not an anti-detection guarantee and does not bypass site authentication checks.

## Open and verify

1. Prepare the named bench profile using the existing offline provisioning flow.
   An existing profile is preserved. Provisioning does not prove authentication.
2. Run `agent-bench-native open NAME URL`. This requires the bench's existing
   `chromium/Default`, launches the packaged Chromium executable directly and
   omits remote debugging and WebDriver flags. Global Chromium wrapper flags are
   not inherited. A running debugging browser is refused, never restarted.
3. Run `agent-bench-native status NAME` and `agent-bench status NAME`.
   Require the native browser's PID and profile to belong to NAME, a workspace
   in 6–11 and agent control. Status does not connect to CDP or enumerate tabs.
4. Connect a fresh `agent-bench mcp NAME` client, or use the current guarded
   multiplexed MCP. Discover the browser through `list_windows`, then call
   `get_window_state` with its observed PID and window ID.

The browser uses the same per-bench profile and `password-store=basic` convention
as the existing runtime. This change does not migrate credential encryption,
read the human keyring or certify transferred logins. File permissions and
separate displays are not a malware sandbox.

## Fast observe, act, verify

- Read through native AT-SPI accessibility, enabled at launch. For frequent
  reads use `get_window_state` with `include_screenshot:false` and a bounded
  `max_elements`. Use `query` to reduce returned text, not as a promise to avoid
  walking the tree.
- Obtain a screenshot initially and whenever the tree is missing or ambiguous.
  A scaled screenshot changes pixel coordinates. Use observed element handles
  or the coordinate system returned with the capture, never guessed offsets.
- Prefer a current `element_token` for clicks. Tokens expire with newer
  snapshots. After navigation, observe again before choosing the next action.
- For text, use the bench-only clipboard, read it back, focus/select the
  intended field and paste. Verify the resulting field. In the public-page
  validation, CUA `type_text` reported success without entering even ASCII.
  Its AT-SPI path also has a known Unicode truncation issue. The validated
  fallback is `agent-bench exec NAME -- xdotool ...` on the inherited display,
  using the current AT-SPI field bounds for focus and Ctrl+V for insertion.
  For ordinary non-secret fields, Ctrl+A/C plus bench clipboard readback can
  verify exact input. Do not copy password fields to logs or evidence.
- Foreground input means the bench's own X11 foreground, never the human
  compositor. All mutations still use the human-control gate.
- After an uncertain action, observe before retrying. Never replay a submit to
  obtain evidence.

`bench_cdp`, `bench_web` and CDP-based browser tools are not available for this
browser. Use native window tools instead. This route does not provide automatic
per-tab ownership enforcement: dedicate the named browser to one mission and
operate only tabs opened by that mission. Close completed own tabs, preserve
unsaved forms and record any pending human authentication.

## Limits

Some pages expose incomplete accessibility trees. Canvas needs pixels.
Native reading speed depends on page size and must be measured on the task's
pages. Sites can still require password entry, MFA, CAPTCHA or human review.
No header, fingerprint, `navigator.webdriver` property or site script is patched.
The absence of remote debugging does not prove that a service will accept login.

Validation evidence and remaining gaps are recorded in
[browser-native-validation.md](browser-native-validation.md).
