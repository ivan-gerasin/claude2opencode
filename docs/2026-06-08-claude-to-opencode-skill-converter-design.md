# Claude Code → OpenCode Skill Converter — Design

## Purpose

Convert Claude Code skills and plugin components (skills, commands, agents) into
`SKILL.md` files that OpenCode can discover and use, normalizing frontmatter to
satisfy OpenCode's stricter schema and consolidating them into a single
OpenCode-discoverable directory.

## Background

OpenCode skills use the *same* `SKILL.md` format as Claude Code (YAML frontmatter +
Markdown body) — this is not a markdown→yaml format change. The real conversion work is:

1. **Location**: the user's skills live scattered across
   `~/.claude/plugins/.../skills/...`, which OpenCode does not scan by default.
   They need to be gathered into `~/.config/opencode/skills/`.
2. **Frontmatter normalization**: OpenCode only recognizes `name`, `description`,
   `license`, `compatibility`, `metadata` and validates strictly:
   - `name` must match `^[a-z0-9]+(-[a-z0-9]+)*$` and equal the containing
     directory name
   - `description` must be 1–1024 characters
   - Claude skills also use `version`, `allowed-tools`, `tools`, `user-invocable`,
     `argument-hint` — fields OpenCode ignores. These are dropped.
3. **Plugin components beyond skills**: Claude plugins also bundle `commands/` and
   `agents/`, which have no OpenCode "skill" equivalent but contain useful
   instructions. These are wrapped into synthetic skills so their guidance remains
   available.

## Discovery sources

1. Standalone: `<claude-dir>/skills/*/SKILL.md`
2. Installed plugins only (read `<claude-dir>/plugins/installed_plugins.json` for
   each plugin's `installPath`, to avoid scanning the much larger marketplace
   catalog of uninstalled plugins):
   - `<installPath>/skills/*/SKILL.md`
   - `<installPath>/commands/*.md`
   - `<installPath>/agents/*.md`

   (Top-level conventional paths only — Claude plugins may declare custom component
   paths via `plugin.json`; this is a known limitation, noted in script output if
   relevant fields are present but unhandled.)

## Conversion rules

### A. Native skills (have `SKILL.md`)
- Parse frontmatter; keep only `name`, `description`, `license` (recognized by
  OpenCode); drop other Claude-only fields.
- Validate `name`:
  - missing or fails `^[a-z0-9]+(-[a-z0-9]+)*$` → error, skip, report reason
  - valid but differs from directory name → use the frontmatter `name` as
    canonical for the **output** directory; report as a notice
- Validate `description`:
  - missing/empty → error, skip
  - > 1024 chars → truncate at the last word boundary ≤ 1024 chars with a
    trailing ellipsis for the frontmatter, and append the full original text to
    the skill body under a "## Original description" section (nothing lost, just
    relocated); report as a warning
- Copy the entire skill directory (SKILL.md + supporting files like `references/`,
  `scripts/`, `examples/`) to the output location, then rewrite SKILL.md with the
  normalized frontmatter and original body (plus any preserved long description).

### B. Commands (`commands/<name>.md`)
- No `name` field in Claude commands — derive from filename: lowercase, replace
  `_`/spaces with `-`, strip `.md`.
- Output skill name: `cmd-<slug>` (e.g. `cmd-commit`, `cmd-clean-gone`) — prefixed
  to avoid collisions with native skill/agent names and to signal provenance.
- description: from frontmatter `description` (truncated per the same rule above
  if needed).
- body: a short provenance note ("Originally the Claude Code slash command
  `/commit` from plugin `commit-commands`...") followed by the original
  instructions verbatim.
- Single-file output (no supporting directory expected for commands).

### C. Agents (`agents/<name>.md`)
- Output skill name: `agent-<slug>`, where slug comes from the frontmatter `name`
  if present (sanitized) else the filename.
- description: from frontmatter `description` (truncate+preserve rule applies —
  some agent descriptions run to several KB with embedded examples).
- body: a short provenance note ("Originally the Claude Code subagent
  `code-simplifier`...") followed by the original system prompt verbatim.

### Output & overwrite behavior
- Each candidate becomes `<output-dir>/<computed-name>/SKILL.md` (+ copied
  supporting files for native skills).
- Existing directories of the same computed name are overwritten; this is reported
  as a warning so the user is aware (in case they hand-authored an OpenCode skill
  with a colliding name).

## CLI

```
convert_skills.py [--claude-dir DIR] [--output-dir DIR] [--dry-run]
```

- `--claude-dir` default `~/.claude`
- `--output-dir` default `~/.config/opencode/skills`
- `--dry-run` preview actions and report without writing anything

## Reporting

At the end, the script prints:
- Discovery summary (counts by category)
- Per-item conversion log (`[skill]`, `[command]`, `[agent]` with source → destination)
- Warnings (truncations, name/dir mismatches resolved, overwrites)
- Errors/skips with the specific reason (missing required field, invalid name
  pattern, etc.)
- A final tally line: `Done: N converted, M skipped, K warnings.`

## Implementation notes

- Pure Python 3 stdlib (no PyYAML/Node available in this environment). Frontmatter
  is simple enough (flat `key: value` pairs, occasional `description: |` block
  scalars) that a small hand-rolled parser is more robust than requiring an extra
  dependency.
- Output frontmatter is generated directly as text (only `name`, `description`,
  optionally `license` — all simple scalars), using JSON string escaping for the
  description (a valid subset of YAML double-quoted scalar syntax), avoiding the
  need for a YAML serializer.
