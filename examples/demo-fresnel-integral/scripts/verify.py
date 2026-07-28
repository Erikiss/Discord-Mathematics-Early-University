#!/usr/bin/env python3
"""Numerical verification for the #calculus integral.

    I = int_0^{pi/2} sin(cot^2 x) sec^2 x dx        (the ORIGINAL expression)

Commandment II: this script evaluates the ORIGINAL integrand, never the derived
form. The claimed closed form is only compared against it.

The integrand oscillates infinitely fast as x -> 0 (there cot^2 x -> infinity),
so plain quadrature over [0, pi/2] does NOT converge. The domain is therefore
split at a small delta:

  * [delta, pi/2] -- smooth, ordinary high-precision quadrature on the original
    integrand;
  * [0, delta]    -- the oscillatory head, mapped by the EXACT change of
    variables t = cot x, u = t^2 onto (1/2) * int_{cot(delta)^2}^inf sin(u) u^{-3/2} du,
    which has regular period-2*pi oscillation and is handled by mpmath's
    oscillatory quadrature. A change of variables is an identity, not an
    approximation, so this remains a verification of the original integral.

Run:  uv run python scripts/verify.py     (or: python scripts/verify.py)
"""

import mpmath as mp

mp.mp.dps = 30

DELTA = mp.mpf("0.05")  # split point between smooth part and oscillatory head


def original_integrand(x):
    """The integrand exactly as posted in #calculus."""
    return mp.sin(mp.cot(x) ** 2) * mp.sec(x) ** 2


def smooth_part():
    """int_delta^{pi/2} of the original integrand -- no oscillation here."""
    return mp.quad(original_integrand, [DELTA, mp.pi / 3, mp.pi / 2])


def oscillatory_head():
    """int_0^delta of the original integrand, via the exact substitution."""
    lower = mp.cot(DELTA) ** 2
    return mp.mpf(1) / 2 * mp.quadosc(
        lambda u: mp.sin(u) / u ** mp.mpf("1.5"), [lower, mp.inf], omega=1
    )


def numeric():
    return smooth_part() + oscillatory_head()


def conjectured():
    """The derived closed form: sqrt(pi/2)."""
    return mp.sqrt(mp.pi / 2)


def independent_bracket():
    """Crude but assumption-free bound, using only |sin| <= 1 on the head.

    |int_0^delta sin(cot^2 x) sec^2 x dx| <= delta * sec^2(delta)
    """
    smooth = smooth_part()
    bound = DELTA * mp.sec(DELTA) ** 2
    return smooth - bound, smooth + bound


if __name__ == "__main__":
    value = numeric()
    closed = conjectured()
    diff = abs(value - closed)

    print(f"numeric (original) = {mp.nstr(value, 25)}")
    print(f"closed form        = {mp.nstr(closed, 25)}   [sqrt(pi/2)]")
    print(f"abs diff           = {mp.nstr(diff, 5)}")

    low, high = independent_bracket()
    inside = low <= closed <= high
    print()
    print("independent sanity bracket (no substitution used):")
    print(f"  [{mp.nstr(low, 12)}, {mp.nstr(high, 12)}]  contains closed form: {inside}")

    ok = diff < mp.mpf("1e-15") and inside
    print()
    print("VERIFICATION:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)
