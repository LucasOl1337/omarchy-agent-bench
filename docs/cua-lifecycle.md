# Control plane and CUA lifetime

`systemd_env()` in `desktop/bench_ops.py` resolves the local user manager through
`/run/user/<uid>/bus`. It checks the UID, directory type and write permissions,
and requires an owned Unix socket, refusing symlinks or regular files. This
environment is passed only to systemd clients. It does not mutate the harness
environment or replace the isolated D-Bus/XDG context of the bench applications.
Consequently a cold MCP can start a bench without inheriting login variables.

The CLI, hub and idle reaper share `stop(name)`. The doctor uses
`bench_browser_snapshot(name)`, the same ownership gate as browser status, before
querying CDP. A stale state path or a Chromium belonging to the human or another
bench cannot turn diagnostics into a route to that browser.

Both MCP transports launch CUA in a transient user service:

```
agent-bench-cua-<name>-<unique-id>.service
  BindsTo=agent-bench@<name>.service
  After=agent-bench@<name>.service
  KillMode=control-group
  TimeoutStopSec=3s
```

The driver has its own cgroup, linked to its bench's lifetime. Stopping a bench
from any client stops its drivers, including drivers belonging to other MCP
processes. It does not stop a neighboring bench or its drivers. A pool closes
only units it created, using their exact unique names. Failed initialization
also cleans up its unit. No global process-name kill is used.

`systemd-run --pipe --wait` preserves the stdio MCP protocol. The driver receives
only the bench's desktop environment plus HOME, PATH, locale and temporary path;
it does not inherit the user manager's display or harness credentials. The
manager bus remains limited to the systemd client process.

## Environment precondition

Before starting `systemd-run`, both MCP routes call
`bench_cua_env.validate_cua_env`. The launcher requires nonempty bench name,
DISPLAY, XAUTHORITY, session-bus address and private XDG_RUNTIME_DIR, plus the
existing X11 backend context (`XDG_SESSION_TYPE=x11`, `GDK_BACKEND=x11`,
`QT_QPA_PLATFORM=xcb`, `GTK_USE_PORTAL=0`). It does not fill missing fields from
the harness or user manager. Missing DISPLAY or session bus must not allow the
driver to discover a host default.

The validator reads fresh status from the bench's existing control socket,
checks the name/environment against that response, and verifies the expected
owned runtime directory and regular Xauthority file. It does not use a stale
session.json or require a new server protocol/viewer version. The reported
Xvnc PID must belong to the named bench cgroup, have a matching actual executable,
and use this exact display and Xauthority in its arguments.

Only one `unix:path=...` D-Bus address, optionally with `guid=...`, is supported.
This is the format emitted by the current `dbus-run-session` setup. Percent
escaping follows the D-Bus specification; duplicate/unknown keys, malformed
escaping, alternative address lists, abstract sockets, autolaunch and executable
or network transports are refused. A path name alone does not prove ownership.
The validator connects with a one-second socket timeout and obtains Linux
`SO_PEERCRED`; the peer UID and process must belong to the same bench cgroup.
It sends no authentication bytes or D-Bus messages and cannot request service
activation. The socket closes on success and every error path. Server and peer
starttime/executable identities and membership are checked again before return.

Failure raises `CUA_ENV_UNVERIFIED` before the systemd client or driver is started.
Validation never starts/restarts a bench, opens a viewer, performs recovery or
resumes human control. It neither changes environment values nor touches HOME.
Successful validation still goes through the kernel-input guard below.

This is a conservative precondition, not a same-UID security boundary or atomic
reservation of the endpoints until driver exec. Unavailable status/proc/cgroup
data, changing identities or unsupported endpoint formats are refusals. Existing
drivers are not retroactively revalidated. A legitimate deployment using a
different private D-Bus transport needs an explicitly validated adapter; it must
not bypass this check or fall back to the human bus.

`test_bench_cua_env.py` uses temporary proc/cgroup trees and an owned Unix socket
with real kernel peer credentials, without D-Bus protocol, display or systemd.
It covers missing/malformed/mismatched context, a fake human/neighbor peer,
identity changes, timeouts, socket closure and refusal before process launch.
The older JSON-only systemd lifecycle fixture explicitly mocks this precondition;
that fixture tests unit cleanup, not private graphical endpoints.

References: [D-Bus address format](https://dbus.freedesktop.org/doc/dbus-specification.html#addresses)
and [Linux SO_PEERCRED](https://man7.org/linux/man-pages/man7/unix.7.html).

## Kernel input creation guard

New `launch_cua` processes execute `/usr/bin/python3 -I -S
desktop/bench_uinput_guard.py /absolute/path/to/cua-driver ...` inside their own
transient unit and isolated bench environment. Before `exec`, this fresh,
single-threaded wrapper loads a libseccomp filter with `no_new_privs=1`. Python's
isolated mode and disabled site initialization avoid Python environment/site
hooks before the filter. HOME, the bench desktop environment and the unit's
`BindsTo` / `KillMode=control-group` remain unchanged.

The supported ABI is Linux x86_64, little endian, 64-bit Python. The local
`linux/uinput.h` value `UI_DEV_CREATE = _IO('U', 1)` is `0x5501`; ioctl is syscall
16 on this ABI. The filter returns `EPERM` for ioctl argument 1 whose low 32 bits
equal `0x5501`, on any file descriptor. The kernel accepts an unsigned 32-bit
command, so masking is necessary to cover equivalent requests with upper bits
set. Other native syscall requests retain the default allow policy; libseccomp's
native-architecture check also prevents using an unsupported compatibility ABI.
This may affect a driver that tries to execute 32-bit helpers.

After loading, the wrapper checks both the normal command and an upper-bit alias
using only `fd=-1`. A missing libseccomp library/API, unsupported ABI, filter
failure or failed self-check exits with status 126 and
`CUA_UINPUT_GUARD_FAILED` on stderr **before executing the driver**. There is no
unprotected fallback. Success writes the following marker to stderr, leaving
stdout exclusively for MCP:

```text
CUA_UINPUT_GUARD_READY version=1 abi=linux-x86_64 ioctl=0x5501 mask=0xffffffff verified=1
```

The loaded filter and `no_new_privs` survive exec and are inherited by descendants.
The latter also prevents gaining privileges through setuid/file-capability exec.
No filter is installed in the MCP parent, user manager, neighboring unit or human
desktop. Already-running drivers are not changed: their owner must reconnect to
launch a guarded process. Closing a guarded driver still uses only its existing
unique unit cleanup.

This is a narrow prohibition of kernel input-device creation through this ioctl,
**not a general sandbox or proof that CUA input is confined**. It does not block
other input APIs, stop another process, constrain a device created before the
filter, or isolate files/network by itself. It also denies the same numerical
request if another device interprets it differently. Pixel-input behavior has
not been validated by the non-graphical tests and must not be inferred from the
success marker. Bench-scoped CLI execution and the validated browser route do
not acquire this CUA filter.

`test_bench_uinput_guard.py` runs the real filter only in owned subprocesses.
It compares invalid-descriptor and `/dev/null` ioctl results before/after exec,
checks descendant inheritance, upper-bit aliases, unaffected sibling processes,
and unchanged errors for other requests. Failure fixtures assert that exec is
never called. No test opens an input device, creates one, or sends input.

Primary references: [kernel uinput API](https://docs.kernel.org/input/uinput.html),
[seccomp inheritance and limits](https://docs.kernel.org/userspace-api/seccomp_filter.html),
[kernel ioctl implementation](https://github.com/torvalds/linux/blob/master/fs/ioctl.c),
[libseccomp rule API](https://github.com/seccomp/libseccomp/blob/main/doc/man/man3/seccomp_rule_add.3).

Run the unit suite without creating user services:

```sh
python -m unittest discover -s desktop -p 'test_*.py'
```

For an explicit systemd integration test on a logged-in Linux host:

```sh
AGENT_BENCH_RUN_SYSTEMD_TESTS=1 python -m unittest discover -s desktop -p 'test_bench_infra.py' -v
```

That test creates two uniquely named non-graphical parent services and two fake
MCP drivers. It checks their actual cgroups, stops one parent, verifies its driver
PID disappeared and confirms the neighboring driver still responds. Every test
unit is stopped in cleanup; the parent fixtures also expire in 60 seconds. It
does not open a bench, touch a viewer or prove native input behavior. Actual CUA
startup and native input require a separate authorized bench test.

Already-running MCP processes keep the older code and drivers until their owner
reconnects them. This change does not kill legacy processes retroactively. The
systemd route requires a local user manager; there is no fallback to the human
display or to a detached driver when the user bus is unavailable.

## Bounded hub transport

Both MCP entry points use `bench_transport.CuaChannel`, with nonblocking file
descriptors and `selectors` for the CUA stdio pipe. The hub's entire initialize + tools/list handshake has a 15-second
deadline; each subsequent request has a 60-second deadline across writing and
reading. Partial JSON lines, a child that stops reading stdin, or a stream of
progress notifications cannot extend that deadline. Frames are limited to
32 MiB. No background reader thread is created.

On a transport failure, the hub removes that driver from its pool and stops
only its uniquely named service. Existing cleanup has its own bounded systemctl
and process waits, so the transport deadline is not a total wall-clock bound
for ensure, initialization and cleanup together.

- `CUA_INIT_TIMEOUT`: initialization failed before a task action was sent.
- `CUA_TIMEOUT`: a read operation did not return a complete answer in time.
- `CUA_RESULTADO_INCERTO`: a mutation was attempted but no complete answer was
  received, including an EOF or malformed response. It may already have taken
  effect. Observe the application before deciding the next action.

There is no automatic retry, including for reads. A later explicit call creates
a fresh driver; it does not replay the failed request or reuse a late response.
Other pool entries remain alive. Calls for separate benches have separate I/O
locks; the stdio hub dispatch loop itself remains sequential, so an incoming
neighbor request can wait for the failing request's bounded timeout/cleanup.

## Bounded dedicated bridge

`agent-bench mcp NAME` also bounds each driver exchange. An `initialize` request
has a 15-second deadline; other requests and notification writes have 60 seconds.
The same absolute deadline covers writing and reading, so filling the input pipe
cannot grant a fresh read budget. These are per-exchange transport deadlines,
not a deadline for the client's whole session: the bridge may wait for the next
client line without holding the input gate. Launch, control acquisition, native
guard checks and unit cleanup remain separate bounded or externally governed
operations. There is no claim of a total wall-clock limit for the whole command.

The bridge forwards the client's original initialize request and response, then
the client's `notifications/initialized`. It does not synthesize initialization,
retry actions, reopen the driver or replay queued calls. Client notifications
are sent with a write deadline and do not require a response. `tools/call`
requires an ID so the input gate can remain held until the corresponding reply.

Requests are processed sequentially. A second request, notification or
cancellation waits for the current exchange to finish or time out; it cannot
interrupt an in-flight action. IDs may be reused after a response: there are no
simultaneously pending calls or a separate response pump. Native prechecks,
identity rechecks after control acquisition, discovery filters and tool-catalog
filters remain in place. The input gate is released before writing the reply
to a slow client, reading the next client message or stopping the failed driver.

Like the hub channel, the bridge consumes driver notifications and unrelated
messages while waiting for the matching response; it does not forward progress
notifications or support server-initiated sampling/elicitation callbacks. Those
messages cannot extend the deadline. This is an explicit compatibility limit
of the sequential transport, not a promise of full-duplex MCP.

Transport failure ends the dedicated connection with status 1 and stops only
its own unique CUA unit. Initialize failures use a JSON-RPC error; tool failures
use an MCP error result. Mutations without a complete response use
`CUA_RESULTADO_INCERTO`, including a blocked write, partial line, EOF or malformed
JSON: observe the application before deciding the next action. Cleanup failure
is reported on stderr and does not trigger broader process termination.

A later explicit reconnect starts a new bridge/driver; the client must initialize
it and decide the next action. No automatic retry exists for reads either.
Native refusals before dispatch keep the connection available, since no action
was attempted. A slow or silent client can still block its own stdio stream;
that wait does not retain the bench's input gate.

`test_bench_bridge.py` covers partial lines, full pipes, silence, notification
floods, EOF, send/read sharing one deadline, handshake preservation, gate release,
no replay and preservation of a neighboring fake process. All use short injected
deadlines and owned JSON-only subprocesses. No GUI or user service is touched.
