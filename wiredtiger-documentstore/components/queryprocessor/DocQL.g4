grammar DocQL;

query
    : queryMode SEMI? EOF
    ;

queryMode
    : statementDDL | statementDQL | statementDML
    ;

statementDDL
    : createStatementDDL | deleteStatementDDL
    ;

createStatementDDL
    : CREATE TABLE name=ID
    ;

deleteStatementDDL
    : DELETE TABLE name=ID
    ;

statementDQL
    : SELECT projectionList FROM tableName=ID whereClause?
    ;

projectionList
    : attributeRef (COMMA attributeRef)*
    ;

attributeRef
    : attribute=ID
    ;

statementDML
    : insertStatementDML | updateStatementDML | deleteStatementDML
    ;

insertStatementDML
    : INSERT INTO tableName=ID VALUES LPAREN mapList RPAREN
    ;

mapList
    : map (COMMA map)*
    ;

deleteStatementDML
    : DELETE FROM tableName=ID whereClause?
    ;

updateStatementDML
    : UPDATE tableName=ID SET setList whereClause?
    ;

setList
    : setStatement (COMMA setStatement)*
    ;

setStatement
    : attribute=ID EQ updateValue=value
    ;

whereClause
    : WHERE expression
    ;

expression
    : expression OR expression                   # orExpression // alternative labels in antlr, affect the generated code
    | expression AND expression                  # andExpression
    | NOT expression                             # notExpression
    | LPAREN expression RPAREN                   # parenExpression
    | operand comparisonOperator operand         # comparisonExpression
    ;

    operand
    : attribute=ID                               # attributeOperand
    | value                                      # valueOperand
    ;

value
    : STRING
    | NUMBER
    | NULL
    ;

map
    : LCURLY kvpairList? RCURLY
    ;

kvpairList
    : kvpair (COMMA kvpair)*
    ;

kvpair
    : STRING COLON value
    ;

comparisonOperator
    : EQ
    | NEQ
    | LT
    | LTE
    | GT
    | GTE
    ;


CREATE  : [cC][rR][eE][aA][tT][eE];
DELETE  : [dD][eE][lL][eE][tT][eE];
TABLE   : [tT][aA][bB][lL][eE];
SELECT  : [sS][eE][lL][eE][cC][tT];
FROM    : [fF][rR][oO][mM];
WHERE   : [wW][hH][eE][rR][eE];
INSERT  : [iI][nN][sS][eE][rR][tT];
INTO    : [iI][nN][tT][oO];
VALUES  : [vV][aA][lL][uU][eE][sS];
UPDATE  : [uU][pP][dD][aA][tT][eE];
SET     : [sS][eE][tT];

AND    : [aA][nN][dD];
OR     : [oO][rR];
NOT    : [nN][oO][tT];
NULL   : [nN][uU][lL][lL];


EQ      : '=';
NEQ     : '<>' | '!=';
LTE     : '<=';
GTE     : '>=';
LT      : '<';
GT      : '>';

LPAREN  : '(';
RPAREN  : ')';
LCURLY  : '{';
RCURLY  : '}';

NUMBER  : [0-9]+ ('.' [0-9]+)?;
STRING  : '\'' ('\'\'' | ~'\'')* '\'';

COLON   : ':';
SEMI    : ';';
COMMA   : ',';
DOT     : '.';

// NOTE, positioning of tokens matters for resolution!
ID      : [a-zA-Z_0-9][a-zA-Z_0-9]*;
WS      : [ \t\r\n]+ -> skip;
