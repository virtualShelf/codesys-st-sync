# -*- coding: utf-8 -*-
"""
st_sync_ui.py : CODESYS プロジェクトと .st ファイルを同期する画面（Streamlit）

ST フォルダ（初期値はプロジェクトと同じ階層の src）に export し、同じフォルダから import する。

起動:  python -m streamlit run st_sync_ui.py
       （または st-sync-ui.cmd をダブルクリック）
"""
from pathlib import Path

import streamlit as st

import st_sync_runner as runner

st.set_page_config(page_title="ST Sync", page_icon="🔁", layout="centered")

# ---- 初期値（st-sync.cmd の値。プロジェクトは空） ----
if "initialized" not in st.session_state:
    defaults = runner.read_cmd_defaults()
    st.session_state.codesys_exe = defaults.codesys_exe
    st.session_state.profile = defaults.profile
    st.session_state.project = defaults.project
    st.session_state.st_dir = ""
    st.session_state.auto_st_dir = ""
    st.session_state.result = None
    st.session_state.initialized = True


def sync_st_dir():
    """プロジェクトが変わったら、ST フォルダを初期値（同じ階層の src）に合わせる。
    ST フォルダを手で書き換えていた場合は触らない"""
    new = runner.default_st_dir(st.session_state.project)
    if st.session_state.st_dir in ("", st.session_state.get("auto_st_dir", "")):
        st.session_state.st_dir = new
    st.session_state.auto_st_dir = new


def browse_project():
    """ファイル選択ダイアログでプロジェクトを選ぶ（PC 上で動かしているときだけ使える）"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        current = st.session_state.project
        path = filedialog.askopenfilename(
            title="CODESYS プロジェクトを選択",
            filetypes=[("CODESYS プロジェクト", "*.project"), ("すべてのファイル", "*.*")],
            initialdir=str(Path(current).parent) if current else None,
        )
        root.destroy()
        if path:
            st.session_state.project = str(Path(path))
            sync_st_dir()
    except Exception as e:  # tkinter が使えない環境では手入力してもらう
        st.session_state.browse_error = str(e)


def path_status(path, kind):
    if not path:
        return
    p = Path(path)
    ok = p.is_file() if kind == "file" else p.is_dir()
    st.caption("✅ 見つかりました" if ok else "⚠️ 見つかりません")


# ---- 画面 ----
st.title("ST Sync")
st.write("ST フォルダの `.st` ファイルと CODESYS プロジェクトの中身を同期します。")

st.subheader("設定")

st.text_input("CODESYS.exe", key="codesys_exe")
path_status(st.session_state.codesys_exe, "file")

profiles = runner.find_profiles(st.session_state.codesys_exe)
if profiles:
    if not st.session_state.profile:
        st.session_state.profile = profiles[0]
    elif st.session_state.profile not in profiles:
        profiles = [st.session_state.profile] + profiles
    st.selectbox("プロファイル", profiles, key="profile")
else:
    st.text_input("プロファイル", key="profile",
                  help="CODESYS の Profiles フォルダにある .profile.xml のファイル名（拡張子なし）")

col_path, col_button = st.columns([5, 1])
with col_path:
    st.text_input("プロジェクトファイル（.project）", key="project",
                  placeholder=r"C:\...\YourProject.project", on_change=sync_st_dir)
with col_button:
    st.write("")  # ボタンの高さを入力欄に揃える
    st.button("参照…", on_click=browse_project, use_container_width=True)
if st.session_state.pop("browse_error", None):
    st.caption("⚠️ ファイル選択ダイアログを開けませんでした。パスを直接入力してください")
path_status(st.session_state.project, "file")

st.text_input("ST フォルダ（エクスポート先・インポート元）", key="st_dir",
              help="初期値はプロジェクトファイルと同じ階層の src フォルダ。"
                   "エクスポートでここに .st を書き出し、インポートはここの .st を読みます")
if st.session_state.st_dir:
    folder = Path(st.session_state.st_dir)
    if not folder.is_dir():
        st.caption("📁 まだありません。エクスポート時に作成します")
    else:
        count = sum(1 for p in folder.iterdir() if p.suffix.lower() == ".st")
        st.caption("✅ .st ファイル {0} 個".format(count))

settings = runner.Settings(
    codesys_exe=st.session_state.codesys_exe.strip().strip('"'),
    profile=st.session_state.profile.strip(),
    project=st.session_state.project.strip().strip('"'),
    st_dir=st.session_state.st_dir.strip().strip('"'),
)

st.subheader("実行")
st.caption("実行前に CODESYS でプロジェクトを閉じてください。1回あたり数十秒かかります。")

export_problems = runner.check(settings, "export")
import_problems = runner.check(settings, "import")

col_export, col_import = st.columns(2)
with col_export:
    st.markdown("**エクスポート**　プロジェクト → ST フォルダ")
    do_export = st.button("エクスポート", type="primary", use_container_width=True,
                          disabled=bool(export_problems))
with col_import:
    st.markdown("**インポート**　ST フォルダ → プロジェクト")
    confirm = st.checkbox("プロジェクトを上書きして保存してよい",
                          help="同じ名前の POU / DUT / GVL は .st の内容で置き換わります")
    do_import = st.button("インポート", use_container_width=True,
                          disabled=bool(import_problems) or not confirm)

problems = sorted(set(export_problems) | set(import_problems))
for p in problems:
    st.warning(p)

mode = "export" if do_export else "import" if do_import else None
if mode:
    label = "エクスポート" if mode == "export" else "インポート"
    with st.spinner("CODESYS を起動して{0}しています…".format(label)):
        result = runner.run_sync(mode, settings)
    st.session_state.result = (mode, result,
                               runner.git_changes(runner.target_dir(settings, mode)))
    st.rerun()  # 実行後のフォルダの状態でチェック表示を描き直す

# ---- 結果 ----
if st.session_state.result:
    mode, result, changes = st.session_state.result
    st.subheader("結果")
    if result.ok:
        st.success(result.message)
    else:
        st.error(result.message)
    if result.log:
        st.code(result.log, language="text")
    if result.output.strip():
        with st.expander("CODESYS の出力"):
            st.code(result.output, language="text")

    if changes is None:
        st.caption("Git の変更は表示できません（Git が無いか、フォルダが Git の管理外です）")
    else:
        status, diff = changes
        if not status:
            st.info("フォルダに Git の変更はありません")
        else:
            st.markdown("**Git の変更**（コミットはまだしていません）")
            st.code(status, language="text")
            if diff:
                with st.expander("差分を見る", expanded=(mode == "export")):
                    st.code(diff, language="diff")

with st.expander("同じことをコマンドで実行するには"):
    command = runner.build_command(settings)
    if not isinstance(command, str):
        command = " ".join(command)
    st.code("set ST_SYNC_MODE=export\nset ST_SYNC_PROJECT={0}\nset ST_SYNC_DIR={1}\n{2}".format(
        settings.project or "<プロジェクトのパス>",
        settings.st_dir or "<ST フォルダ>", command), language="bat")
