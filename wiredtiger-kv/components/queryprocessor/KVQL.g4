grammar KVQL;

query
    : op SEMI? EOF
    ;

op
    : putStatement | getStatement | deleteStatement
    ;

putStatement
    : PUT key=ID value=ID
    ;

getStatement
    : GET key=ID
    ;

deleteStatement
    : DELETE key=ID
    ;


PUT     : [pP][uU][tT];
GET     : [gG][eE][tT];
DELETE  : [dD][eE][lL][eE][tT][eE];
SEMI    : ';';
ID      : [a-zA-Z_0-9][a-zA-Z_0-9]*;
WS      : [ \t\r\n]+ -> skip;
