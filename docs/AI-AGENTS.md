# Building and changing dashboards with an AI agent

You describe a dashboard, or a change to one chart, in plain words. Your agent writes or edits the spec file, and you get the dashboard built in Superset with every reference checked first, each chart confirmed on the dashboard and its query run. Ask for a change and the same dashboard and charts are updated, with no copies left behind.

## Connect your agent

Install the MCP server, which adds the `chartwright-mcp` command (Python 3.11 or newer):

```bash
pip install "chartwright[mcp]"
```

Register it with Claude Code. `CHARTWRIGHT_PROFILES` points the server at a profiles file holding only the instances you want the agent to reach (see [Keep production out of reach](#keep-production-out-of-reach)):

```bash
claude mcp add chartwright -e CHARTWRIGHT_PROFILES="$HOME/.config/chartwright/agent-profiles.toml" -- chartwright-mcp
```

For Claude Desktop or any other MCP client, add the server to the client's config:

```json
{
  "mcpServers": {
    "chartwright": {
      "command": "chartwright-mcp",
      "env": {"CHARTWRIGHT_PROFILES": "/Users/you/.config/chartwright/agent-profiles.toml"}
    }
  }
}
```

- Use the absolute path to the profiles file; `~` isn't expanded there.
- If the client can't find `chartwright-mcp`, give the full path that `which chartwright-mcp` prints.
- The server reads passwords the same way the CLI does. A client started from your desktop doesn't see variables exported in a shell, so give the profile a `password_cmd` (a command that prints the password, such as your password manager's CLI) instead of `password_env`.

### The Claude Code skill

The repository also ships a Claude Code skill. With it, Claude reads the schema and the design brief, writes only the spec, then runs `validate`, `check`, `advise` and `apply` in order, and edits the spec to change a dashboard later. It runs from a clone:

```bash
git clone https://github.com/debabsah/chartwright && cd chartwright
python -m venv .venv && .venv/bin/pip install -e ".[mcp]"
.venv/bin/python install-skill.py
```

- The installer copies the skill to `~/.claude/skills/superset-dashboard/`, so it works from any directory, and writes `.mcp.json` in the clone to register the MCP server there.
- The skill keeps specs in the clone's `specs/` folder. Re-run `install-skill.py` after moving the clone. On Windows, run `.venv\Scripts\python install-skill.py`.

## The 15 tools

Each tool runs the same code as its CLI command and returns a JSON report.

| MCP tool | CLI command | What the agent gets |
|---|---|---|
| `get_spec_schema` | `chartwright schema` | The JSON Schema every spec follows |
| `design_brief` | `chartwright brief` | The design brief to read before writing |
| `validate_spec` | `chartwright validate` | Field and layout errors, offline |
| `check_spec` | `chartwright check` | Every missing dataset, column or metric, plus design advice |
| `advise_spec` | `chartwright advise` | The readability review; pass a profile for the data-aware rules |
| `fix_spec` | `chartwright advise --fix` | The spec with safe fixes applied and design defaults filled in, returned instead of written to a file |
| `explain_spec` | `chartwright explain` | For each chart, where each display setting's value comes from and how to change it |
| `plan_dashboard` | `chartwright plan` | What a build would add, change or remove |
| `build_dashboard` | `chartwright apply` | The build report: dashboard URL, backup path, each chart's query result, the design advice; with `save_queries`, each chart's saved query for CSV reports |
| `decompile_dashboard` | `chartwright decompile` | A spec of a live dashboard, with what it couldn't carry |
| `adopt_dashboard` | `chartwright adopt` | A spec that takes over a dashboard made in the UI where it is, with what the first build resets |
| `redesign_dashboard` | `chartwright redesign` | A decompiled spec with the safe fixes applied and the remaining findings |
| `standards_check` | `chartwright standards check` | The spec reviewed under your organisation's standard, offline |
| `standards_show` | `chartwright standards show` | A standard after its inheritance: each setting, where it was set and whether it is locked |
| `standards_apply` | `chartwright standards apply` | The spec with its standard's header, footer, CSS and settings written in |

The agent passes the spec as JSON text and a profile name. `fix_spec` and `redesign_dashboard` hand back a spec; the agent saves it and builds it.

When something fails, a tool returns the same JSON error the CLI prints, so the agent can tell the kind of failure apart: `"stage": "profile"` for a missing profile or an unset password variable, code `api` for a failed sign-in or a Superset error, and code `unexpected` for anything else.

## What gets checked

Before writing the spec:

- `design_brief` gives the agent sizing budgets, chart choice and layout rules for an executive, analytical or operational audience.
- `advise_spec` reviews the spec for readability, such as a pie with too many slices or a chart too short for its axis labels. `fix_spec` applies the safe ones, such as heights and bar orientation, and fills design defaults the spec left unset, such as axis label formats; the agent keeps the spec it returns and edits that. On a `sketch` layout, those fixes write explicit heights that override the drawing; to keep the drawing, edit the sketch lines instead.

Before anything is written to Superset, Chartwright looks up, on the instance, each dataset the spec names and each column and metric its charts and filters use. A dataset is matched by its database connection name, table and schema. The agent gets the full list of missing references in one response, not one at a time. Columns inside a custom SQL metric or filter can't be looked up this way; they come back under `unchecked_sql`. The check also confirms the spec's owners and theme exist and that the instance's Superset release can take every field.

After the import, `build_dashboard`:

- takes off any chart the spec doesn't name, such as one added in the UI, naming it in a warning, then confirms the dashboard holds exactly the spec's charts;
- applies each filter's chart scope;
- runs every chart's query, and names any chart whose query fails or returns no rows.

When the dashboard already exists, a backup is taken first. If anything fails before or during the import, including the in-place chart updates, that backup is put back automatically. A failure at the linkage, scope, owners or query step leaves the new version live; restore the backup from the CLI (see [What the MCP server leaves to the CLI](#what-the-mcp-server-leaves-to-the-cli)).

## Updated in place

A dashboard's id comes from its `slug`, and each chart's from the slug plus the chart's name. Building the spec again, or retrying a failed build, reaches the same dashboard and the same charts, and charts keep their Superset ids, so links to them keep working.

- Renaming a chart shows in `plan_dashboard` as one chart removed and one added; the build deletes the old chart and creates a new one. In a spec from `adopt_dashboard`, which names the dashboard and its charts by their own ids, a renamed chart keeps its id.
- Changing the slug builds a new dashboard and leaves the old one as it was; retire the old one in Superset.
- Builds stop at a slug that holds a dashboard Chartwright didn't build. To manage that dashboard where it is, have the agent run `adopt_dashboard` and show you what it lists under `resets` before the first build; otherwise choose another slug.
- Two specs with the same slug manage the same dashboard, so give the agent its own slug prefix.

## Change one chart

Ask for the change in words, such as "show only the top 5 regions in Revenue by Region".

1. The agent edits that chart's entry in the spec, here `"row_limit": 5`, and leaves the rest alone.
2. `plan_dashboard` lists it under `charts_changed`. It also lists anything edited in the Superset UI since the last build, which the build would overwrite.
3. `build_dashboard` updates the charts in place and keeps their ids.

Every build rewrites the dashboard from the spec, so edits made in the UI are undone; owners, tags and a theme the spec leaves out stay as they are. Make lasting changes, and add charts, in the spec; a chart added to the dashboard in the UI is taken off it on the next build and stays under Charts.

## What the agent sees

Through Chartwright, the agent sees names and counts:

- each missing dataset, column or metric by name; a missing saved metric lists the dataset's saved metrics, and a missing column suggests up to three close matches (in the error's `candidates` field) and lists the dataset's columns;
- specs of existing dashboards, including titles, markdown text, filter default values and the owners' usernames or emails;
- distinct-value counts per column, capped, when `advise_spec` or `redesign_dashboard` runs with a profile;
- the number of rows each chart's test query returned.

No tool returns query rows. No tool lists a dataset's columns up front: the agent learns them from `check_spec`, whose error for a wrong column names the closest matches and the dataset's columns. To skip that round, put the column names in your request. The agent passes only a profile name; the password stays in the environment variable or password command the profile names, and no tool returns it.

## Keep production out of reach

Give the agent only a dev or test profile:

- Point the MCP server's `CHARTWRIGHT_PROFILES` at a file that holds only those profiles. The server reads every profile in its file, and an unknown-profile error lists the names in it.
- With the skill, Claude runs the CLI from your shell; start that session with `CHARTWRIGHT_PROFILES` set to the same file, and keep production passwords out of its environment.
- Build on production yourself, or from CI, once you've reviewed the spec.

## What the MCP server leaves to the CLI

- **Restore:** there's no restore tool. Run `chartwright restore <backup.zip> --profile <name>`, using the `backup` path from the build report.
- **Kept UI heights:** run `chartwright absorb <spec> --profile <name>` before the agent's next build; it copies chart heights set in Superset into the spec.
- **Offline import ZIP:** run `chartwright compile` (see [DASHBOARDS-FROM-CODE.md](DASHBOARDS-FROM-CODE.md)).

## Problems reported with Superset's own MCP tools

| Superset 6.1.0's MCP service | Reported in | With Chartwright |
|---|---|---|
| `generate_dashboard` always creates a new dashboard, `add_chart_to_existing_dashboard` only appends charts, and no tool changes an existing dashboard's layout, filters or title, so each round of tweaks leaves another "My Dashboard (3)" | [#39864](https://github.com/apache/superset/discussions/39864) | `build_dashboard` updates the dashboard at the spec's slug, and its charts, in place |
| Agents reference columns the dataset doesn't have; `generate_chart` catches them one chart at a time, as it creates each chart | [#40920](https://github.com/apache/superset/discussions/40920) | Every reference in the whole spec is checked, and every missing one listed, before anything is written |
| `generate_dashboard` saves the dashboard unpublished with the agent's user as its only owner, and no tool changes that, so the person who asked may not see it | [#42001](https://github.com/apache/superset/discussions/42001) | Chartwright builds dashboards as published unless the spec sets `"published": false`, and Superset lists published dashboards for anyone with access to one of their datasets; `dashboard.owners` names the owners |
| With more than two `generate_chart` calls at once, a call can report failure for a chart it already saved, and the retry adds a duplicate (fixed on Superset's master branch after 6.1.0, not yet released) | [#42567](https://github.com/apache/superset/issues/42567) | Chart ids come from the slug and chart name, so a retried build updates the same charts |

Superset's master branch adds MCP tools such as `update_dashboard`, none of them in a release yet.

## Related

- [DASHBOARDS-FROM-CODE.md](DASHBOARDS-FROM-CODE.md): the spec's fields, JSON output and exit codes
- [LAYOUT-GUIDE.md](LAYOUT-GUIDE.md): drawing layouts with `sketch`
- [HISTORY-AND-ROLLBACK.md](HISTORY-AND-ROLLBACK.md): backups and `restore`
- [DESIGN-BRAIN.md](DESIGN-BRAIN.md): the rules behind the brief and the review
- [LIMITS.md](LIMITS.md): every limit, with what to do instead
- [CONTRACTS.md](CONTRACTS.md): Superset's behaviour, cited to source
