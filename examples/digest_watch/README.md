# Digest lamp

Print each `round_start.config_digest` from a running serve. Same-path
prompt-body edits move the digest; host-health edits do not. It is not a
supervisor and does not restart serve.

**Philosophy** ([examples/README.md](../README.md)): a lamp, not a judge.
See what the child hashed (paths and bytes). The kill path does not read
this field; neither does this script except to print it.

```bash
python examples/digest_watch/digest_watch.py --log-dir logs
```

Example line: `R4 5eda8cf12c6c changed=True`.

`config_changed=true` is not proof *your* swap landed — a `[goal]` ledger
listed in `[prompt] files` also moves the hash. See
[between_rounds](../between_rounds/) to perform the swap;
[events.md](../../docs/events.md) for the JSONL contract.
