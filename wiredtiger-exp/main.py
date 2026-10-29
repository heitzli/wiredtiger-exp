import os
import uuid
from pydoc import doc

import wiredtiger
from wiredtiger import (
    stat,
    wiredtiger_open,
    wiredtiger_strerror,
)

# Connect to the database and open a session
os.system("rm -rf WT_HOME")
os.makedirs("WT_HOME")

"""
uS means the key is (identifier, attribute).
identifier use UUIDS as binary
attribute use String name of attribute
S means the value begins with a type tag.
u means the remaining value field is an opaque byte array.
"""

uid = uuid.uuid4()

uid_bytes = uid.bytes

print(uid)  # e.g. 550e8400-e29b-41d4-a716-446655440000
print(uid_bytes)  # 16 raw bytes
print(len(uid_bytes))  # 16

# XXX only one can be opened at a time! enforced by file lock!

# NOTE: key value store the value is a byte sequence, no type information in the store, needs to be handled by the application to properly transform the stored values

conn: wiredtiger.Connection = wiredtiger_open("WT_HOME", "create,log=(enabled),statistics=(all)")
with conn.open_session() as session:
    print(conn.query_timestamp())
    # Create a simple table
    session.create("table:T", "key_format=uS,value_format=Su")
    # Open a cursor and insert a record
    with session.open_cursor("table:T", None, None) as cursor:
        doc_id = uuid.uuid4().bytes
        attr1 = "name"
        attr2 = "age"
        attr3 = "hobby"
        cursor.set_key(doc_id, attr1)
        cursor.set_value("str", "Luc".encode("utf-8"))
        cursor.insert()
        cursor.set_key(doc_id, attr2)
        cursor.set_value("int", bytes(27))
        cursor.insert()
        cursor.set_key(doc_id, attr3)
        cursor.set_value("str", "Judo".encode("utf-8"))
        cursor.insert()

        cursor.reset()

        cursor.set_key(doc_id, "")
        ret = cursor.search_near()
        # ret = cursor.next()
        if ret == 1:
            key = cursor.get_key()
            print(key)
            value = cursor.get_value()
            print(value)
            ret = cursor.next()
        if ret > 0:
            ret = cursor.next()
        while ret == 0:
            key = cursor.get_key()
            print(key)
            value = cursor.get_value()
            print(value)
            ret = cursor.next()
