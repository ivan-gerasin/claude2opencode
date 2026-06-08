import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from convert_skills import parse_frontmatter, render_frontmatter
from convert_skills import is_valid_skill_name, slugify
from convert_skills import truncate_description, MAX_DESCRIPTION_LEN
from convert_skills import find_installed_plugin_paths
from convert_skills import Candidate, discover_candidates
from convert_skills import ConversionResult, convert_skill
from convert_skills import convert_command, convert_agent
from convert_skills import print_report
from convert_skills import main


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


if __name__ == '__main__':
    unittest.main()
