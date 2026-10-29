import wiredtiger
from wiredtiger import wiredtiger_open

from components import parse_docql

# NOTE: currently no way to delete an existing column from documents, only can set to none through update

"""
q = parse_docql("CREATE TABLE test123;")
q.print_tree()
print()
q2 = parse_docql("DELETE TABLE test123;")
q2.print_tree()
print()
q3 = parse_docql("SELECT name, blah, blob FROM abcdefg WHERE heigt = 1.8 AND age = 18 OR name = 'andy';")
q3.print_tree()
print()
q4 = parse_docql(
    "UPDATE test123 SET name = 'Luc', age = 28, height=1.79 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';"
)
q4.print_tree()

print()
q5 = parse_docql("INSERT INTO test VALUES ({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL})")
q5.print_tree()
print()
q4 = parse_docql("DELETE FROM test123 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';")
q4.print_tree()
"""
conn: wiredtiger.Connection = wiredtiger_open(
    "WT_DOC_HOME", "log=(enabled),statistics=(all),transaction_sync=(enabled)"
)
with conn.open_session() as session:
    with session.open_cursor("table:test") as cursor:
        while cursor.next() == 0:
            key = cursor.get_key()
            val = cursor.get_value()
            print(key)
            print(val)
            print()
