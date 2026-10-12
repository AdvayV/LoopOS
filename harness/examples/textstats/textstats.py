"""Small text utilities used to generate practice tasks for LoopOS loops."""
from __future__ import annotations

import re
from collections import Counter

ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
         (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def top_words(text: str, n: int = 3) -> list[tuple[str, int]]:
    """The n most common words; ties are broken alphabetically."""
    counts = Counter(words(text))
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]


def slugify(title: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap; a word longer than width gets its own line."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def to_roman(number: int) -> str:
    if not 1 <= number <= 3999:
        raise ValueError("number out of range")
    out = ""
    for value, symbol in ROMAN:
        while number >= value:
            out += symbol
            number -= value
    return out


def parse_csv_line(line: str) -> list[str]:
    """Split a CSV line, honouring double quotes and doubled quotes inside them."""
    fields: list[str] = []
    field = ""
    quoted = False
    i = 0
    while i < len(line):
        char = line[i]
        if quoted:
            if char == '"' and i + 1 < len(line) and line[i + 1] == '"':
                field += '"'
                i += 1
            elif char == '"':
                quoted = False
            else:
                field += char
        elif char == '"':
            quoted = True
        elif char == ",":
            fields.append(field)
            field = ""
        else:
            field += char
        i += 1
    fields.append(field)
    return fields


def is_palindrome(text: str) -> bool:
    letters = [c for c in text.lower() if c.isalnum()]
    return letters == letters[::-1] and len(letters) > 0


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
