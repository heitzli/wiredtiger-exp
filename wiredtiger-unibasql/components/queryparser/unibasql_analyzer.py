# NOTE: currently a mcok implementation but normally should check if the syntactically correct query is also
# semantically correct by checking table schemas etc through the catalog manager


from .expression import BinOp, ColumnRef, Expr, LiteralExpr, UnOp
from .unibasql_algebra import (
    AbstractAlgebraNode,
    Cross,
    Projection,
    ResolvedColumn,
    ResolvedTable,
    Selection,
    TableScan,
)
from .unibasql_unresolved import (
    DataType,
    Star,
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
    def analyze_query(self, unresolved_query: UnresolvedQuery) -> AbstractAlgebraNode:
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
            pass
        if isinstance(unresolved_query, UnresolvedDML):
            pass
        if isinstance(unresolved_query, UnresolvedTableDDL):
            pass

        if isinstance(unresolved_query, UnresolvedIndexDDL):
            pass

        # TODO check TX
        #
        #
        #

    def _resolve_expr(self, expr: Expr | None, resolved_tables: list[ResolvedTable]) -> Expr | None:
        if expr is None:
            return None
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
        table_info = MOCK_CATALOG.get(table_name)
        if not table_info:
            raise ValueError(f"Table {table_name} does not exist")
        for k, v in table_info.items():
            schema.append(ResolvedColumn(name=k, type=v, qualifier=alias))

        return tuple(schema)

    def _resolve_table(self, tables: list[UnresolvedTable]) -> list[ResolvedTable]:
        resolved_tables: list[ResolvedTable] = []
        for table in tables:
            if table.name not in MOCK_CATALOG.keys():
                raise ValueError(f"Table {table.name} does not exist")
            table_schema = self._get_schema(table_name=table.name, alias=table.alias)
            resolved_tables.append(ResolvedTable(name=table.name, alias=table.alias, table_schema=table_schema))

        return resolved_tables
