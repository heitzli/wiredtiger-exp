from components import parse_unibasql

if __name__ == "__main__":
    t1 = parse_unibasql(
        """
        SELECT * FROM test t, testest tt WHERE tt.col1 = 'val1' OR t.col1 <> TRUE

        UNION

        SELECT * FROM test t, testest tt WHERE tt.col1 = 'val1' OR t.col1 <> TRUE

        EXCEPT

        SELECT * FROM test t, testest tt WHERE tt.col1 = 'val1' OR t.col1 <> TRUE;
        """
    )
    print(t1)
