grammar UnibaSQL;

options {
    caseInsensitive = true;
}

query
    : (
          transaction
        | statementList
        | txStart
        | txEnd
      ) EOF
    ;

statementList
    : unibasqlStatement (SEMI unibasqlStatement)* SEMI?
    ;

unibasqlStatement
    : statementDDL                                          # ddlStatement
    | statementDQL                                          # dqlStatement
    | statementDQL ((UNION | EXCEPT) statementDQL)+         # setOperationStatement
    | statementDML                                          # dmlStatement
    ;

statementDDL
    : createTable
    | dropTable
    | createIndex
    | dropIndex
    ;

//  -------------------------------- TCL --------------------------------


txStart
    : BEGIN TRANSACTION txName=ID? SEMI?
    ;

txEnd
    : (ROLLBACK | COMMIT) SEMI?
    ;

transaction
    : txStart SEMI statementList txEnd SEMI?
    ;


//  -------------------------------- DDL --------------------------------

createTable
    : CREATE TABLE tableName=ID LPAREN tableDefList RPAREN
    ;

tableDefList
    : tableDefPrimary (COMMA tableDefTriple)*
    ;

tableDefPrimary
    : columnName=ID type=datatype PRIMARY KEY
    ;

tableDefTriple
    : columnName=ID type=datatype tableDefConstraint?
    ;

tableDefConstraint
    : NOT NULL          # notNullConstraint
    | PRIMARY KEY       # primaryKeyConstraint
    ;

datatype
    : STRING_TYPE
    | INT_TYPE
    | DOUBLE_TYPE
    | BOOL_TYPE
    ;

dropTable
    : DROP TABLE tableName=ID
    ;

createIndex
    : CREATE INDEX indexName=ID ON tableName=ID LPAREN columns=selectList RPAREN
    ;

dropIndex
    : DROP INDEX indexName=ID ON tableName=ID
    ;


//  -------------------------------- DQL --------------------------------

statementDQL
    : SELECT (selectList | STAR) FROM tableList whereClause?
    ;

selectList
    : columnRef (COMMA columnRef)*
    ;

tableList
    : tableRef (COMMA tableRef)*
    ;

columnRef
    : (table=ID DOT)? column=ID
    ;

tableRef
    : name=ID (AS? alias=ID)?
    ;

//  -------------------------------- DDL --------------------------------

statementDML
    : insertStatementDML | deleteStatementDML | updateStatementDML
    ;

insertStatementDML
    : INSERT INTO tableName=ID
      (LPAREN columns=selectList RPAREN)?
      VALUES insertList
    ;

insertList
    : valueRow (COMMA valueRow)*
    ;

valueRow
    : LPAREN valueList RPAREN
    ;

valueList
    : value (COMMA value)*
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
    : col=columnRef EQ updateValue=value
    ;

//  -------------------------------- EXPR --------------------------------

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
    : col=columnRef                                     # columnOperand
    | value                                             # valueOperand
    ;

value
    : STRING
    | NUMBER
    | TRUE
    | FALSE
    | NULL
    ;

comparisonOperator
    : EQ
    | NEQ
    | LT
    | LTE
    | GT
    | GTE
    ;

//  -------------------------------- Lexer --------------------------------

CREATE      : 'CREATE';
DROP        : 'DROP';
TABLE       : 'TABLE';
INDEX       : 'INDEX';
ON          : 'ON';
PRIMARY     : 'PRIMARY';
KEY         : 'KEY';

SELECT      : 'SELECT';
FROM        : 'FROM';
WHERE       : 'WHERE';

INSERT      : 'INSERT';
DELETE      : 'DELETE';
INTO        : 'INTO';
VALUES      : 'VALUES';
UPDATE      : 'UPDATE';
SET         : 'SET';

BEGIN       : 'BEGIN';
TRANSACTION : 'TRANSACTION';
COMMIT      : 'COMMIT';
ROLLBACK    : 'ROLLBACK';

AND         : 'AND';
OR          : 'OR';
NOT         : 'NOT';
NULL        : 'NULL';
UNION       : 'UNION';
EXCEPT      : 'EXCEPT';

EQ          : '=';
NEQ         : '<>' | '!=';
LTE         : '<=';
GTE         : '>=';
LT          : '<';
GT          : '>';

LPAREN      : '(';
RPAREN      : ')';
LCURLY      : '{';
RCURLY      : '}';

NUMBER      : '-'? [0-9]+ ('.' [0-9]+)?;
STRING      : '\'' ('\'\'' | ~'\'')* '\'';
TRUE        : 'TRUE';
FALSE       : 'FALSE';

BOOL_TYPE   : 'BOOL';
STRING_TYPE : 'STRING';
INT_TYPE    : 'INT';
DOUBLE_TYPE : 'DOUBLE';

AS          : 'AS';
COLON       : ':';
SEMI        : ';';
COMMA       : ',';
DOT         : '.';
STAR        : '*';

// NOTE, positioning of tokens matters for resolution!
ID          : [a-z_][a-z_0-9]*;
WS          : [ \t\r\n]+ -> skip;
