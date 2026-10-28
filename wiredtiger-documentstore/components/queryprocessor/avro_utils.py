from .docalgebra import (
    AbstractAlgebraNode,
    Attribute,
    CreateTable,
    Delete,
    DeleteTable,
    Insert,
    Projection,
    Selection,
    TableScan,
    Update,
)
from .expression import (
    AttributeRef,
    BinOp,
    Expr,
    LiteralExpr,
    UnOp,
)


def to_avro_expr(expr: Expr) -> dict:
    if isinstance(expr, LiteralExpr):
        branch = {"kind": "LiteralExpr", "value": expr.value, "type": expr.type}

    elif isinstance(expr, BinOp):
        branch = {"kind": "BinOp", "op": expr.op, "left": to_avro_expr(expr.left), "right": to_avro_expr(expr.right)}

    elif isinstance(expr, UnOp):
        branch = {"kind": "UnOp", "op": expr.op, "expr": to_avro_expr(expr.expr)}

    elif isinstance(expr, AttributeRef):
        branch = {"kind": "AttributeRef", "name": expr.name}

    else:
        raise ValueError(f"Unsupported expression: {type(expr).__name__}")

    return {"expr": branch}


def from_avro_expr(data: dict) -> Expr:
    branch = data.get("expr", data)
    kind = branch.get("kind")

    if kind == "LiteralExpr":
        return LiteralExpr(value=branch["value"], type=branch["type"])

    elif kind == "BinOp":
        return BinOp(op=branch["op"], left=from_avro_expr(branch["left"]), right=from_avro_expr(branch["right"]))

    elif kind == "UnOp":
        return UnOp(op=branch["op"], expr=from_avro_expr(branch["expr"]))

    elif kind == "AttributeRef":
        return AttributeRef(name=branch["name"])

    else:
        raise ValueError(f"Unsupported Avro Expr kind: {kind!r}; data={branch!r}")


def to_avro_literal(literal: LiteralExpr) -> dict:
    return {"kind": "LiteralExpr", "value": literal.value, "type": literal.type}


def to_avro_node(node: AbstractAlgebraNode) -> dict:
    kind = node.kind  # type: ignore XXX all that inherit from abstractalgebra node have a kind field

    if isinstance(node, CreateTable):
        branch = {"kind": kind, "table": node.table, "custom": node.custom}

    elif isinstance(node, DeleteTable):
        branch = {"kind": kind, "table": node.table, "custom": node.custom}

    elif isinstance(node, Delete):
        branch = {
            "kind": kind,
            "table": node.table,
            "expr": to_avro_expr(node.expr) if node.expr is not None else None,
            "custom": node.custom,
        }

    elif isinstance(node, Insert):
        branch = {
            "kind": kind,
            "table": node.table,
            "values": [{key: to_avro_literal(value) for key, value in row.items()} for row in node.values],
            "custom": node.custom,
        }

    elif isinstance(node, Update):
        branch = {
            "kind": kind,
            "table": node.table,
            "kv": [
                {
                    "key": key,
                    "value": to_avro_literal(value),
                    "custom": None,
                }
                for key, value in node.kv
            ],
            "expr": to_avro_expr(node.expr) if node.expr is not None else None,
            "custom": node.custom,
        }

    elif isinstance(node, TableScan):
        branch = {"kind": kind, "table": node.table, "custom": node.custom}

    elif isinstance(node, Projection):
        branch = {
            "kind": kind,
            "attributes": [{"kind": "AttributeRef", "name": attr.name, "custom": None} for attr in node.attributes],
            "child": to_avro_node(node.child),
            "custom": node.custom,
        }

    elif isinstance(node, Selection):
        branch = {
            "kind": kind,
            "child": to_avro_node(node.child),
            "expr": to_avro_expr(node.expr),
            "custom": node.custom,
        }

    else:
        raise ValueError(f"Unsupported algebra node: {type(node).__name__}")

    return {"node": branch}


def from_avro_node(data: dict) -> AbstractAlgebraNode:
    branch = data.get("node", data)
    kind = branch.get("kind")

    if kind == "CreateTable":
        return CreateTable.model_validate(branch)

    elif kind == "DeleteTable":
        return DeleteTable.model_validate(branch)

    elif kind == "Delete":
        return Delete(
            kind=kind,
            table=branch["table"],
            expr=(from_avro_expr(branch["expr"]) if branch.get("expr") is not None else None),
            custom=branch.get("custom"),
        )

    elif kind == "Insert":
        return Insert(
            kind=kind,
            table=branch["table"],
            values=[
                {
                    key: LiteralExpr(value=value["value"], type=value["type"]) for key, value in row.items()
                }  # TODO check if this is correct
                for row in branch["values"]
            ],
            custom=branch.get("custom"),
        )

    elif kind == "Update":
        return Update(
            kind=kind,
            table=branch["table"],
            kv=[
                (
                    item["key"],
                    LiteralExpr(
                        value=item["value"]["value"], type=item["value"]["type"]
                    ),  # TODO check if this is correct
                )
                for item in branch["kv"]
            ],
            expr=(from_avro_expr(branch["expr"]) if branch.get("expr") is not None else None),
            custom=branch.get("custom"),
        )

    elif kind == "TableScan":
        return TableScan.model_validate(branch)

    elif kind == "Projection":
        return Projection(
            attributes=tuple(Attribute(name=attr["name"]) for attr in branch["attributes"]),
            child=from_avro_node(branch["child"]),
            custom=branch.get("custom"),
        )

    elif kind == "Selection":
        return Selection(
            expr=from_avro_expr(branch["expr"]), child=from_avro_node(branch["child"]), custom=branch.get("custom")
        )

    else:
        raise ValueError(f"Unsupported Avro AlgebraNode kind: {kind!r}; data={branch!r}")
