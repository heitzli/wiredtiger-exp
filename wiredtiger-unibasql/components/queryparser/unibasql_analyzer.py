# NOTE: currently a mcok implementation but normally should check if the syntactically correct query is also
# semantically correct by checking table schemas etc through the catalog manager


from .expression import BinOp, ColumnRef, Expr, LiteralExpr, UnOp
from .unibasql_algebra import (
    AbstractAlgebraNode,
    Cross,
    Difference,
    Projection,
    ResolvedColumn,
    ResolvedTable,
    ResolvedValue,
    Selection,
    TableScan,
    TupleUpdate,
    Union,
    VirtualTableScan,
)
from .unibasql_ddl_nodes import AbstractDDLNode, IndexDDL, TableDDL
from .unibasql_tcl_nodes import AbstractTCLNode, TransactionNode, TxEndNode, TxStartNode
from .unibasql_unresolved import (
    AbstractStatement,
    DataType,
    DDLOp,
    DMLOp,
    EndTransaction,
    SetOperation,
    Star,
    StartTransaction,
    Transaction,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
    UnresolvedIndexDDL,
    UnresolvedLiteralExpr,
    UnresolvedQuery,
    UnresolvedSetOperation,
    UnresolvedTable,
    UnresolvedTableDDL,
)

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


class QueryAnalyzer:
    def __init__(self, mock_catalog: dict = MOCK_CATALOG) -> None:
        self.mock_catalog = mock_catalog

    def analyze_query(
        self, unresolved_query: UnresolvedQuery | AbstractStatement
    ) -> AbstractAlgebraNode | AbstractDDLNode | AbstractTCLNode:
        if isinstance(unresolved_query, UnresolvedDQL):
            resolved_tables = self._resolve_table(tables=unresolved_query.table)
            resolved_projection = self._resolve_projection(
                unresolved_projection=unresolved_query.projection, resolved_tables=resolved_tables
            )
            # TODO: currently unresolved
            expr = self._resolve_expr(expr=unresolved_query.expr, resolved_tables=resolved_tables)
            root = self._build_from(resolved_tables=resolved_tables)
            root = self._apply_selection(root, where=expr)
            root = self._apply_projection(root, projection=resolved_projection)

            return root
        if isinstance(unresolved_query, UnresolvedSetOperation):
            left = self.analyze_query(unresolved_query=unresolved_query.left)
            right = self.analyze_query(unresolved_query=unresolved_query.right)
            # TODO should check if the sets are compatible, e.g have the same columns!
            match unresolved_query.op:
                case SetOperation.UNION:
                    return Union(left=left, right=right)

                case SetOperation.EXCEPT:
                    return Difference(left=left, right=right)

        if isinstance(unresolved_query, UnresolvedDML):
            resolved_table = self._resolve_table(tables=[unresolved_query.table])
            if len(resolved_table) != 1:
                raise ValueError(
                    f"DML statement can only be performed on exactly one table. Provided {len(resolved_table)} tables {resolved_table}"
                )

            match unresolved_query.op:
                # TODO: should check if primary key is present!
                case DMLOp.INSERT:
                    all_columns = self._resolve_projection(
                        unresolved_projection="*",
                        resolved_tables=resolved_table,
                    )

                    if unresolved_query.columns:
                        insert_columns = self._resolve_projection(
                            unresolved_projection=unresolved_query.columns,
                            resolved_tables=resolved_table,
                        )
                    else:
                        insert_columns = all_columns

                    for tup in unresolved_query.values:
                        if len(tup) != len(insert_columns):
                            raise ValueError(
                                f"Length of columns ({len(insert_columns)}) "
                                f"does not match length of insert tuple ({len(tup)}, {tup})"
                            )

                    padded_values = self._pad_insert_values(
                        all_columns=all_columns,
                        insert_columns=insert_columns,
                        values=unresolved_query.values,
                    )

                    # NOTE: Resolved table has exactly on entry
                    left = TableScan(table=resolved_table[0])
                    right = VirtualTableScan(values=padded_values)
                    return Union(left=left, right=right, custom={"source": "insert"})
                case DMLOp.UPDATE:
                    all_columns = self._resolve_projection(unresolved_projection="*", resolved_tables=resolved_table)
                    resolved_expr = self._resolve_expr(unresolved_query.expr, resolved_tables=resolved_table)
                    update = self._build_update(
                        table=resolved_table[0], where=resolved_expr, setlist=unresolved_query.setlist
                    )
                    return update
                case DMLOp.DELETE:
                    resolved_expr = self._resolve_expr(unresolved_query.expr, resolved_tables=resolved_table)
                    left = TableScan(table=resolved_table[0])
                    if resolved_expr:
                        right = Selection(expr=resolved_expr, child=TableScan(table=resolved_table[0]))
                    else:
                        right = TableScan(table=resolved_table[0])

                    return Difference(left=left, right=right, custom={"source": "delete"})

        if isinstance(unresolved_query, UnresolvedTableDDL):
            match unresolved_query.op:
                case DDLOp.CREATE:
                    if unresolved_query.table.name in self.mock_catalog.keys():
                        raise ValueError(f"Table {unresolved_query.table.name} already exists")

                    resolved_columns = []
                    for col in unresolved_query.table_schema:
                        resolved_columns.append(
                            ResolvedColumn(
                                name=col.column.name,
                                qualifier=col.column.qualifier,
                                type=col.datatype,
                                constraint=col.constraint,
                            )
                        )
                    return TableDDL(
                        op=DDLOp.CREATE,
                        table=ResolvedTable(
                            name=unresolved_query.table.name,
                            alias=unresolved_query.table.alias,
                            table_schema=tuple(resolved_columns),
                        ),
                    )
                case DDLOp.DROP:
                    resolved_table = self._resolve_table([unresolved_query.table])
                    if len(resolved_table) != 1:
                        raise ValueError("Only can drop one table at a time")
                    return TableDDL(op=DDLOp.DROP, table=resolved_table[0])
        if isinstance(unresolved_query, UnresolvedIndexDDL):
            match unresolved_query.op:
                case DDLOp.CREATE:
                    existing_info = self.mock_catalog.get(unresolved_query.idx_name)
                    if existing_info is not None:
                        if existing_info:
                            raise ValueError(
                                f"Index name {unresolved_query.idx_name} collides with table {unresolved_query.idx_name}"
                            )
                        raise ValueError(f"Index {unresolved_query.idx_name} already exists")
                    resolved_table = self._resolve_table([unresolved_query.table])
                    # NOTE resuse of resolve proj maybe rename func
                    proj_col = self._resolve_projection(
                        unresolved_projection=unresolved_query.columns, resolved_tables=resolved_table
                    )
                    # TODO proper index lookup from catalog and allow for index names to be the same over different tables
                    # in the future

                    return IndexDDL(
                        op=DDLOp.CREATE, idx_name=unresolved_query.idx_name, table=resolved_table[0], columns=proj_col
                    )

                case DDLOp.DROP:
                    resolved_table = self._resolve_table([unresolved_query.table])
                    if unresolved_query.idx_name not in self.mock_catalog.keys():
                        raise ValueError(f"Index {unresolved_query.idx_name} does not exist")

                    return IndexDDL(op=DDLOp.DROP, table=resolved_table[0], idx_name=unresolved_query.idx_name)

        if isinstance(unresolved_query, Transaction):
            start = TxStartNode(tx_id=unresolved_query.tx_start.tx_id)
            end = TxEndNode(tx_result=unresolved_query.tx_end.tx_result)
            trees = []
            for statemnt in unresolved_query.statements:
                trees.append(self.analyze_query(statemnt))

            return TransactionNode(tx_start=start, tx_end=end, statements=trees)

        if isinstance(unresolved_query, StartTransaction):
            return TxStartNode(tx_id=unresolved_query.tx_id)

        if isinstance(unresolved_query, EndTransaction):
            return TxEndNode(tx_result=unresolved_query.tx_result)

        raise TypeError(f"Cannot analyze statement of type {type(unresolved_query).__name__}")

    def _build_update(
        self,
        table: ResolvedTable,
        where: Expr | None,
        setlist: list[tuple[UnresolvedColumn, UnresolvedLiteralExpr]],
    ) -> AbstractAlgebraNode:
        scan = TableScan(table=table)
        resolved_where = self._resolve_expr(where, resolved_tables=[table])
        assignments = tuple(
            (self._resolve_projection([column], [table])[0], self._resolve_expr(value, [table]))
            for column, value in setlist
        )

        selected = Selection(expr=resolved_where, child=scan) if resolved_where is not None else scan
        remaining = Difference(left=scan, right=selected)
        updated = TupleUpdate(assignments=assignments, child=selected)

        return Union(left=remaining, right=updated, custom={"source": "update"})

    def _pad_insert_values(
        self,
        all_columns: tuple[ResolvedColumn, ...],
        insert_columns: tuple[ResolvedColumn, ...],
        values: list[list[UnresolvedLiteralExpr]],
    ) -> list[tuple[ResolvedValue, ...]]:

        column_indices = {column.name: i for i, column in enumerate(all_columns)}

        padded_values = []

        for row in values:
            padded_row = [
                ResolvedValue(
                    type=column.type,
                    value=None,
                )
                for column in all_columns
            ]

            for column, value in zip(insert_columns, row):
                index = column_indices[column.name]

                padded_row[index] = ResolvedValue(
                    type=column.type if value.type == "null" else DataType(value.type),
                    value=value.value,
                )

            padded_values.append(tuple(padded_row))

        return padded_values

    def _resolve_expr(self, expr: Expr | None, resolved_tables: list[ResolvedTable]) -> Expr | None:
        if expr is None:
            return None
        if isinstance(expr, (ColumnRef, LiteralExpr)):
            return expr
        if isinstance(expr, UnresolvedColumn):
            candidates = [
                (table, column)
                for table in resolved_tables
                for column in table.table_schema
                if column.name == expr.name and (expr.qualifier is None or table.qualifier == expr.qualifier)
            ]

            if len(candidates) != 1:
                raise ValueError(f"Cannot resolve column {expr.name}")

            table, column = candidates[0]

            return ColumnRef(
                name=column.name,
                qualifier=table.qualifier,
                custom=expr.custom,
            )

        if isinstance(expr, UnresolvedLiteralExpr):
            return LiteralExpr(
                type=expr.type,
                value=expr.value,
                custom=expr.custom,
            )

        if isinstance(expr, BinOp):
            return expr.model_copy(
                update={
                    "left": self._resolve_expr(expr.left, resolved_tables),
                    "right": self._resolve_expr(expr.right, resolved_tables),
                }
            )

        if isinstance(expr, UnOp):
            return expr.model_copy(
                update={
                    "expr": self._resolve_expr(expr.expr, resolved_tables),
                }
            )

        raise TypeError(f"Unknown expression type: {type(expr).__name__}")

    def _build_from(self, resolved_tables: list[ResolvedTable]) -> AbstractAlgebraNode:
        if not resolved_tables:
            raise ValueError("At least one table is required")

        root: AbstractAlgebraNode = TableScan(table=resolved_tables[0])

        for table in resolved_tables[1:]:
            root = Cross(left=root, right=TableScan(table=table))

        return root

    def _apply_selection(self, root: AbstractAlgebraNode, where: Expr | None) -> AbstractAlgebraNode:
        if where is None:
            return root
        return Selection(expr=where, child=root)

    def _apply_projection(
        self, root: AbstractAlgebraNode, projection: tuple[ResolvedColumn, ...]
    ) -> AbstractAlgebraNode:
        return Projection(attributes=projection, child=root)

    def _resolve_projection(
        self, unresolved_projection: Star | list[UnresolvedColumn], resolved_tables: list[ResolvedTable]
    ) -> tuple[ResolvedColumn, ...]:
        if unresolved_projection == "*":
            return tuple(schema for table in resolved_tables for schema in table.table_schema)

        projection = []

        for uc in unresolved_projection:
            candidates = [
                (table, schema)
                for table in resolved_tables
                for schema in table.table_schema
                if schema.name == uc.name and (uc.qualifier is None or table.qualifier == uc.qualifier)
            ]

            if not candidates:
                raise ValueError(f"Column {uc.qualifier + '.' if uc.qualifier else ''}{uc.name} does not exist")

            if len(candidates) > 1:
                raise ValueError(f"Column {uc.name} is ambiguous")

            table, schema = candidates[0]

            projection.append(
                ResolvedColumn(
                    name=uc.name,
                    qualifier=table.qualifier,
                    type=schema.type,
                )
            )

        return tuple(projection)

    def _get_schema(self, table_name: str, alias: str | None = None) -> tuple[ResolvedColumn, ...]:
        schema: list[ResolvedColumn] = []
        table_info = self.mock_catalog.get(table_name)
        if not table_info:
            raise ValueError(f"Table {table_name} does not exist")
        qualifier = alias or table_name
        for k, v in table_info.items():
            schema.append(ResolvedColumn(name=k, type=v, qualifier=qualifier))

        return tuple(schema)

    def _resolve_table(self, tables: list[UnresolvedTable]) -> list[ResolvedTable]:
        resolved_tables: list[ResolvedTable] = []
        for table in tables:
            if table.name not in self.mock_catalog.keys():
                raise ValueError(f"Table {table.name} does not exist")
            table_schema = self._get_schema(table_name=table.name, alias=table.alias)
            resolved_tables.append(ResolvedTable(name=table.name, alias=table.alias, table_schema=table_schema))

        return resolved_tables
