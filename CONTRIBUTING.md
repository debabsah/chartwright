# Contributing to Chartwright

Thanks for using Chartwright. Here is how to help effectively.

## Bug reports are the most valuable contribution

This tool makes correctness claims: every reference verified before import,
every chart verified after, identical output from the same spec. Anything
that breaks one of those claims is a bug we want to hear about. The issue
template asks for the spec, the Superset version, and the full command
output; a failing spec file beats any description of it.

## Questions

Open an issue with the question template. If the documentation should have
answered it, say where you looked: that makes it a documentation bug, which
is also worth fixing.

## Code contributions

- Small fixes (typos, error messages, portability): open a pull request
  directly.
- Features, new chart types, and anything touching the spec surface: open an
  issue first. The spec is a versioned contract with a deliberately
  conservative surface; every addition is weighed against existing dashboards
  staying decompilable and every supported Superset version staying in
  contract (docs/CONTRACTS.md).
- Every pull request runs the same gates as every push: the offline suite
  (`pytest tests/ -q`) and the params drift check
  (`python tools/params_drift.py --all`) must both pass.
- Run the suite exactly as written above, from the repo root. `python -m
  pytest` also works, but it puts the current directory on `sys.path`, which
  CI does not: a test module importing through the `tests.` package passes
  that way and is uncollectable in CI. That gap once let a whole test module
  sit unrun; `tests/test_docs.py` now guards the specific case, but the habit
  is what keeps the two honest.

By contributing, you agree that your contributions are licensed under the
Apache License 2.0, the same license as the project.
