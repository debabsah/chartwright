# Stability and deprecation

What you can rely on when you upgrade Chartwright, and how a change that breaks something reaches you first. The promise below starts with version 1.0.0. Until then, versions are 0.x: a minor release may break something, and its changelog entry says what under "Upgrading from".

## What stays compatible within a major version

From 1.0.0, a 1.x release keeps these working as they did in every earlier 1.x release:

- **Specs:** a spec that validates on 1.x validates on every later 1.x. New fields are optional.
- **Commands and flags:** every command and flag keeps its name and meaning, and every exit code keeps its meaning: 0 for success, 1 for a finding or a failure, and 2 for a command line the parser can't read.
- **JSON output:** every field a command prints keeps its name, type and meaning. New fields may appear, so read the fields you use and ignore the rest.
- **MCP tools:** every tool keeps its name, its parameters and the fields it returns. New tools, new optional parameters and new returned fields may appear.

New capabilities arrive in minor releases (1.1, 1.2), fixes in patch releases (1.1.1).

## How a removal reaches you

Nothing on that list is removed without warning:

1. A minor release marks it deprecated. Using it still works and prints a warning naming what replaces it.
2. It stays, deprecated and listed under "Deprecated" in the changelog, in every later 1.x release.
3. It goes only in the next major release (2.0.0), so at least one minor release warns you first; "Upgrading from 1.x" says what to change.

## What changes outside that promise

These follow Superset and good design practice, so they change in minor releases. Each change is in the changelog.

- **Design findings:** the design review's rules, thresholds and wording improve over time, and a new rule can report something it didn't before. A pipeline that fails on warnings (`advise --strict`, `--design strict`) can therefore fail after an upgrade. The `design_brain` version in the advice output changes whenever findings can change; pin Chartwright's version in CI, and before you raise it, read that release's changelog entry for the design brain.
- **Supported Superset releases:** the releases Chartwright builds and tests against (today 4.1.4, 5.0.0 and 6.1.0, [how it's tested](VERIFICATION.md)) are added and retired as Superset releases, each change in the changelog.

## Related

- [CHANGELOG.md](../CHANGELOG.md): every release, with "Upgrading from" notes
- [LIMITS.md](LIMITS.md): what Chartwright doesn't do today, with what to do instead
