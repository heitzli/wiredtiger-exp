from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from ..errors import KVQLSyntaxError
from .algebra import AbstractAlgebraNode, DELETENode, GETNode, PUTNode
from .generated.KVQLLexer import KVQLLexer
from .generated.KVQLParser import KVQLParser
from .generated.KVQLVisitor import KVQLVisitor


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
        raise KVQLSyntaxError(f"Syntax error at line {line}, column {column}: {msg}")


class KVQLSyntaxExtractor(KVQLVisitor):
    def visitQuery(self, ctx: KVQLParser.QueryContext):
        return self.visit(tree=ctx.op())  # type: ignore

    def visitOp(self, ctx: KVQLParser.OpContext):
        if ctx.putStatement():
            return self.visit(tree=ctx.putStatement())  # type: ignore
        if ctx.getStatement():
            return self.visit(tree=ctx.getStatement())  # type: ignore
        if ctx.deleteStatement():
            return self.visit(tree=ctx.deleteStatement())  # type: ignore

        raise ValueError("Unsupported query")

    def visitGetStatement(self, ctx: KVQLParser.GetStatementContext) -> GETNode:  # type: ignore
        return GETNode(key=str(ctx.key.text))  # type: ignore

    def visitPutStatement(self, ctx: KVQLParser.PutStatementContext) -> PUTNode:  # type: ignore
        return PUTNode(key=str(ctx.key.text), value=ctx.value.text)  # type: ignore

    def visitDeleteStatement(self, ctx: KVQLParser.DeleteStatementContext) -> DELETENode:  # type: ignore
        return DELETENode(key=str(ctx.key.text))  # type: ignore


def parse_kvql(kvql: str) -> AbstractAlgebraNode:
    lexer = KVQLLexer(input=InputStream(data=kvql))
    tokens = CommonTokenStream(lexer=lexer)
    parser = KVQLParser(input=tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(listener=RaisingErrorListener())

    try:
        tree = parser.query()
    except KVQLSyntaxError as e:
        raise e

    return KVQLSyntaxExtractor().visit(tree=tree)  # type: ignore
