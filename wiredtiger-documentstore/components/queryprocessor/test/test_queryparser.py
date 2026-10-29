import io
from pathlib import Path

import avro.io
import avro.schema
import pytest

from ..avro_utils import from_avro_node, to_avro_node
from ..queryparser import parse_docql


@pytest.fixture
def project_root(pytestconfig):
    return Path(pytestconfig.rootdir)


@pytest.fixture
def schema(project_root):
    schema_path = project_root / "protocol" / "algebranode.avsc"
    with schema_path.open() as f:
        return avro.schema.parse(f.read())


test_queries_parser = [
    ("CREATE TABLE test123;", False),
    ("DELETE TABLE test123;", False),
    ("SELECT name, blah, blob FROM abcdefg WHERE heigt = 1.8 AND age = 18 OR name = 'andy';", False),
    ("UPDATE test123 SET name = 'Luc', age = 28, height=1.79 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';", False),
    ("INSERT INTO test VALUES ({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL})", False),
    ("DELETE FROM test123 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';", False),
]


@pytest.mark.parametrize("query, expected_exception", test_queries_parser)
def test_parser(query: str, expected_exception: bool):
    if expected_exception:
        with pytest.raises(Exception):
            parse_docql(docql=query)
    else:
        parsed_query = parse_docql(docql=query)
        assert parsed_query is not None


test_queries_serde = [
    ("CREATE TABLE test123;", False),
    ("DELETE TABLE test123;", False),
    ("SELECT name, blah, blob FROM abcdefg WHERE heigt = 1.8 AND age = 18 OR name = 'andy';", False),
    ("UPDATE test123 SET name = 'Luc', age = 28, height=1.79 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';", False),
    ("INSERT INTO test VALUES ({'name':'luc', 'age':27, 'height':1.79, 'hobby': NULL})", False),
    ("DELETE FROM test123 WHERE heigt = 1.8 AND age = 18 OR name = 'andy';", False),
]


@pytest.mark.parametrize("query, expected_exception", test_queries_serde)
def test_serde(query: str, expected_exception: bool, schema):
    if expected_exception:
        with pytest.raises(Exception):
            translated = parse_docql(docql=query)
            node_data = to_avro_node(translated)

            buffer = io.BytesIO()
            writer = avro.io.DatumWriter(schema)
            writer.write(node_data, avro.io.BinaryEncoder(buffer))

            encoded = buffer.getvalue()

            data = io.BytesIO(encoded)
            reader = avro.io.DatumReader(writers_schema=schema, readers_schema=schema)
            res = reader.read(decoder=avro.io.BinaryDecoder(data))

            deserialized = from_avro_node(res)  # type: ignore
    else:
        translated = parse_docql(docql=query)
        node_data = to_avro_node(translated)

        buffer = io.BytesIO()
        writer = avro.io.DatumWriter(schema)
        writer.write(node_data, avro.io.BinaryEncoder(buffer))

        encoded = buffer.getvalue()

        data = io.BytesIO(encoded)
        reader = avro.io.DatumReader(writers_schema=schema, readers_schema=schema)
        res = reader.read(decoder=avro.io.BinaryDecoder(data))

        deserialized = from_avro_node(res)  # type: ignore

        assert translated == deserialized
