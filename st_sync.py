# -*- coding: utf-8 -*-
"""
st_sync.py : ST フォルダの *.st と CODESYS プロジェクトを同期する（CODESYS Scripting 用）

CODESYS.exe の --runscript で実行する。直接 python で動かすものではない。
設定は環境変数で受け取る（st-sync.cmd / st_sync_runner.py がセットする）:
  ST_SYNC_MODE     import | export
  ST_SYNC_PROJECT  .project ファイルのパス
  ST_SYNC_DIR      .st を置くフォルダ（ST フォルダ）

import : ST フォルダの *.st の内容でプロジェクトの POU / DUT / GVL を作成・上書きして保存
export : プロジェクト内のテキスト（ST）オブジェクトを ST フォルダの *.st に書き出す

対応しているのは ST のテキストで書けるオブジェクトだけ
（PROGRAM / FUNCTION_BLOCK / FUNCTION / TYPE / VAR_GLOBAL）。
FB のメソッド・アクション、ラダーや SFC などのグラフィカル言語は対象外。
CODESYS 内の Python は 2 系なので、2/3 両対応で書いている。
"""
from __future__ import print_function, unicode_literals
import io
import os
import re
import sys

LOG_LINES = []


def log(msg):
    LOG_LINES.append(msg)
    print(msg)


# ---------------------------------------------------------------------------
# テキスト解析（CODESYS に依存しない部分）
# ---------------------------------------------------------------------------
_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_PRAGMA = re.compile(r"\{[^}]*\}")

_KIND_PATTERNS = [
    ("FUNCTION_BLOCK", re.compile(r"^\s*FUNCTION_BLOCK\s+(?:(?:ABSTRACT|FINAL|PUBLIC|INTERNAL)\s+)*(\w+)", re.I | re.M)),
    ("FUNCTION", re.compile(r"^\s*FUNCTION\s+(\w+)\s*(?::\s*([\w.]+(?:\([^)]*\))?))?", re.I | re.M)),
    ("PROGRAM", re.compile(r"^\s*PROGRAM\s+(\w+)", re.I | re.M)),
    ("TYPE", re.compile(r"^\s*TYPE\s+(\w+)\s*:", re.I | re.M)),
    ("GVL", re.compile(r"^\s*VAR_GLOBAL\b", re.I | re.M)),
]


def strip_comments(text):
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return _PRAGMA.sub(" ", text)


def detect(text, file_stem):
    """(kind, name, extra) を返す。kind は PROGRAM / FUNCTION_BLOCK / FUNCTION / TYPE / GVL"""
    code = strip_comments(text)
    found = []
    for kind, pat in _KIND_PATTERNS:
        m = pat.search(code)
        if m:
            found.append((m.start(), kind, m))
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    _, kind, m = found[0]
    if kind == "GVL":
        return ("GVL", file_stem, None)
    if kind == "FUNCTION":
        return ("FUNCTION", m.group(1), m.group(2))
    if kind == "TYPE":
        body = code[m.end():]
        if re.match(r"\s*\(", body):
            sub = "Enumeration"
        elif re.match(r"\s*STRUCT\b", body, re.I):
            sub = "Structure"
        elif re.match(r"\s*UNION\b", body, re.I):
            sub = "Union"
        else:
            sub = "Alias"
        return ("TYPE", m.group(1), sub)
    return (kind, m.group(1), None)


def split_pou(text):
    """POU のテキストを (宣言部, 実装部) に分ける。
    最後の END_VAR の行までを宣言部とする。END_VAR が無い POU は
    PROGRAM / FUNCTION_BLOCK / FUNCTION の行までを宣言部とする。"""
    lines = text.replace("\r\n", "\n").split("\n")
    # コメント内の END_VAR に反応しないよう、行数を保ったままコメントを消してから探す
    code_lines = _strip_keep_lines("\n".join(lines)).split("\n")

    cut = -1
    for i, line in enumerate(code_lines):
        if re.match(r"^\s*END_VAR\s*;?\s*$", line, re.I):
            cut = i
    if cut < 0:
        for i, line in enumerate(code_lines):
            if re.match(r"^\s*(PROGRAM|FUNCTION_BLOCK|FUNCTION)\b", line, re.I):
                cut = i
                break
    if cut < 0:
        return text, ""
    decl = "\n".join(lines[:cut + 1]).rstrip() + "\n"
    impl = "\n".join(lines[cut + 1:]).strip("\n") + "\n"
    return decl, impl


def _strip_keep_lines(text):
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = _BLOCK_COMMENT.sub(blank, text)
    text = _LINE_COMMENT.sub(blank, text)
    return _PRAGMA.sub(blank, text)


def read_text(path):
    with io.open(path, "r", encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def write_text(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# CODESYS とのやり取り
# ---------------------------------------------------------------------------
_ORDER = {"TYPE": 0, "GVL": 1, "FUNCTION": 2, "FUNCTION_BLOCK": 3, "PROGRAM": 4}


def find_object(app, name):
    for obj in app.find(name, True):
        if obj.has_textual_declaration:
            return obj
    return None


def do_import(proj, app, st_dir):
    items = []
    for fn in sorted(os.listdir(st_dir)):
        if not fn.lower().endswith(".st"):
            continue
        path = os.path.join(st_dir, fn)
        text = read_text(path)
        info = detect(text, os.path.splitext(fn)[0])
        if info is None:
            log("SKIP  %s : 種類を判別できない" % fn)
            continue
        items.append((_ORDER[info[0]], fn, text, info))
    items.sort(key=lambda x: (x[0], x[1]))

    for _, fn, text, (kind, name, extra) in items:
        obj = find_object(app, name)
        action = "UPDATE"
        if obj is None:
            action = "CREATE"
            if kind == "TYPE":
                dut_type = getattr(DutType, extra)
                if extra == "Alias":
                    obj = app.create_dut(name, dut_type, "INT")
                else:
                    obj = app.create_dut(name, dut_type)
            elif kind == "GVL":
                obj = app.create_gvl(name)
            elif kind == "PROGRAM":
                obj = app.create_pou(name, PouType.Program)
            elif kind == "FUNCTION_BLOCK":
                obj = app.create_pou(name, PouType.FunctionBlock)
            elif kind == "FUNCTION":
                obj = app.create_pou(name, PouType.Function, return_type=extra or "BOOL")

        if kind in ("TYPE", "GVL"):
            obj.textual_declaration.replace(text)
        else:
            decl, impl = split_pou(text)
            obj.textual_declaration.replace(decl)
            if obj.has_textual_implementation:
                obj.textual_implementation.replace(impl)
        log("%-6s %s (%s)" % (action, name, kind))

    proj.save()
    log("プロジェクトを保存しました")


def do_export(app, st_dir):
    if not os.path.isdir(st_dir):
        os.makedirs(st_dir)
    count = 0
    for obj in app.get_children(True):
        if not getattr(obj, "has_textual_declaration", False):
            continue
        name = obj.get_name()
        text = obj.textual_declaration.text.replace("\r\n", "\n").rstrip() + "\n"
        if getattr(obj, "has_textual_implementation", False):
            impl = obj.textual_implementation.text.replace("\r\n", "\n").strip("\n")
            if impl:
                text += "\n" + impl + "\n"
        write_text(os.path.join(st_dir, name + ".st"), text)
        log("EXPORT %s" % name)
        count += 1
    log("%d 個のオブジェクトを書き出しました" % count)


def main():
    mode = os.environ.get("ST_SYNC_MODE", "").lower()
    project_path = os.environ.get("ST_SYNC_PROJECT", "")
    st_dir = os.environ.get("ST_SYNC_DIR", "")
    if mode not in ("import", "export") or not project_path or not st_dir:
        log("ERROR ST_SYNC_MODE / ST_SYNC_PROJECT / ST_SYNC_DIR を設定してください")
        return 1

    project_path = os.path.abspath(project_path)
    st_dir = os.path.abspath(st_dir)
    log("mode=%s project=%s dir=%s" % (mode, project_path, st_dir))

    proj = projects.primary
    if proj is None or os.path.abspath(proj.path).lower() != project_path.lower():
        if proj is not None:
            proj.close()
        proj = projects.open(project_path)
    app = proj.active_application

    if mode == "import":
        do_import(proj, app, st_dir)
    else:
        do_export(app, st_dir)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # CODESYS の --noUI では例外が見えにくいのでログに残す
        log("ERROR %s: %s" % (type(e).__name__, e))
        code = 1
    log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv else __file__)), "st_sync.log")
    try:
        write_text(log_path, "\n".join(LOG_LINES) + "\n")
    except Exception:
        pass
