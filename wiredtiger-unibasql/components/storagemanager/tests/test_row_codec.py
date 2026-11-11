"""Unit tests for the relational cell codec (casting table).

Key invariants: 'u' (DOUBLE) is physical big-endian IEEE-754 — never bytes-decoded;
NULL is nullmap-driven, never value-encoded; the wire container pre-casts BOOL to 0/1
because the cell union order makes avro collapse bools into numerics.
"""

import struct

import pytest

from components.queryprocessor.unibasql_unresolved import DataType
from components.storagemanager import row_codec

# --- type tags -----------------------------------------------------------------------------------------


def test_type_tags_match_the_wire_vocabulary():
    assert row_codec.type_tag(DataType.INT) == "INT"
    assert row_codec.type_tag(DataType.BOOL) == "BOOL"
    assert row_codec.type_tag(DataType.STRING) == "STRING"
    assert row_codec.type_tag(DataType.DOUBLE) == "DOUBLE"


# --- nullmap -------------------------------------------------------------------------------------------


def test_nullmap_roundtrip_marks_exactly_the_flagged_value_columns():
    flags = [False, True, False, True, True]
    packed = row_codec.encode_nullmap(flags)
    assert row_codec.decode_nullmap(packed, 5) == flags


def test_nullmap_little_endian_bit_layout():
    # column 0 and column 7 of one byte: 0b10000001
    assert row_codec.decode_nullmap(bytes([0b1000_0001]), 8) == [True] + [False] * 6 + [True]


def test_nullmap_short_input_pads_with_present_values():
    assert row_codec.decode_nullmap(bytes([]), 3) == [False, False, False]


# --- cells ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        (DataType.BOOL, True),
        (DataType.BOOL, False),
        (DataType.INT, 27),
        (DataType.INT, -5),
        (DataType.STRING, "emp0 ünicode"),
        (DataType.DOUBLE, 0.0),
        (DataType.DOUBLE, -13.37),
        (DataType.DOUBLE, 1e308),  # extreme magnitude, still exact round-trip
    ],
)
def test_encode_decode_cell_roundtrip(dtype, value):
    assert row_codec.decode_cell(dtype, row_codec.encode_cell(dtype, value)) == value


def test_double_is_big_endian_ieee754_never_decoded_as_text():
    packed = row_codec.encode_cell(DataType.DOUBLE, 40.5)
    assert packed == struct.pack(">d", 40.5)
    assert struct.unpack(">d", packed)[0] == 40.5
    # little-endian interpretation would differ: prove orientation explicitly
    assert row_codec.decode_cell(DataType.DOUBLE, struct.pack(">d", 1.0)) == 1.0


def test_blank_defaults_align_with_decode():
    for dtype, blanks in ((DataType.INT, 0), (DataType.BOOL, 0), (DataType.STRING, "")):
        assert row_codec.blank_cell(dtype) == blanks
        assert row_codec.decode_cell(dtype, row_codec.blank_cell(dtype)) in (0, "")
    assert row_codec.decode_cell(DataType.DOUBLE, row_codec.blank_cell(DataType.DOUBLE)) == 0.0


def test_unsupported_dtypes_raise():
    with pytest.raises(TypeError):
        row_codec.encode_cell(DataType.NULL, 1)
    with pytest.raises(TypeError):
        row_codec.blank_cell(DataType.NULL)
    with pytest.raises(TypeError):
        row_codec.wire_cell("BAD", 1)


# --- wire container --------------------------------------------------------------------------------------


def test_wire_cell_precasts_bools_to_01_and_passes_others_verbatim():
    assert row_codec.wire_cell(DataType.BOOL, True) == 1
    assert row_codec.wire_cell(DataType.BOOL, False) == 0
    assert row_codec.wire_cell(DataType.INT, 27) == 27
    assert row_codec.wire_cell(DataType.DOUBLE, 1.5) == 1.5
    assert row_codec.wire_cell(DataType.STRING, "s") == "s"
    assert row_codec.wire_cell(DataType.INT, None) is None  # NULL rides the union's null branch
