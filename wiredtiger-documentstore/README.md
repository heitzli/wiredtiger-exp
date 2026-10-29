# wiredtiger-documentstore

Document store prototype on top of WiredTiger: schema-flexible, JSON-like
documents with a small query language (DocQL) supporting projections,
predicates, updates and deletes. Same component architecture as
`wiredtiger-kv`: broker, ANTLR-based query processor and storage manager run
as separate processes exchanging Avro-encoded messages over Unix domain
sockets.

## Run

Requires Python 3.14 and cmake/ninja/SWIG (the `wiredtiger` wheel is compiled
on install):

```bash
uv sync
```

Then, from `wiredtiger-documentstore/`, one terminal each:

```bash
uv run broker.py    # message broker (Avro RPC router, /tmp/broker.sock)
uv run qp.py        # query processor (DocQL)
uv run sm.py        # storage manager (WiredTiger)
uv run demo.py      # client demo: create_table/insert/select/update/delete
```
