---
name: named-login-bench
description: Persistent logged-in Chromium on a named agent-bench. Load before recurring product UI, cookies that must survive rounds, or any ensure+keep+cdp flow. Never stop mid-form.
---

# Named login bench

Use one **stable bench name** per product front so cookies live in that session folder. Xvnc is cheap; killing Chromium mid-form is not.

## Before the first tab

1. Choose the name (`login-app`, `staging-admin`, …). Do not suffix `-2` next round.
2. Prepare and read the JSON:

```sh
# from a clone or the install prefix
skills/named-login-bench/scripts/prepare login-app
```

The script `ensure`s, writes `.keep`, prints CDP. It never `stop`s. If `control` is `humano`, it fails: wait for the operator to give the bench back.

3. Confirm the account in **that** window.
4. Forms: Playwright `connectOverCDP` on the printed endpoint. CUA only with `bench=NAME` and only if the page is opaque.

## Never

- Personal Chromium, `padrao`, workspaces 1–5.
- `agent-bench stop` or `gc --apply` in the middle of a round.
- Restart the bench to “refresh” while a form or uncertain submit exists.
- `hyprctl dispatch workspace`, grim of the human session, global ydotool, `resume` without being asked.

When a front is idle and has **no** login to preserve, `agent-bench stop NAME` (never `padrao`). Cookies remain on disk. Authenticated fronts keep `.keep`.

Details: https://github.com/LucasOl1337/omarchy-agent-bench/blob/main/docs/named-benches.md
