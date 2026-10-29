# wiredtiger-exp

Experiment playground for the [WiredTiger](https://github.com/wiredtiger/wiredtiger)
storage engine's Python bindings. The `wiredtiger/` fork ships the bindings with
complete PEP 561 type stubs and backs the two prototypes:

- **`wiredtiger-kv/`** — key/value prototype: a mini database server executing
  SQL-like `PUT/GET/DELETE` queries (KVQL) via a broker / query-processor /
  storage-manager process architecture.
- **`wiredtiger-documentstore/`** — document-store prototype: schema-flexible
  documents with a small query language (DocQL); same component architecture.
- **`wiredtiger/`** — the fork: Python bindings with generated `.pyi` stubs.
- **`wiredtiger-exp/`** — sandbox project used to develop and verify the stubs.

## Quick start

Each prototype manages its own environment; `wiredtiger` is pulled from the
fork and compiled on install:

```bash
cd wiredtiger-kv            # or: cd wiredtiger-documentstore
uv sync
```

Requires Python 3.14 and cmake, ninja, SWIG plus a C/C++ toolchain.

Then, from the prototype directory, one terminal each (entry-point names
differ slightly — see the prototype's README):

```bash
uv run broker.py    # message broker (Avro RPC router, /tmp/broker.sock)
uv run qp.py        # query processor (KVQL / DocQL)
uv run sm.py        # storage manager (WiredTiger)
uv run demo.py      # client demo
```

For building the fork and its type stubs from source, see [BUILDING.md](BUILDING.md).
