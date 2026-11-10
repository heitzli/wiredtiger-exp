import wiredtiger
from wiredtiger import wiredtiger_open

if __name__ == "__main__":
    conn: wiredtiger.Connection = wiredtiger_open("WT_UNIBASQL_HOME")
    with conn.open_session() as session:
        with session.open_cursor("metadata:") as cur:
            for val in cur:
                print(val)
