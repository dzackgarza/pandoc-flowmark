"""Bracketed prose keeps Pandoc's authored whitespace interpretation."""

import pytest

from flowmark.reformat_api import reformat_text
from flowmark.pandoc_verify import check_meaning_preserved


@pytest.mark.parametrize(
    "source",
    [
        "Before\n[\nvalue\n]\nafter.\n",
        "::: {.problem}\nLet\n[\np:Y to Y/G\n]\nbe the quotient map.\n:::\n",
        "Before\n[\n`literal text` and $x$\n]\nafter.\n",
    ],
)
def test_bracketed_prose_preserves_its_pandoc_reading(source: str) -> None:
    result = reformat_text(source)
    check_meaning_preserved(source, result)
    assert reformat_text(result) == result
