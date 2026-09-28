import re
from re import Pattern

from flowmark.linewrapping.tag_handling import TEMPLATE_TAG_PATTERN

# Precompiled regex patterns
PARAGRAPH_BREAK_PATTERN: Pattern[str] = re.compile(r"\n\s*\n")

# Pattern excludes content that contains the same type of quote characters
# Double quotes exclude double quotes, single quotes exclude single quotes.
# Also as a special case allows quotes to start after an em dash (but not other punctuation
# as this is more likely to be code), or inside the `~~` of a strikethrough, which the
# quotes are applied through in Markdown source.
QUOTE_PATTERN: Pattern[str] = re.compile(
    r'(^|\s|—|~~)(?:"([^"\u201c\u201d]*)"|\'([^\'\u2018\u2019]*)\')(\s|$|\.|,|;|:|\?|!|—|\)|~~)',
    re.MULTILINE,
)


# A straight quote in prose position (start/whitespace/em-dash before, non-space
# after) is where a markdown reader may open a quotation.  One of these left
# unconverted before a convertible span (an elision like '90s or 'til) means the
# reader pairs *it* with one of the span's quotes, so converting the span would
# move which text the document quotes.  Mirrors QUOTE_PATTERN's prefix class.
OPENER_SHAPED_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[\s—])(['\"])(?=\S)", re.MULTILINE
)

# An elision apostrophe: a leading straight quote standing for omitted characters,
# before a digit ('90s, '20s) or opening one of the words below ('til, 'em, 'tis).
# Pandoc reads a lone one as an apostrophe rather than an open quote -- two of them
# don't even pair with each other -- so curling it is meaning-neutral.  Verified
# against pandoc 3.9.0.2; the probes are recorded as data in
# `tests/test_smartquotes.py::ELISION_PROBES` so a change in pandoc's reading fails
# there rather than silently making these conversions unsound.
#
# The exception is a closing-capable straight quote later in the segment (one
# preceded by non-whitespace, like the trailing quote of 'n'): pandoc then pairs the
# elision with *it* into a `Quoted` span, and curling would erase that span.  So the
# elision must stay straight, and `CLOSER_CAPABLE_PATTERN` is what detects it.
#
# ## The paired case is a permanent refusal
#
# In `the '90s, rock 'n' roll` pandoc pairs the quote before `90s` with the one
# after `n`, giving one `Quoted SingleQuote` span across the whole run.  Curling the
# elisions erases it -- a meaning change, not a spelling one -- so flowmark leaves
# them straight, and that is deliberate and permanent rather than pending.
#
# No verify normalization is added for it.  An entry narrow enough to accept this
# while still refusing genuinely moved quote pairing would have to reproduce
# pandoc's left-to-right pairing algorithm, at which point the gate stops being an
# independent check on the formatter and becomes a copy of it.  See #13.
#
# Letter elisions are an explicit list rather than `[a-z]`.  Any lowercase letter
# would also curl the opening quote of an unterminated quotation (`he said 'hello`),
# turning an author's intent to quote into an apostrophe.
_ELISION_WORDS = ("til", "em", "tis", "twas", "cause", "bout", "round", "n")
DIGIT_ELISION_PATTERN: Pattern[str] = re.compile(
    r"(^|\s)'(?=\d|(?:" + "|".join(_ELISION_WORDS) + r")\b)", re.MULTILINE
)
CLOSER_CAPABLE_PATTERN: Pattern[str] = re.compile(r"\S'")


def is_multi_paragraph(text: str) -> bool:
    """Check if text contains paragraph breaks (two newlines with optional whitespace)."""
    return PARAGRAPH_BREAK_PATTERN.search(text) is not None


def _apply_smart_quotes_to_text(text: str) -> str:
    """
    Apply smart quote conversion to a text segment.

    This is the core smart quotes logic, applied only to text that is NOT inside
    template tags.
    """

    # Handle quoted text - both single and double quotes.
    #
    # A span is converted only if no opener-shaped straight quote of the same
    # type sits unconverted before it.  Such a quote (an elision like '90s or
    # 'til, or a span this pass skipped) changes how a reader pairs quotes:
    # it would pair with one of this span's quotes, so curling the span would
    # move which text the document quotes.  Quotes this pass converts are no
    # longer straight, so they stop counting as strays.
    openers = [(m.start(1), m.group(1)) for m in OPENER_SHAPED_PATTERN.finditer(text)]
    converted: set[int] = set()
    parts: list[str] = []
    last_end = 0

    for match in QUOTE_PATTERN.finditer(text):
        prefix = match.group(1)
        double_content = match.group(2)  # Content of double quotes
        single_content = match.group(3)  # Content of single quotes
        suffix = match.group(4)

        content = double_content if double_content is not None else single_content
        quote_char = '"' if double_content is not None else "'"
        open_idx = match.start() + len(prefix)
        close_idx = match.end() - len(suffix) - 1

        stray_before = any(
            idx < open_idx and char == quote_char and idx not in converted
            for idx, char in openers
        )
        # Don't convert quotes that contain paragraph breaks, or whose pairing
        # is ambiguous because of an earlier stray quote.
        if stray_before or is_multi_paragraph(content):
            continue

        converted.update((open_idx, close_idx))
        if double_content is not None:
            # Replace double quotes with typographic quotes
            replacement = prefix + "\u201c" + double_content + "\u201d" + suffix
        else:
            # Replace single quotes with typographic quotes
            replacement = prefix + "\u2018" + single_content + "\u2019" + suffix
        parts.append(text[last_end : match.start()])
        parts.append(replacement)
        last_end = match.end()

    parts.append(text[last_end:])
    result = "".join(parts)

    # Handle apostrophes/contractions
    # Only convert single quotes that are:
    # 1. The only quote in the word
    # 2. Have word characters on both sides OR are possessives at end of words ending in s/S

    # A possessive-shaped mark (`quotes'`) is really the *closer* of a straight
    # single-quote pair whenever an opener-shaped straight single quote sits earlier
    # in the segment -- the shape of a pair the double-span pass swallowed whole
    # (`"Nested 'single quotes' inside"`), which never offered its inner span to the
    # span pass.  Curling only the closer there half-converts the pair and moves what
    # the document quotes, so the mark must stay straight.  Any convertible single
    # span is already curled by this point, so a straight opener-shaped single quote
    # still in `result` is exactly such a stray.  Mirrors the span pass's
    # `stray_before`; a contraction (`\w'\w`) never closes a quote, so it is exempt.
    single_opener_positions = [
        m.start(1) for m in OPENER_SHAPED_PATTERN.finditer(result) if m.group(1) == "'"
    ]

    # Split by whitespace to process words individually
    words = re.split(r"(\s+)", result)

    offset = 0
    for i, word in enumerate(words):
        word_start = offset
        offset += len(word)

        # Skip whitespace
        if word.isspace():
            continue

        # Count straight quotes in the word
        quote_count = word.count("'")

        # Only process if there's exactly one straight quote
        if quote_count == 1:
            # Check if it's surrounded by word characters (contractions)
            apostrophe_pattern = r"(\w)\'(\w)"
            if re.search(apostrophe_pattern, word):
                # Replace the single quote with apostrophe
                words[i] = re.sub(r"\'", "\u2019", word)
            # Check if it's a possessive at the end of a word ending in s/S
            elif re.match(r"\w*[sS]\'$", word):
                close_pos = word_start + len(word) - 1
                # ...unless an earlier opener-shaped single quote pairs with this
                # mark, making it a quote closer rather than a possessive.
                if not any(pos < close_pos for pos in single_opener_positions):
                    # Replace the single quote with apostrophe
                    words[i] = re.sub(r"\'", "\u2019", word)

    result = "".join(words)

    # Curl digit elisions ('90s) where no later quote could pair with them.
    def replace_elision(match: re.Match[str]) -> str:
        if CLOSER_CAPABLE_PATTERN.search(result, match.end()):
            return match.group(0)
        return match.group(1) + "\u2019"

    return DIGIT_ELISION_PATTERN.sub(replace_elision, result)


def smart_quotes(text: str) -> str:
    r"""
    Replace straight ASCII quotes and apostrophes with typographic quotes and apostrophes
    when this can be done safely. Aims to be conservative so it doesn't break code or
    things that aren't language.

    IMPORTANT: Quotes inside template tags (Jinja/Markdoc `{% %}`, `{# #}`, `{{ }}`,
    and HTML comments `<!-- -->`) are NEVER converted, as this would break template
    syntax.

    Text that is wrapped in single or double quotes is replaced with typographic quotes
    if it has whitespace or a newline at the front and is followed by whitespace or
    a [.,?!]. The content inside quotes must not contain any of the same type (single
    or double). Quotes containing paragraph breaks (two newlines) are left unchanged.

    Straight quotes are converted to apostrophes if they are the only straight quote
    in the word, and have word characters on both sides:

    I'm there with "George" -> I’m there with “George”
    "Hello," he said. -> “Hello,” he said.
    "I know!" -> “I know!”

    Words in 'single quotes' work too -> Words in 'single quotes' work too

    I'm there -> I’m there
    I'll be there, don't worry -> I’ll be there, don’t worry
    X is 'foo' -> X is ‘foo’

    A few special rules to better help with English:

    Jill's -> Jill’s
    James' -> James’
    the '80s and '90s -> the ’80s and ’90s

    Digit elisions curl only when no later straight quote could pair with
    them, and letter elisions ('til, 'em) never do -- a reader takes those
    as open quotes:

    'til we meet -> 'til we meet

    Other patterns are unchanged:

    x="foo" -> x="foo"
    x='foo' -> x='foo'
    Blah'blah'blah -> Blah'blah'blah
    ""quotes"s -> ""quotes"s
    \"escaped\" -> \"escaped\"
    'apos -> 'apos
    'apos'trophes -> 'apos'trophes
    $James' -> $James'

    A straight quote left in place before a quoted span (such as an elision
    apostrophe) makes the pairing ambiguous -- a reader may pair it with one of
    the span's quotes -- so spans after it are also left unchanged:

    the '90s, rock 'n' roll -> the '90s, rock 'n' roll
    'til you 'see' it -> 'til you 'see' it

    Template tag content is never modified:

    {% field kind="string" %} -> {% field kind="string" %}
    {{ variable }} -> {{ variable }}
    {# comment "here" #} -> {# comment "here" #}
    <!-- html kind="comment" --> -> <!-- html kind="comment" -->

    """
    # Split text into segments: template tags (protected) and regular text.
    # We apply smart quotes only to regular text segments.
    segments: list[str] = []
    last_end = 0

    for match in TEMPLATE_TAG_PATTERN.finditer(text):
        start, end = match.span()

        # Add the text before this tag (apply smart quotes to it)
        if start > last_end:
            before_text = text[last_end:start]
            segments.append(_apply_smart_quotes_to_text(before_text))

        # Add the tag itself unchanged
        segments.append(match.group(0))
        last_end = end

    # Add any remaining text after the last tag
    if last_end < len(text):
        remaining = text[last_end:]
        segments.append(_apply_smart_quotes_to_text(remaining))

    return "".join(segments)
