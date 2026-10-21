# -*- coding: utf-8 -*-
"""ST Sync の画面（Streamlit）のテスト。Streamlit 公式の AppTest で、ブラウザなしで画面を動かす

実行: python -m unittest discover -s tests -v   （streamlit が入っていなければスキップ）
"""
import importlib.util
import unittest

from helpers import APP_DIR, Workspace

HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None
APP = str(APP_DIR / "st_sync_ui.py")


def button(at, label):
    return next(b for b in at.button if b.label == label)


def runner_import(ws):
    """fixtures の ST を偽プロジェクトに入れる（画面を通さずに準備する）"""
    from helpers import runner
    result = runner.run_sync("import", ws.settings())
    assert result.ok, result.log + result.output


@unittest.skipUnless(HAS_STREAMLIT, "streamlit がありません（pip install -r requirements.txt）")
class TestUI(unittest.TestCase):

    def make_app(self, ws=None):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(APP, default_timeout=60)
        if ws is not None:
            # 初期化済みとして、偽 CODESYS とテスト用プロジェクトを入れておく
            s = ws.settings()
            at.session_state["initialized"] = True
            at.session_state["codesys_exe"] = s.codesys_exe
            at.session_state["profile"] = s.profile
            at.session_state["project"] = s.project
            at.session_state["st_dir"] = s.st_dir
            at.session_state["auto_st_dir"] = ""
            at.session_state["result"] = None
        return at

    def test_first_view_has_blank_project_and_disabled_buttons(self):
        at = self.make_app().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.text_input(key="project").value, "")
        self.assertTrue(button(at, "エクスポート").disabled)
        self.assertTrue(button(at, "インポート").disabled)
        self.assertTrue(any("プロジェクトファイル" in w.value for w in at.warning))

    def test_project_input_fills_src_and_import_reads_exported_files(self):
        with Workspace() as ws:
            runner_import(ws)  # 偽プロジェクトに中身を入れておく（CODESYS で作ってある想定）
            at = self.make_app(ws)
            at.session_state["project"] = ""
            at.session_state["st_dir"] = ""
            at.run()
            self.assertTrue(button(at, "エクスポート").disabled)

            at.text_input(key="project").input(str(ws.project)).run()
            src = ws.project.parent / "src"
            self.assertEqual(at.text_input(key="st_dir").value, str(src))

            # src がまだ無いのでインポートは押せない
            at.checkbox[0].check().run()
            self.assertTrue(button(at, "インポート").disabled)

            button(at, "エクスポート").click().run()
            self.assertFalse(at.exception)
            self.assertTrue(any("エクスポートしました" in s.value for s in at.success))
            self.assertTrue((src / "PLC_PRG.st").exists())

            # エクスポートした src の .st からインポートできる
            self.assertFalse(button(at, "インポート").disabled)
            button(at, "インポート").click().run()
            self.assertTrue(any("インポートしました" in s.value for s in at.success))

    def test_hand_edited_st_dir_is_kept_when_project_changes(self):
        with Workspace() as ws:
            at = self.make_app(ws)
            at.session_state["project"] = ""
            at.session_state["st_dir"] = ""
            at.run()
            custom = str(ws.dir / "my_st")
            at.text_input(key="st_dir").input(custom).run()
            at.text_input(key="project").input(str(ws.project)).run()
            self.assertEqual(at.text_input(key="st_dir").value, custom)

    def test_import_needs_confirmation(self):
        with Workspace() as ws:
            at = self.make_app(ws).run()
            self.assertTrue(button(at, "インポート").disabled)
            at.checkbox[0].check().run()
            self.assertFalse(button(at, "インポート").disabled)
            button(at, "インポート").click().run()
            self.assertTrue(any("インポートしました" in s.value for s in at.success))
            self.assertEqual(len(ws.project_objects()), 3)


if __name__ == "__main__":
    unittest.main()
