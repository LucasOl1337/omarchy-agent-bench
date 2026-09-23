# Initial agent profile preparation

`agent-bench-profile` prepares a persistent Chromium directory for a named bench.
It runs from any harness with a terminal, without MCP, an extension, GUI or a
background service. It does not start Chromium or claim that logins still work.

```sh
agent-bench-profile prepare my-task          # dry-run; no directories created
agent-bench-profile prepare my-task --apply  # initial copy only
```

The only source is `BASE/desktop/browser-seed/chromium`, the official agent seed
prepared earlier. The only destination is
`BASE/desktop/sessions/my-task/chromium`. `BASE` is the runtime installation
containing this command. There is no CLI source override, empty-profile fallback,
credential conversion, or copying from the human browser or another bench.

The seed must be offline, owned by the current user, with a private root (0700),
and contain nonempty regular `Local State` and `Default/Preferences` files.
Prepare a consistent seed through the authorized offline workflow first if it is
missing. Do not use this command to take a live browser snapshot.

Every invocation inspects the destination first. A structurally prepared,
inactive destination returns `preserved` even when the seed is gone. An incomplete
destination is refused and left intact. There is no refresh, merge, overwrite or
repair mode; sessions renewed in a bench remain there across later invocations.

The command refuses Chromium Singleton locks, including stale or foreign locks;
it does not delete them or infer inactivity from a dead PID. The owner must
reconcile the browser state. Symlinks in any path component or profile entry,
regular files with multiple hardlinks, foreign owners, and special files are
also refused. This conservative first version does not follow even internal
profile links. Concurrent preparers serialize on an advisory seed directory lock.

Before and after copying it scans same-user processes for `--user-data-dir` and
open descriptors below either profile. Unreadable or indeterminate application
process identity is a refusal. The only protected-FD exception is the user
`systemd --user` manager (parent PID 1) and its `(sd-pam)` child, with exact
arguments, UID, parentage and the user's `init.scope` cgroup verified. Omarchy
denies even that UID access to those infrastructure descriptors. The JSON
`process_check.protected_infrastructure` reports the count; this is not a generic
exception for apps or browser processes. The complete source metadata is compared across the copy,
including inode, nanosecond modification/change times and size; file identity is
also checked around each read. The staging files are verified by SHA-256 against
the copied bytes. Digests, filenames inside the profile, argv, cookies and tokens
are not printed.

Only after those checks does Linux `renameat2(RENAME_NOREPLACE)` publish the
complete private staging directory. Even an empty destination appearing at that
instant is preserved. Runtime `DevToolsActivePort` is omitted. Copy errors remove
only this invocation's staging directory; the final profile never contains a
partially copied tree. An empty newly created session parent may remain.

## Limits and next step

- `/proc` checks are observations, not a lock honored by Chromium. The caller
  must keep the seed offline for the operation. A same-user program that opens
  and closes it between checks without changing observable metadata cannot be
  excluded; arbitrary hostile same-user processes or root are outside this
  mechanism. For a stronger concurrent-writer guarantee, use an owner-controlled
  immutable/read-only filesystem snapshot as the official seed and coordinate
  browser starts. Do not treat an advisory `flock` as that guarantee.
- SIGKILL or power loss can leave a private `.chromium-prepare-*` staging folder;
  a later invocation does not adopt or automatically delete it. Reconcile the
  original executor before cleaning its resources. Publication uses fsync but
  filesystem or hardware failures still require inspection after an uncertain
  result, rather than an overwrite attempt.
- This Linux command requires Python 3.11+, `/proc`, and libc/kernel support for
  `renameat2` without replacement. Missing capabilities cause refusal. It is
  portable across agent harnesses, not a Windows/macOS profile copier.

After a successful prepare, start/use the browser through `agent-bench` and
validate `lives_in`, the bench's `user_data_dir`, workspace 6–11, control state
and the authorized account visually. `prepared` means the files were provisioned;
it does not prove authentication, login freshness, ownership of tabs or permission
to send messages, submit forms or perform other external actions.

Exit 0 returns JSON with `would_prepare`, `prepared` or `preserved`; exit 2 returns
`refused` and a sanitized reason. Dry-run examines metadata/processes without
creating locks on disk, profiles or staging folders. As with ordinary filesystem
reads, the OS may update access times. No real profile is required by the tests:

```sh
cd desktop
python3 -m unittest test_bench_profile -v
```

To exercise the installation's filesystem rather than the default temporary
filesystem, set `AGENT_BENCH_PROFILE_FS_TEST_DIR` to an existing private test
directory. Fixtures still contain artificial data and clean up their own trees.
The real Omarchy Btrfs proof exposed stale enumeration from a directory opened
before it was populated, while the same tests passed on tmpfs. Enumeration now
opens a fresh `.` descriptor under the held directory before reading entries;
verification remains strict. An independent report of this platform behavior
exists in [Zig's Btrfs investigation](https://github.com/ziglang/zig/issues/17095).
