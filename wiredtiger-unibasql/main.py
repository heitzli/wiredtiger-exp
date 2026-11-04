from components import QueryAnalyzer, parse_unibasql

"""
MOCK_CATALOG = {
    "employees": {
        "id": DataType.INT,
        "name": DataType.STRING,
        "salary": DataType.DOUBLE,
        "active": DataType.BOOL,
        "department": DataType.INT,
    },
    "departments": {"id": DataType.INT, "name": DataType.STRING},
}
"""

if __name__ == "__main__":
    t1 = parse_unibasql(
        """
        Begin transaction t1;

        Select * from employees e;

        Update employees set name = 'test' where id > 1;

        Rollback;
        """
    )
    # print(t1)
    alg = QueryAnalyzer().analyze_query(t1)
    alg.print_tree()
