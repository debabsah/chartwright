"""`standards verify-visible`: locked text checked on the rendered dashboard
(chartwright/visible.py).

The browser half (Playwright, an optional extra) measures; `judge` decides. These tests
drive `judge` and the command with a fake page: the facts a browser would report for
text that is visible, or hidden in each way the check knows. A live test runs the real
browser when CHARTWRIGHT_VISIBLE_LIVE names a Superset (base URL; admin/admin unless
CHARTWRIGHT_VISIBLE_USER/_PASSWORD say otherwise) with the standards_live fixture
applied at slug cw-std4-visible.
"""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import chartwright.cli as cli
from chartwright import visible as V
from chartwright.design.standards import StandardsSource
from chartwright.spec import load_spec
from test_standards import make_repo, run
from test_standards_content import apply, read, spec_file

pytest.importorskip("yaml")

FIXTURE = Path(__file__).parent / "fixtures" / "standards_live"
SEEN = V.Facts(found=True, x=48, y=780, width=1500, height=20, page_width=1600,
               page_height=1200, color=(0, 0, 0, 1), background=(255, 255, 255, 1),
               font_px=14, selector="p")


@pytest.fixture(autouse=True)
def no_overlay(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CHARTWRIGHT_DESIGN_DIR", str(home))
    monkeypatch.delenv("CHARTWRIGHT_STANDARDS_DIR", raising=False)


# -- judge: the verdict over what a browser measured ---------------------------------


def test_visible_text_passes():
    assert V.judge(SEEN) == []


@pytest.mark.parametrize("facts, says", [
    (V.Facts(found=False), "not on the page"),
    (replace(SEEN, display_none=True, width=0, height=0), "display none"),
    (replace(SEEN, rendered=False), "rendered text lacks it"),
    (replace(SEEN, visibility="hidden"), "visibility: hidden"),
    (replace(SEEN, width=0.4), "no size"),
    (replace(SEEN, x=4064), "off the page"),
    (replace(SEEN, x=-1600), "off the page"),
    (replace(SEEN, y=5000), "off the page"),
    (replace(SEEN, visible_ratio=0.2), "cut off by its container (20% of it shows)"),
    (replace(SEEN, opacity=0.0), "transparent (opacity 0.00)"),
    (replace(SEEN, clip="clip-path: inset(50%) on div"), "clipped"),
    (replace(SEEN, font_px=1), "too small to read (1px)"),
    (replace(SEEN, color=(253, 253, 253, 1)), "colour contrast 1.02:1 (#fdfdfd on #ffffff)"),
    (replace(SEEN, color=(0, 0, 0, 0)), "colour contrast 1.00:1"),
    (replace(SEEN, covered_by="div#MARKDOWN-sdc-footer-2-1::after"), "covered by div#MARKDOWN"),
    (replace(SEEN, blur_px=4), "blurred (filter: blur(4px))"),
    (replace(SEEN, font_px=0.7), "too small to read (0.7px)"),
])
def test_each_way_to_hide_text_is_named(facts, says):
    reasons = V.judge(facts)
    assert reasons and any(says in r for r in reasons), reasons


def test_contrast_is_wcags_ratio():
    assert V.contrast((0, 0, 0, 1), (255, 255, 255, 1)) == pytest.approx(21.0)
    assert V.contrast((255, 255, 255, 1), (255, 255, 255, 1)) == pytest.approx(1.0)
    assert V.contrast((119, 119, 119, 1), (255, 255, 255, 1)) == pytest.approx(4.48, abs=0.01)


def test_the_contrast_threshold_is_a_setting():
    grey = replace(SEEN, color=(204, 204, 204, 1))          # 1.61:1 on white
    assert V.judge(grey) and not V.judge(grey, min_contrast=1.5)


def test_an_unknown_background_skips_the_contrast_check():
    assert V.judge(replace(SEEN, color=(255, 255, 255, 1), background=None)) == []


# -- what to look for ------------------------------------------------------------------


def test_markdown_and_rendered_text_compare_alike():
    assert V.normalize("**Confidential**: for named recipients only.") == \
        V.normalize("Confidential: for named recipients only.")
    assert V.normalize("[the guide](https://x)  `#ops`") == "the guide ops"
    assert V.lines_of("- one\n\n2. two\n<b>three</b>") == ["one", "two", "three"]


@pytest.fixture
def live_repo(tmp_path):
    repo = make_repo(tmp_path / "repo", {
        "org.yaml": (FIXTURE / "standards" / "org.yaml").read_text(),
        "teams/ops.yaml": (FIXTURE / "standards" / "teams" / "ops.yaml").read_text()}).parent
    return repo


def applied(capsys, repo) -> Path:
    path = spec_file(repo, data=json.loads((FIXTURE / "spec.json").read_text()))
    assert apply(capsys, str(path))[0] == 0
    return path


def std_for(repo, path, **kw):
    return StandardsSource(repo / "standards", **kw).standard_for(load_spec(read(path)),
                                                                  spec_path=path)


def test_targets_are_the_locked_rows_the_spec_holds(live_repo, capsys):
    path = applied(capsys, live_repo)
    found, skipped = V.targets(std_for(live_repo, path), load_spec(read(path)))
    assert [(t.item, t.text) for t in found] == [
        ("layout.footer[org][0]", "Acme Air internal data: do not share outside the company."),
        ("layout.footer[org][classification=confidential][0]",
         "**Confidential**: for named recipients only.")]
    assert skipped == []      # the ops header is open, so not looked for


def test_a_locked_row_the_spec_lacks_is_left_to_standards_check(live_repo, capsys):
    path = applied(capsys, live_repo)
    data = read(path)
    data["layout"]["footer"] = data["layout"]["footer"][:1]   # keep the author's row only
    spec = load_spec(data)
    found, skipped = V.targets(std_for(live_repo, path), spec)
    assert found == [] and {s.item for s in skipped} == {
        "layout.footer[org][0]", "layout.footer[org][classification=confidential][0]"}
    assert "standards check reports that" in skipped[0].why


# -- verify and the command ------------------------------------------------------------


def fake_page(**hide):
    """A measurer: every text is seen, except the ones `hide` names (by substring) with
    the facts given."""
    def measure(texts):
        out = []
        for t in texts:
            facts = SEEN
            for needle, change in hide.items():
                if needle in t:
                    facts = replace(SEEN, **change)
            out.append(facts)
        return out
    return measure


def test_verify_passes_a_dashboard_whose_locked_text_shows(live_repo, capsys):
    path = applied(capsys, live_repo)
    out = V.verify(std_for(live_repo, path), load_spec(read(path)), base_url="", username="",
                   password="", measurer=fake_page())
    assert out["ok"] and out["hidden"] == [] and len(out["items"]) == 2
    assert out["items"][0]["contrast"] == 21.0


def test_verify_lists_each_hidden_item_with_why(live_repo, capsys):
    path = applied(capsys, live_repo)
    out = V.verify(std_for(live_repo, path), load_spec(read(path)), base_url="", username="",
                   password="", measurer=fake_page(confidential={"color": (255, 255, 255, 1)}))
    assert not out["ok"]
    assert out["hidden"] == ["layout.footer[org][classification=confidential][0]"]
    bad = next(i for i in out["items"] if not i["visible"])
    assert bad["reasons"][0].startswith("colour contrast 1.00:1")


def profiles(tmp_path, monkeypatch, extra=""):
    p = tmp_path / "profiles.toml"
    p.write_text('[s]\nbase_url = "http://superset.test"\nusername = "admin"\n'
                 f'password_env = "CW_TEST_PW"\n{extra}', encoding="utf-8")
    monkeypatch.setenv("CHARTWRIGHT_PROFILES", str(p))
    monkeypatch.setenv("CW_TEST_PW", "admin")


def test_the_command_exits_1_naming_hidden_items(live_repo, capsys, monkeypatch, tmp_path):
    path = applied(capsys, live_repo)
    profiles(tmp_path, monkeypatch)
    seen = {}

    def fake_measure(base_url, username, password, slug, texts, **kw):
        seen.update(base_url=base_url, slug=slug)
        return fake_page(acme={"display_none": True, "width": 0, "height": 0})(texts)

    monkeypatch.setattr(V, "measure", fake_measure)
    code, out = run(capsys, "standards", "verify-visible", str(path), "--profile", "s")
    assert code == 1 and out["hidden"] == ["layout.footer[org][0]"]
    assert seen == {"base_url": "http://superset.test", "slug": "cw-live-standards"}


def test_without_the_visual_extra_the_command_says_how_to_install_it(
        live_repo, capsys, monkeypatch, tmp_path):
    path = applied(capsys, live_repo)
    profiles(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    code, out = run(capsys, "standards", "verify-visible", str(path), "--profile", "s")
    assert code == 1 and out["errors"][0]["code"] == "visual_extra_missing"
    assert "pip install 'chartwright[visual]'" in out["errors"][0]["detail"]
    assert "playwright install chromium" in out["errors"][0]["detail"]


def test_playwright_is_no_core_dependency():
    import tomllib

    project = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    core = " ".join(project["project"]["dependencies"])
    assert "playwright" not in core
    assert project["project"]["optional-dependencies"]["visual"] == ["playwright>=1.40"]
    assert "playwright" not in (Path(__file__).parent.parent / "chartwright" / "cli.py").read_text() \
        .split("def _verify_visible")[0]


def test_a_preset_token_profile_is_refused(live_repo, capsys, monkeypatch, tmp_path):
    path = applied(capsys, live_repo)
    p = tmp_path / "profiles.toml"
    p.write_text('[s]\nbase_url = "https://x.us1a.app.preset.io"\napi_token = "t"\n'
                 'api_secret_env = "CW_TEST_PW"\n', encoding="utf-8")
    monkeypatch.setenv("CHARTWRIGHT_PROFILES", str(p))
    monkeypatch.setenv("CW_TEST_PW", "s")
    code, out = run(capsys, "standards", "verify-visible", str(path), "--profile", "s")
    assert code == 1 and out["errors"][0]["code"] in ("visible_needs_password", "profile")


# -- live ---------------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("CHARTWRIGHT_VISIBLE_LIVE"),
                    reason="set CHARTWRIGHT_VISIBLE_LIVE to a Superset base URL to run")
def test_live_locked_text_is_visible_and_hiding_css_is_caught(live_repo, capsys):
    """Against a real Superset: the standards_live spec applied at cw-std4-visible shows
    its locked footer; the same spec with CSS that turns the footer white, which
    standard.css-hides doesn't know, applied at cw-std4-hide-white, is caught."""
    pytest.importorskip("playwright")
    base = os.environ["CHARTWRIGHT_VISIBLE_LIVE"].rstrip("/")
    user = os.environ.get("CHARTWRIGHT_VISIBLE_USER", "admin")
    pw = os.environ.get("CHARTWRIGHT_VISIBLE_PASSWORD", "admin")
    path = applied(capsys, live_repo)
    std, spec = std_for(live_repo, path), load_spec(read(path))
    data = spec.model_dump(mode="json", exclude_unset=True)
    data["dashboard"]["slug"] = "cw-std4-visible"
    out = V.verify(std, load_spec(data), base_url=base, username=user, password=pw,
                   timeout_s=60)
    assert out["ok"], out
    data["dashboard"]["slug"] = "cw-std4-hide-white"
    out = V.verify(std, load_spec(data), base_url=base, username=user, password=pw,
                   timeout_s=30)
    assert not out["ok"] and len(out["hidden"]) == 2


# -- what real browsers measured, judged offline -----------------------------------

MEASURED = json.loads((Path(__file__).parent / "fixtures" / "visible" / "measurements.json")
                      .read_text(encoding="utf-8"))
# What the verdict must say for each recorded stylesheet (tools/record_visible_measurements.py).
EXPECT = {"visible": None, "display": "display none", "visibility": "visibility: hidden",
          "nearwhite": "colour contrast", "darkband": "colour contrast",
          "opacity": "transparent", "overlay": "covered by", "offscreen": "off the page",
          "textindent": "off the page", "blur": "blurred", "scale": "too small to read"}


def as_facts(raw: dict) -> V.Facts:
    return V.Facts(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items()})


@pytest.mark.parametrize("release", sorted(MEASURED["releases"]))
def test_recorded_measurements_get_the_right_verdict(release):
    """text-indent: -9999px and filter: blur(4px) passed as visible before: the box was
    the element's, not its text's, and blur wasn't read."""
    recorded = MEASURED["releases"][release]
    assert set(recorded) == set(EXPECT) == set(MEASURED["css"])
    for name, lines in recorded.items():
        assert len(lines) == 2, name
        for raw in lines:
            reasons = V.judge(as_facts(raw))
            if EXPECT[name] is None:
                assert reasons == [], (release, name, reasons)
            else:
                assert any(EXPECT[name] in r for r in reasons), (release, name, reasons)


def test_the_browser_script_returns_exactly_the_facts_judge_reads():
    """MEASURE_JS can't run without a browser; its result's keys can still be held to
    Facts, so a renamed or dropped measurement fails here."""
    import dataclasses
    import re

    body = V.MEASURE_JS[V.MEASURE_JS.rindex("return {"):]
    keys = set(re.findall(r"([a-z_]+):", body[:body.index("};")]))
    assert keys == {f.name for f in dataclasses.fields(V.Facts)}
    assert "createRange" in V.MEASURE_JS and "blur" in V.MEASURE_JS


# -- the browser, faked: errors and TLS ------------------------------------------------


class FakeError(Exception):
    pass


class FakeTimeout(FakeError):
    pass


def fake_playwright(calls, fail=None):
    class Page:
        url = "http://superset.test/superset/welcome/"

        def set_default_timeout(self, ms):
            calls["timeout_ms"] = ms

        def goto(self, url):
            calls.setdefault("goto", []).append(url)
            if fail and "dashboard" in url:
                raise fail

        def wait_for_selector(self, sel):
            pass

        def locator(self, sel):
            class L:
                first = type("F", (), {"fill": lambda self, v: None})()
            return L()

        def fill(self, sel, v):
            pass

        keyboard = type("K", (), {"press": lambda self, k: None})()
        mouse = type("M", (), {"wheel": lambda self, x, y: None})()

        def wait_for_load_state(self, s):
            pass

        def wait_for_timeout(self, ms):
            pass

        def evaluate(self, js, texts):
            return [{"found": True, "x": 10, "y": 10, "width": 300, "height": 16,
                     "page_width": 1600, "page_height": 1200, "font_px": 14,
                     "color": [0, 0, 0, 1], "background": [255, 255, 255, 1]} for _ in texts]

    class Browser:
        def new_context(self, **kw):
            calls["context"] = kw
            return type("C", (), {"new_page": lambda self: Page()})()

        def close(self):
            pass

    class P:
        chromium = type("Ch", (), {"launch": lambda self: Browser()})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda: (lambda: P(), FakeError, FakeTimeout)


def test_a_slow_dashboard_is_a_typed_error_not_a_traceback(monkeypatch):
    calls = {}
    monkeypatch.setattr(V, "_playwright", fake_playwright(calls, FakeTimeout("Timeout 60000ms")))
    with pytest.raises(V.VisibleError) as e:
        V.measure("http://superset.test", "admin", "pw", "s", ["x"], timeout_s=60)
    assert e.value.code == "visible_timeout" and "--timeout" in str(e.value)
    monkeypatch.setattr(V, "_playwright", fake_playwright(
        calls, FakeError("net::ERR_CERT_AUTHORITY_INVALID at https://x")))
    with pytest.raises(V.VisibleError) as e:
        V.measure("https://superset.test", "admin", "pw", "s", ["x"])
    assert e.value.code == "visible_tls"
    monkeypatch.setattr(V, "_playwright", fake_playwright(calls, FakeError("boom")))
    with pytest.raises(V.VisibleError) as e:
        V.measure("http://superset.test", "admin", "pw", "s", ["x"])
    assert e.value.code == "visible_browser"


def test_the_command_reports_a_timeout_as_json(live_repo, capsys, monkeypatch, tmp_path):
    path = applied(capsys, live_repo)
    profiles(tmp_path, monkeypatch)
    monkeypatch.setattr(V, "_playwright", fake_playwright({}, FakeTimeout("Timeout")))
    code, out = run(capsys, "standards", "verify-visible", str(path), "--profile", "s")
    assert code == 1 and out["errors"][0]["code"] == "visible_timeout"


@pytest.mark.parametrize("extra, ignore, note", [
    ("", False, None),
    ('ca_bundle = "/etc/corp-ca.pem"\n', False, "operating system's certificates"),
    ("verify = false\n", True, "did not check"),
])
def test_tls_is_verified_unless_the_profile_turns_it_off(live_repo, capsys, monkeypatch,
                                                         tmp_path, extra, ignore, note):
    """A ca_bundle profile used to switch certificate checks off in the browser, silently."""
    path = applied(capsys, live_repo)
    profiles(tmp_path, monkeypatch, extra)
    calls = {}
    monkeypatch.setattr(V, "_playwright", fake_playwright(calls))
    code, out = run(capsys, "standards", "verify-visible", str(path), "--profile", "s")
    assert code == 0 and calls["context"]["ignore_https_errors"] is ignore
    assert (note in out["tls"]) if note else "tls" not in out


def test_ci_fails_on_a_verify_visible_regression_once_the_browser_installs():
    """The check used to run under continue-on-error, so a regression only reported."""
    import yaml

    ci = yaml.safe_load((Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml")
                        .read_text(encoding="utf-8"))
    steps = ci["jobs"]["live"]["steps"]
    install = next(s for s in steps if "visual" in str(s.get("run", "")) and "pip" in s["run"])
    check = next(s for s in steps if "ci_live_visible.py" in str(s.get("run", "")))
    assert install.get("continue-on-error") is True and install.get("id")
    assert "continue-on-error" not in check
    assert f"steps.{install['id']}.outcome == 'success'" in check["if"]
