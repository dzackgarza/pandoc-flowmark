"""Tests for the public Markdown-inline API: flowmark.atomic_spans and flowmark.markdown_ast."""

from __future__ import annotations

import json

from flowmark import Link, extract_links
from flowmark.atomic_spans import (
    ATOMIC_PATTERNS,
    AUTOLINK,
    BARE_URL,
    INLINE_CODE_SPAN,
    INLINE_MATH,
    MARKDOWN_INLINE_PATTERNS,
    MARKDOWN_LINK,
    AtomicPattern,
    iter_atomic_spans,
    iter_atomic_words,
    split_sentences_atomic,
    split_sentences_with_spans,
)
from flowmark.markdown_ast import walk_elements
from flowmark.pandoc_reader import pandoc_executable, read_source_ast


def _parse(text: str) -> str:
    return text


def test_atomic_pattern_constructs_with_name_and_pattern_only() -> None:
    p = AtomicPattern(name="x", pattern=r"foo")
    assert (
        p.open_delim == ""
        and p.close_delim == ""
        and p.open_re == ""
        and p.close_re == ""
    )


def test_markdown_inline_patterns_includes_links_and_urls() -> None:
    assert MARKDOWN_INLINE_PATTERNS == (
        INLINE_CODE_SPAN,
        INLINE_MATH,
        MARKDOWN_LINK,
        AUTOLINK,
        BARE_URL,
    )
    # Purpose-built, deliberately NOT a subset of the wrapping set.
    assert not set(MARKDOWN_INLINE_PATTERNS).issubset(set(ATOMIC_PATTERNS))


def test_inline_math_is_atomic_in_both_pattern_sets() -> None:
    """
    Math must be unbreakable for wrapping (#17 part 2) and whole for sentence
    splitting, so it belongs to both sets rather than only the wrapping one.
    """
    assert INLINE_MATH in ATOMIC_PATTERNS
    assert INLINE_MATH in MARKDOWN_INLINE_PATTERNS


def test_inline_math_spans_are_kept_whole_but_prose_currency_is_not() -> None:
    """
    The pattern is pandoc's rule: a single-`$` span needs non-whitespace just
    inside both delimiters. Without that, `their $420K ... paying $` matches as one
    48-character atomic token, though pandoc reads it as prose.
    """
    math = [
        s.text
        for s in iter_atomic_spans(r"and $H^1(X,\mathcal O_X)=0$ plus")
        if s.is_atomic
    ]
    assert math == [r"$H^1(X,\mathcal O_X)=0$"]

    currency = [
        s.text for s in iter_atomic_spans("it costs $5 and $10 more") if s.is_atomic
    ]
    assert currency == []


def test_autolink_pattern_matches_angle_url_and_email() -> None:
    import re

    pat = re.compile(AUTOLINK.pattern)
    assert pat.fullmatch("<http://example.com>")
    assert pat.fullmatch("<user@example.com>")


def test_bare_url_pattern_excludes_trailing_sentence_punctuation() -> None:
    import re

    pat = re.compile(BARE_URL.pattern)
    m = pat.search("Visit https://example.com. Next.")
    assert m is not None
    assert m.group(0) == "https://example.com"
    m2 = pat.search("see www.example.com/x")
    assert m2 is not None
    assert m2.group(0) == "www.example.com/x"


def test_extract_inline_link_with_title() -> None:
    doc = _parse('See [text](http://x.com "the title") here.\n')
    assert extract_links(doc) == [Link("text", "http://x.com", "the title")]


def test_extract_reference_link_resolves_destination() -> None:
    doc = _parse("See [text][r].\n\n[r]: http://ref.com\n")
    assert extract_links(doc) == [Link("text", "http://ref.com", None)]


def test_extract_collapsed_and_shortcut_references() -> None:
    doc = _parse("[r][] and [r].\n\n[r]: http://ref.com\n")
    assert [link.url for link in extract_links(doc)] == [
        "http://ref.com",
        "http://ref.com",
    ]


def test_nested_inline_markup_in_link_text() -> None:
    doc = _parse("[**bold** and `code`](http://x.com)\n")
    links = extract_links(doc)
    assert len(links) == 1
    assert links[0].url == "http://x.com"
    assert "bold" in links[0].text and "code" in links[0].text


def test_escaped_brackets_are_not_a_link() -> None:
    doc = _parse("not a \\[link\\] here.\n")
    assert extract_links(doc) == []


def test_images_excluded_by_default_included_on_request() -> None:
    doc = _parse("![alt](img.png)\n")
    assert extract_links(doc) == []
    assert extract_links(doc, include_images=True) == [Link("alt", "img.png", None)]


def test_email_autolink_text_is_display_not_destination() -> None:
    doc = _parse("<user@example.com>\n")
    assert extract_links(doc) == [
        Link("user@example.com", "mailto:user@example.com", None)
    ]


def test_empty_link_title_is_preserved_distinct_from_none() -> None:
    assert extract_links(_parse('[x](http://u "")\n')) == [Link("x", "http://u", "")]
    assert extract_links(_parse("[x](http://u)\n")) == [Link("x", "http://u", None)]


def test_angle_autolink_and_bare_url_extraction() -> None:
    doc = _parse("<http://auto.com> and https://bare.com/x\n")
    urls = [link.url for link in extract_links(doc)]
    assert "http://auto.com" in urls
    assert "https://bare.com/x" in urls
    assert extract_links(doc, include_autolinks=False) == []


def test_duplicate_link_text_yields_separate_links() -> None:
    doc = _parse("[go](http://1.com) and [go](http://2.com)\n")
    assert [link.url for link in extract_links(doc)] == ["http://1.com", "http://2.com"]


def test_link_syntax_inside_code_span_is_not_a_link() -> None:
    doc = _parse("`[notalink](x)` text\n")
    assert extract_links(doc) == []


def test_walk_elements_yields_code_block_text_but_extract_links_excludes_it() -> None:
    source = "```\n[notalink](x)\n```\n"
    ast = read_source_ast(source, pandoc_executable())
    assert any(node.get("t") == "CodeBlock" for node in walk_elements(ast))
    assert extract_links(source) == []


def test_walk_elements_is_read_only() -> None:
    ast = read_source_ast("a [b](http://x.com) c\n", pandoc_executable())
    before = json.dumps(ast, sort_keys=True)
    list(walk_elements(ast))
    assert json.dumps(ast, sort_keys=True) == before


def test_iter_atomic_spans_round_trip_and_offsets() -> None:
    s = "See [a b](http://x.com) and `co de` end."
    spans = list(iter_atomic_spans(s))
    assert "".join(sp.text for sp in spans) == s
    assert all(s[sp.start : sp.end] == sp.text for sp in spans)
    assert [sp.text for sp in spans if sp.is_atomic] == [
        "[a b](http://x.com)",
        "`co de`",
    ]


def test_atomic_span_name_distinguishes_link_from_code() -> None:
    s = "[a b](http://x.com) and `co de`"
    atomic = [sp for sp in iter_atomic_spans(s) if sp.is_atomic]
    assert [(sp.text, sp.name) for sp in atomic] == [
        ("[a b](http://x.com)", "markdown_link"),
        ("`co de`", "inline_code_span"),
    ]
    # Non-atomic gaps carry no name.
    assert all(sp.name is None for sp in iter_atomic_spans(s) if not sp.is_atomic)


def test_iter_atomic_spans_empty_patterns_yields_single_nonatomic_span() -> None:
    from flowmark.atomic_spans import AtomicSpan

    assert list(iter_atomic_spans("abc", patterns=())) == [
        AtomicSpan("abc", 0, 3, False)
    ]
    assert list(iter_atomic_spans("", patterns=())) == []


def test_iter_atomic_words_glues_atomic_and_keeps_offsets() -> None:
    s = "foo[a](b)bar [click here](u) end"
    words = list(iter_atomic_words(s))
    assert [w.text for w in words] == ["foo[a](b)bar", "[click here](u)", "end"]
    assert all(s[w.start : w.end] == w.text for w in words)


def test_split_sentences_with_spans_are_verbatim() -> None:
    s = "This is one sentence. Here is the second one."
    spans = split_sentences_with_spans(s, min_length=0)
    assert all(s[sp.start : sp.end] == sp.text for sp in spans)
    assert [sp.text for sp in spans] == [
        "This is one sentence.",
        "Here is the second one.",
    ]


def test_sentence_span_never_bisects_a_link_with_spaces() -> None:
    s = "See [click here for info](http://x.com) now. Done with it."
    spans = split_sentences_with_spans(s, min_length=0)
    assert all(s[sp.start : sp.end] == sp.text for sp in spans)
    # The whole link stays inside a single sentence span (never split on its inner space).
    assert any("[click here for info](http://x.com)" in sp.text for sp in spans)


def test_split_sentences_atomic_does_not_break_inside_link_with_period() -> None:
    # "St." inside the link text must not end a sentence (it does with the plain splitter).
    s = "He attended [St. John's School](http://x.com) in England."
    assert split_sentences_atomic(s, min_length=0) == [s]
