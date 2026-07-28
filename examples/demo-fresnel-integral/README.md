# Demo: von der Discord-Diskussion zum Research-Ergebnis

Dieses Verzeichnis zeigt **eine vollständig durchgeführte Recherche** zu genau der
Gleichung aus dem Screenshot des Kanals `#calculus` — also das, was am Ende der
Pipeline herauskommt:

```
Discord #calculus  ->  extract_discussions.py  ->  make_research_projects.py  ->  Research-Agent
```

## Inhalt

| Datei | Rolle |
| --- | --- |
| `PROBLEM.md` | Die fixierte Problemstellung (vom Agent nicht änderbar, Commandment II) |
| `SECTION8.md` | Der vorausgefüllte Abschnitt 8 für The-Agentic-Researcher |
| `scripts/verify.py` | Numerische Verifikation am **Original**-Integranden |
| `report.tex` | Das Forschungsprotokoll im Format des Frameworks (E000–E003) |
| `TODO.md` | Offene Anschlussfragen |

## Ergebnis

$$\int_{0}^{\pi/2}\sin(\cot^{2}x)\,\sec^{2}x\,dx=\sqrt{\tfrac{\pi}{2}}=\tfrac{\sqrt{2\pi}}{2}\approx 1.2533141373155003$$

Die Substitution $t=\cot x$ lässt den Faktor $\sec^2 x$ exakt gegen die
Jacobi-Determinante wegfallen und reduziert das Integral auf
$\int_0^\infty \sin(t^2)/t^2\,dt$ — ein **Fresnel-Integral**.

## Selbst nachrechnen

```bash
pip install mpmath        # oder: uv run --with mpmath python scripts/verify.py
python scripts/verify.py
```

Erwartete Ausgabe: Abweichung `9.1215e-18` zwischen Original und geschlossener
Form, `VERIFICATION: PASS`.

> **Fallstrick, den die Demo bewusst dokumentiert:** Der Integrand oszilliert bei
> $x\to0$ unendlich schnell. Naive Quadratur über $[0,\pi/2]$ liefert
> stillschweigend `1.25075…` — auf drei Nachkommastellen falsch. `verify.py`
> spaltet deshalb bei $\delta=0.05$ und behandelt den Kopf oszillatorisch.
