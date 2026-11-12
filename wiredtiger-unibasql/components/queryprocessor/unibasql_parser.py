from __future__ import annotations

from typing import TypeAlias
from uuid import uuid4

from antlr4 import CommonTokenStream, InputStream, Token
from antlr4.error.ErrorListener import ErrorListener
from antlr4.error.Errors import RecognitionException

from ..errors import UnibaSQLSyntaxError
from .expression import BinOp, Expr, UnOp
from .generated.UnibaSQLLexer import UnibaSQLLexer
from .generated.UnibaSQLParser import UnibaSQLParser
from .generated.UnibaSQLVisitor import UnibaSQLVisitor
from .unibasql_unresolved import (
    AbstractStatement,
    ColumnConstraint,
    DataType,
    DDLOp,
    DMLOp,
    EndTransaction,
    SetOperation,
    Star,
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

ParseResult: TypeAlias = Transaction | StartTransaction | EndTransaction | list[AbstractStatement]
SetMembersType: TypeAlias = tuple[UnresolvedColumn, UnresolvedLiteralExpr]


class RaisingErrorListener(ErrorListener):
    """Turns ANTLR lexer/parser errors into ``UnibaSQLSyntaxError``."""

    def syntaxError(
        self,
        recognizer,
        offendingSymbol: Token | None,
        line: int,
        column: int,
        msg: str,
        e: RecognitionException | None,
    ) -> None:
        """Raise immediately; errors abort the parse (no recovery)."""

        raise UnibaSQLSyntaxError(f"Syntax error at line {line}, column {column}: {msg}")


class UnibaSQLSyntaxExtractor(UnibaSQLVisitor):
    """Builds the unresolved AST from the ANTLR parse tree of a UnibaSQL query."""

    # +++ Query / Transactions

    def visitQuery(self, ctx: UnibaSQLParser.QueryContext) -> ParseResult:  # type: ignore
        """Visit whichever alternative the grammar matched (transaction, statement list, or standalone txn boundary)."""

        if ctx.transaction():
            return self.visit(ctx.transaction())  # type: ignore
        if ctx.statementList():
            return self.visit(ctx.statementList())  # type: ignore
        if ctx.txStart():
            return self.visit(ctx.txStart())  # type: ignore
        if ctx.txEnd():
            return self.visit(ctx.txEnd())  # type: ignore
        raise AssertionError("unreachable: the grammar requires one of the alternatives to match")

    def visitTxStart(self, ctx: UnibaSQLParser.TxStartContext) -> StartTransaction:  # type: ignore
        return StartTransaction(tx_id=ctx.txName.text if ctx.txName else str(uuid4()))  # type: ignore

    def visitTxEnd(self, ctx: UnibaSQLParser.TxEndContext) -> EndTransaction:  # type: ignore
        return EndTransaction(tx_result=TxResult.COMMIT if ctx.COMMIT() else TxResult.ROLLBACK)  # type: ignore

    def visitTransaction(self, ctx: UnibaSQLParser.TransactionContext) -> Transaction:  # type: ignore
        return Transaction(
            tx_start=self.visit(ctx.txStart()),  # type: ignore
            tx_end=self.visit(ctx.txEnd()),  # type: ignore
            statements=self.visit(ctx.statementList()),  # type: ignore
        )

    def visitStatementList(self, ctx: UnibaSQLParser.StatementListContext) -> list[AbstractStatement]:  # type: ignore
        return [self.visit(statement) for statement in ctx.unibasqlStatement()]  # type: ignore

    # ---

    # +++ Statement dispatch

    def visitDdlStatement(self, ctx: UnibaSQLParser.DdlStatementContext) -> AbstractStatement:  # type: ignore
        return self.visit(ctx.statementDDL())  # type: ignore

    def visitDqlStatement(self, ctx: UnibaSQLParser.DqlStatementContext) -> AbstractStatement:  # type: ignore
        return self.visit(ctx.statementDQL())  # type: ignore

    def visitDmlStatement(self, ctx: UnibaSQLParser.DmlStatementContext) -> AbstractStatement:  # type: ignore
        return self.visit(ctx.statementDML())  # type: ignore

    def visitSetOperationStatement(self, ctx: UnibaSQLParser.SetOperationStatementContext) -> UnresolvedSetOperation:  # type: ignore
        """Fold ``SELECT ... UNION/EXCEPT SELECT ...`` chains into left-nested UnresolvedSetOperations."""

        queries = ctx.statementDQL()

        result = self.visit(queries[0])  # type: ignore
        query_index = 1

        for child in ctx.children or []:  # type: ignore
            operator = child.getText().upper()  # type: ignore

            if operator in ("UNION", "EXCEPT"):
                result = UnresolvedSetOperation(
                    op=SetOperation(operator.lower()),  # type: ignore
                    left=result,  # type: ignore
                    right=self.visit(queries[query_index]),  # type: ignore
                )
                query_index += 1

        return result  # type: ignore

    # ---

    # +++ DQL

    def visitStatementDQL(self, ctx: UnibaSQLParser.StatementDQLContext) -> UnresolvedDQL:  # type: ignore
        projection: list[UnresolvedColumn] | Star = (
            self.visit(ctx.selectList()) if ctx.selectList() else "*"  # type: ignore
        )
        tables = self.visit(ctx.tableList())  # type: ignore
        where = self.visit(ctx.whereClause()) if ctx.whereClause() else None  # type: ignore
        return UnresolvedDQL(projection=projection, table=tables, expr=where)  # type: ignore

    def visitTableList(self, ctx: UnibaSQLParser.TableListContext) -> list[UnresolvedTable]:  # type: ignore
        return [self.visit(tableref) for tableref in ctx.tableRef()]  # type: ignore

    def visitTableRef(self, ctx: UnibaSQLParser.TableRefContext) -> UnresolvedTable:  # type: ignore
        # TODO this should be an unresolved tableref!
        return UnresolvedTable(name=ctx.name.text, alias=ctx.alias.text if ctx.alias else None)  # type: ignore

    def visitSelectList(self, ctx: UnibaSQLParser.SelectListContext) -> list[UnresolvedColumn]:  # type: ignore
        return [self.visit(col) for col in ctx.columnRef()]  # type: ignore

    def visitColumnRef(self, ctx: UnibaSQLParser.ColumnRefContext) -> UnresolvedColumn:  # type: ignore
        return UnresolvedColumn(
            name=ctx.column.text,  # type: ignore
            qualifier=ctx.table.text if ctx.table else None,  # type: ignore
        )

    # ---

    # +++ DDL

    def visitStatementDDL(self, ctx: UnibaSQLParser.StatementDDLContext) -> UnresolvedTableDDL | UnresolvedIndexDDL:  # type: ignore
        if ctx.createTable():
            return self.visit(ctx.createTable())  # type: ignore

        if ctx.dropTable():
            return self.visit(ctx.dropTable())  # type: ignore

        if ctx.createIndex():
            return self.visit(ctx.createIndex())  # type: ignore

        return self.visit(ctx.dropIndex())  # type: ignore

    # +++ Create Table

    def visitCreateTable(self, ctx: UnibaSQLParser.CreateTableContext) -> UnresolvedTableDDL:  # type: ignore
        table_def_list = ctx.tableDefList()  # type: ignore

        schema: list[TableDefTriple] = [
            self.visit(table_def_list.tableDefPrimary()),  # type: ignore
            *[self.visit(triple) for triple in table_def_list.tableDefTriple()],  # type: ignore
        ]
        return UnresolvedTableDDL(
            op=DDLOp.CREATE,
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
            table_schema=schema,
        )

    def visitTableDefPrimary(self, ctx: UnibaSQLParser.TableDefPrimaryContext) -> TableDefTriple:  # type: ignore
        return TableDefTriple(
            column=UnresolvedColumn(name=ctx.columnName.text),  # type: ignore
            datatype=DataType[ctx.type_.getText().upper()],  # type: ignore
            constraint=ColumnConstraint.PRIMARY_KEY,
        )

    def visitTableDefTriple(self, ctx: UnibaSQLParser.TableDefTripleContext) -> TableDefTriple:  # type: ignore
        constraint = self.visit(ctx.tableDefConstraint()) if ctx.tableDefConstraint() else None  # type: ignore
        return TableDefTriple(
            column=UnresolvedColumn(name=ctx.columnName.text),  # type: ignore
            datatype=DataType[ctx.type_.getText().upper()],  # type: ignore
            constraint=constraint,
        )

    def visitNotNullConstraint(self, ctx: UnibaSQLParser.NotNullConstraintContext) -> ColumnConstraint:  # type: ignore
        return ColumnConstraint.NOT_NULL

    def visitPrimaryKeyConstraint(self, ctx: UnibaSQLParser.PrimaryKeyConstraintContext) -> ColumnConstraint:  # type: ignore
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

    def visitDropIndex(self, ctx: UnibaSQLParser.DropIndexContext) -> UnresolvedIndexDDL:  # type: ignore
        return UnresolvedIndexDDL(  # type: ignore
            op=DDLOp.DROP,
            idx_name=ctx.indexName.text,  # type: ignore
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
        )

    # ---

    # +++ DML

    def visitStatementDML(self, ctx: UnibaSQLParser.StatementDMLContext) -> UnresolvedDML:  # type: ignore
        if ctx.insertStatementDML():
            return self.visit(ctx.insertStatementDML())  # type: ignore
        if ctx.deleteStatementDML():
            return self.visit(ctx.deleteStatementDML())  # type: ignore
        return self.visit(ctx.updateStatementDML())  # type: ignore

    def visitUpdateStatementDML(self, ctx: UnibaSQLParser.UpdateStatementDMLContext) -> UnresolvedDML:  # type: ignore
        setlist = self.visit(ctx.setList())  # type: ignore
        expr = self.visit(ctx.whereClause()) if ctx.whereClause() else None  # type: ignore
        return UnresolvedDML(
            op=DMLOp.UPDATE,
            table=UnresolvedTable(name=ctx.tableName.text),  # type: ignore
            expr=expr,
            setlist=setlist,
        )

    def visitSetList(self, ctx: UnibaSQLParser.SetListContext) -> list[SetMembersType]:  # type: ignore
        return [self.visit(setstatement) for setstatement in ctx.setStatement()]  # type: ignore

    def visitSetStatement(self, ctx: UnibaSQLParser.SetStatementContext) -> SetMembersType:  # type: ignore
        return (self.visit(ctx.columnRef()), self.visit(ctx.value()))  # type: ignore

    def visitDeleteStatementDML(self, ctx: UnibaSQLParser.DeleteStatementDMLContext) -> UnresolvedDML:  # type: ignore
        expr = self.visit(ctx.whereClause()) if ctx.whereClause() else None  # type: ignore
        return UnresolvedDML(op=DMLOp.DELETE, table=UnresolvedTable(name=ctx.tableName.text), expr=expr)  # type: ignore

    def visitInsertStatementDML(self, ctx: UnibaSQLParser.InsertStatementDMLContext) -> UnresolvedDML:  # type: ignore
        columns = self.visit(ctx.selectList()) if ctx.selectList() else None  # type: ignore
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

    # ---

    # +++ Expressions

    def visitOrExpression(self, ctx: UnibaSQLParser.OrExpressionContext) -> BinOp:  # type: ignore
        return BinOp(op="OR", left=self.visit(ctx.expression(0)), right=self.visit(ctx.expression(1)))  # type: ignore

    def visitAndExpression(self, ctx: UnibaSQLParser.AndExpressionContext) -> BinOp:  # type: ignore
        return BinOp(op="AND", left=self.visit(ctx.expression(0)), right=self.visit(ctx.expression(1)))  # type: ignore

    def visitNotExpression(self, ctx: UnibaSQLParser.NotExpressionContext) -> UnOp:  # type: ignore
        return UnOp(op="NOT", expr=self.visit(ctx.expression()))  # type: ignore

    def visitParenExpression(self, ctx: UnibaSQLParser.ParenExpressionContext) -> Expr:  # type: ignore
        return self.visit(ctx.expression())  # type: ignore

    def visitComparisonExpression(self, ctx: UnibaSQLParser.ComparisonExpressionContext) -> BinOp:  # type: ignore
        return BinOp(
            left=self.visit(ctx.operand(0)),  # type: ignore
            op=ctx.comparisonOperator().getText(),  # type: ignore
            right=self.visit(ctx.operand(1)),  # type: ignore
        )

    def visitValueOperand(self, ctx: UnibaSQLParser.ValueOperandContext) -> UnresolvedLiteralExpr:  # type: ignore
        return self.visit(ctx.value())  # type: ignore

    def visitColumnOperand(self, ctx: UnibaSQLParser.ColumnOperandContext) -> UnresolvedColumn:  # type: ignore
        return self.visit(ctx.columnRef())  # type: ignore

    # ---

    # +++ Values

    def visitValue(self, ctx: UnibaSQLParser.ValueContext) -> UnresolvedLiteralExpr:  # type: ignore
        """Classify a literal by token; numbers with ``.`` become doubles, others ints."""

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

        return UnresolvedLiteralExpr(value=None, type=DataType.NULL)

    # ---


def parse_unibasql(docql: str) -> ParseResult:  # type: ignore
    """Parse a UnibaSQL query string into its unresolved statement tree (or transaction)."""

    lexer = UnibaSQLLexer(input=InputStream(data=docql))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener=RaisingErrorListener())
    tokens = CommonTokenStream(lexer=lexer)
    parser = UnibaSQLParser(input=tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(listener=RaisingErrorListener())

    return UnibaSQLSyntaxExtractor().visit(parser.query())  # type: ignore
