# wiredtiger-kv

Key/value store prototype on top of WiredTiger: a mini database server that
executes `PUT`/`GET`/`DELETE` queries written in a small SQL-like language
(KVQL). The query processor parses KVQL with an ANTLR-generated parser and
turns it into an algebra plan that the storage manager executes with
WiredTiger.

Broker, query processor and storage manager run as separate processes and
exchange Avro-encoded messages over Unix domain sockets.

## Run

Requires Python 3.14 and cmake/ninja/SWIG (the `wiredtiger` wheel is compiled
on install):

```bash
uv sync
```

Then, from `wiredtiger-kv/`, one terminal each:

```bash
uv run brockoli.py     # message broker (Avro RPC router, /tmp/broker.sock)
uv run qp.py           # query processor (KVQL)
uv run storageman.py   # storage manager (WiredTiger)
uv run demo.py         # client demo: put/get/delete via KVQL
```
