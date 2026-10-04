"""Standards: rule settings a repository shares across its dashboards
(docs/DESIGN-BRAIN.md sec.18).

A standards directory holds YAML files, one standard each, in design.yaml's vocabulary
plus `name`, `extends`, `default` and `locked`:

    name: finance
    extends: org                      # optional; one parent
    params: {min_axis_height: 7}
    audiences: {executive: {fold_units: 20}}
    severity: {layout.kpi-first: error}
    disable: [narrative.title-style]
    locked: {rules: [size.min-width], params: [fold_units]}

A spec names its standard in `design.standard`; a spec that names none follows the file
marked `default: true`, if there is one. Standards change advice only: compile, plan and
decompile never read them, so a bundle depends on the spec alone.

Layers, root first: the org file, then optionally a unit file and a team file, each
extending the one before, then the dashboard's own design block. A chain holds at most
three files (MAX_FILES), so with the spec's block it is never more than four layers.
Every file and every chain in the directory is
checked when it loads, whether or not a spec uses it, so a broken file fails every run
that reads the directory instead of the one dashboard that happens to name it.
"""

from __future__ import annotations

import dataclasses
import difflib
import glob
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import RULES, SEVERITY_RANK, canonical_rule_id
from .presets import AUDIENCES, PARAM_NAMES, _check_param_block

FILE_KEYS = ("name", "extends", "default", "params", "audiences", "severity", "disable",
             "content", "classifications", "locked")
LOCK_KEYS = ("rules", "params", "content")
# A standard that locks content locks the rule that reports a locked item the spec lacks,
# so nothing below it (a lower file, design.ignore, --ignore, design.yaml) silences it.
CONTENT_LOCK_RULE = "standard.content-locked"
# org, unit, team; the dashboard's own design block is the fourth layer. The one
# place the depth is set (the author chose three files on 2026-10-04).
MAX_FILES = 3
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
LEVELS = ("error", "warn", "info")
ENV = "CHARTWRIGHT_STANDARDS_DIR"


class StandardsError(ValueError):
    """A typed standards failure: `code` goes into the payload's errors entry."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code

    def as_dict(self) -> dict:
        return {"code": self.code, "detail": str(self)}


@dataclass
class StandardFile:
    """One parsed standards file, its own keys only (nothing inherited)."""

    name: str
    path: Path
    extends: str | None = None
    default: bool = False
    params: dict = field(default_factory=dict)
    audiences: dict = field(default_factory=dict)
    severity: dict = field(default_factory=dict)      # canonical rule id -> level
    disable: list = field(default_factory=list)       # canonical rule ids
    locked_rules: list = field(default_factory=list)  # canonical rule ids
    locked_params: list = field(default_factory=list)
    content: dict = field(default_factory=dict)       # design/content.py parse_content
    classifications: list | None = None
    locked_content: list = field(default_factory=list)  # content slots


@dataclass
class Standard:
    """A standard resolved through its `extends` chain. Each key remembers the layer
    (the file's `name`) that set it."""

    name: str
    chain: list[str]                   # root first: ["org", "finance"]
    params: dict = field(default_factory=dict)
    audiences: dict = field(default_factory=dict)
    severity: dict = field(default_factory=dict)       # rule -> level
    disable: dict = field(default_factory=dict)        # rule -> layer that disabled it
    locked_rules: dict = field(default_factory=dict)   # rule -> layer that locked it
    locked_params: dict = field(default_factory=dict)  # parameter -> layer that locked it
    origins: dict = field(default_factory=dict)        # "params.x", "severity.r", ... -> layer
    # Content, per layer, root first: [(layer, its own content)] (design/content.py).
    content_layers: list = field(default_factory=list)
    content_locks: dict = field(default_factory=dict)  # slot -> layers locking it, root first
    classifications: list | None = None                # the allowed values, if declared
    classifications_layer: str | None = None
    # How a spec came to follow it: "design.standard" or "default". Set per spec.
    via: str | None = None


@dataclass
class Standards:
    """Every standard in one directory, resolved and checked."""

    directory: Path
    files: dict[str, StandardFile]
    default: str | None
    resolved: dict[str, Standard]

    def get(self, name: str) -> Standard:
        if name not in self.resolved:
            near = difflib.get_close_matches(name, list(self.resolved), n=1)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            raise StandardsError(
                "unknown_standard",
                f"no standard named {name!r} in {display(self.directory)} "
                f"(standards: {sorted(self.resolved)}){hint}")
        return self.resolved[name]


def display(path: Path) -> str:
    """A path as a person would type it here: relative to the working directory when
    it lies under it, absolute otherwise."""
    try:
        return str(Path(os.path.relpath(path)))
    except ValueError:  # another drive on Windows
        return str(path)


# -- parsing ------------------------------------------------------------------


def _rule_ids(entries, where: str) -> list[str]:
    """Canonical rule ids (renamed ids resolve through the alias table). An unknown id
    is an error, never a no-op: a misspelt lock is a lock that never locks."""
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise StandardsError("standards_file", f"{where} must be a list of rule ids")
    out, unknown = [], []
    for entry in entries:
        if "@" in entry:
            raise StandardsError(
                "standards_file",
                f"{where}: {entry!r} names one chart; a standard covers every dashboard "
                f"that follows it, so it takes rule ids only (a spec's design.ignore takes "
                f"rule@Chart)")
        canon = canonical_rule_id(entry)
        (out if canon in RULES else unknown).append(canon if canon in RULES else entry)
    if unknown:
        hints = []
        for u in unknown:
            near = difflib.get_close_matches(u, list(RULES), n=1)
            hints.append(f"{u!r} (did you mean {near[0]!r}?)" if near else repr(u))
        raise StandardsError("unknown_rule", f"{where}: unknown rule ids {', '.join(hints)}")
    return out


def _name(value, where: str) -> str:
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise StandardsError(
            "standards_file",
            f"{where} must be a name of letters, digits, '-' and '_', got {value!r}")
    return value


def parse_file(path: Path) -> StandardFile:
    import yaml

    where = display(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise StandardsError("standards_file", f"{where} is not valid YAML: {e}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise StandardsError("standards_file", f"{where} could not be read: {e}") from e
    if not isinstance(data, dict):
        raise StandardsError("standards_file", f"{where} must be a YAML mapping")
    unknown = sorted(set(data) - set(FILE_KEYS))
    if unknown:
        raise StandardsError("standards_file",
                             f"{where}: unknown keys {unknown} (known: {list(FILE_KEYS)})")
    if "name" not in data:
        raise StandardsError("standards_file", f"{where}: `name` is required")
    sf = StandardFile(name=_name(data["name"], f"{where}: name"), path=path)
    if data.get("extends") is not None:
        sf.extends = _name(data["extends"], f"{where}: extends")
        if sf.extends == sf.name:
            raise StandardsError("standards_cycle", f"{where}: {sf.name!r} extends itself")
    default = data.get("default", False)
    if not isinstance(default, bool):
        raise StandardsError("standards_file", f"{where}: default must be true or false")
    sf.default = default
    what = f"standards file {where}"
    try:
        sf.params = dict(_check_param_block(data.get("params") or {}, "params", what))
        audiences = data.get("audiences") or {}
        if not isinstance(audiences, dict):
            raise ValueError(f"{what}: audiences must be a mapping")
        bad = sorted(set(audiences) - set(AUDIENCES))
        if bad:
            raise ValueError(f"{what}: unknown audiences {bad} (known: {sorted(AUDIENCES)})")
        sf.audiences = {a: dict(_check_param_block(b or {}, f"audiences.{a}", what))
                        for a, b in audiences.items()}
    except StandardsError:
        raise
    except ValueError as e:
        raise StandardsError("standards_file", str(e)) from e
    severity = data.get("severity") or {}
    if not isinstance(severity, dict):
        raise StandardsError("standards_file",
                             f"{where}: severity must be a mapping of rule id -> level")
    for rule_id, level in severity.items():
        if level not in LEVELS:
            raise StandardsError(
                "standards_file",
                f"{where}: severity[{rule_id!r}] must be error|warn|info, got {level!r}")
    ids = _rule_ids([str(k) for k in severity], f"{where}: severity")
    sf.severity = dict(zip(ids, severity.values()))
    sf.disable = _rule_ids(data.get("disable") or [], f"{where}: disable")
    locked = data.get("locked") or {}
    if not isinstance(locked, dict):
        raise StandardsError("standards_file",
                             f"{where}: locked must be a mapping with `rules` and/or `params`")
    bad = sorted(set(locked) - set(LOCK_KEYS))
    if bad:
        raise StandardsError("standards_file",
                             f"{where}: locked: unknown keys {bad} (known: {list(LOCK_KEYS)})")
    sf.locked_rules = _rule_ids(locked.get("rules") or [], f"{where}: locked.rules")
    lp = locked.get("params") or []
    if not isinstance(lp, list) or not all(isinstance(x, str) for x in lp):
        raise StandardsError("standards_file",
                             f"{where}: locked.params must be a list of parameter names")
    unknown_params = sorted(set(lp) - PARAM_NAMES)
    if unknown_params:
        raise StandardsError(
            "standards_file",
            f"{where}: locked.params: unknown parameters {unknown_params} "
            f"(known: {sorted(PARAM_NAMES)})")
    sf.locked_params = list(lp)
    from . import content as _content

    try:
        if data.get("content") is not None:
            sf.content = _content.parse_content(data["content"], where)
        if data.get("classifications") is not None:
            sf.classifications = _content.parse_classifications(data["classifications"], where)
        sf.locked_content = _content.parse_content_locks(locked.get("content") or [], where)
    except ValueError as e:
        raise StandardsError("standards_file", str(e)) from e
    return sf


# -- resolution ---------------------------------------------------------------


def _most_severe(rule_id: str) -> str:
    return min(RULES[rule_id].severities, key=SEVERITY_RANK.get)


def _check_against_locks(sf: StandardFile, std: Standard) -> None:
    """A lower layer may not loosen what a layer above it locked."""
    where = display(sf.path)
    blocks = [("params", sf.params)] + [(f"audiences.{a}", b) for a, b in sf.audiences.items()]
    for label, block in blocks:
        for k in block:
            if k in std.locked_params:
                raise StandardsError(
                    "locked",
                    f"{where}: {label}.{k} is locked by {std.locked_params[k]!r}; "
                    f"only {std.locked_params[k]!r} (or a layer above it) sets it")
    for r in sf.disable:
        if r in std.locked_rules:
            raise StandardsError(
                "locked",
                f"{where}: disables {r}, which {std.locked_rules[r]!r} locks; "
                f"a locked rule can't be disabled")
    for r, level in sf.severity.items():
        if r in std.locked_rules:
            base = std.severity.get(r) or _most_severe(r)
            if SEVERITY_RANK[level] > SEVERITY_RANK[base]:
                raise StandardsError(
                    "locked",
                    f"{where}: lowers {r} from {base} to {level}, which "
                    f"{std.locked_rules[r]!r} locks; a locked rule's severity can only be "
                    f"raised")


def _merge(sf: StandardFile, std: Standard) -> None:
    """Fold one layer into the standard, per key (docs/DESIGN-BRAIN.md sec.18):
    params and audiences override per parameter (recommended_heights per chart type),
    severity per rule; disable and locked add up."""

    def merge_block(target: dict, block: dict, prefix: str) -> None:
        for k, v in block.items():
            if k == "recommended_heights":
                heights = target.setdefault("recommended_heights", {})
                for t, h in v.items():
                    heights[t] = h
                    std.origins[f"{prefix}.recommended_heights.{t}"] = sf.name
            else:
                target[k] = v
                std.origins[f"{prefix}.{k}"] = sf.name

    merge_block(std.params, sf.params, "params")
    for a, block in sf.audiences.items():
        merge_block(std.audiences.setdefault(a, {}), block, f"audiences.{a}")
    for r, level in sf.severity.items():
        std.severity[r] = level
        std.origins[f"severity.{r}"] = sf.name
    for r in sf.disable:
        std.disable.setdefault(r, sf.name)
    for r in sf.locked_rules:
        std.locked_rules.setdefault(r, sf.name)
    for p in sf.locked_params:
        std.locked_params.setdefault(p, sf.name)
    for r in std.locked_rules:
        if r in std.disable:
            raise StandardsError(
                "locked",
                f"{display(sf.path)}: {r} is locked by {std.locked_rules[r]!r} and disabled "
                f"by {std.disable[r]!r}; a locked rule can't be disabled")


def _chain(name: str, files: dict[str, StandardFile], directory: Path) -> list[StandardFile]:
    chain: list[StandardFile] = []
    seen: list[str] = []
    cur: str | None = name
    while cur is not None:
        if cur in seen:
            raise StandardsError(
                "standards_cycle",
                f"standards extend each other in a circle: {' -> '.join(seen + [cur])}")
        seen.append(cur)
        sf = files.get(cur)
        if sf is None:
            child = files[seen[-2]]
            raise StandardsError(
                "unknown_parent",
                f"{display(child.path)}: extends {cur!r}, which no file in "
                f"{display(directory)} names (standards: {sorted(files)})")
        chain.append(sf)
        cur = sf.extends
    if len(chain) > MAX_FILES:
        words = {2: "two", 3: "three", 4: "four"}.get(MAX_FILES, str(MAX_FILES))
        raise StandardsError(
            "standards_depth",
            f"standard {name!r} is a chain over {words} files: "
            f"{' -> '.join(s.name for s in reversed(chain))} "
            f"({', '.join(display(s.path) for s in reversed(chain))}); a chain holds at "
            f"most {MAX_FILES} files, and the dashboard's own design block is the layer "
            f"after them")
    return list(reversed(chain))


def resolve(name: str, files: dict[str, StandardFile], directory: Path) -> Standard:
    chain = _chain(name, files, directory)
    std = Standard(name=name, chain=[s.name for s in chain])
    for sf in chain:
        _check_against_locks(sf, std)
        _merge(sf, std)
        _check_locked_values(sf, std)
        _merge_content(sf, std)
    from . import content as _content

    try:
        _content.check_resolved(std)
    except ValueError as e:
        raise StandardsError("standards_file", f"{display(chain[-1].path)}: {e}") from e
    return std


def _merge_content(sf: StandardFile, std: Standard) -> None:
    """Fold one layer's content in (design/content.py): rows and CSS add up per layer,
    scalars and keys are the innermost layer's, locks add up. A lower layer can't
    override locked content, and a lock needs content to lock."""
    from . import content as _content

    try:
        _content.check_layer(sf.name, display(sf.path), sf.content, sf.locked_content,
                             sf.classifications, std)
    except _content.LockError as e:
        raise StandardsError("locked", str(e)) from e
    except ValueError as e:
        raise StandardsError("standards_file", str(e)) from e
    std.content_layers.append((sf.name, sf.content))
    for slot in sf.locked_content:
        std.content_locks.setdefault(slot, []).append(sf.name)
    if sf.locked_content:
        std.locked_rules.setdefault(CONTENT_LOCK_RULE, sf.name)
        if CONTENT_LOCK_RULE in std.disable:
            raise StandardsError(
                "locked", f"{display(sf.path)}: locks content but {std.disable[CONTENT_LOCK_RULE]!r} "
                          f"disables {CONTENT_LOCK_RULE}, the rule that reports it")
    if sf.classifications is not None:
        std.classifications = list(sf.classifications)
        std.classifications_layer = sf.name


def _check_locked_values(sf: StandardFile, std: Standard) -> None:
    """A locked parameter needs a value by the layer that locks it: in `params`, or in
    every audience's block. A lock on an unset parameter would pin only the preset's
    value, which the spec's design.audience chooses, so a looser audience would move it."""
    for p in sf.locked_params:
        if p in std.params or all(p in std.audiences.get(a, {}) for a in AUDIENCES):
            continue
        raise StandardsError(
            "standards_file",
            f"{display(sf.path)}: locks {p} without a value; lock a value, not a slot: set "
            f"params.{p} (or {p} under every audience) in this file or one it extends")


def _standard_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*")
                  if p.suffix in (".yaml", ".yml") and p.is_file()
                  and not any(part.startswith(".") for part in p.relative_to(directory).parts))


def load_standards(directory: Path) -> Standards:
    """Parse and check every standards file in a directory (its subfolders too)."""
    if not directory.is_dir():
        raise StandardsError("standards_dir", f"{display(directory)} is not a directory")
    paths = _standard_files(directory)
    if not paths:
        raise StandardsError("standards_dir",
                             f"{display(directory)} holds no .yaml standards files")
    files: dict[str, StandardFile] = {}
    for p in paths:
        sf = parse_file(p)
        if sf.name in files:
            raise StandardsError(
                "standards_file",
                f"two files name the standard {sf.name!r}: {display(files[sf.name].path)} "
                f"and {display(p)}")
        files[sf.name] = sf
    defaults = sorted(n for n, sf in files.items() if sf.default)
    if len(defaults) > 1:
        raise StandardsError(
            "standards_file",
            f"more than one standard says default: true ({defaults}); a repository has "
            f"one default")
    resolved = {n: resolve(n, files, directory) for n in sorted(files)}
    return Standards(directory, files, defaults[0] if defaults else None, resolved)


# -- discovery ----------------------------------------------------------------


def _declares_a_standard(path: Path) -> bool:
    """The file is a YAML mapping with a `name` key: what makes a folder a standards
    folder. Unreadable or broken files declare nothing."""
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and "name" in data


def _has_standards(d: Path) -> bool:
    """A `standards` folder counts only once one of its YAML files declares a standard,
    so a repository's unrelated standards/ (linters, style guides) never opts its
    dashboards in. Once it counts, every file in it is validated (load_standards)."""
    return d.is_dir() and any(_declares_a_standard(p) for p in _standard_files(d))


def discover(start: Path) -> Path | None:
    """The standards directory for a spec in folder `start`: a `standards` folder in
    `start` or a folder above it, up to the repository root (the first folder holding
    `.git`), with at least one YAML file declaring a standard (a mapping with `name`).
    Exactly one is used and nothing merges: two on the way is an error. Outside a
    repository nothing is found."""
    start = start.resolve()
    found: list[Path] = []
    for d in (start, *start.parents):
        if _has_standards(d / "standards"):
            found.append(d / "standards")
        if (d / ".git").exists():
            if len(found) > 1:
                raise StandardsError(
                    "standards_dir",
                    f"two standards directories above {display(start)}: "
                    f"{', '.join(display(f) for f in found)}; keep one per repository, "
                    f"or pass --standards DIR")
            return found[0] if found else None
    return None


@dataclass
class StandardsSource:
    """Where one run takes its standards from: a directory (named, discovered or set in
    the environment), or none. Loads lazily and once; a discovery failure is kept and
    raised on first use, so advice that must not break a pipeline can report it."""

    directory: Path | None = None
    error: StandardsError | None = None
    hint: str = ("pass --standards DIR, or keep a standards/ folder at or above the spec "
                 "inside its git repository")
    _loaded: Standards | None = None

    @classmethod
    def for_cli(cls, explicit: str | None, spec_path: str | Path | None) -> "StandardsSource":
        if explicit:
            return cls(Path(explicit))
        if spec_path is None:
            return cls()
        try:
            return cls(discover(Path(spec_path).parent))
        except StandardsError as e:
            return cls(error=e)

    @classmethod
    def from_env(cls) -> "StandardsSource":
        """The MCP server's standards: it sees specs, never paths, so the directory is
        $CHARTWRIGHT_STANDARDS_DIR, or else the one discovered from the server's working
        directory, as the CLI discovers from a spec's folder."""
        hint = (f"set {ENV} for the MCP server to the repository's standards directory, "
                f"or start the server with a working directory inside the repository")
        d = os.environ.get(ENV)
        if d:
            return cls(Path(d), hint=hint)
        try:
            return cls(discover(Path.cwd()), hint=hint)
        except StandardsError as e:
            return cls(error=e, hint=hint)

    def load(self) -> Standards | None:
        if self.error is not None:
            raise self.error
        if self.directory is None:
            return None
        if self._loaded is None:
            self._loaded = load_standards(self.directory)
        return self._loaded

    def standard_for(self, spec) -> Standard | None:
        """The resolved standard this spec follows, or None. Raises StandardsError."""
        named = spec.design.standard if spec.design else None
        standards = self.load()
        if standards is None:
            if named:
                raise StandardsError(
                    "no_standards_dir",
                    f"the spec follows standard {named!r} (design.standard), but no "
                    f"standards directory was found; {self.hint}")
            return None
        if named:
            return dataclasses.replace(standards.get(named), via="design.standard")
        if standards.default:
            return dataclasses.replace(standards.resolved[standards.default], via="default")
        return None


# -- presentation -------------------------------------------------------------


def report_block(std: Standard, refused: list[str]) -> dict:
    """The `standard` block of an advice payload."""
    out: dict = {"name": std.name, "chain": list(std.chain), "via": std.via,
                 "locked": {"rules": dict(sorted(std.locked_rules.items())),
                            "params": dict(sorted(std.locked_params.items()))}}
    if refused:
        out["refused_ignores"] = [
            {"entry": e, "locked_by": std.locked_rules[canonical_rule_id(e.partition("@")[0])]}
            for e in refused]
    return out


def show_payload(standards: Standards, std: Standard) -> dict:
    """`standards show --json`: the resolved standard, each key with the layer that set
    it and whether it is locked."""

    def entries(block: dict, prefix: str) -> dict:
        out = {}
        for k, v in sorted(block.items()):
            if k == "recommended_heights":
                for t, h in sorted(v.items()):
                    out[f"recommended_heights.{t}"] = {
                        "value": h, "layer": std.origins[f"{prefix}.recommended_heights.{t}"],
                        "locked": k in std.locked_params}
            else:
                out[k] = {"value": v, "layer": std.origins[f"{prefix}.{k}"],
                          "locked": k in std.locked_params}
        return out

    files = standards.files
    out = {
        "stage": "standards", "ok": True, "standards_dir": display(standards.directory),
        "standard": std.name, "default": std.name == standards.default,
        **({"via": std.via} if std.via else {}),
        "chain": [{"name": n, "file": display(files[n].path)} for n in std.chain],
        "params": entries(std.params, "params"),
        "audiences": {a: entries(b, f"audiences.{a}") for a, b in sorted(std.audiences.items())},
        "severity": {r: {"value": lvl, "layer": std.origins[f"severity.{r}"],
                         "locked": r in std.locked_rules}
                     for r, lvl in sorted(std.severity.items())},
        "disable": {r: {"layer": layer} for r, layer in sorted(std.disable.items())},
        "locked": {"rules": dict(sorted(std.locked_rules.items())),
                   "params": dict(sorted(std.locked_params.items()))},
    }
    # Content appears only when the standard has some, so a rules-only standard shows
    # exactly what it showed before content existed.
    from . import content as C

    entries = C.show_entries(std)
    if entries:
        out["content"] = entries
    if std.content_locks:
        out["locked"]["content"] = {s: layers[0] for s, layers in sorted(std.content_locks.items())}
    if std.classifications is not None:
        out["classifications"] = {"value": list(std.classifications),
                                  "layer": std.classifications_layer}
    return out


def show(source: StandardsSource, name: str | None = None, spec=None,
         spec_label: str = "the spec") -> dict:
    """The `standards show` payload (CLI and MCP alike): the named standard, the one
    `spec` follows, or the default. Raises StandardsError."""
    if name and spec is not None:
        raise StandardsError("usage", "name a standard or give a spec, not both")
    standards = source.load()
    if standards is None:
        raise StandardsError("no_standards_dir", f"no standards directory found; {source.hint}")
    if spec is not None:
        std = source.standard_for(spec)
        if std is None:
            return {"stage": "standards", "ok": True,
                    "standards_dir": display(standards.directory), "standard": None,
                    "detail": f"{spec_label} follows no standard: it has no design.standard "
                              f"and no standard says default: true"}
        return show_payload(standards, std)
    chosen = name or standards.default
    if chosen is None:
        raise StandardsError(
            "no_default", "name a standard or give a spec; no standard says default: true "
                          f"(standards: {sorted(standards.resolved)})")
    return show_payload(standards, standards.get(chosen))


def render_show(payload: dict) -> str:
    if payload.get("standard") is None:
        return f"{payload['detail']}\n"
    chain = " -> ".join(c["name"] for c in payload["chain"])
    head = f"Standard {payload['standard']}: {chain}"
    if payload.get("via"):
        head += f"  (via {payload['via']})"
    elif payload["default"]:
        head += "  (the default)"
    lines = [head, *[f"  {c['name']:<12} {c['file']}" for c in payload["chain"]], ""]
    rows: list[tuple[str, str, str, bool]] = []
    for k, e in payload["params"].items():
        rows.append((f"params.{k}", json.dumps(e["value"]), e["layer"], e["locked"]))
    for a, block in payload["audiences"].items():
        for k, e in block.items():
            rows.append((f"audiences.{a}.{k}", json.dumps(e["value"]), e["layer"], e["locked"]))
    for r, e in payload["severity"].items():
        rows.append((f"severity.{r}", e["value"], e["layer"], e["locked"]))
    for r, e in payload["disable"].items():
        rows.append((f"disable.{r}", "", e["layer"], False))
    shown = {r for r in payload["severity"]}
    for r, layer in payload["locked"]["rules"].items():
        if r not in shown:
            rows.append((f"rule {r}", "", layer, True))
    shown_params = {k.split(".")[0] for k in payload["params"]} | {
        k.split(".")[0] for b in payload["audiences"].values() for k in b}
    for p, layer in payload["locked"]["params"].items():
        if p not in shown_params:
            rows.append((f"params.{p}", "(preset)", layer, True))
    if payload.get("classifications"):
        c = payload["classifications"]
        rows.append(("classifications", json.dumps(c["value"]), c["layer"], False))
    if payload.get("content"):
        from .content import describe

        for key, e in payload["content"].items():
            rows.append((key, describe(e["value"], e["slot"]), e["layer"], e["locked"]))
    if not rows:
        lines.append("  sets nothing: the rulebook and the audience presets apply as they are")
    width = max((len(r[0]) for r in rows), default=0)
    vwidth = max([8, *(len(r[1]) for r in rows)])
    for key, value, layer, locked in rows:
        lines.append(f"  {key:<{width}}  {value:<{vwidth}} {layer:<12} "
                     f"{'locked' if locked else ''}".rstrip())
    return "\n".join(lines) + "\n"


# -- specs on disk ------------------------------------------------------------


def expand_specs(args: list[str]) -> list[Path]:
    """Spec files from folders (every .json beneath, hidden folders skipped), globs and
    file paths, in a stable order, each once."""
    out: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out += sorted(q for q in p.rglob("*.json") if q.is_file()
                          and not any(part.startswith(".") for part in q.relative_to(p).parts))
        elif p.is_file():
            out.append(p)
        else:
            hits = sorted(Path(h) for h in glob.glob(a, recursive=True) if Path(h).is_file())
            if not hits:
                raise StandardsError("no_specs", f"{a!r} is no file or folder and matches none")
            out += hits
    seen: set[Path] = set()
    unique = []
    for p in out:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def split_specs(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """(specs, skipped): a JSON file without a top-level spec_version is no spec (a
    package.json beside the specs), so a fleet run lists it and moves on. A file that
    isn't valid JSON at all stays in, to be reported as unreadable: it may be a broken
    spec."""
    specs, skipped = [], []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            specs.append(p)
            continue
        (specs if isinstance(data, dict) and "spec_version" in data else skipped).append(p)
    return specs, skipped


def source_for_specs(explicit: str | None, paths: list[Path]) -> StandardsSource:
    """One standards directory for a run over many specs: --standards, or the one every
    spec discovers. Specs that discover different directories, or none, are an error:
    a fleet run checks one repository's specs against one set of standards."""
    if explicit:
        return StandardsSource(Path(explicit))
    found: dict[Path | None, list[Path]] = {}
    by_folder: dict[Path, Path | None] = {}   # specs share folders; walk each once
    for p in paths:
        folder = p.parent.resolve()
        if folder not in by_folder:
            by_folder[folder] = discover(folder)
        found.setdefault(by_folder[folder], []).append(p)
    dirs = [d for d in found if d is not None]
    if not dirs:
        raise StandardsError(
            "no_standards_dir",
            "no standards directory found above these specs; pass --standards DIR, or keep "
            "a standards/ folder at or above the specs inside their git repository")
    if len(found) > 1:
        parts = [f"{display(d) if d else 'none'} for {display(found[d][0])}"
                 + (f" and {len(found[d]) - 1} more" if len(found[d]) > 1 else "")
                 for d in sorted(found, key=lambda d: str(d))]
        raise StandardsError(
            "standards_dir",
            "these specs find different standards directories (" + "; ".join(parts)
            + "); check each repository on its own, or pass --standards DIR")
    return StandardsSource(dirs[0])


def load_spec_file(path: Path):
    """(spec, None) or (None, error dict) for one spec file."""
    from pydantic import ValidationError

    from ..spec import load_spec

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, {"code": "unreadable_spec", "detail": str(e)}
    try:
        return load_spec(data), None
    except ValidationError as e:
        errs = json.loads(e.json())
        first = errs[0] if errs else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        return None, {"code": "schema",
                      "detail": f"{len(errs)} schema error(s); first at {loc}: {first.get('msg')}"}


# -- check and report ---------------------------------------------------------


def check_spec(spec, source: StandardsSource, *, strict: bool = False) -> dict:
    """One spec's `standards check` entry: the advice with its standard applied, immune to
    the per-machine design.yaml. `ok` is false on an error finding, or on a warn under
    `strict`. Raises nothing: a standard that can't be resolved is an error entry."""
    from . import advise
    from .presets import Overlay

    try:
        std = source.standard_for(spec)
    except StandardsError as e:
        named = spec.design.standard if spec.design else None
        return {"ok": False, "standard": named, "errors": [e.as_dict()]}
    # The overlay is set aside wholesale, so nothing in it reaches the result.
    report = advise(spec, overlay=Overlay(), strict=True, standard=std)
    payload = report.payload()
    out = {"ok": not report.gate(strict),
           "standard": std.name if std else None,
           "chain": list(std.chain) if std else [],
           "via": std.via if std else None,
           "audience": payload["audience"],
           "counts": payload["counts"],
           "findings": payload["findings"],
           "ignored": payload["ignored"]}
    if std is not None:
        block = payload["standard"]
        out["locks"] = {"rules": block["locked"]["rules"], "params": block["locked"]["params"],
                        "refused_ignores": block.get("refused_ignores", [])}
    if payload.get("unmatched_ignores"):
        out["unmatched_ignores"] = payload["unmatched_ignores"]
    if payload.get("polished"):
        out["polished"] = payload["polished"]
    if not out["ok"]:
        from . import locked_note

        levels = ("error",) if report.counts["error"] else ("warn",)
        out["errors"] = [{"code": "design_gate", "detail": (
            ("error-severity findings fail standards check" if report.counts["error"]
             else "warn-severity findings fail standards check --strict")
            + locked_note(out["findings"], levels))}]
    return out


def locks_hit(entry: dict) -> list[str]:
    """Locked rules this spec ran into: a finding of one, or an ignore entry refused."""
    hit = {f["rule"] for f in entry.get("findings", []) if f.get("locked")}
    hit |= {canonical_rule_id(r["entry"].partition("@")[0])
            for r in entry.get("locks", {}).get("refused_ignores", [])}
    return sorted(hit)


def fleet_report(entries: list[dict], *, strict: bool, standards_dir: str,
                 skipped: list[str] = ()) -> dict:
    """`standards check --report`: per spec its standard, pass or fail, findings counted
    by rule and severity and the locks it hit; then the totals across the fleet."""
    specs = []
    totals: dict = {"specs": len(entries), "passed": 0, "failed": 0,
                    "counts": {"error": 0, "warn": 0, "info": 0},
                    "by_rule": {}, "by_standard": {}, "locks_hit": {}}
    for e in entries:
        by_rule: dict = {}
        for f in e.get("findings", []):
            rule = by_rule.setdefault(f["rule"], {})
            rule[f["severity"]] = rule.get(f["severity"], 0) + 1
            tot = totals["by_rule"].setdefault(f["rule"], {})
            tot[f["severity"]] = tot.get(f["severity"], 0) + 1
        hit = locks_hit(e)
        row = {"spec": e["spec"], "standard": e.get("standard"), "ok": e["ok"],
               "counts": e.get("counts", {"error": 0, "warn": 0, "info": 0}),
               "by_rule": dict(sorted(by_rule.items())), "locks_hit": hit}
        if any(err["code"] != "design_gate" for err in e.get("errors", [])):
            row["errors"] = [err for err in e["errors"] if err["code"] != "design_gate"]
        specs.append(row)
        totals["passed" if e["ok"] else "failed"] += 1
        for level, n in row["counts"].items():
            totals["counts"][level] += n
        name = e.get("standard") or None
        key = name if name is not None else "(none)"
        s = totals["by_standard"].setdefault(key, {"specs": 0, "passed": 0, "failed": 0})
        s["specs"] += 1
        s["passed" if e["ok"] else "failed"] += 1
        for r in hit:
            totals["locks_hit"][r] = totals["locks_hit"].get(r, 0) + 1
    totals["by_rule"] = dict(sorted(totals["by_rule"].items()))
    totals["by_standard"] = dict(sorted(totals["by_standard"].items()))
    totals["locks_hit"] = dict(sorted(totals["locks_hit"].items()))
    return {"stage": "standards", "report": True, "ok": totals["failed"] == 0,
            "strict": strict, "standards_dir": standards_dir, "specs": specs,
            "skipped": list(skipped), "totals": totals}


# -- assign -------------------------------------------------------------------


def assign(paths: list[Path], name: str, standards: Standards) -> dict:
    """Write design.standard into each spec file. A file already naming the standard is
    left untouched; any other is rewritten (2-space JSON, as advise --fix writes). A file
    that isn't a valid spec is reported and never written."""
    from pydantic import ValidationError

    from ..spec import load_spec

    standards.get(name)  # an unknown name stops the run before any write
    written, unchanged, errors = [], [], []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            errors.append({"spec": str(p), "code": "unreadable_spec", "detail": str(e)})
            continue
        if not isinstance(data, dict):
            errors.append({"spec": str(p), "code": "schema", "detail": "a spec is a JSON object"})
            continue
        design = data.get("design")
        was = design.get("standard") if isinstance(design, dict) else None
        if was == name:
            unchanged.append(str(p))
            continue
        new = dict(data)
        new["design"] = {**(design if isinstance(design, dict) else {}), "standard": name}
        try:
            load_spec(new)
        except ValidationError as e:
            errs = json.loads(e.json())
            first = errs[0] if errs else {}
            loc = ".".join(str(x) for x in first.get("loc", ()))
            errors.append({"spec": str(p), "code": "schema",
                           "detail": f"not a valid spec, left as it is; first error at "
                                     f"{loc}: {first.get('msg')}"})
            continue
        p.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append({"spec": str(p), "was": was})
    return {"stage": "standards", "ok": not errors, "standard": name,
            "written": written, "unchanged": unchanged, "errors": errors}


# -- apply: content into specs ------------------------------------------------


def apply_spec(data: dict, spec, std: Standard, *, locked: bool = False,
               claim: bool = False) -> tuple[dict, dict]:
    """(new spec data, the spec's entry) for one spec that follows `std`: the content
    the standard has, written by the ownership rules of design/content.py. The new data
    is validated; a result that isn't a valid spec is an error entry and the data is
    returned unchanged. Deterministic: the same spec and standards give the same bytes."""
    from pydantic import ValidationError

    from ..spec import load_spec
    from . import content as C

    analysis = C.analyze(std, spec)
    entry: dict = {"standard": std.name, "chain": list(std.chain)}
    errors = [{"code": "css_markers", "detail": f"{e['item']}: {e['detail']}; nothing was "
                                                f"written: fix the markers by hand, then run "
                                                f"standards apply again"}
              for e in analysis.errors]
    new, changes = C.execute(data, spec, analysis, locked=locked, claim=claim)
    if errors:
        new, changes = data, []    # a spec apply can't fully read is left as it is
    try:
        load_spec(new)
    except ValidationError as e:
        errs = json.loads(e.json())
        first = errs[0] if errs else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        errors.append({"code": "schema", "detail": f"the spec with the standard's content is "
                                                   f"not valid, so nothing was written; first "
                                                   f"error at {loc}: {first.get('msg')}"})
        new, changes = data, []
    acted = {c.item for c in changes}
    entry["changes"] = [c.as_dict() for c in changes]
    # stale: apply would change content. locked_stale: locked content the spec doesn't
    # hold as the standard has it, what --check fails on. locked: the items among those
    # their authors changed, which this run left as they are (only --locked rewrites).
    entry["stale"] = C.stale(analysis)
    entry["locked_stale"] = C.locked_stale(analysis)
    entry["locked"] = [d.id for d in analysis.decisions if d.violation and d.id not in acted]
    entry["decisions"] = analysis.decisions   # for the summary; dropped from the payload
    entry["errors"] = errors
    entry["ok"] = not errors and not entry["locked"]
    return new, entry


def _bucket(d, changes: dict) -> tuple[str, object]:
    """Where one spec's decision on one item lands in the grouped summary."""
    c = changes.get(d.id)
    if c is not None and c["action"] in ("add", "refresh", "remove", "claim", "rewrite"):
        return "change", (c["action"], c.get("to"))
    if d.violation:
        return "locked", None
    if d.state == "unrecorded":
        return "unrecorded", None
    if d.conforms or d.state == "current":
        return "current", None
    if d.state in ("released", "deleted", "author", "tombstone"):
        return "released", None
    return "other", None


def apply_summary(entries: list[dict]) -> list[dict]:
    """The rollout grouped by standard, then by item: per item, how many specs take the
    same change, which ones their authors released (skipped), which a lock holds back,
    and how many are current already."""
    by_std: dict[str, dict] = {}
    for e in entries:
        if e.get("standard") is None or "decisions" not in e:
            continue
        group = by_std.setdefault(e["standard"], {"chain": e["chain"], "specs": 0,
                                                  "items": {}})
        group["specs"] += 1
        changes = {c["item"]: c for c in e["changes"]}
        for d in e["decisions"]:
            item = group["items"].setdefault(d.id, {
                "item": d.id, "layer": d.layer, "locked_by": d.locked_by,
                "changes": {}, "current": 0, "released": [], "locked": [], "unrecorded": []})
            where, key = _bucket(d, changes)
            if where == "change":
                item["changes"].setdefault(key, []).append(e["spec"])
            elif where == "current":
                item["current"] += 1
            elif where in ("released", "locked", "unrecorded"):
                item[where].append(e["spec"])
    out = []
    for name in sorted(by_std):
        g = by_std[name]
        items = []
        for item_id in sorted(g["items"]):
            it = g["items"][item_id]
            it["changes"] = [{"action": a, "to": to, "specs": specs}
                             for (a, to), specs in sorted(
                                 it["changes"].items(), key=lambda kv: (kv[0][0], str(kv[0][1])))]
            if not it["locked_by"]:
                it.pop("locked_by")
            items.append(it)
        out.append({"standard": name, "chain": g["chain"], "specs": g["specs"], "items": items})
    return out


def _names(specs: list[str], limit: int = 8) -> str:
    shown = ", ".join(specs[:limit])
    return shown + (f" and {len(specs) - limit} more" if len(specs) > limit else "")


def render_apply(payload: dict) -> str:
    """The grouped summary as text, ready for a pull request's description."""
    t = payload["totals"]
    verb = "would write" if payload["check"] else "wrote"
    head = (f"standards apply{' --check' if payload['check'] else ''}: {t['specs']} spec"
            f"{'s' if t['specs'] != 1 else ''} following a standard; {verb} {t['written']}, "
            f"{t['unchanged']} unchanged")
    if payload.get("no_standard"):
        head += f"; {len(payload['no_standard'])} follow no standard and stay as they are"
    if payload["check"]:
        n = t["locked_stale"]
        head += (f"\n{n} spec{'s' if n != 1 else ''} lack locked content as the standard has "
                 f"it now" if n else "\nevery spec holds its standard's locked content")
    lines = [head]
    for g in payload["summary"]:
        lines += ["", f"{g['standard']} ({' -> '.join(g['chain'])}), {g['specs']} spec"
                      f"{'s' if g['specs'] != 1 else ''}"]
        for it in g["items"]:
            parts = []
            for c in it["changes"]:
                n = len(c["specs"])
                to = f" {c['to']}" if c.get("to") is not None else ""
                parts.append(f"same change × {n} ({c['action']}{to})" if n > 1
                             else f"{c['action']}{to}: {c['specs'][0]}")
            if it["locked"]:
                parts.append(f"{len(it['locked'])} changed by their authors but locked, left "
                             f"as they are (--locked rewrites them): {_names(it['locked'])}")
            if it["unrecorded"]:
                parts.append(f"{len(it['unrecorded'])} hold it already, unrecorded (--claim "
                             f"records them): {_names(it['unrecorded'])}")
            if it["released"]:
                parts.append(f"{len(it['released'])} released by authors, skipped: "
                             f"{_names(it['released'])}")
            if it["current"]:
                parts.append(f"{it['current']} already current")
            lock = f" (locked by {it['locked_by']})" if it.get("locked_by") else ""
            lines.append(f"  {it['item']}{lock}: {'; '.join(parts) or 'nothing to do'}")
    errors = [(e["spec"], err["detail"]) for e in payload["specs"] for err in e.get("errors", [])]
    if errors:
        lines.append("")
        lines += [f"error: {spec}: {detail}" for spec, detail in errors]
    return "\n".join(lines) + "\n"


def apply_files(paths: list[Path], source: StandardsSource, *, check: bool = False,
                locked: bool = False, claim: bool = False, only: str | None = None,
                skipped: list[str] = ()) -> dict:
    """`standards apply` over spec files. Writes only a spec whose data changed (2-space
    JSON, as advise --fix writes), and nothing in check mode. `only` limits the run to
    the specs following that standard, so a rollout splits into one pull request per
    team."""
    standards = source.load()
    if only is not None:
        standards.get(only)   # an unknown name stops the run before any write
    entries, written, unchanged, no_standard, other = [], [], [], [], []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            entries.append({"spec": str(p), "ok": False, "standard": None,
                            "errors": [{"code": "unreadable_spec", "detail": str(e)}]})
            continue
        spec, err = load_spec_file(p)
        if err is not None:
            entries.append({"spec": str(p), "ok": False, "standard": None, "errors": [err]})
            continue
        try:
            std = source.standard_for(spec)
        except StandardsError as e:
            entries.append({"spec": str(p), "ok": False,
                            "standard": spec.design.standard if spec.design else None,
                            "errors": [e.as_dict()]})
            continue
        if std is None:
            no_standard.append(str(p))
            continue
        if only is not None and std.name != only:
            other.append(str(p))
            continue
        new, entry = apply_spec(data, spec, std, locked=locked, claim=claim)
        entry = {"spec": str(p), **entry}
        changed = new != data
        if changed and not check:
            p.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        entry["written"] = changed and not check
        (written if changed else unchanged).append(str(p))
        entries.append(entry)
    summary = apply_summary(entries)
    for e in entries:
        e.pop("decisions", None)
    if check:
        # The decision record's #7: --check fails on locked content only, so unlocked
        # changes can reach teams in their own pull requests while CI stays green.
        ok = not any(e.get("locked_stale") or e.get("errors") for e in entries)
    else:
        ok = all(e.get("ok", False) for e in entries)
    out = {"stage": "standards", "ok": ok, "check": check, "locked": locked, "claim": claim,
           "standards_dir": display(standards.directory), "specs": entries,
           "summary": summary,
           "totals": {"specs": sum(1 for e in entries if "changes" in e),
                      "written": len(written), "unchanged": len(unchanged),
                      "stale": sum(1 for e in entries if e.get("stale")),
                      "locked_stale": sum(1 for e in entries if e.get("locked_stale"))},
           "skipped": list(skipped)}
    if no_standard:
        out["no_standard"] = no_standard
    if other:
        out["other_standards"] = other
    return out
