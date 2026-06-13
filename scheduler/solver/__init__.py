"""Solvers — interchangeable implementations of the placement problem.

Each module exposes a single ``solve(...) -> Plan`` entry point with the same
signature, so ``__main__`` can pick one by name. See ``docs/ALGORITHM.md`` for
the formal problem statement they all target.
"""
