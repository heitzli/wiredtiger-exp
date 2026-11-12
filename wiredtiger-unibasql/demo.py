"""End-to-end demo: run DDL/inserts/selects against the stack via the typed Client."""

from pathlib import Path

from components import Client
from components.errors import WiredTigerError

TABLE = "test"
DDL = f"CREATE TABLE {TABLE} (id INT PRIMARY KEY, name STRING, salary DOUBLE, active BOOL, id2 INT PRIMARY KEY);"
INSERTS = [
    f"INSERT INTO {TABLE} VALUES (1, 'ada', 95.5, TRUE, 11);",
    f"INSERT INTO {TABLE} VALUES (2, 'grace', 90.0, FALSE, 21);",
    f"INSERT INTO {TABLE} VALUES (3, NULL, 88.25, TRUE, 31);",
    f"INSERT INTO {TABLE} VALUES (4, 'alan', NULL, FALSE, 41);",
    f"INSERT INTO {TABLE} VALUES (5, 'edsger', 77.75, NULL, 51);",
]
SELECT = f"SELECT * FROM {TABLE};"


def parse_header(header: str) -> list[tuple[str, str]]:
    """Header CSV ("name:TAG,...") -> [(name, TAG)]."""
    return [tuple(part.rsplit(":", 1)) for part in header.split(",")]  # type: ignore[return-value]


def render_cell(tag: str, cell: object) -> str:
    """Cell -> display string per header tag (BOOL arrives as 0/1 on the wire)."""
    if cell is None:
        return "NULL"
    if tag == "BOOL":
        return "True" if cell else "False"
    if tag == "DOUBLE":
        return repr(cell)
    return str(cell)


def render_row(columns: list[tuple[str, str]], row: list) -> str:
    """Format one row as "name=value" pairs joined by commas."""
    return ", ".join(f"{name}={render_cell(tag, cell)}" for (name, tag), cell in zip(columns, row))


if __name__ == "__main__":
    client = Client(
        name="in_code_client",
        broker_protocol_path=Path("protocol/broker.avpr").absolute(),
        protocol_path=Path("protocol/client.avpr").absolute(),
        broker_socket="/tmp/broker.sock",
        component_socket="/tmp/client.sock",
    )

    try:
        client._query(f"DROP TABLE {TABLE};")
    except Exception:  # noqa: BLE001 — first run has no leftover table
        pass

    print(client._query(DDL))
    for stmt in INSERTS:
        print(client._query(stmt))

    # streamed SELECT: a RemoteStream comes back; the header rides the init reply
    sel = client._query(SELECT, max_items=2)
    columns = parse_header(sel.header)
    print(sel.header)
    for row in sel:  # exhausting the iterator disposes the stream
        print(render_row(columns, row))

    print(sum(1 for _ in client._query(SELECT)))  # rows-only pass: the row count

    # wire-level probes via the typed client surface: one page, close, repage
    probe = client._query(SELECT)
    client.stream_next(probe.stream_id, max_items=2)
    print(client.stream_close(probe.stream_id))  # close cascade QP mirror -> EE mirror -> SM scan
    try:
        client.stream_next(probe.stream_id, max_items=1)  # closed id: typed error expected
    except WiredTigerError as e:
        print(f"WiredTigerError: {e}")

    print(client._query(f"CREATE INDEX test_idx ON {TABLE} (id, salary);"))
    print(client._query(f"DROP INDEX test_idx ON {TABLE};"))
    print(client._query(f"DROP TABLE {TABLE};"))
