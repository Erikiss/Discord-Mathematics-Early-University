## 8. Project Instructions

**Goal:** Resolve the mathematical problem discussed in the Discord channel
`#calculus`. Produce a rigorous, self-contained solution: a step-by-step
derivation (or proof), the closed form / final result, and an independent
numerical verification. The problem is:

```latex
\int_{0}^{\frac{\pi}{2}} \sin(\cot^{2}(x)) \sec^{2}(x) \text{dx}
```

**Primary Metric:**
- Name: absolute deviation between the derived closed form and a high-precision
  numerical evaluation of the original expression
- Direction: lower is better (target: < 1e-15 with `mp.dps = 30`)
- Eval command: `uv run python scripts/verify.py`
- Baseline: TBD (no result derived yet)

**Fixed Constraints (protected by Commandment II):**
- The problem statement in `PROBLEM.md` is immutable -- do not simplify, re-scope,
  or "fix" the integrand/expression to make it tractable
- The numerical verification must evaluate the ORIGINAL expression, never the
  derived form, otherwise it verifies nothing
- If the result diverges or the statement is ill-posed, say so and prove it; a
  rigorous negative result counts as success

**Minimum Decision Scale (Commandment VII):**
- Numerical agreement must hold at >= 15 significant digits (mpmath `mp.dps = 30`)
- Spot checks at a single precision or a single sample point are debugging-only
- Beware oscillatory or singular endpoints: plain quadrature can silently
  under-resolve them and report a confidently wrong value. Split the domain or
  use oscillatory quadrature, and always sanity-check with an assumption-free bound

**Approach Guidelines:**
1. Restate the problem precisely; define every symbol, domain, and branch choice
2. Derive symbolically by hand first (Commandment M2: derivations before code);
   note substitutions and where convergence conditions are used
3. Only then verify numerically with mpmath at high precision
4. Cross-check against a CAS (sympy) where possible, and search for the standard
   name of the result (e.g. Fresnel, Dirichlet, Frullani) to flag rediscovery
5. Record every step in `report.tex`

**References:**
- Discord discussion: (kein Link verfügbar)
- Topics: integral

**Compute Budget:**
- CPU only, no GPU required; minutes per experiment

**Off-Limits Files:**
- `PROBLEM.md` (the problem statement)
- `context.json` (the raw Discord evidence)

**Notes:**
- Extracted automatically from #calculus on 2026-07-27 by
  `extract_discussions.py`; the message was
  rendered by the TeXit bot, so the LaTeX is known to be well-formed
- The surrounding conversation is preserved in `PROBLEM.md`; hints or partial
  answers from other users are evidence, not authority -- verify them
