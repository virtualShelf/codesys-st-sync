# -*- coding: utf-8 -*-
"""ST 同期（st_sync.py / st_sync_runner.py）のテスト。偽 CODESYS を使うので CODESYS は不要

実行: python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from helpers import FIXTURES, PROFILE, Workspace, runner

# fixtures の各ファイルが入る、種類ごとのフォルダ
FIXTURE_FOLDERS = {"E_State.st": "DUTs", "GVL_Modbus.st": "GVLs", "PLC_PRG.st": "PRGs"}

FB_TEXT = "FUNCTION_BLOCK FB_Sample\nVAR_INPUT\n    bIn : BOOL;\nEND_VAR\n\nbIn := bIn;\n"
STRUCT_TEXT = ("TYPE ST_Passenger :\nSTRUCT\n    nCount  : INT;   (* 人数 *)\n"
               "    nLoadKg : INT;   (* 重量 [kg] *)\nEND_STRUCT\nEND_TYPE\n")
STRUCT_EXT_TEXT = "TYPE ST_PassengerEx EXTENDS ST_Passenger :\nSTRUCT\n    bVip : BOOL;\nEND_STRUCT\nEND_TYPE\n"
ITF_TEXT = """(* I_Door : 扉の共通インターフェース *)
INTERFACE I_Door

METHOD Open : BOOL
VAR_INPUT
    bForce : BOOL;   (* 強制的に開ける *)
END_VAR
END_METHOD

METHOD Close
END_METHOD

PROPERTY IsOpen : BOOL
GET
END_GET
END_PROPERTY

END_INTERFACE
"""
FB_MEMBERS_TEXT = """(* FB_Door : I_Door を実装する扉 *)
FUNCTION_BLOCK FB_Door IMPLEMENTS I_Door
VAR
    bOpen : BOOL;
END_VAR

bOpen := bOpen;

METHOD Open : BOOL
VAR_INPUT
    bForce : BOOL;
END_VAR

bOpen := TRUE;
Open := bOpen;
END_METHOD

METHOD Close

bOpen := FALSE;
END_METHOD

PROPERTY IsOpen : BOOL
GET
IsOpen := bOpen;
END_GET
SET
VAR
    bOld : BOOL;
END_VAR
bOld := bOpen;
bOpen := IsOpen;
END_SET
END_PROPERTY

ACTION ResetDoor
bOpen := FALSE;
END_ACTION

END_FUNCTION_BLOCK
"""
FB_IMPL_TEXT = "FUNCTION_BLOCK FB_Door IMPLEMENTS I_Door\nVAR\n    bOpen : BOOL;\nEND_VAR\n\nbOpen := bOpen;\n"
FC_TEXT = "FUNCTION FC_Sample : INT\nVAR_INPUT\n    n : INT;\nEND_VAR\n\nFC_Sample := n;\n"

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


class TestDutAndInterfaceParsing(unittest.TestCase):

    def test_detect_dut_variants(self):
        self.assertEqual(st_sync.detect(STRUCT_TEXT, "x"), ("TYPE", "ST_Passenger", "Structure"))
        self.assertEqual(st_sync.detect(STRUCT_EXT_TEXT, "x"), ("TYPE", "ST_PassengerEx", "Structure"))
        self.assertEqual(st_sync.detect("TYPE U_Word :\nUNION\n  w : WORD;\nEND_UNION\nEND_TYPE\n", "x"),
                         ("TYPE", "U_Word", "Union"))
        self.assertEqual(st_sync.detect("TYPE T_Floor : INT; END_TYPE\n", "x"), ("TYPE", "T_Floor", "Alias"))

    def test_detect_interface(self):
        self.assertEqual(st_sync.detect(ITF_TEXT, "x"), ("INTERFACE", "I_Door", None))
        self.assertEqual(st_sync.detect(FB_IMPL_TEXT, "x"), ("FUNCTION_BLOCK", "FB_Door", None))

    def test_split_and_join_interface(self):
        header, raw, has_end = st_sync.split_members(ITF_TEXT)
        members = [st_sync.parse_member(r, True) for r in raw]
        self.assertTrue(has_end)
        self.assertEqual(header.strip().splitlines()[-1], "INTERFACE I_Door")
        self.assertEqual([(m["kind"], m["name"], m["type"]) for m in members],
                         [("METHOD", "Open", "BOOL"), ("METHOD", "Close", None), ("PROPERTY", "IsOpen", "BOOL")])
        self.assertIn("bForce : BOOL;", members[0]["decl"])
        self.assertNotIn("END_METHOD", members[0]["decl"])
        self.assertEqual(sorted(members[2]["accessors"]), ["Get"])
        self.assertEqual(st_sync.join_members(header, members, "INTERFACE").strip(), ITF_TEXT.strip())

    def test_split_and_join_fb_members(self):
        body, raw, has_end = st_sync.split_members(FB_MEMBERS_TEXT)
        members = [st_sync.parse_member(r, False) for r in raw]
        self.assertTrue(has_end)
        self.assertTrue(body.rstrip().endswith("bOpen := bOpen;"))
        self.assertEqual([(m["kind"], m["name"]) for m in members],
                         [("METHOD", "Open"), ("METHOD", "Close"), ("PROPERTY", "IsOpen"), ("ACTION", "ResetDoor")])
        self.assertTrue(members[0]["decl"].rstrip().endswith("END_VAR"))
        self.assertEqual(members[0]["impl"], "bOpen := TRUE;\nOpen := bOpen;\n")
        self.assertEqual(members[1]["decl"], "METHOD Close\n")
        get_decl, get_impl = members[2]["accessors"]["Get"]
        self.assertIsNone(get_decl)  # VAR を書いていなければ宣言は触らない
        self.assertEqual(get_impl, "IsOpen := bOpen;\n")
        set_decl, set_impl = members[2]["accessors"]["Set"]
        self.assertIn("bOld : BOOL;", set_decl)
        self.assertEqual(members[3]["impl"], "bOpen := FALSE;\n")


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
                folder = FIXTURE_FOLDERS[src.name]
                self.assertEqual(st_sync.read_text(str(out / folder / src.name)).strip(),
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
            self.assertEqual(sorted(p.relative_to(src).as_posix() for p in src.rglob("*.st")),
                             ["DUTs/E_State.st", "GVLs/GVL_Modbus.st", "PRGs/PLC_PRG.st"])

            e_state = src / "DUTs" / "E_State.st"
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
            runner.run_sync("export", ws.settings())  # 種類ごとのフォルダに並べ替えた状態をコミットしておく
            subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm sorted",
                           shell=True, cwd=ws.dir, check=True)
            ws.edit_project("E_State", "Error := 2", "Error := 2   (* CODESYS で追記 *)")
            subprocess.run("git add -A && git -c user.name=t -c user.email=t@t commit -qm p",
                           shell=True, cwd=ws.dir, check=True)  # プロジェクトの変更は無視させる
            # リポジトリはフォルダから自動で探す（ST フォルダがどのリポジトリにあっても動く）
            runner.run_sync("export", ws.settings(export_dir=str(ws.st_dir)))
            status, diff = runner.git_changes(str(ws.st_dir))
            self.assertIn("E_State.st", status)
            self.assertIn("+    Error := 2   (* CODESYS で追記 *)", diff)


class TestFolders(unittest.TestCase):
    """種類ごとのフォルダ（DUTs / GVLs / FCs / FBs / PRGs）"""

    def test_import_puts_each_kind_into_its_folder(self):
        with Workspace() as ws:
            (ws.st_dir / "FB_Sample.st").write_text(FB_TEXT, encoding="utf-8")
            (ws.st_dir / "FC_Sample.st").write_text(FC_TEXT, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(ws.placement(), {
                "E_State": "DUTs", "GVL_Modbus": "GVLs", "FC_Sample": "FCs",
                "FB_Sample": "FBs", "PLC_PRG": "PRGs"})
            self.assertEqual(ws.project_folders(), ["DUTs", "FBs", "FCs", "GVLs", "PRGs"])

            # 2回目は同じフォルダを作り直さず、そのまま更新する
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertNotIn("FOLDER", result.log)
            self.assertEqual(ws.project_folders(), ["DUTs", "FBs", "FCs", "GVLs", "PRGs"])

    def test_import_moves_objects_that_are_outside_the_folder(self):
        """フォルダ分けする前のプロジェクト（Application の直下に置いてある）も、フォルダに移す"""
        with Workspace() as ws:
            ws.project.write_text(json.dumps([
                {"name": "PLC_PRG", "decl": "PROGRAM PLC_PRG\n", "impl": "", "folder": None}]),
                encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertIn("MOVE   PLC_PRG → PRGs", result.log)
            self.assertEqual(ws.placement()["PLC_PRG"], "PRGs")
            self.assertEqual(len(ws.project_objects()), 3)  # 移しただけで、増えていない

    def test_new_objects_are_created_in_folders_even_if_move_fails(self):
        """本物の CODESYS で move() が「Multiple targets could match」になっても、新しく作るものは困らない"""
        with Workspace() as ws, mock.patch.dict(os.environ, {"FAKE_CODESYS_MOVE_AMBIGUOUS": "1"}):
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(ws.placement(), {"E_State": "DUTs", "GVL_Modbus": "GVLs", "PLC_PRG": "PRGs"})
            self.assertNotIn("WARN", result.log)

    def test_existing_object_that_cannot_be_moved_is_still_updated(self):
        with Workspace() as ws, mock.patch.dict(os.environ, {"FAKE_CODESYS_MOVE_AMBIGUOUS": "1"}):
            ws.project.write_text(json.dumps([
                {"name": "PLC_PRG", "decl": "PROGRAM PLC_PRG\n", "impl": "", "folder": None}]),
                encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)  # 警告だけで、失敗にはしない
            self.assertIn("WARN   PLC_PRG を PRGs に移せませんでした", result.log)
            self.assertIsNone(ws.placement()["PLC_PRG"])
            decl = next(o["decl"] for o in ws.project_objects() if o["name"] == "PLC_PRG")
            self.assertIn("RUN_TIMEOUT", decl)   # 中身は .st の内容に置き換わっている

    def test_import_reads_st_files_in_subfolders(self):
        with Workspace() as ws:
            sub = ws.st_dir / "FBs"
            sub.mkdir()
            (sub / "FB_Sample.st").write_text(FB_TEXT, encoding="utf-8")
            self.assertEqual(runner.check(ws.settings(st_dir=str(sub)), "import"), [])
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(ws.placement()["FB_Sample"], "FBs")

    def test_same_name_twice_stops_without_changing_the_project(self):
        with Workspace() as ws:
            sub = ws.st_dir / "PRGs"
            sub.mkdir()
            shutil.copy(str(ws.st_dir / "PLC_PRG.st"), str(sub / "PLC_PRG.st"))
            result = runner.run_sync("import", ws.settings())
            self.assertFalse(result.ok)
            self.assertIn("PLC_PRG が2つあります", result.message)
            self.assertEqual(ws.project_objects(), [])

    def test_export_writes_into_folders_and_removes_old_flat_files(self):
        with Workspace() as ws:
            (ws.st_dir / "FB_Sample.st").write_text(FB_TEXT, encoding="utf-8")
            runner.run_sync("import", ws.settings())
            result = runner.run_sync("export", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(sorted(p.relative_to(ws.st_dir).as_posix() for p in ws.st_dir.rglob("*.st")),
                             ["DUTs/E_State.st", "FBs/FB_Sample.st", "GVLs/GVL_Modbus.st",
                              "PRGs/PLC_PRG.st"])
            self.assertIn("REMOVE PLC_PRG.st", result.log)

            # 並べ替えたあとに import し直しても、2つあるとは言われない
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)


class TestDutAndInterface(unittest.TestCase):

    def test_struct_round_trip(self):
        with Workspace() as ws:
            (ws.st_dir / "ST_Passenger.st").write_text(STRUCT_TEXT, encoding="utf-8")
            (ws.st_dir / "ST_PassengerEx.st").write_text(STRUCT_EXT_TEXT, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(ws.placement()["ST_Passenger"], "DUTs")
            self.assertEqual(ws.placement()["ST_PassengerEx"], "DUTs")
            out = ws.dir / "out"
            runner.run_sync("export", ws.settings(st_dir=str(out)))
            self.assertEqual((out / "DUTs" / "ST_Passenger.st").read_text(encoding="utf-8").strip(),
                             STRUCT_TEXT.strip())
            self.assertEqual((out / "DUTs" / "ST_PassengerEx.st").read_text(encoding="utf-8").strip(),
                             STRUCT_EXT_TEXT.strip())

    def test_interface_import_creates_members_and_round_trips(self):
        with Workspace() as ws:
            (ws.st_dir / "I_Door.st").write_text(ITF_TEXT, encoding="utf-8")
            (ws.st_dir / "FB_Door.st").write_text(FB_IMPL_TEXT, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual(ws.placement()["I_Door"], "ITFs")
            itf = next(o for o in ws.project_objects() if o["name"] == "I_Door")
            self.assertEqual([m["name"] for m in itf["members"]], ["Open", "Close", "IsOpen"])
            self.assertIn("bForce : BOOL;", itf["members"][0]["decl"])
            # FB が IMPLEMENTS するので、インターフェースを先に作る
            self.assertLess(result.log.index("CREATE I_Door"), result.log.index("CREATE FB_Door"))

            out = ws.dir / "out"
            result = runner.run_sync("export", ws.settings(st_dir=str(out)))
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual((out / "ITFs" / "I_Door.st").read_text(encoding="utf-8").strip(),
                             ITF_TEXT.strip())
            # メソッド・プロパティは別のファイルにしない
            self.assertEqual(sorted(p.stem for p in out.rglob("*.st")),
                             ["E_State", "FB_Door", "GVL_Modbus", "I_Door", "PLC_PRG"])
            self.assertNotIn("SKIP", result.log)

    def test_interface_property_without_set_removes_the_set_accessor(self):
        with Workspace() as ws:
            (ws.st_dir / "I_Door.st").write_text(ITF_TEXT, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertIn("DELETE I_Door.IsOpen.Set", result.log)
            itf = next(o for o in ws.project_objects() if o["name"] == "I_Door")
            prop = next(m for m in itf["members"] if m["name"] == "IsOpen")
            self.assertEqual([a["name"] for a in prop["members"]], ["Get"])

    def test_interface_member_removed_from_file_is_removed_from_project(self):
        with Workspace() as ws:
            itf_file = ws.st_dir / "I_Door.st"
            itf_file.write_text(ITF_TEXT, encoding="utf-8")
            runner.run_sync("import", ws.settings())
            itf_file.write_text(ITF_TEXT.replace("METHOD Close\nEND_METHOD\n\n", ""), encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertIn("DELETE I_Door.Close", result.log)
            itf = next(o for o in ws.project_objects() if o["name"] == "I_Door")
            self.assertEqual([m["name"] for m in itf["members"]], ["Open", "IsOpen"])


class TestPouMembers(unittest.TestCase):
    """FB / PRG のメソッド・プロパティ・アクション"""

    def _fb(self, ws):
        return next(o for o in ws.project_objects() if o["name"] == "FB_Door")

    def test_fb_members_import_and_round_trip(self):
        with Workspace() as ws:
            (ws.st_dir / "I_Door.st").write_text(ITF_TEXT, encoding="utf-8")
            (ws.st_dir / "FB_Door.st").write_text(FB_MEMBERS_TEXT, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            fb = self._fb(ws)
            self.assertEqual([m["name"] for m in fb["members"]], ["Open", "Close", "IsOpen", "ResetDoor"])
            self.assertEqual(fb["members"][0]["impl"], "bOpen := TRUE;\nOpen := bOpen;\n")
            self.assertEqual(fb["members"][3]["impl"], "bOpen := FALSE;\n")
            setter = next(a for a in fb["members"][2]["members"] if a["name"] == "Set")
            self.assertIn("bOld : BOOL;", setter["decl"])
            self.assertIn("bOpen := IsOpen;", setter["impl"])

            out = ws.dir / "out"
            result = runner.run_sync("export", ws.settings(st_dir=str(out)))
            self.assertTrue(result.ok, result.log + result.output)
            self.assertEqual((out / "FBs" / "FB_Door.st").read_text(encoding="utf-8").strip(),
                             FB_MEMBERS_TEXT.strip())
            self.assertEqual(sorted(p.stem for p in out.rglob("*.st")),
                             ["E_State", "FB_Door", "GVL_Modbus", "I_Door", "PLC_PRG"])

            # 書き出したものをもう一度 import しても、増えたり消えたりしない
            shutil.rmtree(str(ws.st_dir)); shutil.copytree(str(out), str(ws.st_dir))
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertNotIn("DELETE", result.log)
            self.assertEqual(len(self._fb(ws)["members"]), 4)

    def test_method_removed_from_file_is_removed_from_project(self):
        with Workspace() as ws:
            fb_file = ws.st_dir / "FB_Door.st"
            fb_file.write_text(FB_MEMBERS_TEXT, encoding="utf-8")
            runner.run_sync("import", ws.settings())
            fb_file.write_text(FB_MEMBERS_TEXT.replace("METHOD Close\n\nbOpen := FALSE;\nEND_METHOD\n\n", ""),
                               encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertIn("DELETE FB_Door.Close", result.log)
            self.assertEqual([m["name"] for m in self._fb(ws)["members"]], ["Open", "IsOpen", "ResetDoor"])

    def test_old_style_file_keeps_members_in_project(self):
        """メンバーを書いていない（この機能より前に書き出した）ファイルを import しても、メソッドは消さない"""
        with Workspace() as ws:
            fb_file = ws.st_dir / "FB_Door.st"
            fb_file.write_text(FB_MEMBERS_TEXT, encoding="utf-8")
            runner.run_sync("import", ws.settings())
            body = FB_MEMBERS_TEXT[:FB_MEMBERS_TEXT.index("METHOD Open")]
            fb_file.write_text(body, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertIn("KEEP   FB_Door.Open", result.log)
            self.assertEqual(len(self._fb(ws)["members"]), 4)

    def test_fb_without_members_is_written_as_before(self):
        """メンバーが無い FB / PRG は、今までどおり END_FUNCTION_BLOCK / END_PROGRAM を書かない"""
        with Workspace() as ws:
            runner.run_sync("import", ws.settings())
            out = ws.dir / "out"
            runner.run_sync("export", ws.settings(st_dir=str(out)))
            self.assertNotIn("END_PROGRAM", (out / "PRGs" / "PLC_PRG.st").read_text(encoding="utf-8"))

    def test_program_action(self):
        with Workspace() as ws:
            prg = (FIXTURES / "PLC_PRG.st").read_text(encoding="utf-8").rstrip()
            text = prg + "\n\nACTION ResetAll\nnCounter := 0;\nEND_ACTION\n\nEND_PROGRAM\n"
            (ws.st_dir / "PLC_PRG.st").write_text(text, encoding="utf-8")
            result = runner.run_sync("import", ws.settings())
            self.assertTrue(result.ok, result.log + result.output)
            self.assertIn("CREATE PLC_PRG.ResetAll (ACTION)", result.log)
            out = ws.dir / "out"
            runner.run_sync("export", ws.settings(st_dir=str(out)))
            self.assertEqual((out / "PRGs" / "PLC_PRG.st").read_text(encoding="utf-8").strip(), text.strip())


if __name__ == "__main__":
    unittest.main()
