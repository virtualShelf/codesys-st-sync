# -*- coding: utf-8 -*-
"""本物の CODESYS でエクスポートだけを実行し、ST フォルダの *.st と中身が一致するか確かめる

プロジェクトは変更しない（書き出し先は一時フォルダ）。CODESYS でプロジェクトを閉じてから実行する。
CODESYS.exe とプロファイルは st-sync.cmd の値を使う。

使い方: python tests/check_real_export.py "C:\\...\\YourProject.project" "C:\\...\\codesys\\src"
"""
import difflib
import shutil
import sys
import tempfile
from pathlib import Path

from helpers import runner


def normalize(path):
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip().splitlines()


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    st_dir = Path(sys.argv[2].strip('"'))
    if not st_dir.is_dir():
        print("NG: ST フォルダが見つかりません: {0}".format(st_dir))
        return 2
    out_dir = Path(tempfile.mkdtemp(prefix="st_sync_real_"))
    try:
        settings = runner.read_cmd_defaults()
        settings.project = sys.argv[1].strip('"')
        settings.st_dir = str(st_dir)
        settings.export_dir = str(out_dir)  # 書き出し先は一時フォルダ（ST フォルダには書かない）

        problems = runner.check(settings, "export")
        if problems:
            print("NG: 実行できません\n  " + "\n  ".join(problems))
            return 1

        print("CODESYS でエクスポート中（数十秒かかります）...")
        result = runner.run_sync("export", settings)
        print(result.log)
        if not result.ok:
            print("NG:", result.message)
            return 1

        ok = True
        # 種類ごとのサブフォルダも含めて、ファイル名で突き合わせる
        mine = {p.name.lower(): p for p in st_dir.rglob("*.st")}
        theirs = {p.name.lower(): p for p in out_dir.rglob("*.st")}
        for key in sorted(mine):
            src = mine[key]
            rel = src.relative_to(st_dir).as_posix()
            exported = theirs.get(key)
            if exported is None:
                print("NG: {0} がプロジェクトにありません（import していない？）".format(rel))
                ok = False
                continue
            expected_rel = exported.relative_to(out_dir).as_posix()
            if rel != expected_rel:
                print("参考: {0} は {1} に置くと、export の置き場所と揃います".format(rel, expected_rel))
            a, b = normalize(src), normalize(exported)
            if a == b:
                print("OK: " + rel)
            else:
                ok = False
                print("NG: {0} がプロジェクトの中身と違います".format(rel))
                for line in difflib.unified_diff(a, b, "ST フォルダ/" + rel, "project", lineterm=""):
                    print("    " + line)
        extra = sorted(theirs[k].relative_to(out_dir).as_posix() for k in theirs if k not in mine)
        if extra:
            print("参考: ST フォルダにないオブジェクトがプロジェクトにあります: " + ", ".join(extra))
        print("結果: " + ("一致しました" if ok else "差分があります"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
