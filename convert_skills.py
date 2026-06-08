#!/usr/bin/env python3
"""Convert Claude Code skills, slash commands, and subagents into
OpenCode-compatible SKILL.md files."""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER_KEY = re.compile(r'^([A-Za-z_][A-Za-z0-9_-]*):\s?(.*)$')
_BLOCK_INDICATORS = ('|', '>', '|-', '|+', '>-', '>+', '')
NAME_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
MAX_DESCRIPTION_LEN = 1024


def truncate_description(description, max_len=MAX_DESCRIPTION_LEN):
    """Return ``(frontmatter_description, was_truncated)``.

    If `description` already fits within `max_len`, returns it stripped and
    unchanged. Otherwise cuts it at the last whitespace boundary that keeps
    the result (plus a trailing ellipsis) within `max_len`, so callers can
    preserve the untruncated text elsewhere (e.g. in the skill body)."""
    description = description.strip()
    if len(description) <= max_len:
        return description, False
    ellipsis = '...'
    limit = max_len - len(ellipsis)
    truncated = description[:limit]
    cut = truncated.rfind(' ')
    if cut > 0:
        truncated = truncated[:cut]
    return truncated.rstrip() + ellipsis, True


def is_valid_skill_name(name):
    """True if `name` satisfies OpenCode's skill-name rule:
    lowercase alphanumeric segments joined by single hyphens."""
    return bool(name) and bool(NAME_PATTERN.match(name))


def slugify(text):
    """Convert arbitrary text into a string matching NAME_PATTERN by
    lowercasing and collapsing every run of non-alphanumeric characters
    into a single hyphen, trimming leading/trailing hyphens."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')


def parse_frontmatter(text):
    """Parse a Markdown file's leading YAML frontmatter.

    Returns ``(frontmatter, body)`` where ``frontmatter`` is a dict mapping
    keys to string scalar values (plain, quoted, or block ``|``/``>`` style)
    and ``body`` is everything after the closing ``---`` line. Non-scalar
    values (lists, nested maps) are returned as their raw joined text — the
    callers here only ever read scalar fields (name, description, license).

    If the text has no frontmatter, returns ``({}, text)``.
    """
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip('\n')
    body = text[end + 4:].lstrip('\n')
    return _parse_scalar_mapping(block.split('\n') if block else []), body


def _parse_scalar_mapping(lines):
    result = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        match = FRONTMATTER_KEY.match(line)
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2).strip()
        if rest in _BLOCK_INDICATORS:
            value, i = _read_block(lines, i + 1, folded=rest.startswith('>'))
            result[key] = value
            continue
        result[key] = _unquote(rest)
        i += 1
    return result


def _read_block(lines, start, folded):
    block_lines = []
    base_indent = None
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            block_lines.append('')
            i += 1
            continue
        indent = len(line) - len(line.lstrip(' '))
        if base_indent is None:
            if indent == 0:
                break
            base_indent = indent
        if indent < base_indent:
            break
        block_lines.append(line[base_indent:])
        i += 1
    while block_lines and block_lines[-1] == '':
        block_lines.pop()
    if folded:
        value = ' '.join(l for l in block_lines if l.strip())
    else:
        value = '\n'.join(block_lines)
    return value, i


def render_frontmatter(name, description, license=None):
    """Render OpenCode-compatible YAML frontmatter (name, description,
    optional license) as a `---`-delimited block ending in a newline."""
    lines = ['---', f'name: {name}', f'description: {_yaml_string(description)}']
    if license:
        lines.append(f'license: {_yaml_string(license)}')
    lines.append('---')
    return '\n'.join(lines) + '\n'


def _yaml_string(value):
    """Render ``value`` as a YAML double-quoted scalar. JSON string escaping
    is a valid subset of YAML double-quoted scalar escaping, so json.dumps
    gives us a correct, dependency-free serializer."""
    return json.dumps(value, ensure_ascii=False)


def _unquote(value):
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def find_installed_plugin_paths(claude_dir):
    """Read <claude_dir>/plugins/installed_plugins.json and return a list of
    ``(plugin_name, install_path)`` for every installed plugin whose
    installPath exists on disk. `plugin_name` is the short name without the
    `@marketplace` suffix. Returns [] if the manifest is absent or unreadable."""
    manifest_path = claude_dir / 'plugins' / 'installed_plugins.json'
    if not manifest_path.is_file():
        return []
    try:
        with open(manifest_path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    result = []
    for full_name, entries in data.get('plugins', {}).items():
        plugin_name = full_name.split('@', 1)[0]
        for entry in entries:
            install_path = entry.get('installPath')
            if install_path and Path(install_path).is_dir():
                result.append((plugin_name, Path(install_path)))
    return result


@dataclass
class Candidate:
    """A thing discovered in Claude Code that can become an OpenCode skill."""
    kind: str          # 'skill' | 'command' | 'agent'
    source_path: Path  # path to SKILL.md (skills) or the .md file (commands/agents)
    plugin_name: str   # owning plugin's short name, or '' for standalone skills
    display_name: str  # human-friendly identifier used in reports


def discover_candidates(claude_dir):
    """Find every skill, command, and agent worth converting: standalone
    skills under <claude_dir>/skills/, plus skills/commands/agents bundled in
    each currently-installed plugin (top-level conventional paths only)."""
    candidates = []

    standalone_dir = claude_dir / 'skills'
    if standalone_dir.is_dir():
        for skill_dir in sorted(p for p in standalone_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / 'SKILL.md'
            if skill_md.is_file():
                candidates.append(Candidate('skill', skill_md, '', skill_dir.name))

    for plugin_name, install_path in find_installed_plugin_paths(claude_dir):
        skills_dir = install_path / 'skills'
        if skills_dir.is_dir():
            for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
                skill_md = skill_dir / 'SKILL.md'
                if skill_md.is_file():
                    candidates.append(Candidate('skill', skill_md, plugin_name, skill_dir.name))

        commands_dir = install_path / 'commands'
        if commands_dir.is_dir():
            for cmd_file in sorted(commands_dir.glob('*.md')):
                candidates.append(Candidate('command', cmd_file, plugin_name, cmd_file.stem))

        agents_dir = install_path / 'agents'
        if agents_dir.is_dir():
            for agent_file in sorted(agents_dir.glob('*.md')):
                candidates.append(Candidate('agent', agent_file, plugin_name, agent_file.stem))

    return candidates


@dataclass
class ConversionResult:
    """Outcome of converting one Candidate into an OpenCode skill."""
    candidate: Candidate
    output_name: str = ''
    output_path: Path = None
    status: str = 'converted'              # 'converted' | 'skipped'
    notes: list = field(default_factory=list)   # informational/warning strings
    error: str = ''


def convert_skill(candidate, output_dir, dry_run):
    """Convert a native Claude Code skill (one with a SKILL.md) into an
    OpenCode skill: validate+normalize its frontmatter, copy its directory
    (including supporting files) to <output_dir>/<name>/, and rewrite
    SKILL.md with only the OpenCode-recognized fields."""
    skill_dir = candidate.source_path.parent
    text = candidate.source_path.read_text(encoding='utf-8')
    frontmatter, body = parse_frontmatter(text)

    name = frontmatter.get('name', '').strip()
    if not name:
        return ConversionResult(candidate, status='skipped',
                                 error="missing required 'name' field in frontmatter")
    if not is_valid_skill_name(name):
        return ConversionResult(
            candidate, status='skipped',
            error=f"name '{name}' does not match the required pattern "
                  f"^[a-z0-9]+(-[a-z0-9]+)*$")

    description = frontmatter.get('description', '').strip()
    if not description:
        return ConversionResult(candidate, status='skipped',
                                 error="missing required 'description' field in frontmatter")

    result = ConversionResult(candidate, output_name=name)
    if name != skill_dir.name:
        result.notes.append(
            f"frontmatter name '{name}' differs from directory '{skill_dir.name}'; "
            f"using '{name}' for the output directory")

    fm_description, truncated = truncate_description(description)
    if truncated:
        result.notes.append(
            f"description truncated from {len(description)} to {len(fm_description)} "
            f"chars for frontmatter; full text preserved in skill body")
        body = body.rstrip('\n') + '\n\n## Original description\n\n' + description + '\n'

    license_value = frontmatter.get('license', '').strip() or None

    dest_dir = output_dir / name
    result.output_path = dest_dir
    if dest_dir.exists():
        result.notes.append(f"overwriting existing skill directory '{dest_dir}'")

    if not dry_run:
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.copytree(skill_dir, dest_dir)
        new_text = render_frontmatter(name, fm_description, license_value) + '\n' + body
        (dest_dir / 'SKILL.md').write_text(new_text, encoding='utf-8')

    return result


def _build_wrapped_skill(output_dir, dry_run, candidate, frontmatter, body, *,
                         prefix, name, kind_label, note, display_label=None):
    """Shared logic for wrapping a Claude Code command or subagent (which
    have no SKILL.md of their own) into a synthetic OpenCode skill named
    `<prefix>-<slug(name)>`. `note` is a short Markdown blockquote explaining
    the conversion, prepended to the original body. `display_label` (defaults
    to `name`) is how the thing is referred to in the synthesized description,
    e.g. `/clean_gone` for slash commands."""
    output_name = f'{prefix}-{slugify(name)}'
    if display_label is None:
        display_label = name

    description = frontmatter.get('description', '').strip()
    if not description:
        description = f"Guidance from the Claude Code {kind_label} {display_label}."

    result = ConversionResult(candidate, output_name=output_name)
    fm_description, truncated = truncate_description(description)

    new_body = note + '\n' + body.strip() + '\n'
    if truncated:
        result.notes.append(
            f"description truncated from {len(description)} to {len(fm_description)} "
            f"chars for frontmatter; full text preserved in skill body")
        new_body += '\n## Original description\n\n' + description + '\n'

    dest_dir = output_dir / output_name
    result.output_path = dest_dir
    if dest_dir.exists():
        result.notes.append(f"overwriting existing skill directory '{dest_dir}'")

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        new_text = render_frontmatter(output_name, fm_description) + '\n' + new_body
        (dest_dir / 'SKILL.md').write_text(new_text, encoding='utf-8')

    return result


def convert_command(candidate, output_dir, dry_run):
    """Wrap a Claude Code slash command (commands/<name>.md, no name field —
    the name comes from the filename) into an OpenCode skill `cmd-<slug>`."""
    text = candidate.source_path.read_text(encoding='utf-8')
    frontmatter, body = parse_frontmatter(text)
    cmd_name = candidate.display_name
    note = (f"> Converted from the Claude Code slash command `/{cmd_name}` "
            f"(plugin `{candidate.plugin_name or 'unknown'}`). Use this guidance "
            f"whenever the user's request matches what `/{cmd_name}` used to do.")
    return _build_wrapped_skill(output_dir, dry_run, candidate, frontmatter, body,
                                prefix='cmd', name=cmd_name,
                                kind_label='slash command', note=note,
                                display_label=f'/{cmd_name}')


def convert_agent(candidate, output_dir, dry_run):
    """Wrap a Claude Code subagent (agents/<name>.md) into an OpenCode skill
    `agent-<slug>`, preferring the agent's frontmatter `name` over its filename."""
    text = candidate.source_path.read_text(encoding='utf-8')
    frontmatter, body = parse_frontmatter(text)
    agent_name = frontmatter.get('name', '').strip() or candidate.display_name
    note = (f"> Converted from the Claude Code subagent `{agent_name}` "
            f"(plugin `{candidate.plugin_name or 'unknown'}`). This was the "
            f"agent's full system prompt; use it as guidance for the same kind "
            f"of task.")
    return _build_wrapped_skill(output_dir, dry_run, candidate, frontmatter, body,
                                prefix='agent', name=agent_name,
                                kind_label='subagent', note=note)


def convert_all(claude_dir, output_dir, dry_run):
    """Discover every candidate under `claude_dir` and convert each one,
    dispatching by kind. Returns the list of ConversionResults in discovery order."""
    results = []
    for candidate in discover_candidates(claude_dir):
        if candidate.kind == 'skill':
            results.append(convert_skill(candidate, output_dir, dry_run))
        elif candidate.kind == 'command':
            results.append(convert_command(candidate, output_dir, dry_run))
        elif candidate.kind == 'agent':
            results.append(convert_agent(candidate, output_dir, dry_run))
    return results


def print_report(results, output_dir, dry_run):
    """Print a summary: discovery counts, per-item conversions, warnings
    (collected from every result's `notes`), errors/skips, and a final tally."""
    counts = {'skill': 0, 'command': 0, 'agent': 0}
    for r in results:
        counts[r.candidate.kind] += 1
    print(f"Discovered: {counts['skill']} skills, {counts['command']} commands, "
          f"{counts['agent']} agents ({len(results)} candidates)")
    print()

    converted = [r for r in results if r.status == 'converted']
    skipped = [r for r in results if r.status == 'skipped']
    warnings = [(r, note) for r in results for note in r.notes]

    label = 'Would convert' if dry_run else 'Converted'
    print(f"{label} ({len(converted)}):")
    for r in converted:
        print(f"  [{r.candidate.kind:7}] {r.candidate.display_name:28} -> {r.output_path}")
    print()

    if warnings:
        print(f"Warnings ({len(warnings)}):")
        for r, note in warnings:
            print(f"  - [{r.candidate.kind}] {r.candidate.display_name}: {note}")
        print()

    if skipped:
        print(f"Errors / skipped ({len(skipped)}):")
        for r in skipped:
            print(f"  - [{r.candidate.kind}] {r.candidate.display_name}: {r.error}")
        print()

    verb = 'would be converted' if dry_run else 'converted'
    print(f"Done: {len(converted)} {verb}, {len(skipped)} skipped, {len(warnings)} warnings.")
    print(f"Output directory: {output_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert Claude Code skills, commands, and agents into "
                    "OpenCode SKILL.md files.")
    parser.add_argument('--claude-dir', type=Path, default=Path.home() / '.claude',
                        help='Claude Code config directory (default: ~/.claude)')
    parser.add_argument('--output-dir', type=Path,
                        default=Path.home() / '.config' / 'opencode' / 'skills',
                        help='OpenCode skills directory to write to '
                             '(default: ~/.config/opencode/skills)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview the conversion without writing any files')
    args = parser.parse_args(argv)

    claude_dir = args.claude_dir.expanduser()
    output_dir = args.output_dir.expanduser()

    if not claude_dir.is_dir():
        print(f"error: Claude config directory not found: {claude_dir}", file=sys.stderr)
        return 1

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    results = convert_all(claude_dir, output_dir, args.dry_run)
    print_report(results, output_dir, args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
