# TODO

## Linter scope: mechanically checkable parts

Put only the mechanically checkable parts into the linter. The strongest candidates are:

1. **House delimiter macros.** Flag `\bigl...\bigr`, `\Bigl...\Bigr`, and ordinary `\left(...\right)` / `\left[...\right]` when the corresponding house macro exists. This is deterministic and usually autofixable to `\qty`, `\bqty`, `\abs`, etc.

2. **Semantic macro arity.** The linter should know the macro manifest and reject malformed invocation of semantic macros. If `\disc` has arity 1, then `\disc L` and `\disc(L)` are wrong source forms; `\disc{L}` is required. Same for `\cardinality`, `\padiccompletion`, and other object-valued macros. This belongs in the linter much more strongly than in prose policy.

3. **TikZCD source syntax.** Flag `\begin{tikzcd}` inside display math, unsupported attributes on `tikzcd` fences, and other noncanonical diagram wrappers. This is fully syntactic and often autofixable.

4. **Display-math economy.** Warn when a display contains only one short inline-safe expression with no alignment, numbering, multiline structure, large operator, matrix, or diagram. Warning, not error; no blind autofix.

5. **Bare manual cardinality syntax.** A limited rule is worthwhile: flag obvious `|X|`/`\lvert X\rvert` cases where `X` is syntactically a named set/group/object and suggest `\cardinality{X}`. Do not try to infer cardinality from arbitrary `\abs{...}`; that requires mathematical typing and will create false positives.

6. **Statement-block structural violations.** Deterministically flag proof fences/environments nested inside theorem/proposition/definition statement fences, examples inside definitions, theorem text after an explicit `Proof.` marker in the same block, etc. The stronger rule "construction belongs in the proof" is semantic and should remain review policy.

7. **Untyped vague-zero phrases.** A small prose rule can catch the known bad idioms: "mixed terms vanish," "the remaining terms vanish," "the radical vanishes," "the map is natural," etc., when no mathematical referent occurs in the clause. This should be a warning with a request to name the map/component, not an autofix.

8. **Known adjectival map prose.** Existing linting should catch phrases such as "the inclusion," "the natural map," "the induced map," "the restriction," or "the carrier morphism" when the sentence introduces no nearby typed arrow. Again, warning only. This operationalizes `SYM-14`.

I would not put these into the linter as general rules: "take cokernels in the correct structured category," left/right radical completeness, full ambient-category correctness, whether a proposition statement contains too much construction, whether an example is really a proposition, and whether a negative formulation should be replaced by a positive theorem. Those require mathematical understanding; heuristic lint rules would mostly reward superficial rewrites.

So the clean division is: **syntax and recoverable local structure → linter; mathematical type correctness and epistemic status → annotations/review.** The first four above should be hard linter work now; 5–8 are useful warning-level rules.