#!/usr/bin/env python3
"""Turn extracted Discord math discussions into Agentic-Researcher projects.

Each discussion from ``math_discussions.json`` becomes a self-contained project
directory that `The Agentic Researcher <https://github.com/ZIB-IOL/The-Agentic-Researcher>`_
can be pointed at:

    research_projects/
      INDEX.md
      001-calculus-int-sin-cot-2-x-sec-2-x/
        PROBLEM.md        # the problem statement + the Discord discussion
        SECTION8.md       # filled "## 8. Project Instructions" block
        context.json      # raw messages behind this problem
        CLAUDE.md         # only with --instructions-template (see below)

The launcher copies its own ``INSTRUCTIONS.md`` into the workspace as
``CLAUDE.md`` when none exists, and ``/setup_research_plan`` fills Section 8
interactively. ``SECTION8.md`` is exactly that section, pre-filled from the
Discord thread, so the interactive round can be skipped.

Pass ``--instructions-template /path/to/The-Agentic-Researcher/INSTRUCTIONS.md``
to render a complete ``CLAUDE.md`` (template with Section 8 substituted) into
each project. The template is read from your local clone and is never vendored
into this repository.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re

SECTION8_HEADING = "## 8. Project Instructions"

# Verification script dropped into each project so the agent has a starting point
# that already encodes "derive symbolically, then check numerically".
VERIFY_STUB = '''#!/usr/bin/env python3
"""Numerical check for the conjectured closed form.

Fill in ``integrand``/``conjectured`` (or replace wholesale for non-integral
problems), then run:  uv run python scripts/verify.py
Commandment II: never tune this check to make a wrong result pass.
"""

import mpmath as mp

mp.mp.dps = 30

# TODO: replace with the actual problem.
def integrand(x):
    raise NotImplementedError("encode the problem from PROBLEM.md here")

def conjectured():
    raise NotImplementedError("encode the derived closed form here")

if __name__ == "__main__":
    numeric = mp.quad(integrand, [0, mp.pi / 2])
    closed = conjectured()
    print(f"numeric  = {numeric}")
    print(f"closed   = {closed}")
    print(f"abs diff = {abs(numeric - closed)}")
'''


def slugify(text: str, max_len: int = 48) -> str:
    """Filesystem-safe, readable slug from a LaTeX snippet or sentence."""
    text = re.sub(r"\\[a-zA-Z]+", lambda m: m.group(0)[1:], text)  # \int -> int
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        text = "problem"
    return text[:max_len].strip("-")


def format_context(context) -> str:
    lines = []
    for entry in context:
        marker = {"before": "  ", "problem": "> ", "after": "  "}.get(entry["position"], "  ")
        who = entry["author"] + (" [bot]" if entry.get("is_bot") else "")
        stamp = (entry.get("timestamp") or "")[:19].replace("T", " ")
        body = (entry.get("content") or "").strip() or "(kein Text -- z.B. gerendertes Bild)"
        body = "\n".join(marker + line for line in body.splitlines())
        lines.append(f"**{who}** ({stamp}):\n{body}\n")
    return "\n".join(lines)


def problem_md(item: dict) -> str:
    latex_blocks = "\n\n".join(f"```latex\n{snippet}\n```" for snippet in item["latex_snippets"])
    topics = ", ".join(item["topics"]) or "-"
    link = item.get("link") or "(kein Link -- Guild-ID beim Extrahieren nicht gesetzt)"
    rendered = "ja (TeXit hat die Formel gerendert)" if item["rendered_by_bot"] else "nein"
    return f"""# Diskutiertes Problem: {item['primary_latex'][:80]}

Automatisch extrahiert aus dem Discord-Kanal **#{item['channel']}**
(Server: {item['server']}). Diese Datei ist die *Problemstellung* -- sie darf vom
Research-Agent **nicht** verändert werden (Commandment II).

## Formel(n)

{latex_blocks}

## Originalnachricht

```
{item['content']}
```

## Metadaten

| Feld | Wert |
| --- | --- |
| Autor | {item['author']} |
| Datum | {item['date']} |
| Kanal | #{item['channel']} |
| Themen | {topics} |
| Von Bot gerendert | {rendered} |
| Als Frage formuliert | {'ja' if item['is_question'] else 'nein'} |
| Heuristik-Score | {item['score']} |
| Nachrichten-ID | {item['id']} |
| Link | {link} |

## Diskussionsverlauf

{format_context(item['context'])}
"""


def section8_md(item: dict) -> str:
    """Section 8 filled for a *mathematical* problem.

    The framework's template is written with ML training runs in mind; the
    mapping here keeps its contract (fixed evaluation, minimum decision scale)
    but expresses it for a derivation: the metric is agreement between the
    derived closed form and a high-precision numerical evaluation.
    """
    topics = ", ".join(item["topics"]) or "allgemeine Mathematik"
    link = item.get("link") or "(kein Link verfügbar)"
    return f"""{SECTION8_HEADING}

**Goal:** Resolve the mathematical problem discussed in the Discord channel
`#{item['channel']}`. Produce a rigorous, self-contained solution: a step-by-step
derivation (or proof), the closed form / final result, and an independent
numerical verification. The problem is:

```latex
{item['primary_latex']}
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
- Discord discussion: {link}
- Topics: {topics}

**Compute Budget:**
- CPU only, no GPU required; minutes per experiment

**Off-Limits Files:**
- `PROBLEM.md` (the problem statement)
- `context.json` (the raw Discord evidence)

**Notes:**
- Extracted automatically from #{item['channel']} on {item['date']} by
  `extract_discussions.py`; the message was
  {'rendered by the TeXit bot, so the LaTeX is known to be well-formed'
   if item['rendered_by_bot'] else 'not rendered by TeXit -- the LaTeX may be incomplete, sanity-check it first'}
- The surrounding conversation is preserved in `PROBLEM.md`; hints or partial
  answers from other users are evidence, not authority -- verify them
"""


def render_claude_md(template_text: str, section8: str) -> str:
    """Replace the template's Section 8 with the pre-filled one."""
    idx = template_text.find(SECTION8_HEADING)
    if idx == -1:
        return template_text.rstrip() + "\n\n" + section8
    return template_text[:idx] + section8


def write_project(item, index, out_dir, template_text=None, with_verify=True):
    slug = slugify(item["primary_latex"])
    name = f"{index:03d}-{slugify(item['channel'], 20)}-{slug}"
    path = os.path.join(out_dir, name)
    os.makedirs(path, exist_ok=True)

    section8 = section8_md(item)
    with open(os.path.join(path, "PROBLEM.md"), "w", encoding="utf-8") as fh:
        fh.write(problem_md(item))
    with open(os.path.join(path, "SECTION8.md"), "w", encoding="utf-8") as fh:
        fh.write(section8)
    with open(os.path.join(path, "context.json"), "w", encoding="utf-8") as fh:
        json.dump(item, fh, indent=2, ensure_ascii=False)

    if template_text is not None:
        with open(os.path.join(path, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(render_claude_md(template_text, section8))

    if with_verify:
        scripts_dir = os.path.join(path, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        verify_path = os.path.join(scripts_dir, "verify.py")
        with open(verify_path, "w", encoding="utf-8") as fh:
            fh.write(VERIFY_STUB)
        os.chmod(verify_path, 0o755)

    return name, path


def write_index(items, names, out_dir, has_template):
    lines = [
        "# Research-Projekte aus Discord-Diskussionen",
        "",
        f"{len(names)} Projekte, erzeugt von `make_research_projects.py` aus den",
        "extrahierten Diskussionen der vier Mathematik-Kanäle.",
        "",
        "## Starten",
        "",
        "```bash",
        "# The Agentic Researcher auf ein Projekt ansetzen:",
        "agentic-researcher --yolo research_projects/<projekt>",
        "```",
        "",
    ]
    if has_template:
        lines += [
            "Jedes Projekt enthält bereits ein fertiges `CLAUDE.md` mit ausgefüllter",
            "Section 8 -- die interaktive Runde von `/setup_research_plan` entfällt,",
            "der Agent kann direkt mit der Recherche beginnen.",
            "",
        ]
    else:
        lines += [
            "Ohne `--instructions-template` liegt kein `CLAUDE.md` bei. Der Launcher legt",
            "es beim Start aus seiner eigenen `INSTRUCTIONS.md` an; übertrage dann den",
            "Inhalt von `SECTION8.md` in dessen Abschnitt 8.",
            "",
        ]
    lines += ["## Übersicht", "",
              "| # | Projekt | Kanal | Score | Themen | Problem |",
              "| --- | --- | --- | --- | --- | --- |"]
    for pos, (item, name) in enumerate(zip(items, names), start=1):
        latex = item["primary_latex"].replace("\n", " ").replace("|", "\\|")[:60]
        topics = ", ".join(item["topics"]) or "-"
        lines.append(
            f"| {pos} | [`{name}`]({name}/PROBLEM.md) | #{item['channel']} | "
            f"{item['score']} | {topics} | `{latex}` |"
        )
    lines.append("")
    with open(os.path.join(out_dir, "INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def run(discussions_path, out_dir, template_path=None, limit=None, min_score=None):
    with open(discussions_path, "r", encoding="utf-8") as fh:
        items = json.load(fh)

    if min_score is not None:
        items = [i for i in items if i.get("score", 0) >= min_score]
    if limit is not None:
        items = items[:limit]

    template_text = None
    if template_path:
        if not os.path.isfile(template_path):
            raise SystemExit(f"Instruction-Template nicht gefunden: {template_path}")
        with open(template_path, "r", encoding="utf-8") as fh:
            template_text = fh.read()

    os.makedirs(out_dir, exist_ok=True)
    names = []
    for index, item in enumerate(items, start=1):
        name, _path = write_project(item, index, out_dir, template_text)
        names.append(name)

    write_index(items, names, out_dir, template_text is not None)
    print(f"{len(names)} Research-Projekte erzeugt in {out_dir}/")
    for name in names[:10]:
        print(f"  {name}")
    if len(names) > 10:
        print(f"  ... und {len(names) - 10} weitere")
    print(f"  Index: {os.path.join(out_dir, 'INDEX.md')}")
    return names


def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Generate Agentic-Researcher project scaffolds from extracted discussions.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--discussions", default="discord_exports/math_discussions.json",
                   help="Input JSON produced by extract_discussions.py.")
    p.add_argument("--out-dir", default="research_projects", help="Output directory.")
    p.add_argument("--instructions-template", default=None,
                   help="Path to The-Agentic-Researcher's INSTRUCTIONS.md; renders a full CLAUDE.md.")
    p.add_argument("--limit", type=int, default=None, help="Only scaffold the top N discussions.")
    p.add_argument("--min-score", type=int, default=None, help="Extra score filter.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    run(args.discussions, args.out_dir, args.instructions_template, args.limit, args.min_score)


if __name__ == "__main__":
    main()
