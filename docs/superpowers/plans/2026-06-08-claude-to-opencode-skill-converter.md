# Claude Code → OpenCode Skill Converter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `convert_skills.py`, a stdlib-only Python 3 script that discovers Claude Code skills, slash commands, and subagents from the user's installed plugins, converts/wraps them into OpenCode-compatible `SKILL.md` files, writes them to an OpenCode skills directory, and prints a report of what was converted, skipped, and any warnings.

**Architecture:** Single script `convert_skills.py` containing: a small hand-rolled YAML-frontmatter parser/renderer, name/description normalization helpers, a discovery layer that reads `installed_plugins.json` and walks each installed plugin's `skills/`, `commands/`, `agents/` directories, a conversion layer producing `ConversionResult` objects, and a CLI/report layer. `test_convert_skills.py` covers each piece with `unittest` + `tempfile`.

**Tech Stack:** Python 3 standard library only (`argparse`, `json`, `re`, `shutil`, `pathlib`, `dataclasses`, `unittest`, `tempfile`, `io`, `contextlib`). No PyYAML, no Node — neither is available in the target environment.

**Reference design doc:** `docs/2026-06-08-claude-to-opencode-skill-converter-design.md`

---

> Note: this directory is not a git repository (by the user's choice), so the
> usual "commit" steps from the plan template are omitted. Just verify each
> task's tests pass before moving to the next.

## Task 1: Frontmatter parsing

**Files:**
- Create: `convert_skills.py`
- Create: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Create `test_convert_skills.py`:

```python
import unittest

from convert_skills import parse_frontmatter


class ParseFrontmatterTests(unittest.TestCase):
    def test_plain_scalars(self):
        text = (
            "---\n"
            "name: my-skill\n"
            "description: Does a thing\n"
            "version: 1.0.0\n"
            "---\n"
            "\n"
            "# Body\n"
            "Hello\n"
        )
        frontmatter, body = parse_frontmatter(text)
        self.assertEqual(frontmatter, {
            'name': 'my-skill',
            'description': 'Does a thing',
            'version': '1.0.0',
        })
        self.assertEqual(body, "# Body\nHello\n")

    def test_block_scalar_description(self):
        text = (
            "---\n"
            "name: agent-x\n"
            "description: |\n"
            "  Line one of the description.\n"
            "  Line two with more detail.\n"
            "\n"
            "  A paragraph after a blank line.\n"
            "model: opus\n"
            "---\n"
            "Body text\n"
        )
        frontmatter, body = parse_frontmatter(text)
        self.assertEqual(frontmatter['name'], 'agent-x')
        self.assertEqual(
            frontmatter['description'],
            "Line one of the description.\nLine two with more detail.\n\n"
            "A paragraph after a blank line."
        )
        self.assertEqual(frontmatter['model'], 'opus')
        self.assertEqual(body, "Body text\n")

    def test_quoted_scalar(self):
        text = (
            '---\n'
            'name: my-skill\n'
            'description: "Has a colon: and \\"quotes\\""\n'
            '---\n'
            'Body\n'
        )
        frontmatter, body = parse_frontmatter(text)
        self.assertEqual(frontmatter['description'], 'Has a colon: and "quotes"')

    def test_missing_frontmatter_returns_whole_text_as_body(self):
        text = "# Just a heading\nNo frontmatter here.\n"
        frontmatter, body = parse_frontmatter(text)
        self.assertEqual(frontmatter, {})
        self.assertEqual(body, text)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: `ImportError: cannot import name 'parse_frontmatter' from 'convert_skills'` (module doesn't exist yet) — or `ModuleNotFoundError: No module named 'convert_skills'`.

- [ ] **Step 3: Write the implementation**

Create `convert_skills.py`:

```python
#!/usr/bin/env python3
"""Convert Claude Code skills, slash commands, and subagents into
OpenCode-compatible SKILL.md files."""

import re

FRONTMATTER_KEY = re.compile(r'^([A-Za-z_][A-Za-z0-9_-]*):\s?(.*)$')
_BLOCK_INDICATORS = ('|', '>', '|-', '|+', '>-', '>+', '')


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


def _unquote(value):
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all 4 tests in `ParseFrontmatterTests` PASS.

> **Standing convention for every later task:** always insert new imports and
> `TestCase` classes **above** the final `if __name__ == '__main__': unittest.main()`
> line. Classes defined after it are still discovered by `python3 -m unittest`
> (it imports the module without running that block), but `python3
> test_convert_skills.py` would call `unittest.main()` before later classes are
> defined and silently skip them. Keeping everything above that line avoids the
> trap entirely.

---

## Task 2: Frontmatter rendering

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py` (above the `if __name__ == '__main__':` line):

```python
from convert_skills import render_frontmatter


class RenderFrontmatterTests(unittest.TestCase):
    def test_basic(self):
        text = render_frontmatter('my-skill', 'Does a thing')
        self.assertEqual(text, '---\nname: my-skill\ndescription: "Does a thing"\n---\n')

    def test_with_license(self):
        text = render_frontmatter('my-skill', 'Does a thing', 'MIT')
        self.assertEqual(
            text,
            '---\nname: my-skill\ndescription: "Does a thing"\nlicense: "MIT"\n---\n')

    def test_escapes_special_characters(self):
        text = render_frontmatter('my-skill', 'Has "quotes" and: a colon')
        self.assertIn('description: "Has \\"quotes\\" and: a colon"', text)
```

(Also add `from convert_skills import render_frontmatter` near the top alongside the existing `parse_frontmatter` import — or combine into one `from convert_skills import (parse_frontmatter, render_frontmatter)` import line.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.RenderFrontmatterTests -v`
Expected: `ImportError: cannot import name 'render_frontmatter'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py` (the `import json` line goes with the other imports at the top):

```python
import json
```

```python
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
    return json.dumps(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (7 total so far).

---

## Task 3: Name validation and slugification

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py`:

```python
from convert_skills import is_valid_skill_name, slugify


class NameHelperTests(unittest.TestCase):
    def test_is_valid_skill_name(self):
        self.assertTrue(is_valid_skill_name('my-skill'))
        self.assertTrue(is_valid_skill_name('skill123'))
        self.assertFalse(is_valid_skill_name('My-Skill'))
        self.assertFalse(is_valid_skill_name('my_skill'))
        self.assertFalse(is_valid_skill_name('-leading-hyphen'))
        self.assertFalse(is_valid_skill_name(''))

    def test_slugify(self):
        self.assertEqual(slugify('clean_gone'), 'clean-gone')
        self.assertEqual(slugify('Code Simplifier'), 'code-simplifier')
        self.assertEqual(slugify('commit-push-pr'), 'commit-push-pr')
        self.assertEqual(slugify('  Multiple   Spaces--and--dashes  '),
                         'multiple-spaces-and-dashes')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.NameHelperTests -v`
Expected: `ImportError: cannot import name 'is_valid_skill_name'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py`:

```python
NAME_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (9 total so far).

---

## Task 4: Description truncation

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py`:

```python
from convert_skills import truncate_description, MAX_DESCRIPTION_LEN


class TruncateDescriptionTests(unittest.TestCase):
    def test_short_description_is_unchanged(self):
        text, truncated = truncate_description('Short description.')
        self.assertEqual(text, 'Short description.')
        self.assertFalse(truncated)

    def test_long_description_is_truncated_at_word_boundary(self):
        long_text = 'word ' * 300  # 1500 characters
        text, truncated = truncate_description(long_text)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), MAX_DESCRIPTION_LEN)
        self.assertTrue(text.endswith('...'))
        self.assertFalse(text[:-3].endswith(' '))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.TruncateDescriptionTests -v`
Expected: `ImportError: cannot import name 'truncate_description'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (11 total so far).

---

## Task 5: Discovery — locating installed plugins

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py` (add `import json`, `import tempfile`, `from pathlib import Path` to the imports at the top of the file alongside `import unittest`):

```python
import json
import tempfile
from pathlib import Path

from convert_skills import find_installed_plugin_paths


class FindInstalledPluginPathsTests(unittest.TestCase):
    def test_returns_existing_install_paths_with_short_plugin_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp)
            plugins_dir = claude_dir / 'plugins'
            plugins_dir.mkdir()

            install_a = claude_dir / 'plugin-a-install'
            install_a.mkdir()

            manifest = {
                "version": 2,
                "plugins": {
                    "plugin-a@claude-plugins-official": [
                        {"scope": "user", "installPath": str(install_a)}
                    ],
                    "plugin-missing@claude-plugins-official": [
                        {"scope": "user", "installPath": str(claude_dir / 'does-not-exist')}
                    ],
                },
            }
            (plugins_dir / 'installed_plugins.json').write_text(json.dumps(manifest))

            result = find_installed_plugin_paths(claude_dir)

            self.assertEqual(result, [('plugin-a', install_a)])

    def test_missing_manifest_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_installed_plugin_paths(Path(tmp)), [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.FindInstalledPluginPathsTests -v`
Expected: `ImportError: cannot import name 'find_installed_plugin_paths'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py` (add `import json` and `from pathlib import Path` to its imports — `json` is already imported from Task 2; add `from pathlib import Path`):

```python
from pathlib import Path
```

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (13 total so far).

---

## Task 6: Discovery — enumerating skill/command/agent candidates

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing test**

Add to `test_convert_skills.py`:

```python
from convert_skills import Candidate, discover_candidates


class DiscoverCandidatesTests(unittest.TestCase):
    def test_finds_standalone_and_plugin_bundled_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp)

            standalone_dir = claude_dir / 'skills' / 'standalone-skill'
            standalone_dir.mkdir(parents=True)
            (standalone_dir / 'SKILL.md').write_text(
                '---\nname: standalone-skill\ndescription: x\n---\nBody\n')

            plugin_root = claude_dir / 'my-plugin'
            (plugin_root / 'skills' / 'plugin-skill').mkdir(parents=True)
            (plugin_root / 'skills' / 'plugin-skill' / 'SKILL.md').write_text(
                '---\nname: plugin-skill\ndescription: x\n---\nBody\n')
            (plugin_root / 'commands').mkdir(parents=True)
            (plugin_root / 'commands' / 'do-thing.md').write_text(
                '---\ndescription: Do a thing\n---\nInstructions\n')
            (plugin_root / 'agents').mkdir(parents=True)
            (plugin_root / 'agents' / 'helper.md').write_text(
                '---\nname: helper\ndescription: Helps\n---\nPrompt\n')

            plugins_dir = claude_dir / 'plugins'
            plugins_dir.mkdir()
            manifest = {
                "plugins": {
                    "my-plugin@marketplace": [
                        {"scope": "user", "installPath": str(plugin_root)}
                    ]
                }
            }
            (plugins_dir / 'installed_plugins.json').write_text(json.dumps(manifest))

            candidates = discover_candidates(claude_dir)

            simplified = sorted((c.kind, c.display_name, c.plugin_name) for c in candidates)
            self.assertEqual(simplified, [
                ('agent', 'helper', 'my-plugin'),
                ('command', 'do-thing', 'my-plugin'),
                ('skill', 'plugin-skill', 'my-plugin'),
                ('skill', 'standalone-skill', ''),
            ])
            for c in candidates:
                self.assertTrue(c.source_path.is_file())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.DiscoverCandidatesTests -v`
Expected: `ImportError: cannot import name 'Candidate'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py` (add `from dataclasses import dataclass, field` to its imports):

```python
from dataclasses import dataclass, field
```

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (14 total so far).

---

## Task 7: Converting native skills

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py` (add `import shutil` to the imports):

```python
import shutil

from convert_skills import ConversionResult, convert_skill


class ConvertSkillTests(unittest.TestCase):
    def _make_skill_candidate(self, root, dirname, frontmatter_lines,
                              body="# Body\n\nSome instructions.\n", extra_files=None):
        skill_dir = root / 'source' / dirname
        skill_dir.mkdir(parents=True)
        text = '---\n' + '\n'.join(frontmatter_lines) + '\n---\n\n' + body
        (skill_dir / 'SKILL.md').write_text(text)
        for relpath, content in (extra_files or {}).items():
            path = skill_dir / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return Candidate('skill', skill_dir / 'SKILL.md', 'my-plugin', dirname)

    def test_normalizes_frontmatter_and_copies_supporting_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._make_skill_candidate(
                root, 'my-skill',
                ['name: my-skill', 'description: Does a thing', 'version: 1.0.0',
                 'allowed-tools: Read, Write'],
                extra_files={'references/notes.md': 'Reference notes'})
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_skill(candidate, output_dir, dry_run=False)

            self.assertEqual(result.status, 'converted')
            self.assertEqual(result.output_name, 'my-skill')
            dest = output_dir / 'my-skill'
            self.assertTrue((dest / 'references' / 'notes.md').is_file())

            frontmatter, body = parse_frontmatter((dest / 'SKILL.md').read_text())
            self.assertEqual(frontmatter, {'name': 'my-skill', 'description': 'Does a thing'})
            self.assertIn('Some instructions.', body)

    def test_resolves_name_directory_mismatch_using_frontmatter_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._make_skill_candidate(
                root, 'writing-rules',
                ['name: writing-hookify-rules', 'description: How to write rules'])
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_skill(candidate, output_dir, dry_run=False)

            self.assertEqual(result.output_name, 'writing-hookify-rules')
            self.assertTrue((output_dir / 'writing-hookify-rules' / 'SKILL.md').is_file())
            self.assertFalse((output_dir / 'writing-rules').exists())
            self.assertTrue(any('differs from directory' in note for note in result.notes))

    def test_skips_when_description_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._make_skill_candidate(root, 'no-desc', ['name: no-desc'])
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_skill(candidate, output_dir, dry_run=False)

            self.assertEqual(result.status, 'skipped')
            self.assertIn('description', result.error)
            self.assertFalse((output_dir / 'no-desc').exists())

    def test_truncates_long_description_and_preserves_full_text_in_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            long_description = ('Detail. ' * 200).strip()  # ~1599 chars
            candidate = self._make_skill_candidate(
                root, 'verbose-skill',
                ['name: verbose-skill', f'description: {long_description}'])
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_skill(candidate, output_dir, dry_run=False)

            self.assertEqual(result.status, 'converted')
            self.assertTrue(any('truncated' in note for note in result.notes))
            frontmatter, body = parse_frontmatter((output_dir / 'verbose-skill' / 'SKILL.md').read_text())
            self.assertLessEqual(len(frontmatter['description']), 1024)
            self.assertIn('## Original description', body)
            self.assertIn(long_description, body)

    def test_dry_run_reports_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._make_skill_candidate(
                root, 'my-skill', ['name: my-skill', 'description: Does a thing'])
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_skill(candidate, output_dir, dry_run=True)

            self.assertEqual(result.status, 'converted')
            self.assertFalse((output_dir / 'my-skill').exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.ConvertSkillTests -v`
Expected: `ImportError: cannot import name 'ConversionResult'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py` (add `import shutil` to its imports):

```python
import shutil
```

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (19 total so far).

---

## Task 8: Wrapping commands and agents as skills

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py`:

```python
from convert_skills import convert_command, convert_agent


class ConvertWrappedTests(unittest.TestCase):
    def _write_md(self, root, relpath, frontmatter_lines, body):
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        text = '---\n' + '\n'.join(frontmatter_lines) + '\n---\n\n' + body
        path.write_text(text)
        return path

    def test_convert_command_wraps_into_prefixed_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_path = self._write_md(
                root, 'source/commands/clean_gone.md',
                ['description: Cleans up gone branches', 'argument-hint: none'],
                'Run git fetch --prune, then delete [gone] branches.\n')
            candidate = Candidate('command', cmd_path, 'commit-commands', 'clean_gone')
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_command(candidate, output_dir, dry_run=False)

            self.assertEqual(result.status, 'converted')
            self.assertEqual(result.output_name, 'cmd-clean-gone')
            frontmatter, body = parse_frontmatter(
                (output_dir / 'cmd-clean-gone' / 'SKILL.md').read_text())
            self.assertEqual(frontmatter['name'], 'cmd-clean-gone')
            self.assertEqual(frontmatter['description'], 'Cleans up gone branches')
            self.assertIn('/clean_gone', body)
            self.assertIn('commit-commands', body)
            self.assertIn('Run git fetch --prune', body)

    def test_convert_command_synthesizes_description_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_path = self._write_md(root, 'source/commands/commit.md', [],
                                      'Stage and commit the current changes.\n')
            candidate = Candidate('command', cmd_path, 'commit-commands', 'commit')
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_command(candidate, output_dir, dry_run=False)

            frontmatter, _ = parse_frontmatter((output_dir / 'cmd-commit' / 'SKILL.md').read_text())
            self.assertIn('/commit', frontmatter['description'])

    def test_convert_agent_uses_frontmatter_name_for_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_path = self._write_md(
                root, 'source/agents/code-simplifier.md',
                ['name: code-simplifier', 'description: Simplifies code', 'model: opus'],
                'You are an expert code simplification specialist.\n')
            candidate = Candidate('agent', agent_path, 'code-simplifier', 'code-simplifier')
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_agent(candidate, output_dir, dry_run=False)

            self.assertEqual(result.output_name, 'agent-code-simplifier')
            frontmatter, body = parse_frontmatter(
                (output_dir / 'agent-code-simplifier' / 'SKILL.md').read_text())
            self.assertEqual(frontmatter['name'], 'agent-code-simplifier')
            self.assertEqual(frontmatter['description'], 'Simplifies code')
            self.assertIn('subagent `code-simplifier`', body)
            self.assertIn('expert code simplification specialist', body)

    def test_convert_agent_truncates_long_description_and_preserves_full_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            long_description = ('Use this agent for X. ' * 100).strip()  # ~2299 chars
            agent_path = self._write_md(
                root, 'source/agents/verbose.md',
                ['description: |', '  ' + long_description],
                'System prompt body.\n')
            candidate = Candidate('agent', agent_path, 'plugin-x', 'verbose')
            output_dir = root / 'output'
            output_dir.mkdir()

            result = convert_agent(candidate, output_dir, dry_run=False)

            self.assertEqual(result.output_name, 'agent-verbose')
            self.assertTrue(any('truncated' in note for note in result.notes))
            frontmatter, body = parse_frontmatter(
                (output_dir / 'agent-verbose' / 'SKILL.md').read_text())
            self.assertLessEqual(len(frontmatter['description']), 1024)
            self.assertIn('## Original description', body)
            self.assertIn(long_description, body)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.ConvertWrappedTests -v`
Expected: `ImportError: cannot import name 'convert_command'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py`:

```python
def _build_wrapped_skill(output_dir, dry_run, candidate, frontmatter, body, *,
                         prefix, name, kind_label, note):
    """Shared logic for wrapping a Claude Code command or subagent (which
    have no SKILL.md of their own) into a synthetic OpenCode skill named
    `<prefix>-<slug(name)>`. `note` is a short Markdown blockquote explaining
    the conversion, prepended to the original body."""
    output_name = f'{prefix}-{slugify(name)}'

    description = frontmatter.get('description', '').strip()
    if not description:
        description = f"Guidance from the Claude Code {kind_label} {name}."

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
                                kind_label='slash command', note=note)


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (23 total so far).

---

## Task 9: Reporting and CLI

**Files:**
- Modify: `convert_skills.py`
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing test**

Add to `test_convert_skills.py` (add `import contextlib` and `import io` to the imports):

```python
import contextlib
import io

from convert_skills import print_report


class PrintReportTests(unittest.TestCase):
    def test_summarizes_converted_warned_and_skipped_results(self):
        skill_candidate = Candidate('skill', Path('/src/skills/a/SKILL.md'), 'plugin-a', 'a')
        cmd_candidate = Candidate('command', Path('/src/commands/b.md'), 'plugin-a', 'b')
        bad_candidate = Candidate('skill', Path('/src/skills/c/SKILL.md'), 'plugin-a', 'c')

        results = [
            ConversionResult(skill_candidate, output_name='a', output_path=Path('/out/a'),
                             status='converted',
                             notes=["overwriting existing skill directory '/out/a'"]),
            ConversionResult(cmd_candidate, output_name='cmd-b', output_path=Path('/out/cmd-b'),
                             status='converted'),
            ConversionResult(bad_candidate, status='skipped',
                             error="missing required 'description' field in frontmatter"),
        ]

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_report(results, Path('/out'), dry_run=False)
        output = buf.getvalue()

        self.assertIn('Discovered: 2 skills, 1 commands, 0 agents (3 candidates)', output)
        self.assertIn('Converted (2):', output)
        self.assertIn('[skill  ] a', output)
        self.assertIn('[command] b', output)
        self.assertIn('Warnings (1):', output)
        self.assertIn("overwriting existing skill directory '/out/a'", output)
        self.assertIn('Errors / skipped (1):', output)
        self.assertIn("missing required 'description' field in frontmatter", output)
        self.assertIn('Done: 2 converted, 1 skipped, 1 warnings.', output)
        self.assertIn('Output directory: /out', output)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.PrintReportTests -v`
Expected: `ImportError: cannot import name 'print_report'`

- [ ] **Step 3: Write the implementation**

Add to `convert_skills.py` (add `import argparse`, `import sys` to its imports):

```python
import argparse
import sys
```

```python
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
```

Note: move the existing `if __name__ == '__main__': unittest.main()` block in
`test_convert_skills.py` so it remains the very last thing in that file (it
currently is — just make sure the new imports/classes above are inserted
*before* it, not after).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (24 total so far).

---

## Task 10: End-to-end integration tests

**Files:**
- Modify: `test_convert_skills.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_convert_skills.py`:

```python
from convert_skills import main


class EndToEndTests(unittest.TestCase):
    def _build_claude_dir(self, root):
        claude_dir = root / 'claude-home'

        good = claude_dir / 'skills' / 'good-skill'
        good.mkdir(parents=True)
        (good / 'SKILL.md').write_text(
            '---\nname: good-skill\ndescription: A good skill to use\nversion: 2.0.0\n'
            '---\n\nDo good things.\n')

        broken = claude_dir / 'skills' / 'broken-skill'
        broken.mkdir(parents=True)
        (broken / 'SKILL.md').write_text(
            '---\nname: broken-skill\n---\n\nNo description here.\n')

        plugin_root = root / 'installs' / 'demo-plugin'
        (plugin_root / 'commands').mkdir(parents=True)
        (plugin_root / 'commands' / 'ship-it.md').write_text(
            '---\ndescription: Ships the current branch\n---\n\n'
            'Run the release checklist.\n')
        (plugin_root / 'agents').mkdir(parents=True)
        (plugin_root / 'agents' / 'reviewer.md').write_text(
            '---\nname: reviewer\ndescription: Reviews pull requests\n---\n\n'
            'You review code carefully.\n')

        plugins_dir = claude_dir / 'plugins'
        plugins_dir.mkdir()
        manifest = {
            "plugins": {
                "demo-plugin@marketplace": [
                    {"scope": "user", "installPath": str(plugin_root)}
                ]
            }
        }
        (plugins_dir / 'installed_plugins.json').write_text(json.dumps(manifest))
        return claude_dir

    def test_dry_run_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = self._build_claude_dir(root)
            output_dir = root / 'opencode-skills'

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = main(['--claude-dir', str(claude_dir),
                                  '--output-dir', str(output_dir), '--dry-run'])
            output = buf.getvalue()

            self.assertEqual(exit_code, 0)
            self.assertFalse(output_dir.exists())
            self.assertIn('Would convert (3):', output)
            self.assertIn('Errors / skipped (1):', output)
            self.assertIn("missing required 'description' field", output)

    def test_full_run_writes_normalized_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = self._build_claude_dir(root)
            output_dir = root / 'opencode-skills'

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = main(['--claude-dir', str(claude_dir),
                                  '--output-dir', str(output_dir)])
            output = buf.getvalue()

            self.assertEqual(exit_code, 0)
            self.assertIn('Done: 3 converted, 1 skipped, 0 warnings.', output)

            good_fm, _ = parse_frontmatter((output_dir / 'good-skill' / 'SKILL.md').read_text())
            self.assertEqual(good_fm, {'name': 'good-skill', 'description': 'A good skill to use'})

            cmd_fm, _ = parse_frontmatter((output_dir / 'cmd-ship-it' / 'SKILL.md').read_text())
            self.assertEqual(cmd_fm['name'], 'cmd-ship-it')
            self.assertEqual(cmd_fm['description'], 'Ships the current branch')

            agent_fm, _ = parse_frontmatter((output_dir / 'agent-reviewer' / 'SKILL.md').read_text())
            self.assertEqual(agent_fm['name'], 'agent-reviewer')

            self.assertFalse((output_dir / 'broken-skill').exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills.EndToEndTests -v`
Expected: `ImportError: cannot import name 'main'` (it exists by now from Task 9 — if this passes immediately instead of failing on import, check that the assertions fail instead, e.g. on the `Done: 3 converted...` line; either failure mode confirms the test is exercising real code, not a stub).

- [ ] **Step 3: No new implementation needed**

`main`, `convert_all`, and `print_report` were implemented in Task 9; this task only adds coverage that exercises the full pipeline together. If a step-2 assertion failed for a reason other than "not implemented yet" (e.g. a wrong path or count), fix the test or the implementation now.

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `cd /home/igerasin/projects/claude2opencode && python3 -m unittest test_convert_skills -v`
Expected: all tests PASS (26 total).

---

## Task 11: Smoke test against the real Claude Code config, then run for real

**Files:** none (this exercises the finished script against real data)

- [ ] **Step 1: Dry run against the real `~/.claude` directory**

Run: `cd /home/igerasin/projects/claude2opencode && python3 convert_skills.py --dry-run`

Expected: a report ending in a line like `Done: 28 would be converted, 0 skipped, 0 warnings.`
(or similar — exact counts depend on the user's currently-installed plugins) and
`Output directory: /home/igerasin/.config/opencode/skills`. Read through the
"Would convert" list and any warnings/errors sections to confirm they look sane —
e.g. entries like `[skill  ] brainstorming -> /home/igerasin/.config/opencode/skills/brainstorming`,
`[command] commit -> .../cmd-commit`, `[agent] code-simplifier -> .../agent-code-simplifier`.

- [ ] **Step 2: Show the dry-run report to the user and ask for confirmation before writing real files**

This step writes into the user's home directory (`~/.config/opencode/skills/`),
which is outside the project directory and affects how OpenCode behaves — pause
here and confirm with the user that the dry-run output looks right before
proceeding to Step 3.

- [ ] **Step 3: Run for real**

Run: `cd /home/igerasin/projects/claude2opencode && python3 convert_skills.py`

Expected: the same report as the dry run but with "Converted" instead of "Would
convert" and "Done: N converted, ..." Verify the files landed:

Run: `ls ~/.config/opencode/skills/ | head -30 && find ~/.config/opencode/skills -maxdepth 2 -name SKILL.md | wc -l`

Expected: a directory per converted skill (e.g. `brainstorming/`, `cmd-commit/`,
`agent-code-simplifier/`), and the `SKILL.md` count matches the "Done: N converted"
number from the report.

- [ ] **Step 4: Spot-check one of each kind for correct frontmatter**

Run: `head -5 ~/.config/opencode/skills/brainstorming/SKILL.md ~/.config/opencode/skills/cmd-commit/SKILL.md ~/.config/opencode/skills/agent-code-simplifier/SKILL.md`

Expected: each file starts with `---`, a `name:` line matching its directory
name, and a `description:` line — with no `version:`, `allowed-tools:`, `model:`,
or other Claude-only fields present.
