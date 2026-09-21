> English · **[中文](README.zh.md)**

# Digest lamp

Print each `round_start.config_digest` from a running serve. Same-path
prompt-body edits move the digest; host-health edits do not. It is not a
supervisor and does not restart serve.

```bash
python examples/digest_watch/digest_watch.py --log-dir logs
```

Example line: `R4 5eda8cf12c6c changed=True`.

`config_changed=true` is not proof *your* swap landed — a `[goal]` ledger
listed in `[prompt] files` also moves the hash. See
[between_rounds](../between_rounds/) to perform the swap;
[events.md](../../docs/events.md) for the JSONL contract.
