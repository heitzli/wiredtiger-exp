from __future__ import annotations

from uuid import uuid4

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from ..errors import UnibaSQLSyntaxError
from .expression import BinOp, UnOp
from .generated.UnibaSQLLexer import UnibaSQLLexer
from .generated.UnibaSQLParser import UnibaSQLParser
from .generated.UnibaSQLVisitor import UnibaSQLVisitor
from .unibasql_unresolved import (
    ColumnConstraint,
    DataType,
    DDLOp,
    DMLOp,
    EndTransaction,
    SetOperation,
    StartTransaction,
    TableDefTriple,
    Transaction,
    TxResult,
    UnresolvedColumn,
    UnresolvedDML,
    UnresolvedDQL,
    UnresolvedIndexDDL,
    UnresolvedLiteralExpr,
    UnresolvedSetOperation,
    UnresolvedTable,
    UnresolvedTableDDL,
)


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
        raise UnibaSQLSyntaxError(f"Syntax error at line {line}, column {column}: {msg}")


class UnibaSQLSyntaxExtractor(UnibaSQLVisitor):
    def visitQuery(self, ctx: UnibaSQLParser.QueryContext):
        if ctx.transaction():
            return self.visit(ctx.transaction())  # type: ignore
        if ctx.statementList():
            return self.visit(ctx.statementList())  # type: ignore
        if ctx.txStart():
            return self.visit(ctx.txStart())  # type: ignore
        if ctx.txEnd():
            return self.visit(ctx.txEnd())  # type: ignore

    def visitTxStart(self, ctx: UnibaSQLParser.TxStartContext) -> StartTransaction:  # type: ignore
        return StartTransaction(tx_id=ctx.txName.text if ctx.txName else str(uuid4()))

    def visitTxEnd(self, ctx: UnibaSQLParser.TxEndContext) -> EndTransaction:  # type: ignore
        return EndTransaction(tx_result=TxResult.COMMIT if ctx.COMMIT() else TxResult.ROLLBACK)

    def visitTransaction(self, ctx: UnibaSQLParser.TransactionContext) -> Transaction:  # type: ignore
        return Transaction(
            tx_start=self.visit(ctx.txStart()),  # type: ignore
            tx_end=self.visit(ctx.txEnd()),  # type: ignore
            statements=self.visit(ctx.statementList()),  # type: ignore
        )

    def visitStatementList(self, ctx: UnibaSQLParser.StatementListContext):  # type: ignore
        return [self.visit(statement) for statement in ctx.unibasqlStatement()]  # type: ignore

    def visitDdlStatement(self, ctx: UnibaSQLParser.DdlStatementContext):
        return self.visit(ctx.statementDDL())  # type: ignore

    def visitDqlStatement(self, ctx: UnibaSQLParser.DqlStatementContext):
        return self.visit(ctx.statementDQL())  # type: ignore

    def visitDmlStatement(self, ctx: UnibaSQLParser.DmlStatementContext):
        return self.visit(ctx.statementDML())  # type: ignore

    def visitSetOperationStatement(self, ctx: UnibaSQLParser.SetOperationStatementContext):  # type: ignore
        queries = ctx.statementDQL()

        result = self.visit(queries[0])  # type: ignore
        query_index = 1

        for child in ctx.children:  # type: ignore
            operator = child.getText()  # type: ignore

            if operator in ("UNION", "EXCEPT"):
                result = UnresolvedSetOperation(
                    op=SetOperation(operator.lower()),
                    left=result,  # type: ignore
                    right=self.visit(queries[query_index]),  # type: ignore
                )
                query_index += 1

        return result

    def visitStatementDQL(self, ctx: UnibaSQLParser.StatementDQLContext):  # type: ignore
        if ctx.selectList():
            projection: list[UnresolvedColumn] = self.visit(ctx.selectList())  # type: ignore
        else:
            projection = "*"  # type: ignore
        tables = self.visit(ctx.tableList())  # type: ignore
        where = self.visit(ctx.whereClause()) if ctx.whereClause else None  # type: ignore
        return UnresolvedDQL(projection=projection, table=tables, expr=where)  # type: ignore

    def visitTableList(self, ctx: UnibaSQLParser.TableListContext):  # type: ignore
        return [self.visit(tableref) for tableref in ctx.tableRef()]  # type: ignore

    def visitTableRef(self, ctx: UnibaSQLParser.TableRefContext):  # type: ignore
        # TODO this should be an unresolved tableref!
        return UnresolvedTable(name=ctx.name.text, alias=ctx.alias.text if ctx.alias else None)  # type: ignore

    def visitStatementDDL(self, ctx: UnibaSQLParser.StatementDDLContext):
        if ctx.createTable():
            return self.visit(ctx.createTable())  # type: ignore

        if ctx.dropTable():
            return self.visit(ctx.dropTable())  # type: ignore

        if ctx.createIndex():
            return self.visit(ctx.createIndex())  # type: ignore

        if ctx.dropIndex():
            return self.visit(ctx.dropIndex())  # type: ignore

    # +++ Create Table
    def visitCreateTable(self, ctx: UnibaSQLParser.CreateTableContext):  # type: ignore
        table_def_list = ctx.tableDefList()

        schema: list[TableDefTriple] = [
            self.visit(table_def_list.tableDefPrimary()),  # type: ignore
            *[self.visit(triple) for triple in table_def_list.tableDefTriple()],  # type: ignore
        ]
        return UnresolvedTableDDL(
            op=DDLOp.CREATE,
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
            table_schema=schema,
        )

    def visitTableDefPrimary(self, ctx: UnibaSQLParser.TableDefPrimaryContext):  # type: ignore
        return TableDefTriple(
            column=UnresolvedColumn(name=ctx.columnName.text),  # type: ignore
            datatype=DataType[ctx.type_.getText()],  # type: ignore
            constraint=ColumnConstraint.PRIMARY_KEY,  # type: ignore
        )

    def visitTableDefTriple(self, ctx: UnibaSQLParser.TableDefTripleContext):  # type: ignore

        constraint = self.visit(ctx.tableDefConstraint()) if ctx.tableDefConstraint() else None  # type: ignore
        return TableDefTriple(
            column=UnresolvedColumn(name=ctx.columnName.text),  # type: ignore
            datatype=DataType[ctx.type_.getText()],  # type: ignore
            constraint=constraint,
        )

    def visitNotNullConstraint(self, ctx: UnibaSQLParser.NotNullConstraintContext):  # type: ignore
        return ColumnConstraint.NOT_NULL

    def visitPrimaryKeyConstraint(self, ctx: UnibaSQLParser.PrimaryKeyConstraintContext):  # type: ignore
        return ColumnConstraint.PRIMARY_KEY

    # ---

    def visitDropTable(self, ctx: UnibaSQLParser.DropTableContext) -> UnresolvedTableDDL:  # type: ignore
        return UnresolvedTableDDL(op=DDLOp.DROP, table=UnresolvedTable(name=ctx.tableName.text))  # type: ignore

    def visitCreateIndex(self, ctx: UnibaSQLParser.CreateIndexContext) -> UnresolvedIndexDDL:  # type: ignore
        columns: list[UnresolvedColumn] = self.visit(ctx.selectList())  # type: ignore
        return UnresolvedIndexDDL(
            op=DDLOp.CREATE,
            idx_name=ctx.indexName.text,  # type: ignore
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
            columns=columns,
        )

    def visitSelectList(self, ctx: UnibaSQLParser.SelectListContext) -> list[UnresolvedColumn]:  # type: ignore
        return [self.visit(col) for col in ctx.columnRef()]  # type: ignore

    def visitColumnRef(self, ctx: UnibaSQLParser.ColumnRefContext) -> UnresolvedColumn:  # type: ignore
        return UnresolvedColumn(
            name=ctx.column.text,  # type: ignore
            qualifier=ctx.table.text if ctx.table else None,
        )

    def visitDropIndex(self, ctx: UnibaSQLParser.DropIndexContext) -> UnresolvedIndexDDL:  # type: ignore
        return UnresolvedIndexDDL(  # type: ignore
            op=DDLOp.DROP,
            idx_name=ctx.indexName.text,  # type: ignore
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
        )

    def visitStatementDML(self, ctx: UnibaSQLParser.StatementDMLContext):
        if ctx.insertStatementDML():
            return self.visit(ctx.insertStatementDML())  # type: ignore
        if ctx.deleteStatementDML():
            return self.visit(ctx.deleteStatementDML())  # type: ignore
        if ctx.updateStatementDML():
            return self.visit(ctx.updateStatementDML())  # type: ignore

    def visitUpdateStatementDML(self, ctx: UnibaSQLParser.UpdateStatementDMLContext):  # type: ignore
        setlist = self.visit(ctx.setList())  # type: ignore
        expr = self.visit(ctx.whereClause()) if ctx.whereClause() else None  # type: ignore
        return UnresolvedDML(
            op=DMLOp.UPDATE,
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
            expr=expr,
            setlist=setlist,
        )

    def visitSetList(self, ctx: UnibaSQLParser.SetListContext):  # type: ignore
        return [self.visit(setstatement) for setstatement in ctx.setStatement()]  # type: ignore

    def visitSetStatement(self, ctx: UnibaSQLParser.SetStatementContext):  # type: ignore
        return (self.visit(ctx.columnRef()), self.visit(ctx.value()))  # type: ignore

    def visitDeleteStatementDML(self, ctx: UnibaSQLParser.DeleteStatementDMLContext):  # type: ignore
        expr = self.visit(ctx.whereClause()) if ctx.whereClause() else None  # type: ignore
        return UnresolvedDML(op=DMLOp.DELETE, table=UnresolvedTable(name=ctx.tableName.text), expr=expr)  # type: ignore

    def visitOrExpression(self, ctx: UnibaSQLParser.OrExpressionContext):  # type: ignore
        return BinOp(op="OR", left=self.visit(ctx.expression(0)), right=self.visit(ctx.expression(1)))  # type: ignore

    def visitAndExpression(self, ctx: UnibaSQLParser.AndExpressionContext):  # type: ignore
        return BinOp(op="AND", left=self.visit(ctx.expression(0)), right=self.visit(ctx.expression(1)))  # type: ignore

    def visitNotExpression(self, ctx: UnibaSQLParser.NotExpressionContext):  # type: ignore
        return UnOp(op="NOT", expr=self.visit(ctx.expression()))  # type: ignore

    def visitParenExpression(self, ctx: UnibaSQLParser.ParenExpressionContext):  # type: ignore
        return self.visit(ctx.expression())  # type: ignore

    def visitComparisonExpression(self, ctx: UnibaSQLParser.ComparisonExpressionContext):  # type: ignore
        return BinOp(
            left=self.visit(ctx.operand(0)),  # type: ignore
            op=ctx.comparisonOperator().getText(),  # type: ignore
            right=self.visit(ctx.operand(1)),  # type: ignore
        )

    def visitValueOperand(self, ctx: UnibaSQLParser.ValueOperandContext):  # type: ignore
        return self.visit(ctx.value())  # type: ignore

    def visitColumnOperand(self, ctx: UnibaSQLParser.ColumnOperandContext):  # type: ignore
        colref: UnresolvedColumn = self.visit(ctx.columnRef())  # type: ignore
        return UnresolvedColumn(
            name=colref.name,  # type: ignore
            qualifier=colref.qualifier,
        )

    def visitInsertStatementDML(self, ctx: UnibaSQLParser.InsertStatementDMLContext) -> UnresolvedDML:  # type: ignore
        columns = (
            self.visit(ctx.selectList()) if ctx.selectList() else None  # type: ignore
        )
        values = self.visit(ctx.insertList())  # type: ignore
        return UnresolvedDML(
            op=DMLOp.INSERT,
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
            columns=columns,
            values=values,
        )

    def visitInsertList(self, ctx: UnibaSQLParser.InsertListContext) -> list[list[UnresolvedLiteralExpr]]:  # type: ignore
        return [self.visit(valrow) for valrow in ctx.valueRow()]  # type: ignore

    def visitValueRow(self, ctx: UnibaSQLParser.ValueRowContext) -> list[UnresolvedLiteralExpr]:  # type: ignore
        return self.visit(ctx.valueList())  # type: ignore

    def visitValueList(self, ctx: UnibaSQLParser.ValueListContext) -> list[UnresolvedLiteralExpr]:  # type: ignore
        return [self.visit(val) for val in ctx.value()]  # type: ignore

    def visitValue(self, ctx: UnibaSQLParser.ValueContext) -> UnresolvedLiteralExpr:  # type: ignore
        if ctx.STRING():
            return UnresolvedLiteralExpr(value=ctx.STRING().getText()[1:-1].replace("''", "'"), type="string")  # type: ignore

        if ctx.NUMBER():
            text = ctx.NUMBER().getText()  # type: ignore
            return UnresolvedLiteralExpr(
                value=float(text) if "." in text else int(text), type="double" if "." in text else "int"
            )

        if ctx.TRUE():
            return UnresolvedLiteralExpr(value=True, type="bool")

        if ctx.FALSE():
            return UnresolvedLiteralExpr(value=False, type="bool")

        if ctx.NULL():
            return UnresolvedLiteralExpr(value=None, type="null")


def parse_unibasql(docql: str):
    lexer = UnibaSQLLexer(input=InputStream(data=docql))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener=RaisingErrorListener())
    tokens = CommonTokenStream(lexer=lexer)
    parser = UnibaSQLParser(input=tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(listener=RaisingErrorListener())

    try:
        tree = parser.query()
    except UnibaSQLSyntaxError:
        raise
    return UnibaSQLSyntaxExtractor().visit(tree)
