# TODO / offene Fragen

- [x] Problemstellung aus #calculus extrahieren und fixieren (`PROBLEM.md`)
- [x] E000: Baseline-Messung (naive Quadratur) — verworfen, Messfehler dokumentiert
- [x] E001: Substitution $t=\cot x$ + partielle Integration herleiten
- [x] E002: numerische Verifikation am **Original**-Integranden (9.1e-18)
- [x] E003: symbolischer Gegencheck mit sympy
- [x] Ergebnis: $I=\sqrt{\pi/2}$, als Fresnel-Integral identifiziert (keine Neuheit)

## Offen / mögliche Anschlussfragen

- [ ] Verallgemeinerung: $\int_0^{\pi/2}\sin(\cot^p x)\sec^2 x\,dx$ für $p>0$ —
      liefert die gleiche Substitution eine geschlossene Form über $\Gamma$?
- [ ] Analogon mit $\cos$ statt $\sin$: $\int_0^{\pi/2}\cos(\cot^2 x)\sec^2 x\,dx$
      divergiert (der Integrand geht bei $x\to0$ nicht gegen 0) — sauber beweisen.
- [ ] `quadosc` ab $u=0$ statt ab $\cot^2\delta$ weicht um 4e-3 ab (Endpunkt-Artefakt
      an der integrierbaren Singularität $u^{-3/2}$). Als Fallstrick dokumentiert,
      nicht als Mathematikproblem.
