import pytest

from textstats import is_palindrome, parse_csv_line, slugify, to_roman, top_words, truncate, words, wrap


def test_words_lowercases_and_keeps_apostrophes():
    assert words("Don't Stop, don't!") == ["don't", "stop", "don't"]


def test_top_words_orders_by_count_then_alphabet():
    assert top_words("b a b c a c", 2) == [("a", 2), ("b", 2)]
    assert top_words("x y y", 5) == [("y", 2), ("x", 1)]


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  --Spaces-- ") == "spaces"
    assert slugify("a" * 50, max_length=10) == "a" * 10
    assert slugify("word-" * 4, max_length=9) == "word-word"


def test_wrap_fills_lines_greedily():
    assert wrap("the quick brown fox", 9) == ["the quick", "brown fox"]
    assert wrap("abcdefghijk x", 5) == ["abcdefghijk", "x"]
    assert wrap("", 10) == []
    assert wrap("ab cd", 5) == ["ab cd"]


@pytest.mark.parametrize("number, roman", [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"),
                                           (90, "XC"), (400, "CD"), (1994, "MCMXCIV"), (3999, "MMMCMXCIX")])
def test_to_roman(number, roman):
    assert to_roman(number) == roman


def test_to_roman_rejects_out_of_range():
    with pytest.raises(ValueError):
        to_roman(0)
    with pytest.raises(ValueError):
        to_roman(4000)


def test_parse_csv_line():
    assert parse_csv_line("a,b,c") == ["a", "b", "c"]
    assert parse_csv_line('"x, y",z') == ["x, y", "z"]
    assert parse_csv_line('"say ""hi""",') == ['say "hi"', ""]
    assert parse_csv_line("") == [""]


def test_is_palindrome():
    assert is_palindrome("A man, a plan, a canal: Panama")
    assert not is_palindrome("hello")
    assert not is_palindrome("!!!")


def test_truncate():
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 8) == "hello..."
    assert truncate("abc", 3) == "abc"
    assert truncate("abcd", 2) == "..."
