# Generated from KVQL.g4 by ANTLR 4.13.2
# encoding: utf-8
from antlr4 import *
from io import StringIO
import sys
if sys.version_info[1] > 5:
	from typing import TextIO
else:
	from typing.io import TextIO

def serializedATN():
    return [
        4,1,6,32,2,0,7,0,2,1,7,1,2,2,7,2,2,3,7,3,2,4,7,4,1,0,1,0,3,0,13,
        8,0,1,0,1,0,1,1,1,1,1,1,3,1,20,8,1,1,2,1,2,1,2,1,2,1,3,1,3,1,3,1,
        4,1,4,1,4,1,4,0,0,5,0,2,4,6,8,0,0,29,0,10,1,0,0,0,2,19,1,0,0,0,4,
        21,1,0,0,0,6,25,1,0,0,0,8,28,1,0,0,0,10,12,3,2,1,0,11,13,5,4,0,0,
        12,11,1,0,0,0,12,13,1,0,0,0,13,14,1,0,0,0,14,15,5,0,0,1,15,1,1,0,
        0,0,16,20,3,4,2,0,17,20,3,6,3,0,18,20,3,8,4,0,19,16,1,0,0,0,19,17,
        1,0,0,0,19,18,1,0,0,0,20,3,1,0,0,0,21,22,5,1,0,0,22,23,5,5,0,0,23,
        24,5,5,0,0,24,5,1,0,0,0,25,26,5,2,0,0,26,27,5,5,0,0,27,7,1,0,0,0,
        28,29,5,3,0,0,29,30,5,5,0,0,30,9,1,0,0,0,2,12,19
    ]

class KVQLParser ( Parser ):

    grammarFileName = "KVQL.g4"

    atn = ATNDeserializer().deserialize(serializedATN())

    decisionsToDFA = [ DFA(ds, i) for i, ds in enumerate(atn.decisionToState) ]

    sharedContextCache = PredictionContextCache()

    literalNames = [ "<INVALID>", "<INVALID>", "<INVALID>", "<INVALID>", 
                     "';'" ]

    symbolicNames = [ "<INVALID>", "PUT", "GET", "DELETE", "SEMI", "ID", 
                      "WS" ]

    RULE_query = 0
    RULE_op = 1
    RULE_putStatement = 2
    RULE_getStatement = 3
    RULE_deleteStatement = 4

    ruleNames =  [ "query", "op", "putStatement", "getStatement", "deleteStatement" ]

    EOF = Token.EOF
    PUT=1
    GET=2
    DELETE=3
    SEMI=4
    ID=5
    WS=6

    def __init__(self, input:TokenStream, output:TextIO = sys.stdout):
        super().__init__(input, output)
        self.checkVersion("4.13.2")
        self._interp = ParserATNSimulator(self, self.atn, self.decisionsToDFA, self.sharedContextCache)
        self._predicates = None




    class QueryContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def op(self):
            return self.getTypedRuleContext(KVQLParser.OpContext,0)


        def EOF(self):
            return self.getToken(KVQLParser.EOF, 0)

        def SEMI(self):
            return self.getToken(KVQLParser.SEMI, 0)

        def getRuleIndex(self):
            return KVQLParser.RULE_query

        def accept(self, visitor:ParseTreeVisitor):
            if hasattr( visitor, "visitQuery" ):
                return visitor.visitQuery(self)
            else:
                return visitor.visitChildren(self)




    def query(self):

        localctx = KVQLParser.QueryContext(self, self._ctx, self.state)
        self.enterRule(localctx, 0, self.RULE_query)
        self._la = 0 # Token type
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 10
            self.op()
            self.state = 12
            self._errHandler.sync(self)
            _la = self._input.LA(1)
            if _la==4:
                self.state = 11
                self.match(KVQLParser.SEMI)


            self.state = 14
            self.match(KVQLParser.EOF)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class OpContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser

        def putStatement(self):
            return self.getTypedRuleContext(KVQLParser.PutStatementContext,0)


        def getStatement(self):
            return self.getTypedRuleContext(KVQLParser.GetStatementContext,0)


        def deleteStatement(self):
            return self.getTypedRuleContext(KVQLParser.DeleteStatementContext,0)


        def getRuleIndex(self):
            return KVQLParser.RULE_op

        def accept(self, visitor:ParseTreeVisitor):
            if hasattr( visitor, "visitOp" ):
                return visitor.visitOp(self)
            else:
                return visitor.visitChildren(self)




    def op(self):

        localctx = KVQLParser.OpContext(self, self._ctx, self.state)
        self.enterRule(localctx, 2, self.RULE_op)
        try:
            self.state = 19
            self._errHandler.sync(self)
            token = self._input.LA(1)
            if token in [1]:
                self.enterOuterAlt(localctx, 1)
                self.state = 16
                self.putStatement()
                pass
            elif token in [2]:
                self.enterOuterAlt(localctx, 2)
                self.state = 17
                self.getStatement()
                pass
            elif token in [3]:
                self.enterOuterAlt(localctx, 3)
                self.state = 18
                self.deleteStatement()
                pass
            else:
                raise NoViableAltException(self)

        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class PutStatementContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser
            self.key = None # Token
            self.value = None # Token

        def PUT(self):
            return self.getToken(KVQLParser.PUT, 0)

        def ID(self, i:int=None):
            if i is None:
                return self.getTokens(KVQLParser.ID)
            else:
                return self.getToken(KVQLParser.ID, i)

        def getRuleIndex(self):
            return KVQLParser.RULE_putStatement

        def accept(self, visitor:ParseTreeVisitor):
            if hasattr( visitor, "visitPutStatement" ):
                return visitor.visitPutStatement(self)
            else:
                return visitor.visitChildren(self)




    def putStatement(self):

        localctx = KVQLParser.PutStatementContext(self, self._ctx, self.state)
        self.enterRule(localctx, 4, self.RULE_putStatement)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 21
            self.match(KVQLParser.PUT)
            self.state = 22
            localctx.key = self.match(KVQLParser.ID)
            self.state = 23
            localctx.value = self.match(KVQLParser.ID)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class GetStatementContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser
            self.key = None # Token

        def GET(self):
            return self.getToken(KVQLParser.GET, 0)

        def ID(self):
            return self.getToken(KVQLParser.ID, 0)

        def getRuleIndex(self):
            return KVQLParser.RULE_getStatement

        def accept(self, visitor:ParseTreeVisitor):
            if hasattr( visitor, "visitGetStatement" ):
                return visitor.visitGetStatement(self)
            else:
                return visitor.visitChildren(self)




    def getStatement(self):

        localctx = KVQLParser.GetStatementContext(self, self._ctx, self.state)
        self.enterRule(localctx, 6, self.RULE_getStatement)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 25
            self.match(KVQLParser.GET)
            self.state = 26
            localctx.key = self.match(KVQLParser.ID)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx


    class DeleteStatementContext(ParserRuleContext):
        __slots__ = 'parser'

        def __init__(self, parser, parent:ParserRuleContext=None, invokingState:int=-1):
            super().__init__(parent, invokingState)
            self.parser = parser
            self.key = None # Token

        def DELETE(self):
            return self.getToken(KVQLParser.DELETE, 0)

        def ID(self):
            return self.getToken(KVQLParser.ID, 0)

        def getRuleIndex(self):
            return KVQLParser.RULE_deleteStatement

        def accept(self, visitor:ParseTreeVisitor):
            if hasattr( visitor, "visitDeleteStatement" ):
                return visitor.visitDeleteStatement(self)
            else:
                return visitor.visitChildren(self)




    def deleteStatement(self):

        localctx = KVQLParser.DeleteStatementContext(self, self._ctx, self.state)
        self.enterRule(localctx, 8, self.RULE_deleteStatement)
        try:
            self.enterOuterAlt(localctx, 1)
            self.state = 28
            self.match(KVQLParser.DELETE)
            self.state = 29
            localctx.key = self.match(KVQLParser.ID)
        except RecognitionException as re:
            localctx.exception = re
            self._errHandler.reportError(self, re)
            self._errHandler.recover(self, re)
        finally:
            self.exitRule()
        return localctx





