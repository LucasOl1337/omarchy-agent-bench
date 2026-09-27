# Actor labels and task ownership

`owner.json` is metadata, not a lock or permission. A bench still requires a
unique name per concurrent task. The label does not identify a unique executor,
prove that the executor is alive, or prevent another same-user client from acting.

New records preserve the first observed requester in `owner` and `claimed_at`.
Subsequent `start`/`ensure` calls update `last_actor` and `last_seen_at` without
transferring that label. A failed start may still record a requester. Direct
systemd startup can record `systemd`; unknown harnesses can record `unknown`.
These labels are descriptive and never restrict the operation.

Older records represented the last writer. They retain their value with
`owner_origin: legacy_label`; no original creator is inferred. New records use
`owner_origin: first_observed_actor`. The names remain compatible with existing
status/viewer consumers; no `created_by` history is invented.

Version 2 metadata uses the persistent session record as authority and keeps a
runtime mirror for older consumers. A short flock serializes metadata changes;
atomic rename avoids partial JSON. This lock covers only metadata writes, not
the work being performed. Legacy processes can still overwrite their old files;
reconnecting an MCP loads the new behavior without restarting its desktop.

Exclusive task reservations remain future work. The shared input lock coordinates
human handoff, and web missions track tabs; neither elects a unique task executor.
A future reservation must cover CLI, MCP, native RPC, stop and recovery together.
Previously issued raw CDP connections cannot be revoked by adding a discovery
check. Do not advertise universal exclusivity from an owner label or local flock.
