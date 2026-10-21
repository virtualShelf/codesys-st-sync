# -*- coding: utf-8 -*-
"""ST 同期（st_sync.py / st_sync_runner.py）のテスト。偽 CODESYS を使うので CODESYS は不要

実行: python -m unittest discover -s tests -v
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import FIXTURES, PROFILE, Workspace, runner

import st_sync  # st_sync.py（helpers がリポジトリ直下を import パスに入れている）


class TestParsing(unittest.TestCase):
    """CODESYS に依存しない解析部分"""

    def test_detect_kinds_of_fixture_files(self):
        expected = {
            "E_State.st": ("TYPE", "E_State", "Enumeration"),
            "GVL_Modbus.st": ("GVL", "GVL_Modbus", None),
            "PLC_PRG.st": ("PROGRAM", "PLC_PRG", None),
        }
        for name, kind in expected.items():
            text = st_sync.read_text(str(FIXTURES / name))
            self.assertEqual(st_sync.detect(text, name[:-3]), kind, name)

    def test_split_pou_at_last_end_var(self):
        decl, impl = st_sync.split_pou(st_sync.read_text(str(FIXTURES / "PLC_PRG.st")))
        self.assertEqual(decl.strip().splitlines()[-1].strip(), "END_VAR")
        self.assertNotIn("END_VAR", impl)
        self.assertIn("CASE eState OF", impl)

    def test_end_var_inside_comment_is_ignored(self):
        text = "PROGRAM P\nVAR\n  x : INT;\nEND_VAR\n(* END_VAR はここでは無視 *)\nx := 1;\n"
        decl, impl = st_sync.split_pou(text)
        self.assertTrue(decl.rstrip().endswith("END_VAR"))
        self.assertIn("x := 1;", impl)


class TestRunner(unittest.TestCase):
    """画面から使う処理。偽 CODESYS を実際に起動して確かめる"""

    def test_defaults_come_from_cmd_and_project_is_blank(self):
        d = runner.read_cmd_defaults()
        self.assertTrue(d.codesys_exe.lower().endswith("codesys.exe"))
        self.assertTrue(d.profile)
        self.assertEqual(d.project, "")
        self.assertEqual(d.st_dir, "")

    def test_find_profiles(self):
        with Workspace() as ws:
            self.assertEqual(runner.find_profiles(str(ws.exe)), [PROFILE])

    def test_check_blocks_blank_project_and_open_project(self):
        with Workspace() as ws:
            self.assertIn("プロジェクトファイルを指定してください",
                          runner.check(ws.settings(project=""), "export"))
            runner.lock_file(ws.project).write_text("")
            self.assertTrue(any("開いたまま" in p for p in runner.check(ws.settings(), "export")))
            runner.lock_file(ws.project).unlink()
            self.assertEqual(runner.check(ws.settings(), "import"), [])

    def test_import_then_export_round_trip(self):
        with Workspace() as ws:
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(sorted(o["name"] for o in ws.project_objects()),
                             ["E_State", "GVL_Modbus", "PLC_PRG"])

            out = ws.dir / "exported"
            result = runner.run_sync("export", ws.settings(st_dir=str(out)))
            self.assertTrue(result.ok, result.log + result.output)
            for src in FIXTURES.glob("*.st"):
                self.assertEqual(st_sync.read_text(str(out / src.name)).strip(),
                                 st_sync.read_text(str(src)).strip(), src.name)

    def test_second_import_updates_instead_of_creating(self):
        with Workspace() as ws:
            runner.run_sync("import", ws.settings())
            result = runner.run_sync("import", ws.settings())
            self.assertIn("UPDATE PLC_PRG", result.log)
            self.assertEqual(len(ws.project_objects()), 3)

    def test_default_st_dir_is_src_next_to_project(self):
        project = str(Path("C:/work/ModbusTCP/ModbusTCP.project"))
        self.assertEqual(runner.default_st_dir(project), str(Path("C:/work/ModbusTCP/src")))
        self.assertEqual(runner.default_st_dir(""), "")

    def test_export_to_src_then_import_from_src(self):
        """src に export → src の .st を編集 → 同じ src から import でプロジェクトに反映される"""
        with Workspace() as ws:
            runner.run_sync("import", ws.settings())  # プロジェクトに中身を入れておく
            src = Path(runner.default_st_dir(str(ws.project)))
            self.assertFalse(src.exists())

            result = runner.run_sync("export", ws.settings(st_dir=str(src)))
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(sorted(p.name for p in src.glob("*.st")),
                             ["E_State.st", "GVL_Modbus.st", "PLC_PRG.st"])

            e_state = src / "E_State.st"
            e_state.write_text(e_state.read_text(encoding="utf-8")
                               .replace("Error := 2", "Error := 2   (* src で編集 *)"),
                               encoding="utf-8")
            result = runner.run_sync("import", ws.settings(st_dir=str(src)))
            self.assertTrue(result.ok, result.log + result.output)
            decl = next(o["decl"] for o in ws.project_objects() if o["name"] == "E_State")
            self.assertIn("(* src で編集 *)", decl)

    def test_check_st_dir(self):
        with Workspace() as ws:
            self.assertIn("ST フォルダを指定してください",
                          runner.check(ws.settings(st_dir=""), "export"))
            missing = str(ws.dir / "src")
            self.assertEqual(runner.check(ws.settings(st_dir=missing), "export"), [])  # 作るので OK
            self.assertTrue(any("先にエクスポート" in p
                                for p in runner.check(ws.settings(st_dir=missing), "import")))
            (ws.dir / "src").mkdir()
            self.assertTrue(any(".st ファイルがありません" in p
                                for p in runner.check(ws.settings(st_dir=missing), "import")))

    def test_wrong_profile_is_reported(self):
        with Workspace() as ws:
            result = runner.run_sync("export", ws.settings(profile="CODESYS V3.5 SP99"))
            self.assertFalse(result.ok)
            self.assertIn("プロファイル", result.message)

    @unittest.skipUnless(shutil.which("git"), "git がありません")
    def test_export_shows_git_diff(self):
        with Workspace() as ws:
            subprocess.run("git init -q && git add -A && git -c user.name=t -c user.email=t@t "
                           "commit -qm init", shell=True, cwd=ws.dir, check=True)
            runner.run_sync("import", ws.settings())
            ws.edit_project("E_State", "Error := 2", "Error := 2   (* CODESYS で追記 *)")
            subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm p",
                           shell=True, cwd=ws.dir, check=True)  # プロジェクトの変更は無視させる
            # リポジトリはフォルダから自動で探す（ST フォルダがどのリポジトリにあっても動く）
            runner.run_sync("export", ws.settings(export_dir=str(ws.st_dir)))
            status, diff = runner.git_changes(str(ws.st_dir))
            self.assertIn("E_State.st", status)
            self.assertIn("+    Error := 2   (* CODESYS で追記 *)", diff)


if __name__ == "__main__":
    unittest.main()
