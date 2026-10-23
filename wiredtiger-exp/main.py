import os

import wiredtiger
from wiredtiger import (
    stat,
    wiredtiger_open,
    wiredtiger_strerror,
)

# Connect to the database and open a session
os.system("rm -rf WT_HOME")
os.makedirs("WT_HOME")


# XXX only one can be opened at a time! enforced by file lock!

# NOTE: key value store the value is a byte sequence, no type information in the store, needs to be handled by the application to properly transform the stored values
conn: wiredtiger.Connection = wiredtiger_open("WT_HOME", "create,log=(enabled),statistics=(all)")
with conn.open_session() as session:
    print(conn.query_timestamp())
    # Create a simple table
    session.create("table:T", "key_format=S,value_format=S")
    # Open a cursor and insert a record
    with session.open_cursor("table:T", None, None) as cursor:
        cursor.set_key("key1")
        cursor.set_value("value2")
        cursor.insert()

        # Iterate through the records
        cursor.reset()
        cursor.set_key("key2")
        cursor.set_value("value2")
        cursor.insert()

        # Iterate through the records
        cursor.reset()
        cursor.set_key("key3")
        cursor.set_value("value2")
        cursor.insert()

        # Iterate through the records
        cursor.reset()
        cursor.set_key("key1")
        if err := cursor.search():
            raise ValueError(wiredtiger_strerror(err))
        v = cursor.get_value()
        print(v)
    session2 = conn.open_session()
    cur2 = session2.open_cursor("statistics:table:T")
    cur2.set_key(stat.dsrc.btree_entries)
    cur2.search()
    print(cur2.get_value())

    # conn.close()
