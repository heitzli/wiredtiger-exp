from __future__ import annotations

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from ..errors import DocQLSyntaxError
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
from .expression import AttributeRef, BinOp, LiteralExpr, UnOp
from .generated.DocQLLexer import DocQLLexer
from .generated.DocQLParser import DocQLParser
from .generated.DocQLVisitor import DocQLVisitor


class RaisingErrorListener(ErrorListener):
    def syntaxError(
        self,
        recognizer,
        offendingSymbol,
        line,
        column,
        msg,
        e,
    ):
        raise DocQLSyntaxError(f"Syntax error at line {line}, column {column}: {msg}")


class DocQLSyntaxExtractor(DocQLVisitor):
    def visitQuery(self, ctx: DocQLParser.QueryContext):
        return self.visit(tree=ctx.queryMode())  # type: ignore

    def visitQueryMode(self, ctx: DocQLParser.QueryModeContext):
        if ctx.statementDDL():
            return self.visit(tree=ctx.statementDDL())  # type: ignore
        if ctx.statementDML():
            return self.visit(tree=ctx.statementDML())  # type: ignore
        if ctx.statementDQL():
            return self.visit(tree=ctx.statementDQL())  # type: ignore

    def visitStatementDDL(self, ctx: DocQLParser.StatementDDLContext):
        if ctx.createStatementDDL():
            return self.visit(tree=ctx.createStatementDDL())  # type: ignore
        if ctx.deleteStatementDDL():
            return self.visit(tree=ctx.deleteStatementDDL())  # type: ignore

    def visitCreateStatementDDL(self, ctx: DocQLParser.CreateStatementDDLContext) -> CreateTable:  # type: ignore
        return CreateTable(table=ctx.name.text)  # type: ignore

    def visitDeleteStatementDDL(self, ctx: DocQLParser.DeleteStatementDDLContext) -> DeleteTable:  # type: ignore
        return DeleteTable(table=ctx.name.text)  # type: ignore

    def visitStatementDQL(self, ctx: DocQLParser.StatementDQLContext) -> Projection:  # type: ignore
        table = TableScan(table=ctx.tableName.text)  # type: ignore
        if ctx.whereClause():
            whereclause = self.visit(ctx.whereClause())  # type: ignore
            source = Selection(expr=whereclause, child=table)  # type: ignore
        else:
            source = table

        projection = self.visit(ctx.projectionList())  # type: ignore
        return Projection(attributes=projection, child=source)  # type: ignore

    def visitProjectionList(self, ctx: DocQLParser.ProjectionListContext) -> tuple[str, ...]:  # type: ignore
        return tuple(self.visit(item) for item in ctx.attributeRef())  # type: ignore

    def visitAttributeRef(self, ctx: DocQLParser.AttributeRefContext) -> Attribute:  # type: ignore
        return Attribute(name=ctx.attribute.text)  # type: ignore

    def visitWhereClause(self, ctx: DocQLParser.WhereClauseContext) -> None:  # type: ignore
        return self.visit(ctx.expression())  # type: ignore

    def visitOrExpression(self, ctx: DocQLParser.OrExpressionContext) -> BinOp:  # type: ignore
        return BinOp(op="OR", left=self.visit(ctx.expression(0)), right=self.visit(ctx.expression(1)))  # type: ignore

    def visitAndExpression(self, ctx: DocQLParser.AndExpressionContext) -> BinOp:  # type: ignore
        return BinOp(op="AND", left=self.visit(ctx.expression(0)), right=self.visit(ctx.expression(1)))  # type: ignore

    def visitNotExpression(self, ctx: DocQLParser.NotExpressionContext) -> UnOp:  # type: ignore
        return UnOp(op="NOT", expr=self.visit(ctx.expression()))  # type: ignore

    def visitParenExpression(self, ctx: DocQLParser.ParenExpressionContext):
        return self.visit(ctx.expression())  # type: ignore

    def visitComparisonExpression(self, ctx: DocQLParser.ComparisonExpressionContext) -> BinOp:  # type: ignore
        return BinOp(  # type: ignore
            left=self.visit(ctx.operand(0)),  # type: ignore
            op=ctx.comparisonOperator().getText(),  # type: ignore
            right=self.visit(ctx.operand(1)),  # type: ignore
        )

    def visitValueOperand(self, ctx: DocQLParser.ValueOperandContext):
        return self.visit(ctx.value())  # type: ignore

    def visitAttributeOperand(self, ctx: DocQLParser.AttributeOperandContext) -> AttributeRef:  # type: ignore
        return AttributeRef(name=ctx.attribute.text)  # type: ignore

    def visitValue(self, ctx: DocQLParser.ValueContext) -> LiteralExpr:  # type: ignore
        if ctx.STRING():
            return LiteralExpr(value=ctx.STRING().getText()[1:-1].replace("''", "'"), type="string")  # type: ignore

        if ctx.NUMBER():
            text = ctx.NUMBER().getText()  # type: ignore
            return LiteralExpr(value=float(text) if "." in text else int(text), type="float" if "." in text else "int")

        if ctx.NULL():
            return LiteralExpr(value=None, type="null")

        raise ValueError(f"Unsupported value: {ctx.getText()}")

    def visitStatementDML(self, ctx: DocQLParser.StatementDMLContext):
        if ctx.insertStatementDML():
            return self.visit(ctx.insertStatementDML())  # type: ignore
        if ctx.updateStatementDML():
            return self.visit(ctx.updateStatementDML())  # type: ignore
        if ctx.deleteStatementDML():
            return self.visit(ctx.deleteStatementDML())  # type: ignore

    def visitDeleteStatementDML(self, ctx: DocQLParser.DeleteStatementDMLContext) -> Delete:  # type: ignore
        if ctx.whereClause():
            return Delete(table=ctx.tableName.text, expr=self.visit(ctx.whereClause()))  # type: ignore
        return Delete(table=ctx.tableName.text)  # type: ignore

    def visitUpdateStatementDML(self, ctx: DocQLParser.UpdateStatementDMLContext) -> Update:  # type: ignore
        kv = self.visit(ctx.setList())  # type: ignore
        if ctx.whereClause():
            return Update(table=ctx.tableName.text, kv=kv, expr=self.visit(ctx.whereClause()))  # type: ignore
        return Update(table=ctx.tableName.text, kv=kv)  # type: ignore

    def visitSetList(self, ctx: DocQLParser.SetListContext):  # type: ignore
        return [self.visit(item) for item in ctx.setStatement()]  # type: ignore

    def visitSetStatement(self, ctx: DocQLParser.SetStatementContext):  # type: ignore
        return (ctx.attribute.text, self.visit(ctx.value()))  # type: ignore

    def visitInsertStatementDML(self, ctx: DocQLParser.InsertStatementDMLContext) -> Insert:  # type: ignore
        table = ctx.tableName.text  # type: ignore
        values = self.visit(ctx.mapList())  # type: ignore
        return Insert(table=table, values=values)  # type: ignore

    def visitMapList(self, ctx: DocQLParser.MapListContext):  # type: ignore
        return [self.visit(m) for m in ctx.map_()]  # NOTE: map seems to be a reserved keyword # type: ignore

    def visitMap(self, ctx: DocQLParser.MapContext):  # type: ignore
        if ctx.kvpairList() is None:
            return {}
        kvlist = self.visit(ctx.kvpairList())  # type: ignore
        return {k: v for k, v in kvlist} if kvlist else {}  # type: ignore

    def visitKvpairList(self, ctx: DocQLParser.KvpairListContext):  # type: ignore
        return [self.visit(kvpair) for kvpair in ctx.kvpair()]  # type: ignore

    def visitKvpair(self, ctx: DocQLParser.KvpairContext):  # type: ignore
        key = ctx.STRING().getText()[1:-1].replace("''", "'")  # type: ignore
        value = self.visit(ctx.value())  # type: ignore
        return key, value


def parse_docql(docql: str) -> AbstractAlgebraNode:
    lexer = DocQLLexer(input=InputStream(data=docql))
    tokens = CommonTokenStream(lexer=lexer)
    parser = DocQLParser(input=tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(listener=RaisingErrorListener())

    try:
        tree = parser.query()
    except DocQLSyntaxError:
        raise

    return DocQLSyntaxExtractor().visit(tree=tree)  # type: ignore
