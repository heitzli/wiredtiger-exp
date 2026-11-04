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
        DROP INDEX test_idx ON employees;
        """
    )
    # print(t1)
    alg = QueryAnalyzer().analyze_query(t1[0])
    alg.print_tree()
