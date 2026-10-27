from components import parse_docql

# NOTE: currently no way to delete an existing column from documents, only can set to none through update

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
q5 = parse_docql("INSERT INTO test VALUES ({'name':'luc', 'age':27, 'height':1.79}, {}, {'name':'test', 'age':29})")
q5.print_tree()
print()
q4 = parse_docql("DELETE FROM test123 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';")
q4.print_tree()
