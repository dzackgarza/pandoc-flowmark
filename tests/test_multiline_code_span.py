"""Regression: flowmark must terminate on a list item whose inline code span
spans a hard line break.

Observed defect: feeding the markdown below to `flowmark --inplace <file>`
(equivalently `reformat_text` in markdown mode) never returns — flowmark spins
in an infinite loop in its line-wrapping logic. Reproduced from
ai-review-ci/skills/quality-control/SKILL.md and minimized to two lines.
"""

import signal
from types import FrameType

from flowmark import reformat_text_unchecked

# A list item containing an inline code span `assert x ... is not None` whose
# opening and closing backticks sit on different source lines. This exact byte
# sequence hangs flowmark's markdown line-wrapping.
REPRO = "- **Slop patterns:** tautological checks (e.g., `assert x\nis not None` without asserting values), testing trivial getters.\n"


class _Timeout(Exception):
    pass


def _on_alarm(_signum: int, _frame: FrameType | None) -> None:
    raise _Timeout("reformat_text did not terminate")


def test_list_item_multiline_code_span_terminates() -> None:
    """reformat_text must return on a list item with a multi-line inline code
    span, and must preserve the code span content (CommonMark collapses the
    interior newline to a space)."""
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, 10.0)
    result = reformat_text_unchecked(REPRO)
    signal.setitimer(signal.ITIMER_REAL, 0.0)

    # The inline code span survives reformatting as a single backtick span.
    assert "`assert x is not None`" in result
