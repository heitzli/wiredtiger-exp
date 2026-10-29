"""Unit tests for the expression evaluation helpers in components.queryprocessor.expression."""

from components.queryprocessor.expression import (
    AttributeRef,
    BinOp,
    LiteralExpr,
    UnOp,
    dict_satisfies,
)


def attr(name: str) -> AttributeRef:
    return AttributeRef(name=name)


def lit(value, type_: str) -> LiteralExpr:
    return LiteralExpr(value=value, type=type_)


def binop(op: str, left, right) -> BinOp:
    return BinOp(op=op, left=left, right=right)


LUC = {"name": "luc", "age": 27, "height": 1.79, "hobby": None}


def test_dict_satisfies_accepts_sql_style_single_equals():
    """The DocQL grammar emits '=' for equality (see DocQL.g4); the evaluator
    used to know only '==', so every WHERE equality silently evaluated False."""
    assert dict_satisfies(binop("=", attr("name"), lit("luc", "string")), LUC) is True
    assert dict_satisfies(binop("=", attr("name"), lit("test", "string")), LUC) is False


def test_dict_satisfies_double_equals_matches_like_single_equals():
    assert dict_satisfies(binop("==", attr("name"), lit("luc", "string")), LUC) is True
    assert dict_satisfies(binop("==", attr("name"), lit("test", "string")), LUC) is False


def test_dict_satisfies_comparison_operators():
    assert dict_satisfies(binop(">", attr("age"), lit(18, "int")), LUC) is True
    assert dict_satisfies(binop("<", attr("age"), lit(18, "int")), LUC) is False
    assert dict_satisfies(binop(">=", attr("age"), lit(27, "int")), LUC) is True
    assert dict_satisfies(binop("<=", attr("age"), lit(27, "int")), LUC) is True
    assert dict_satisfies(binop("!=", attr("age"), lit(27, "int")), LUC) is False


def test_dict_satisfies_conjunctions_disjunctions_and_negation():
    assert dict_satisfies(
        binop("AND", binop("=", attr("name"), lit("luc", "string")), binop("=", attr("age"), lit(27, "int"))),
        LUC,
    )
    assert not dict_satisfies(
        binop("AND", binop("=", attr("name"), lit("luc", "string")), binop("=", attr("age"), lit(99, "int"))),
        LUC,
    )
    assert dict_satisfies(
        binop("OR", binop("=", attr("name"), lit("luc", "string")), binop("=", attr("age"), lit(99, "int"))),
        LUC,
    )
    assert dict_satisfies(UnOp(op="NOT", expr=binop("=", attr("name"), lit("test", "string"))), LUC)
    assert not dict_satisfies(UnOp(op="NOT", expr=binop("=", attr("name"), lit("luc", "string"))), LUC)


def test_dict_satisfies_missing_attribute_is_false():
    """AttributeRef on an absent attribute raises KeyError, which evaluates to
    False instead of bubbling up (documents with fewer attributes are OK)."""
    assert dict_satisfies(binop("=", attr("ghost"), lit(1, "int")), LUC) is False


def test_dict_satisfies_none_expr_matches_everything():
    assert dict_satisfies(None, LUC) is True
    assert dict_satisfies(None, {}) is True


def test_dict_satisfies_without_id_can_use_underscore_id():
    doc = {"_id": "06f19bbb-7e73-4b95-9cf9-7a5f79b5a95c", "name": "luc"}
    assert dict_satisfies(binop("=", attr("_id"), lit(doc["_id"], "string")), doc) is True


def test_dict_satisfies_int_attribute_after_avro_normalize():
    """from_avro_expr normalizes wire floats back to int (AVRO-973), so the
    evaluator sees real ints for type-tagged int literals."""
    assert dict_satisfies(binop("=", attr("age"), lit(27, "int")), {"age": 27}) is True
    assert dict_satisfies(binop("=", attr("age"), lit(27, "int")), {"age": 30}) is False
