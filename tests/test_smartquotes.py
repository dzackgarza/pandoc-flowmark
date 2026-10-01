import pytest

from flowmark import FormatOptions, Pass, Semantic
from flowmark.linewrapping.markdown_filling import fill_markdown
from flowmark.pandoc_verify import pandoc_ast
from flowmark.typography.smartquotes import smart_quotes


def test_basic_double_quotes() -> None:
    """Test basic double quote conversion."""
    assert smart_quotes('I\'m there with "George"') == "I\u2019m there with \u201cGeorge\u201d"
    assert smart_quotes('"Hello," he said.') == "\u201cHello,\u201d he said."
    assert smart_quotes('"I know!"') == "\u201cI know!\u201d"


def test_basic_single_quotes() -> None:
    """Test basic single quote conversion."""
    assert smart_quotes("Words in 'single quotes' work too") == "Words in \u2018single quotes\u2019 work too"
    assert smart_quotes("X is 'foo'") == "X is \u2018foo\u2019"


def test_apostrophes_and_contractions() -> None:
    """Test apostrophe and contraction conversion."""
    assert smart_quotes("I'm there") == "I\u2019m there"
    assert smart_quotes("I'll be there, don't worry") == "I\u2019ll be there, don\u2019t worry"
    assert smart_quotes("Jill's") == "Jill\u2019s"
    assert smart_quotes("James'") == "James\u2019"


def test_possessives_at_end_of_words() -> None:
    """Test possessives at the end of words ending in s."""
    assert smart_quotes("James'") == "James\u2019"
    assert smart_quotes("The students' books") == "The students\u2019 books"
    assert smart_quotes("Mr. Jones' house") == "Mr. Jones\u2019 house"
    assert smart_quotes("The cats' toys") == "The cats\u2019 toys"
    assert smart_quotes("Jesus' disciples") == "Jesus\u2019 disciples"
    assert smart_quotes("The class' performance") == "The class\u2019 performance"


def test_patterns_left_unchanged() -> None:
    """Test patterns that should remain unchanged."""
    assert smart_quotes('x="foo"') == 'x="foo"'
    assert smart_quotes("x='foo'") == "x='foo'"
    assert smart_quotes("Blah'blah'blah") == "Blah'blah'blah"
    assert smart_quotes('""quotes"s') == '""quotes"s'
    assert smart_quotes('\\"escaped\\"') == '\\"escaped\\"'
    assert smart_quotes("'apos'trophes") == "'apos'trophes"


def test_quotes_with_punctuation() -> None:
    """Test quotes followed by various punctuation marks."""
    assert smart_quotes('"Hello,"') == "\u201cHello,\u201d"
    assert smart_quotes('"Wait;"') == "\u201cWait;\u201d"
    assert smart_quotes('"Stop:"') == "\u201cStop:\u201d"
    assert smart_quotes('"Really?"') == "\u201cReally?\u201d"
    assert smart_quotes('"Yes!"') == "\u201cYes!\u201d"
    assert smart_quotes('"End."') == "\u201cEnd.\u201d"
    assert smart_quotes('"Em dash"—') == "\u201cEm dash\u201d—"
    assert smart_quotes('"Parenthesis")') == "\u201cParenthesis\u201d)"
    assert smart_quotes("'Single em dash'—") == "\u2018Single em dash\u2019—"
    assert smart_quotes("'Single parenthesis')") == "\u2018Single parenthesis\u2019)"


def test_quotes_at_boundaries() -> None:
    """Test quotes at sentence boundaries."""
    assert smart_quotes('"Start of sentence"') == "\u201cStart of sentence\u201d"
    assert smart_quotes('He said "middle of sentence" and continued') == "He said \u201cmiddle of sentence\u201d and continued"


def test_mixed_quotes_and_apostrophes() -> None:
    """Test text with both quotes and apostrophes."""
    assert smart_quotes('I\'m reading "The Great Gatsby" today') == "I\u2019m reading \u201cThe Great Gatsby\u201d today"
    assert smart_quotes('She said "I can\'t believe it!"') == "She said \u201cI can\u2019t believe it!\u201d"


def test_edge_cases() -> None:
    """Test edge cases."""
    assert smart_quotes("") == ""
    assert smart_quotes("No quotes here") == "No quotes here"
    assert smart_quotes('Just "quotes"') == "Just \u201cquotes\u201d"
    assert smart_quotes("'Single'") == "\u2018Single\u2019"


def test_multiple_quotes_in_text() -> None:
    """Test text with multiple separate quoted sections."""
    assert smart_quotes('He said "hello" and she said "goodbye"') == "He said \u201chello\u201d and she said \u201cgoodbye\u201d"
    assert smart_quotes("The words 'yes' and 'no' are opposites") == "The words \u2018yes\u2019 and \u2018no\u2019 are opposites"


def test_complex_sentences() -> None:
    """Test more complex real-world sentences."""
    text = "John said \"I can't believe it's not butter!\" at the store."
    expected = "John said \u201cI can\u2019t believe it\u2019s not butter!\u201d at the store."
    assert smart_quotes(text) == expected


def test_technical_content_unchanged() -> None:
    """Test that technical content is not modified."""
    assert smart_quotes('function("param")') == 'function("param")'
    assert smart_quotes("array['key']") == "array['key']"
    assert smart_quotes('height="100px"') == 'height="100px"'
    assert smart_quotes("class='my-class'") == "class='my-class'"


def test_complex_cases_unchanged() -> None:
    """Test that nested or complex quote patterns are left alone."""
    assert smart_quotes('quote"in"quote') == 'quote"in"quote'
    assert smart_quotes('""nested""') == '""nested""'
    assert smart_quotes("''nested''") == "''nested''"
    assert smart_quotes('""nested"') == '""nested"'
    assert smart_quotes("'nested''") == "'nested''"
    assert smart_quotes('x="foo"') == 'x="foo"'
    assert smart_quotes("x='foo'") == "x='foo'"
    assert smart_quotes("Blah'blah'blah") == "Blah'blah'blah"
    assert smart_quotes('""quotes"s') == '""quotes"s'
    assert smart_quotes('\\"escaped\\"') == '\\"escaped\\"'
    assert smart_quotes("'apos") == "'apos"
    assert smart_quotes("'apos'trophes") == "'apos'trophes"
    assert smart_quotes("$James'") == "$James'"


def test_quotes_with_newlines() -> None:
    """Test quotes that contain newlines."""
    # Double quotes with newlines
    assert smart_quotes('"Hello\nWorld"') == "\u201cHello\nWorld\u201d"
    assert smart_quotes('He said "Hello\nWorld" today') == "He said \u201cHello\nWorld\u201d today"
    assert smart_quotes('"First line\nSecond line\nThird line"') == "\u201cFirst line\nSecond line\nThird line\u201d"

    # Single quotes with newlines
    assert smart_quotes("'Hello\nWorld'") == "\u2018Hello\nWorld\u2019"
    assert smart_quotes("She said 'Hello\nWorld' today") == "She said \u2018Hello\nWorld\u2019 today"
    assert smart_quotes("'First line\nSecond line\nThird line'") == "\u2018First line\nSecond line\nThird line\u2019"

    # With punctuation after newline quotes
    assert smart_quotes('"Hello\nWorld".') == "\u201cHello\nWorld\u201d."
    assert smart_quotes('"Hello\nWorld"!') == "\u201cHello\nWorld\u201d!"
    assert smart_quotes("'Hello\nWorld'?") == "\u2018Hello\nWorld\u2019?"

    # Mixed with contractions
    assert smart_quotes('I\'m reading "Hello\nWorld" today') == "I\u2019m reading \u201cHello\nWorld\u201d today"

    # Multiple paragraphs in quotes should NOT be converted
    text = '"This is paragraph one.\n\nThis is paragraph two."'
    expected = '"This is paragraph one.\n\nThis is paragraph two."'  # Unchanged
    assert smart_quotes(text) == expected

    # Quotes at start and end of lines
    text = '"Start of text\nMiddle line\nEnd of text"'
    expected = "\u201cStart of text\nMiddle line\nEnd of text\u201d"
    assert smart_quotes(text) == expected

    # Basic paragraph break
    assert smart_quotes('"Para 1.\n\nPara 2."') == '"Para 1.\n\nPara 2."'
    assert smart_quotes("'Para 1.\n\nPara 2.'") == "'Para 1.\n\nPara 2.'"

    # Paragraph break with spaces
    assert smart_quotes('"Para 1.\n \nPara 2."') == '"Para 1.\n \nPara 2."'
    assert smart_quotes('"Para 1.\n  \nPara 2."') == '"Para 1.\n  \nPara 2."'
    assert smart_quotes('"Para 1.\n\t\nPara 2."') == '"Para 1.\n\t\nPara 2."'

    # Multiple paragraph breaks
    assert smart_quotes('"Para 1.\n\nPara 2.\n\nPara 3."') == '"Para 1.\n\nPara 2.\n\nPara 3."'

    # Paragraph break in context
    text = 'He said "Para 1.\n\nPara 2." yesterday.'
    expected = 'He said "Para 1.\n\nPara 2." yesterday.'
    assert smart_quotes(text) == expected

    # Mixed: some with paragraph breaks, some without
    text = 'She said "Hello world" and he said "Para 1.\n\nPara 2." today.'
    expected = 'She said \u201cHello world\u201d and he said "Para 1.\n\nPara 2." today.'
    assert smart_quotes(text) == expected


# ---- Integration tests: smart quoting in container types ----


def test_smart_quotes_in_table_cells() -> None:
    """Test that smart quotes are applied inside GFM table cells."""
    text = '| User Says | Response |\n| --- | --- |\n| "Hello there" | "Goodbye" |\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cHello there\u201d" in result
    assert "\u201cGoodbye\u201d" in result


def test_smart_quotes_apostrophes_in_table_cells() -> None:
    """Test that apostrophes are converted inside table cells."""
    text = "| User Says |\n| --- |\n| There's a bug |\n"
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "There\u2019s" in result


def test_smart_quotes_in_table_preserve_code_spans() -> None:
    """Test that code spans inside table cells are not modified."""
    text = '| Description | Command |\n| --- | --- |\n| "Fix a bug" | `tbd create "..." --type=bug` |\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    # The prose quotes should be converted
    assert "\u201cFix a bug\u201d" in result
    # The code span should be unchanged
    assert '`tbd create "..." --type=bug`' in result


def test_smart_quotes_in_strikethrough() -> None:
    """Test that smart quotes are applied inside strikethrough text."""
    text = '~~"Hello" and don\'t~~ rest of text\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cHello\u201d" in result
    assert "don\u2019t" in result


def test_smart_quotes_spanning_code_span() -> None:
    """Test quotes that span across a code span within a paragraph."""
    text = '**Tell the user:** "First, install the `markform` command."\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cFirst," in result
    assert "command.\u201d" in result


def test_smart_quotes_spanning_code_span_in_blockquote() -> None:
    """Test quotes spanning a code span inside a blockquote."""
    text = '> **Tell the user:** "First, install the `markform` command."\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cFirst," in result
    assert "command.\u201d" in result


def test_smart_quotes_spanning_emphasis() -> None:
    """Test quotes that span across emphasis within a paragraph."""
    text = 'He said "this is *really* important."\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cthis" in result
    assert "important.\u201d" in result


def test_smart_quotes_spanning_strong_emphasis() -> None:
    """Test quotes that span across strong emphasis."""
    text = 'She said "this is **very** important."\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cthis" in result
    assert "important.\u201d" in result


def test_smart_quotes_spanning_link() -> None:
    """Test quotes that span across a link."""
    text = 'Read "the [documentation](https://example.com) first."\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cthe" in result
    assert "first.\u201d" in result


def test_smart_quotes_not_modifying_code_content() -> None:
    """Ensure code spans are never modified even when between smart-quoted text."""
    text = 'Use "the `x="value"` syntax" for this.\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    # Code span content must be preserved exactly
    assert '`x="value"`' in result


def test_smart_quotes_apostrophe_spanning_code_span() -> None:
    """Test apostrophes in text around code spans."""
    text = "I'll use the `markform` tool and it'll work.\n"
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "I\u2019ll" in result
    assert "it\u2019ll" in result


def test_smart_quotes_in_table_with_bold() -> None:
    """Test smart quotes in table cells containing bold text."""
    text = '| Column |\n| --- |\n| **Issues/Beads** |\n| "There\'s a bug" |\n'
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    assert "\u201cThere\u2019s a bug\u201d" in result


def test_smart_quotes_complex_table() -> None:
    """Test the specific table from the bug report."""
    text = (
        "| User Says | You (the Agent) Run |\n"
        "| --- | --- |\n"
        "| **Issues/Beads** |  |\n"
        '| "There\'s a bug where ..." | `tbd create "..." --type=bug` |\n'
        '| "Create a task/feature for ..." | `tbd create "..." --type=task` or `--type=feature` |\n'
    )
    result = fill_markdown(text, FormatOptions(passes=frozenset({Pass.smartquotes})))
    # Prose quotes should be converted
    assert "\u201cThere\u2019s a bug where \u2026\u201d" in result or "\u201cThere\u2019s a bug where ...\u201d" in result
    assert "\u201cCreate a task/feature for \u2026\u201d" in result or "\u201cCreate a task/feature for ...\u201d" in result
    # Code spans should be unchanged
    assert '`tbd create "..." --type=bug`' in result
    assert '`tbd create "..." --type=task`' in result


def test_smart_quotes_blockquote_multiline_with_code_span() -> None:
    """Test the specific blockquote from the bug report."""
    text = (
        "> **Tell the user:** \"First, I'll make sure Markform is installed.\n"
        "> Markform is a CLI tool for creating structured forms that agents can fill via tool\n"
        "> calls. I'll install it globally so we can use the `markform` command.\"\n"
    )
    result = fill_markdown(text, FormatOptions(Semantic(), passes=frozenset({Pass.smartquotes})))
    # The outer quotes should be converted to smart quotes
    assert "\u201cFirst," in result
    assert "command.\u201d" in result
    # Apostrophes should also be converted
    assert "I\u2019ll" in result
    # Code span must be preserved
    assert "`markform`" in result


def test_stray_quote_blocks_later_single_span() -> None:
    """A stray straight quote before a span makes pairing ambiguous: a markdown
    reader pairs the stray with one of the span's quotes, so converting the span
    would move which text the document quotes. Contractions still convert."""
    assert smart_quotes("Apostrophes: the cat's meow, the '90s, rock 'n' roll.") == "Apostrophes: the cat’s meow, the '90s, rock 'n' roll."
    assert smart_quotes("'til you 'see' it") == "'til you 'see' it"


def test_stray_quote_after_span_does_not_block() -> None:
    """A stray quote AFTER a span cannot capture it, so the span still converts;
    the trailing digit elision then curls too (nothing left to pair with it)."""
    assert smart_quotes("rock 'n' roll and the '90s forever") == "rock ‘n’ roll and the ’90s forever"


def test_stray_double_quote_blocks_later_double_span() -> None:
    """Same pairing rule for double quotes."""
    assert smart_quotes('the "90s, rock "n" roll') == 'the "90s, rock "n" roll'


def test_stray_single_inside_converted_double_span_blocks_later_single() -> None:
    """A stray single quote inside a converted double span still counts: it stays
    straight in the output and pairs across the double quotes."""
    assert smart_quotes("He said \"the '90s were fun\" and 'foo' bar") == "He said “the '90s were fun” and 'foo' bar"


def test_single_pair_inside_converted_double_span_stays_straight() -> None:
    """A genuine single-quote pair inside a converted double span stays straight on
    *both* sides (#30).

    The outer double span swallows the interior, so the inner `'single quotes'` is
    never offered to the span pass; leaving both marks straight keeps pandoc reading
    it as one `Quoted SingleQuote` span, exactly as in the source. The bug was that
    the possessive rule saw the word `quotes'` (ending in `s'`) and curled only the
    closing mark, producing an asymmetric `'single quotes’` that pandoc reads
    differently -- a meaning change the verify gate then refuses.
    """
    result = smart_quotes("\"Nested 'single quotes' inside double quotes\" are tricky.")
    assert result == "“Nested 'single quotes' inside double quotes” are tricky."


def test_attribute_style_pair_does_not_block() -> None:
    """x='foo' is not in prose position, and its quotes pair with each other, so
    it does not block later spans."""
    assert smart_quotes("x='foo' and I said 'hi' ok") == "x='foo' and I said ‘hi’ ok"


def test_digit_elision_apostrophe_curls_when_unambiguous() -> None:
    """A lone '90s is an apostrophe to a markdown reader either way (#13)."""
    assert smart_quotes("Back in the '90s.") == "Back in the ’90s."
    assert smart_quotes("the '80s and '90s were rad") == "the ’80s and ’90s were rad"
    # Inside a converted double span it is equally unambiguous.
    assert smart_quotes('He said "the \'90s were fun" then.') == "He said “the ’90s were fun” then."


def test_digit_elision_stays_straight_when_a_closer_follows() -> None:
    """A later closing-capable quote would pair with the elision as a quotation,
    so it must stay straight -- this is the rock-'n'-roll line's shape."""
    assert smart_quotes("Apostrophes: the cat's meow, the '90s, rock 'n' roll.") == "Apostrophes: the cat’s meow, the '90s, rock 'n' roll."


def test_letter_elisions_curl_when_nothing_can_pair_with_them() -> None:
    """
    Replaces `test_letter_elisions_never_curl`, deliberately and with evidence.

    That test asserted "'til/'em read as open quotes to a markdown reader; only
    digits are safe". Probed against pandoc 3.9.0.2 that is false for the unpaired
    case: `don't stop 'til you drop` and `don’t stop ’til you drop` have identical
    ASTs. The probe is recorded in `SAME_READING_PROBES` below.

    What was true, and is kept, is the *paired* case -- see
    `test_digit_elision_stays_straight_when_a_closer_follows`, which is the same
    guard and still holds. The old test drew the line at digit-versus-letter; the
    line actually falls at whether a later quote can pair with the elision.
    """
    assert smart_quotes("'til we meet") == "’til we meet"
    assert smart_quotes("don't stop 'til you drop") == "don’t stop ’til you drop"


# --- #13: letter elisions ----------------------------------------------------
#
# A leading straight quote standing for omitted characters ('90s, 'til, 'em) is an
# apostrophe, and correct typography for it is U+2019. Digit elisions were curled
# in 8b777ae; letter elisions were left straight, and the module asserted they were
# unsafe because "a reader takes them as open quotes".
#
# Probed against pandoc 3.9.0.2, that assertion is wrong for the unpaired cases and
# right for the paired one. Each pair below is the recorded probe.

# (source, curled) pairs pandoc reads the same.
SAME_READING_PROBES = [
    ("don't stop 'til you drop", "don’t stop ’til you drop"),
    ("give 'em hell now", "give ’em hell now"),
    ("'tis the season", "’tis the season"),
]

# (source, curled) pairs pandoc reads differently.
CHANGED_READING_PROBES = [
    # `rock 'n' roll` is different in kind: pandoc pairs the two straight quotes
    # into `Quoted SingleQuote [Str "n"]`, so curling them *as elisions* (U+2019 in
    # both positions) erases the span.
    ("rock 'n' roll", "rock ’n’ roll"),
    ("the '90s, rock 'n' roll", "the ’90s, rock ’n’ roll"),
    # Curling the same span as a *quotation* (U+2018 then U+2019) is a different
    # change and is AST-neutral under the `smart_quotes` normalization, which is
    # why the standalone case is not the one #13 refuses.
    ("rock 'n' roll", "rock ‘n’ roll"),
]

# The probes themselves, checked in rather than described. #13 asks that each
# conversion be "justified by a recorded pandoc probe". This is that record: if
# pandoc's reading ever changes, these fail here rather than the conversions
# silently becoming unsound.


@pytest.mark.parametrize(("source", "curled"), SAME_READING_PROBES)
def test_recorded_probe_curled_elision_reads_the_same(source: str, curled: str) -> None:
    assert pandoc_ast(source + "\n") == pandoc_ast(curled + "\n")


@pytest.mark.parametrize(("source", "curled"), CHANGED_READING_PROBES)
def test_recorded_probe_curled_elision_reads_differently(source: str, curled: str) -> None:
    assert pandoc_ast(source + "\n") != pandoc_ast(curled + "\n")


def test_unpaired_letter_elisions_are_curled() -> None:
    """`don't stop 'til you drop` gets U+2019 in both positions, per #13."""
    assert smart_quotes("don't stop 'til you drop\n") == "don’t stop ’til you drop\n"
    assert smart_quotes("give 'em hell now\n") == "give ’em hell now\n"


def test_paired_elision_is_permanently_left_straight() -> None:
    """
    The recorded decision for #13's paired case: a refusal, not a deferral.

    In `the '90s, rock 'n' roll`, pandoc pairs the quote before `90s` with the one
    after `n` into a single `Quoted SingleQuote` span. Curling the elisions erases
    that span, which is a meaning change rather than a spelling one, so flowmark
    leaves them straight.

    No normalization is added for it. An entry narrow enough to accept this while
    still refusing genuinely moved quote pairing would have to reproduce pandoc's
    left-to-right pairing algorithm, at which point the gate stops being an
    independent check on the formatter and becomes a copy of it. That is the
    trade #13 asked to have decided, and this is the decision.
    """
    assert smart_quotes("the '90s, rock 'n' roll\n") == "the '90s, rock 'n' roll\n"
