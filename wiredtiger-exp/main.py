import os

import wiredtiger
from wiredtiger import wiredtiger_open

# Connect to the database and open a session
os.system("rm -rf WT_HOME")
os.makedirs("WT_HOME")

conn: wiredtiger.Connection = wiredtiger_open("WT_HOME", "create")
session: wiredtiger.Session = conn.open_session()

# Create a simple table
session.create("table:T", "key_format=S,value_format=S")

# Open a cursor and insert a record
cursor: wiredtiger.Cursor = session.open_cursor("table:T")

cursor.set_key("key1")
cursor.set_value("value2")
cursor.insert()

# Iterate through the records
cursor.reset()
for key, value in cursor:
    print(f"Got record: {key} : {value}")

conn.close()
