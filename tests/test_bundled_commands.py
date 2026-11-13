"""Every tool command bundled-mods.toml gives the launcher (build, after_add, after_on) must parse with the launcher's
--library at the end: modman_gui's argv puts it after the subcommand. (castle_build.py once took --library only
before 'build', so Castle date always failed with "unrecognized arguments".) Only the arguments are parsed here:
no tool runs."""
import argparse
import contextlib
import importlib
import io
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
BUNDLED = TOOLS / "packaging" / "bundled-mods.toml"


class _Parsed(BaseException):
    """Raised right after a tool's parse_args worked, so the tool never runs (BaseException: no except Exception
    in a tool's main catches it)."""


def parse_problem(tool: str, args: list[str]) -> str:
    """'' when tools/<tool>'s main parses args, else what argparse said."""
    mod = importlib.import_module(Path(tool).stem)
    real = argparse.ArgumentParser.parse_args

    def parse_then_stop(self, a=None, ns=None):
        real(self, a, ns)
        raise _Parsed

    err = io.StringIO()
    with mock.patch.object(argparse.ArgumentParser, "parse_args", parse_then_stop), \
            contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        try:
            mod.main(args)
        except _Parsed:
            return ""
        except SystemExit as e:
            return err.getvalue().strip().splitlines()[-1] if err.getvalue().strip() else f"exit {e.code}"
    return "main returned before parsing its arguments"


class BundledCommands(unittest.TestCase):
    def test_every_command_takes_the_launchers_library_at_the_end(self):
        mods = tomllib.loads(BUNDLED.read_text(encoding="utf-8"))["mod"]
        seen = 0
        for m in mods:
            for key in ("build", "after_add", "after_on"):
                for cmd in m.get(key) or []:
                    seen += 1
                    with self.subTest(mod=m["id"], key=key, cmd=cmd):
                        self.assertTrue((TOOLS / cmd[0]).is_file(), cmd[0])
                        self.assertEqual(parse_problem(cmd[0], [*cmd[1:], "--library", "X:/lib"]), "")
        self.assertGreater(seen, 0)

    def test_the_check_catches_a_tool_that_refuses_it(self):
        bad = argparse.ArgumentParser()
        bad.add_argument("--library")
        bad.add_subparsers(dest="cmd", required=True).add_parser("build")
        fake = mock.Mock(main=lambda argv: bad.parse_args(argv))
        with mock.patch.object(importlib, "import_module", return_value=fake):
            self.assertIn("unrecognized arguments", parse_problem("fake.py", ["build", "--library", "X:/lib"]))
            self.assertEqual(parse_problem("fake.py", ["--library", "X:/lib", "build"]), "")


if __name__ == "__main__":
    unittest.main()
