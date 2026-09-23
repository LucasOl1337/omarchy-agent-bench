# Named benches and persistent login

A bench name is `^[a-z0-9][a-z0-9-]{0,39}$`. **The Chromium profile lives in** `$PREFIX/desktop/sessions/NAME/chromium`. `stop` kills Xvnc and the browser process; cookies stay on disk. Disk GC (`agent-bench gc --apply`, default 30 days) skips folders with `.keep` and skips live units.

## Recipe

1. Pick a **stable** name per product front (`login-app`, `staging-admin`, `docs-publish`). Do not invent `login-app-2` for the next round.
2. `agent-bench ensure NAME` then `agent-bench keep NAME 'why this login must survive'`.
3. Confirm the account *in that window* (handle, settings, something only the owner can see).
4. Next round: same name, `ensure` / `cdp` again. Never `stop` mid-form.

Script: [`skills/named-login-bench/scripts/prepare`](../skills/named-login-bench/scripts/prepare)

```sh
skills/named-login-bench/scripts/prepare login-app
```

It runs `ensure`, writes `.keep`, prints CDP JSON. It never stops the bench. If `control` is `humano`, it exits non-zero so you wait.

## One name per concurrent GUI task

Concurrent GUI tasks use different names even when they use the same account.
Each bench has its own persistent profile; prepare a missing profile from the
offline agent seed, then verify the account there. Sequential handoff of the same
task can reuse its name after reconciling pending work. The input gate coordinates
human control; it is not an exclusive task lease. See [actor labels and task
ownership](task-ownership.md).

`padrao` starts at login as an empty spare. Do not park product logins there. Do not `stop padrao` unless the human asked.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_BENCH_OWNER` | inferred harness | Actor label in `owner.json`; later callers update `last_actor`, not the stable label |
| `AGENT_BENCH_IDLE_SECONDS` | `10800` | Minimum idle time; work pages, native processes or uncertain state prevent reaping |
| `AGENT_BENCH_GC_DAYS` | `30` | Disk GC age |
| `AGENT_BENCH_PREFIX` | `~/.local/share/omarchy-agent-bench` | Install / uninstall |

systemd limits per bench: `MemoryHigh=4G`, `MemoryMax=6G`, `TasksMax=4096`.

## Hygiene (tabs)

Reuse the search tab. After you discard a page or save proof, close *your* tab. Keep unfinished forms. Do not close another owner’s tabs. Do not resubmit to recreate a screenshot.
