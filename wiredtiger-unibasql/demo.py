from pathlib import Path

from components import Client
from components.errors import WiredTigerError

TABLE = "test"


def parse_header(header: str) -> list[tuple[str, str]]:
    """Header CSV ("name:TAG,...") -> [(name, TAG)]; the tags are the row CELL type authority."""
    return [tuple(part.rsplit(":", 1)) for part in header.split(",")]  # type: ignore[return-value]


def render_cell(tag: str, cell: object) -> str:
    """Cell -> display string per its header tag (BOOL arrives as 0/1 on the wire container)."""
    if cell is None:
        return "NULL"
    if tag == "BOOL":
        return "True" if cell else "False"
    if tag == "DOUBLE":
        return repr(cell)
    return str(cell)


if __name__ == "__main__":
    client = Client(
        name="in_code_client",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/client.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/client.sock",
    )
    q = client._query  # SQL text -> QP evaluate; consumers declare nothing locally (dummy client.avpr)

    # -- setup: idempotent pre-clean (the grammar has no IF EXISTS; a leftover table is harmless) --
    print("setup:")
    try:
        q(f"DROP TABLE {TABLE};")
        print("  dropped leftover table")
    except Exception:  # noqa: BLE001 — no table present is the common case
        pass

    # -- schema (note: TWO primary keys -> physical tuple order is id, id2, name, salary, active) ---
    print("\ncreate table:")
    reply = q(
        f"CREATE TABLE {TABLE} (id INT PRIMARY KEY, name STRING, salary DOUBLE, active BOOL, id2 INT PRIMARY KEY);"
    )
    print("  ", reply)

    # -- seed rows; NULLs are carried by the nullmap (stream-out shows them as None cells) ----------
    print("\ninsert (flows QP -> QO -> EE -> SM as Union(TableScan, VirtualTableScan)):")
    for stmt in [
        f"INSERT INTO {TABLE} VALUES (1, 'ada', 95.5, TRUE, 11);",
        f"INSERT INTO {TABLE} VALUES (2, 'grace', 90.0, FALSE, 21);",
        f"INSERT INTO {TABLE} VALUES (3, NULL, 88.25, TRUE, 31);",  # NULL name
        f"INSERT INTO {TABLE} VALUES (4, 'alan', NULL, FALSE, 41);",  # NULL salary
        f"INSERT INTO {TABLE} VALUES (5, 'edsger', 77.75, NULL, 51);",  # NULL active
    ]:
        print("  ", q(stmt))

    # -- streaming SELECT: paginate manually to show the header + wire-level shape ------------------
    ev_req = {"request": {"query": f"SELECT * FROM {TABLE};", "custom": {"tx_id": client.tx_id}}}
    init = client.call_remote("queryprocessor", "evaluate", ev_req)
    if init.get("stream_id") is None:
        raise RuntimeError(f"expected a streaming reply, got: {init}")
    columns = parse_header(init["value"]["columns"])
    print("\nstreaming select (manual pagination, 2 rows/page):")
    print(f"  header (type authority): {init['value']['columns']}")

    def render(row: list) -> str:
        return ", ".join(f"{name}={render_cell(tag, cell)}" for (name, tag), cell in zip(columns, row))

    rows_total = 0
    stream_id = init["stream_id"]
    while True:
        page = client.call_remote("queryprocessor", "stream_next", {"stream_id": stream_id, "max_items": 2})
        for row in page["rows"]:
            print(f"    {render(row)}")
        rows_total += len(page["rows"])
        if page["done"]:
            break
    print(f"  -> {rows_total} rows streamed (server disposed the stream on exhaustion)")

    # -- same consumption via the convenience generator (paged pulls, auto close) --------------------
    count = sum(1 for _ in client.stream("queryprocessor", "evaluate", ev_req))
    print(f"\nstream() generator: {count} rows, closed in finally")

    # -- early close: disposing the QP mirror cascades (generator finally) through EE and SM --------
    init2 = client.call_remote("queryprocessor", "evaluate", ev_req)
    client.call_remote("queryprocessor", "stream_next", {"stream_id": init2["stream_id"], "max_items": 2})
    print("\nearly close after one page (cascade QP mirror -> EE mirror -> SM scan):")
    print("  ", client.call_remote("queryprocessor", "stream_close", {"stream_id": init2["stream_id"]}))

    # -- error morphology: a closed id is a TYPED error at the client seat ---------------------------
    print("paging a closed stream (expected, typed):")
    try:
        client.call_remote("queryprocessor", "stream_next", {"stream_id": init2["stream_id"], "max_items": 1})
    except WiredTigerError as e:
        print(f"    WiredTigerError: {e}")

    # -- DDL round trip (unchanged from the original demo) --------------------------------------------
    # NOTE works with QueryAnalyzer(bypass_catalog=True)
    print("\nindex ddl:")
    print("  ", q(f"CREATE INDEX test_idx ON {TABLE} (id, salary);"))
    print("  ", q(f"DROP INDEX test_idx ON {TABLE};"))
    print("  ", q(f"DROP TABLE {TABLE};"))

    # print(client.begin_transaction())  # TCL demo kept for later
