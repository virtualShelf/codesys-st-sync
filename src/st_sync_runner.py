# -*- coding: utf-8 -*-
"""
st_sync_runner.py : st_sync.py を CODESYS で動かすための処理（画面なし）

st_sync_ui.py（Streamlit の画面）から使う。画面に依存しないので単体でもテストできる。
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent  # st-sync.cmd はリポジトリ直下
CMD_FILE = ROOT_DIR / "st-sync.cmd"
SYNC_SCRIPT = APP_DIR / "st_sync.py"
LOG_FILE = APP_DIR / "st_sync.log"
TIMEOUT_SEC = 600


@dataclass
class Settings:
    codesys_exe: str
    profile: str
    project: str
    st_dir: str             # ST フォルダ（エクスポート先 兼 インポート元）
    export_dir: str = None  # エクスポートだけ別の場所に出したいとき（テスト用）。None なら st_dir


def default_st_dir(project):
    """ST フォルダの初期値: プロジェクトファイルと同じ階層の src フォルダ"""
    if not project or not project.strip():
        return ""
    return str(Path(project.strip().strip('"')).parent / "src")


def target_dir(settings, mode):
    """そのモードで使うフォルダ（export は書き出し先、import は読み込み元）"""
    if mode == "export" and settings.export_dir is not None:
        return settings.export_dir
    return settings.st_dir


@dataclass
class RunResult:
    ok: bool
    log: str
    output: str
    message: str


def read_cmd_defaults(cmd_file=CMD_FILE):
    """st-sync.cmd の `set "NAME=value"` を読んで初期値にする。プロジェクトと ST フォルダは空にする"""
    values = {}
    try:
        text = Path(cmd_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for m in re.finditer(r'^\s*set\s+"(\w+)=(.*)"\s*$', text, re.I | re.M):
        values[m.group(1).upper()] = m.group(2).strip()
    return Settings(
        codesys_exe=values.get("CODESYS_EXE", ""),
        profile=values.get("CODESYS_PROFILE", ""),
        project="",
        st_dir="",
    )


def find_profiles(codesys_exe):
    """CODESYS.exe の場所から Profiles フォルダを探し、プロファイル名の一覧を返す"""
    if not codesys_exe:
        return []
    # ...\CODESYS\Common\CODESYS.exe → ...\CODESYS\Profiles
    profiles_dir = Path(codesys_exe).parent.parent / "Profiles"
    if not profiles_dir.is_dir():
        return []
    suffix = ".profile.xml"
    return sorted(p.name[: -len(suffix)] for p in profiles_dir.iterdir()
                  if p.name.lower().endswith(suffix))


def lock_file(project):
    """CODESYS がプロジェクトを開いている間にできるロックファイル"""
    return Path(str(project) + ".~u")


def check(settings, mode):
    """実行前のチェック。問題があればメッセージのリストを返す（空なら実行してよい）"""
    problems = []
    if not settings.codesys_exe or not Path(settings.codesys_exe).is_file():
        problems.append("CODESYS.exe が見つかりません")
    if not settings.profile.strip():
        problems.append("プロファイル名が空です")
    if not settings.project.strip():
        problems.append("プロジェクトファイルを指定してください")
    elif not Path(settings.project).is_file():
        problems.append("プロジェクトファイルが見つかりません")
    elif lock_file(settings.project).exists():
        problems.append("CODESYS でプロジェクトを開いたままです。閉じてから実行してください")
    folder = target_dir(settings, mode)
    if not folder.strip():
        problems.append("ST フォルダを指定してください")
    elif mode == "export":
        if Path(folder).exists() and not Path(folder).is_dir():
            problems.append("ST フォルダの場所にフォルダ以外のファイルがあります")
    elif not Path(folder).is_dir():
        problems.append("ST フォルダがまだありません。先にエクスポートしてください")
    elif not any(p.suffix.lower() == ".st" for p in Path(folder).iterdir()):
        problems.append("ST フォルダに .st ファイルがありません。先にエクスポートしてください")
    return problems


def build_command(settings):
    """st-sync.cmd と同じ形の起動コマンド"""
    if os.name == "nt":
        # Windows ではバッチと同じ文字列をそのまま渡す
        return '"{0}" --profile="{1}" --runscript="{2}" --noUI'.format(
            settings.codesys_exe, settings.profile, SYNC_SCRIPT)
    return [settings.codesys_exe, "--profile=" + settings.profile,
            "--runscript=" + str(SYNC_SCRIPT), "--noUI"]


def run_sync(mode, settings, timeout=TIMEOUT_SEC):
    """CODESYS を画面なしで起動して st_sync.py を実行し、ログで結果を判定する"""
    if LOG_FILE.exists():
        LOG_FILE.unlink()  # 前回のログを誤って表示しないように消しておく

    env = os.environ.copy()
    env["ST_SYNC_MODE"] = mode
    env["ST_SYNC_PROJECT"] = settings.project
    env["ST_SYNC_DIR"] = target_dir(settings, mode)  # export でフォルダが無ければ st_sync.py が作る

    try:
        proc = subprocess.run(build_command(settings), env=env, cwd=str(APP_DIR),
                              capture_output=True, text=True, errors="replace", timeout=timeout)
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return RunResult(False, _read_log(), "", "{0} 秒以内に終わりませんでした".format(timeout))
    except OSError as e:
        return RunResult(False, "", "", "CODESYS を起動できませんでした: {0}".format(e))

    log = _read_log()
    if not log:
        return RunResult(False, "", output,
                         "ログが出力されませんでした。プロファイル名が正しいか確認してください")
    errors = [line for line in log.splitlines() if line.startswith("ERROR")]
    if errors:
        return RunResult(False, log, output, errors[0])
    done = "インポートしました" if mode == "import" else "エクスポートしました"
    return RunResult(True, log, output, done)


def _read_log():
    try:
        return LOG_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def git_changes(st_dir):
    """フォルダの Git の変更（status と diff）。Git がない・フォルダが Git の管理外なら None

    リポジトリはフォルダ自身から探すので、ST フォルダがどのリポジトリにあっても動く。
    """
    git = shutil.which("git")
    if not git or not st_dir or not Path(st_dir).is_dir():
        return None
    top = subprocess.run([git, "-C", st_dir, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if top.returncode != 0:
        return None
    base = [git, "-c", "core.quotepath=false", "-C", top.stdout.strip()]
    status = subprocess.run(base + ["status", "--porcelain", "--", st_dir],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if status.returncode != 0:
        return None
    diff = subprocess.run(base + ["diff", "--", st_dir],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return status.stdout.strip(), diff.stdout.strip()
