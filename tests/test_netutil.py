import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from netutil import url_decode


def test_plus_becomes_space():
    assert url_decode("hello+world") == "hello world"


def test_percent_encoded_ascii():
    assert url_decode("a%20b") == "a b"


def test_percent_encoded_multibyte_utf8():
    # "한" = EC 95 9C in UTF-8
    assert url_decode("%ED%95%9C") == "한"


def test_trailing_incomplete_percent():
    assert url_decode("abc%2") == "abc%2"


def test_percent_at_end_of_string():
    assert url_decode("abc%") == "abc%"


def test_invalid_hex_falls_back_to_literal():
    assert url_decode("100%GZ") == "100%GZ"


def test_empty_string():
    assert url_decode("") == ""


def test_combines_plus_and_percent():
    assert url_decode("my+ssid%21") == "my ssid!"
