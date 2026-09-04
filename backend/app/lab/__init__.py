"""Quant Lab: independently researched strategies, tested the same way.

Every strategy is a pure function over a causal context — bars up to the
current one, never beyond — plus metadata stating the hypothesis, the family
it belongs to (for ensemble de-correlation), the markets and timeframes it is
meant for, and how it places stops. One harness backtests all of them with the
same costs, the same chronological splits and the same metrics, so results are
comparable and no strategy can be judged on a private definition of success.
"""
