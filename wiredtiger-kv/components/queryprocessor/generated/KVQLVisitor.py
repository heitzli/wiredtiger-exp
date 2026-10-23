# Generated from KVQL.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .KVQLParser import KVQLParser
else:
    from KVQLParser import KVQLParser

# This class defines a complete generic visitor for a parse tree produced by KVQLParser.

class KVQLVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by KVQLParser#query.
    def visitQuery(self, ctx:KVQLParser.QueryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KVQLParser#op.
    def visitOp(self, ctx:KVQLParser.OpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KVQLParser#putStatement.
    def visitPutStatement(self, ctx:KVQLParser.PutStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KVQLParser#getStatement.
    def visitGetStatement(self, ctx:KVQLParser.GetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by KVQLParser#deleteStatement.
    def visitDeleteStatement(self, ctx:KVQLParser.DeleteStatementContext):
        return self.visitChildren(ctx)



del KVQLParser