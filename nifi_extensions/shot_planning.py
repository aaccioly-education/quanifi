# Quanifi — quantum-computing components for Apache NiFi
# Copyright (C) 2026 Neilson Ramalho
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License, version 3, as published by
# the Free Software Foundation. This program is distributed WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the license along with this program; if not,
# see <https://www.gnu.org/licenses/>. Commercial licensing is also available:
# see COMMERCIAL.md at the repository root.

"""Shot-count planning shared by the oracle and the batch submitters.

This is a helper module, not a processor. The batch submitters need
``required_shots`` for their preflight report, and importing it from the
QuantumSuccessProbabilityOracle processor module would make one processor
depend on another. The Docker build copies only the selected processors plus
the helper modules they import, so such an import can fail to load. Pure
Python on purpose: NiFi installs dependencies per processor.
"""

import math


def _norm_ppf(p):
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to about 1.15e-9 over the open unit interval, which is far beyond
    what a shot-count decision needs.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be in (0, 1), got %r" % (p,))

    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)

    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


def required_shots(p_ref, min_diff, alpha=0.05, power=0.8):
    """Shots per arm needed to detect ``min_diff`` at ``alpha`` and ``power``.

    The standard two-proportion normal approximation:

        n = 2 * p_bar * (1 - p_bar) * (z_{alpha/2} + z_{power})^2 / diff^2

    where ``p_bar`` is the mean of the two proportions being compared, taken
    here as ``p_ref`` and ``p_ref - min_diff``.

    Worked example. Suppose a control run on hardware succeeds about 90% of the
    time, and a mutant is worth catching if it drops that to 80% or below. Then
    ``p_ref = 0.90``, ``min_diff = 0.10``, ``p_bar = 0.85``, and at the
    conventional ``alpha = 0.05`` two-sided and ``power = 0.80``::

        z_{alpha/2} = 1.95996,  z_{power} = 0.84162
        n = 2 * 0.85 * 0.15 * (1.95996 + 0.84162)^2 / 0.10^2
          = 2 * 0.1275 * 7.849 / 0.01
          = 200.1  ->  201 shots per arm

    The result is a ceiling, not a rounding: 200 shots would leave the study
    fractionally under 80% power, so the honest number is 201.

    Two consequences worth internalising before choosing a shot budget. The
    dependence on ``min_diff`` is quadratic, so halving the effect you want to
    catch quadruples the cost. And the dependence on ``p_ref`` is weak, so a
    noisier device does not change the budget nearly as much as a stricter
    detection target does.

    This is the *per-arm* count. A paired comparison of a control against a
    mutant needs it twice.
    """
    if not 0.0 < min_diff <= 1.0:
        raise ValueError("min_diff must be in (0, 1]")
    p2 = max(0.0, min(1.0, p_ref - min_diff))
    p_bar = (p_ref + p2) / 2.0
    z_a = _norm_ppf(1 - alpha / 2.0)
    z_b = _norm_ppf(power)
    return math.ceil(2 * p_bar * (1 - p_bar) * (z_a + z_b) ** 2 / min_diff ** 2)
