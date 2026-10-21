# -*- coding: utf-8 -*-
"""テスト共通: 偽 CODESYS で st_sync_runner を動かすための準備"""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "src"  # st_sync*.py は src\ の下
FIXTURES = Path(__file__).resolve().parent / "fixtures"  # テスト用の ST
FAKE_CODESYS = Path(__file__).resolve().parent / "fake_codesys.py"
PROFILE = "CODESYS V3.5 SP22 Patch 4"

sys.path.insert(0, str(APP_DIR))
import st_sync_runner as runner  # noqa: E402


def fake_command(settings):
    """build_command の代わり。本物の CODESYS.exe の代わりに偽物を Python で起動する"""
    return [sys.executable, str(FAKE_CODESYS), "--profile=" + settings.profile,
            "--runscript=" + str(runner.SYNC_SCRIPT), "--noUI"]


class Workspace:
    """一時フォルダに、空のプロジェクトと fixtures の ST のコピーを用意する"""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="st_sync_test_"))
        self.project = self.dir / "Test.project"
        self.project.write_text("[]", encoding="utf-8")
        self.st_dir = self.dir / "st"
        shutil.copytree(FIXTURES, self.st_dir)
        # CODESYS.exe の存在チェックを通すため、空の実行ファイルの代わりを置く
        self.exe = self.dir / "CODESYS 3.5.22.40" / "CODESYS" / "Common" / "CODESYS.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("")
        profiles = self.exe.parent.parent / "Profiles"
        profiles.mkdir()
        (profiles / (PROFILE + ".profile.xml")).write_text("")
        self.patcher = mock.patch.object(runner, "build_command", fake_command)

    def settings(self, **override):
        values = dict(codesys_exe=str(self.exe), profile=PROFILE,
                      project=str(self.project), st_dir=str(self.st_dir))
        values.update(override)
        return runner.Settings(**values)

    def project_objects(self):
        return json.loads(self.project.read_text(encoding="utf-8"))

    def edit_project(self, name, old, new):
        """CODESYS の画面で宣言部を書き換えた想定"""
        objs = self.project_objects()
        for o in objs:
            if o["name"] == name:
                o["decl"] = o["decl"].replace(old, new)
        self.project.write_text(json.dumps(objs, ensure_ascii=False), encoding="utf-8")

    def __enter__(self):
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()
        shutil.rmtree(self.dir, ignore_errors=True)
        if runner.LOG_FILE.exists():
            runner.LOG_FILE.unlink()
        return False
