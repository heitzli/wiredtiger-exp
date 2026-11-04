from components import QueryAnalyzer, parse_unibasql

if __name__ == "__main__":
    t1 = parse_unibasql(
        """
        SELECT e.id, d.id FROM employees e, departments d WHERE e.id = d.id OR e.id > 5 AND NOT e.id <> e.id;
        """
    )
    print(t1)
    alg = QueryAnalyzer().analyze_query(t1[0])
    alg.print_tree()
