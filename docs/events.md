# Event schema versioning contract

`agent-runner` writes structured JSONL events to `log_dir/events-YYYY-MM.jsonl`.
Downstream consumers (operators, plugins, monitoring tools) parse these events
and depend on stable field semantics.

## The contract

Event **kind names** (`round_start`, `agent_self_terminated`, etc.) are the
**version discriminator**. Existing kinds' payload fields are **append-only**:

- New fields **may** be added to an existing kind in future releases.
- Existing fields **will never** be renamed, removed, or change semantics.
- A breaking field change to an existing kind would require a **new kind name**
  (e.g. `round_start_v2`).

Consumers that branch on `evt["event"]` are implicitly versioned: a kind they
don't recognize is silently ignored; a kind they recognize has stable fields.

## Why no explicit `schema_version: int` field?

The kind name already serves as the discriminator. Adding `schema_version: int`
to every event would imply we maintain semantic versioning of payload schemas
— but the append-only contract is simpler and sufficient. Consumers don't need
to parse a version int; they just check the kind name.

## Consumer guidance

```python
for evt in stream_events_jsonl(log_dir):
    if evt["event"] == "round_start":
        round_num = evt["round_num"]      # guaranteed stable
        phase = evt.get("phase")          # guaranteed stable
        # any new fields → ignored unless we read them
    elif evt["event"] == "agent_self_terminated":
        reason = evt.get("reason", "")    # guaranteed stable
    # unknown kinds: silently ignore
```

## Catalog of built-in event kinds

See the `gen:event-kinds` auto-generated region in `docs/architecture.md` for the
current list. Plugin event kinds register via the
`agent_runner.event_kinds` entry_points group; their schemas are documented by
their plugin authors.
