# -*- coding: utf-8 -*-
"""
st_sync.py : ST フォルダの *.st と CODESYS プロジェクトを同期する（CODESYS Scripting 用）

CODESYS.exe の --runscript で実行する。直接 python で動かすものではない。
設定は環境変数で受け取る（st-sync.cmd / st_sync_runner.py がセットする）:
  ST_SYNC_MODE     import | export
  ST_SYNC_PROJECT  .project ファイルのパス
  ST_SYNC_DIR      .st を置くフォルダ（ST フォルダ）

import : ST フォルダの *.st（サブフォルダも含む）の内容でプロジェクトの POU / DUT / GVL を
         作成・上書きして保存
export : プロジェクト内のテキスト（ST）オブジェクトを ST フォルダの *.st に書き出す

オブジェクトは種類ごとのフォルダにまとめる（FOLDERS の表）。ST フォルダ側も CODESYS 側も同じ名前:
  DUTs / GVLs / ITFs / FCs / FBs / PRGs
  import : CODESYS の Application の下にフォルダを作り、オブジェクトを入れる（別の場所にあれば移す）
  export : ST フォルダの <種類のフォルダ>/<名前>.st に書き出す。古い場所の同じ名前の .st は消す

対応しているのは ST のテキストで書けるオブジェクトだけ
（PROGRAM / FUNCTION_BLOCK / FUNCTION / TYPE（列挙型・構造体・共用体・別名）/ VAR_GLOBAL / INTERFACE）。
FUNCTION_BLOCK / PROGRAM / INTERFACE は、メソッド・プロパティ（Get / Set）・アクションも
1つのファイルにまとめる（METHOD … END_METHOD、PROPERTY … END_PROPERTY、ACTION … END_ACTION）。
ラダーや SFC などのグラフィカル言語は対象外。
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
    ("TYPE", re.compile(r"^\s*TYPE\s+(\w+)\s*(?:EXTENDS\s+[\w.]+\s*)?:", re.I | re.M)),
    ("INTERFACE", re.compile(r"^\s*INTERFACE\s+(\w+)", re.I | re.M)),
    ("GVL", re.compile(r"^\s*VAR_GLOBAL\b", re.I | re.M)),
]


def strip_comments(text):
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return _PRAGMA.sub(" ", text)


def detect(text, file_stem):
    """(kind, name, extra) を返す。kind は PROGRAM / FUNCTION_BLOCK / FUNCTION / TYPE / GVL / INTERFACE"""
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
            if re.match(r"^\s*(PROGRAM|FUNCTION_BLOCK|FUNCTION|METHOD)\b", line, re.I):
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
# メンバー（メソッド・プロパティ・アクション）を1つのファイルにまとめる書き方
#
#   FUNCTION_BLOCK / PROGRAM / INTERFACE の .st の後ろに、次のブロックを続けて書く
#     METHOD 名前 : 型 … END_METHOD           （宣言と中身。インターフェースは宣言だけ）
#     PROPERTY 名前 : 型 GET … END_GET SET … END_SET END_PROPERTY
#     ACTION 名前 … END_ACTION                 （中身だけ。FB / PRG のみ）
#   最後に END_FUNCTION_BLOCK / END_PROGRAM / END_INTERFACE を書く
#   （FB / PRG はメンバーがあるときだけ。インターフェースはいつも書く）
# ---------------------------------------------------------------------------
_MEMBER_START = re.compile(
    r"^\s*(METHOD|PROPERTY|ACTION)\s+(?:(?:PUBLIC|PRIVATE|PROTECTED|INTERNAL|ABSTRACT|FINAL)\s+)*"
    r"(\w+)\s*(?::\s*(.+?))?\s*;?\s*$", re.I)
_END_LINE = re.compile(
    r"^\s*END_(METHOD|PROPERTY|ACTION|GET|SET|INTERFACE|FUNCTION_BLOCK|PROGRAM)\s*;?\s*$", re.I)
_ACCESSOR_START = re.compile(r"^\s*(GET|SET)\s*$", re.I)
_CONTAINER_END = ("INTERFACE", "FUNCTION_BLOCK", "PROGRAM")
ACCESSORS = ("Get", "Set")


def _clean(lines):
    text = "\n".join(lines).strip("\n")
    return text + "\n" if text.strip() else ""


def _last_end_var(code_lines):
    cut = -1
    for i, line in enumerate(code_lines):
        if re.match(r"^\s*END_VAR\s*;?\s*$", line, re.I):
            cut = i
    return cut


def split_members(text):
    """FB / PRG / INTERFACE のファイルを (本体, メンバーの生の行, END_xxx があったか) に分ける。
    メンバーの生の行は {"kind", "name", "type", "lines", "code"} のリスト"""
    lines = text.replace("\r\n", "\n").split("\n")
    code_lines = _strip_keep_lines("\n".join(lines)).split("\n")
    body, members, current, has_end = [], [], None, False
    for line, code in zip(lines, code_lines):
        if current is None:
            m = _MEMBER_START.match(code)
            if m:
                current = {"kind": m.group(1).upper(), "name": m.group(2),
                           "type": (m.group(3) or "").strip() or None, "lines": [line], "code": [code]}
                continue
            e = _END_LINE.match(code)
            if e and e.group(1).upper() in _CONTAINER_END:
                has_end = True
                continue
            body.append(line)
        else:
            e = _END_LINE.match(code)
            if e and e.group(1).upper() == current["kind"]:
                members.append(current)
                current = None
            else:
                current["lines"].append(line)
                current["code"].append(code)
    if current is not None:  # END_METHOD などを書き忘れても、最後まで1つのメンバーとして扱う
        members.append(current)
    return _clean(body), members, has_end


def _parse_accessor(lines, code):
    """GET / SET の中身を (宣言, 中身) に分ける。宣言（VAR … END_VAR）が無ければ宣言は None（触らない）"""
    cut = _last_end_var(code)
    if cut < 0:
        return None, _clean(lines)
    return _clean(lines[:cut + 1]), _clean(lines[cut + 1:])


def parse_member(raw, in_interface):
    """split_members のメンバー1つを、CODESYS に入れる形にする"""
    kind, lines, code = raw["kind"], raw["lines"], raw["code"]
    member = {"kind": kind, "name": raw["name"], "type": raw["type"],
              "decl": None, "impl": None, "accessors": None}
    if kind == "METHOD":
        if in_interface:
            member["decl"] = _clean(lines)
        else:
            cut = _last_end_var(code)
            cut = cut if cut >= 0 else 0  # VAR が無いメソッドは、METHOD の行だけが宣言
            member["decl"] = _clean(lines[:cut + 1])
            member["impl"] = _clean(lines[cut + 1:])
    elif kind == "ACTION":
        member["impl"] = _clean(lines[1:])
    else:  # PROPERTY
        header, accessors, current = [], {}, None
        acc_lines, acc_code = [], []
        for line, c in zip(lines, code):
            if current is None:
                a = _ACCESSOR_START.match(c)
                if a:
                    current, acc_lines, acc_code = a.group(1).capitalize(), [], []
                else:
                    header.append(line)
            else:
                e = _END_LINE.match(c)
                if e and e.group(1).capitalize() == current:
                    accessors[current] = _parse_accessor(acc_lines, acc_code)
                    current = None
                else:
                    acc_lines.append(line)
                    acc_code.append(c)
        if current is not None:
            accessors[current] = _parse_accessor(acc_lines, acc_code)
        member["decl"] = _clean(header)
        member["accessors"] = accessors or None  # GET / SET を書いていない古い形なら、触らない
    return member


def _is_empty_var(decl):
    """CODESYS が作る既定の宣言（VAR と END_VAR だけ）か。書き出すときは省く"""
    code = [l.strip().upper() for l in strip_comments(decl or "").split("\n") if l.strip()]
    return code in ([], ["VAR", "END_VAR"])


def render_member(m):
    kind = m["kind"]
    if kind == "ACTION":
        out = "ACTION %s\n" % m["name"]
        if (m["impl"] or "").strip():
            out += m["impl"].strip("\n") + "\n"
        return out + "END_ACTION\n"
    out = m["decl"].rstrip() + "\n"
    if kind == "METHOD":
        if (m["impl"] or "").strip():
            out += "\n" + m["impl"].strip("\n") + "\n"
        return out + "END_METHOD\n"
    for acc in ACCESSORS:  # PROPERTY
        if acc not in (m["accessors"] or {}):
            continue
        adecl, aimpl = m["accessors"][acc]
        out += acc.upper() + "\n"
        if adecl and not _is_empty_var(adecl):
            out += adecl.rstrip() + "\n"
        if (aimpl or "").strip():
            out += aimpl.strip("\n") + "\n"
        out += "END_" + acc.upper() + "\n"
    return out + "END_PROPERTY\n"


def join_members(body, members, end_kind):
    """split_members の逆。end_kind が None なら END_xxx を書かない"""
    parts = [body.rstrip() + "\n"] + [render_member(m) for m in members]
    if end_kind:
        parts.append("END_" + end_kind + "\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CODESYS とのやり取り
# ---------------------------------------------------------------------------
# 作る順番。FB が IMPLEMENTS するインターフェースは、FB より先に作る
_ORDER = {"TYPE": 0, "GVL": 1, "INTERFACE": 2, "FUNCTION": 3, "FUNCTION_BLOCK": 4, "PROGRAM": 5}

# 種類ごとのフォルダ名（ST フォルダのサブフォルダ名 = CODESYS の Application の下のフォルダ名）
FOLDERS = {
    "TYPE": "DUTs",
    "GVL": "GVLs",
    "INTERFACE": "ITFs",
    "FUNCTION": "FCs",
    "FUNCTION_BLOCK": "FBs",
    "PROGRAM": "PRGs",
}


def list_st_files(st_dir):
    """ST フォルダの *.st を、サブフォルダも含めて返す（パスの順）"""
    found = []
    for root, dirs, files in os.walk(st_dir):
        dirs.sort()
        for fn in sorted(files):
            if fn.lower().endswith(".st"):
                found.append(os.path.join(root, fn))
    return found


def find_folder(app, name):
    for child in app.get_children(False):
        if getattr(child, "is_folder", False) and child.get_name() == name:
            return child
    return None


def get_folder(app, name):
    """Application の直下のフォルダを返す。無ければ作る"""
    folder = find_folder(app, name)
    if folder is not None:
        return folder
    log("FOLDER %s を作成" % name)
    created = app.create_folder(name)
    # 本物の CODESYS の create_folder() は、作ったフォルダを返さない（None）ので探し直す
    folder = created if created is not None else find_folder(app, name)
    if folder is None:
        raise RuntimeError("フォルダ %s を作れませんでした" % name)
    return folder


def in_folder(obj, folder_name):
    parent = obj.parent
    return parent is not None and getattr(parent, "is_folder", False) and parent.get_name() == folder_name


def is_member(obj):
    """メソッド・プロパティなど、POU やインターフェースの中にあるオブジェクトか"""
    parent = obj.parent
    return parent is not None and getattr(parent, "has_textual_declaration", False)


def find_object(app, name):
    for obj in app.find(name, True):
        if obj.has_textual_declaration and not is_member(obj):
            return obj
    return None


def _has_decl(obj):
    return getattr(obj, "has_textual_declaration", False)


def _has_impl(obj):
    return getattr(obj, "has_textual_implementation", False)


def _text(t):
    return t.text.replace("\r\n", "\n")


def member_kind(obj):
    """メンバーの種類（METHOD / PROPERTY / ACTION）。宣言の先頭で見分け、宣言が無く中身だけならアクション"""
    if _has_decl(obj):
        for line in strip_comments(obj.textual_declaration.text).split("\n"):
            if line.strip():
                m = _MEMBER_START.match(line)
                if m and m.group(1).upper() != "ACTION":
                    return m.group(1).upper()
                break
    if _has_impl(obj):
        return "ACTION"
    return None


def _member_children(owner):
    return [c for c in owner.get_children(False)
            if not getattr(c, "is_folder", False) and (_has_decl(c) or _has_impl(c))]


def collect_members(owner, in_interface):
    """CODESYS のメンバーを、render_member で書ける形にして集める"""
    members = []
    for child in _member_children(owner):
        kind = member_kind(child)
        if kind is None:
            log("SKIP   %s.%s : メソッド・プロパティ・アクション以外は対象外" % (owner.get_name(), child.get_name()))
            continue
        m = {"kind": kind, "name": child.get_name(), "type": None,
             "decl": _text(child.textual_declaration) if _has_decl(child) else None,
             "impl": _text(child.textual_implementation) if _has_impl(child) and not in_interface else None,
             "accessors": None}
        if kind == "PROPERTY":
            m["accessors"] = {}
            for acc in child.get_children(False):
                name = acc.get_name().capitalize()
                if name in ACCESSORS:
                    m["accessors"][name] = (
                        _text(acc.textual_declaration) if _has_decl(acc) else None,
                        _text(acc.textual_implementation) if _has_impl(acc) and not in_interface else None)
        members.append(m)
    return members


def _create_member(owner, m):
    try:
        if m["kind"] == "METHOD":
            return owner.create_method(m["name"], m["type"]) if m["type"] else owner.create_method(m["name"])
        if m["kind"] == "PROPERTY":
            return owner.create_property(m["name"], m["type"] or "BOOL")
        return owner.create_action(m["name"])
    except Exception as e:
        # ADD / SUB などの標準ファンクション名や予約語は、メソッド名にできない
        raise RuntimeError("%s.%s を作れません（%s）。名前が予約語や標準ファンクション"
                           "（ADD, SUB, MOVE など）と重なっていないか確認してください"
                           % (owner.get_name(), m["name"], e))


def _find_child(owner, name):
    for c in owner.get_children(False):
        if c.get_name().lower() == name.lower():
            return c
    return None


def sync_accessors(prop, accessors, in_interface, label):
    """プロパティの Get / Set を、ファイルの内容に合わせる"""
    for acc in ACCESSORS:
        child = _find_child(prop, acc)
        if acc in accessors:
            if child is None:
                creator = getattr(prop, "create_%s_accessor" % acc.lower(), None)
                if creator is None:
                    log("WARN   %s の %s を作れませんでした。CODESYS の画面で追加してください" % (label, acc))
                    continue
                child = creator() or _find_child(prop, acc)
                log("  CREATE %s.%s" % (label, acc))
            adecl, aimpl = accessors[acc]
            if adecl is not None and _has_decl(child):
                child.textual_declaration.replace(adecl)
            if aimpl is not None and _has_impl(child) and not in_interface:
                child.textual_implementation.replace(aimpl)
        elif child is not None:
            log("  DELETE %s.%s" % (label, acc))
            child.remove()


def sync_members(owner, members, in_interface, delete_missing):
    """メソッド・プロパティ・アクションを、ファイルの内容に合わせる（作成・上書き・削除）"""
    existing = dict((c.get_name().lower(), c) for c in _member_children(owner))
    for m in members:
        obj = existing.pop(m["name"].lower(), None)
        action = "UPDATE"
        if obj is None:
            action = "CREATE"
            obj = _create_member(owner, m)
        label = "%s.%s" % (owner.get_name(), m["name"])
        log("  %-6s %s (%s)" % (action, label, m["kind"]))
        if m["decl"] is not None and _has_decl(obj):
            obj.textual_declaration.replace(m["decl"])
        if m["impl"] is not None and _has_impl(obj) and not in_interface:
            obj.textual_implementation.replace(m["impl"])
        if m["kind"] == "PROPERTY" and m["accessors"] is not None:
            sync_accessors(obj, m["accessors"], in_interface, label)
    for name, obj in existing.items():
        if delete_missing:
            log("  DELETE %s.%s" % (owner.get_name(), obj.get_name()))  # ファイルから消したメンバー
            obj.remove()
        else:
            # メンバーを書いていない古い形のファイル。消さずに残す（次の export でファイルに入る）
            log("  KEEP   %s.%s（ファイルに無いので、そのまま残します）" % (owner.get_name(), obj.get_name()))


_CREATE_METHOD = {"TYPE": "create_dut", "GVL": "create_gvl", "INTERFACE": "create_interface",
                  "PROGRAM": "create_pou", "FUNCTION_BLOCK": "create_pou", "FUNCTION": "create_pou"}


def create_object(container, kind, name, extra):
    """container（Application かフォルダ）の中に、種類に合ったオブジェクトを作る"""
    if kind == "TYPE":
        dut_type = getattr(DutType, extra)
        if extra == "Alias":
            return container.create_dut(name, dut_type, "INT")  # 中身はあとで宣言ごと置き換える
        return container.create_dut(name, dut_type)
    if kind == "GVL":
        return container.create_gvl(name)
    if kind == "INTERFACE":
        return container.create_interface(name)
    if kind == "PROGRAM":
        return container.create_pou(name, PouType.Program)
    if kind == "FUNCTION_BLOCK":
        return container.create_pou(name, PouType.FunctionBlock)
    return container.create_pou(name, PouType.Function, return_type=extra or "BOOL")


def try_move(obj, folder, name):
    """オブジェクトをフォルダへ移す。移せなければ警告だけ出して続ける（中身の更新は行う）

    CODESYS の move() は、CODESYS 内の Python（IronPython）から呼ぶと
    「Multiple targets could match」になる版があるため、呼び方を変えて試す"""
    for call in (lambda: obj.move(folder), lambda: obj.move(folder, -1)):
        try:
            call()
            return True
        except TypeError:
            pass
    log("WARN   %s を %s に移せませんでした。CODESYS の画面でフォルダへドラッグしてください"
        "（次からはその場所のまま更新します）" % (name, folder.get_name()))
    return False


def do_import(proj, app, st_dir):
    items = []
    seen = {}
    for path in list_st_files(st_dir):
        rel = os.path.relpath(path, st_dir)
        text = read_text(path)
        info = detect(text, os.path.splitext(os.path.basename(path))[0])
        if info is None:
            log("SKIP  %s : 種類を判別できない" % rel)
            continue
        if info[1].lower() in seen:
            # 同じ名前が2つあると、どちらが正しいか分からない。プロジェクトは変更しない
            log("ERROR %s が2つあります: %s と %s" % (info[1], seen[info[1].lower()], rel))
            return
        seen[info[1].lower()] = rel
        items.append((_ORDER[info[0]], rel, text, info))
    items.sort(key=lambda x: (x[0], x[1]))

    for _, fn, text, (kind, name, extra) in items:
        folder_name = FOLDERS[kind]
        obj = find_object(app, name)
        action = "UPDATE"
        if obj is None:
            action = "CREATE"
            folder = get_folder(app, folder_name)
            if hasattr(folder, _CREATE_METHOD[kind]):
                obj = create_object(folder, kind, name, extra)   # はじめからフォルダの中に作る
            else:
                obj = create_object(app, kind, name, extra)      # フォルダに作れない版の CODESYS 向け
                try_move(obj, folder, name)
        elif not in_folder(obj, folder_name):
            if try_move(obj, get_folder(app, folder_name), name):
                log("MOVE   %s → %s" % (name, folder_name))

        if kind in ("TYPE", "GVL"):
            obj.textual_declaration.replace(text)
        elif kind == "INTERFACE":
            header, raw, _ = split_members(text)
            obj.textual_declaration.replace(header)
            log("%-6s %s (%s → %s)" % (action, name, kind, folder_name))
            sync_members(obj, [parse_member(r, True) for r in raw], True, True)
            continue
        elif kind in ("FUNCTION_BLOCK", "PROGRAM"):
            body, raw, has_end = split_members(text)
            decl, impl = split_pou(body)
            obj.textual_declaration.replace(decl)
            if obj.has_textual_implementation:
                obj.textual_implementation.replace(impl)
            log("%-6s %s (%s → %s)" % (action, name, kind, folder_name))
            # END_FUNCTION_BLOCK / END_PROGRAM かメンバーがある新しい形のときだけ、ファイルに無いメンバーを消す
            sync_members(obj, [parse_member(r, False) for r in raw], False, has_end or bool(raw))
            continue
        else:
            decl, impl = split_pou(text)
            obj.textual_declaration.replace(decl)
            if obj.has_textual_implementation:
                obj.textual_implementation.replace(impl)
        log("%-6s %s (%s → %s)" % (action, name, kind, folder_name))

    proj.save()
    log("プロジェクトを保存しました")


def do_export(app, st_dir):
    if not os.path.isdir(st_dir):
        os.makedirs(st_dir)
    existing = list_st_files(st_dir)  # 書き出す前からあった .st（古い場所のものを片付けるため）
    count = 0
    for obj in app.get_children(True):
        if not getattr(obj, "has_textual_declaration", False):
            continue
        if is_member(obj):
            continue  # メソッドなどは親（インターフェース）と一緒に書き出す。FB のメソッドは対象外
        name = obj.get_name()
        text = obj.textual_declaration.text.replace("\r\n", "\n").rstrip() + "\n"
        if getattr(obj, "has_textual_implementation", False):
            impl = obj.textual_implementation.text.replace("\r\n", "\n").strip("\n")
            if impl:
                text += "\n" + impl + "\n"
        info = detect(text, name)
        if info is not None and info[0] == "INTERFACE":
            text = join_members(_text(obj.textual_declaration), collect_members(obj, True), "INTERFACE")
        elif info is not None and info[0] in ("FUNCTION_BLOCK", "PROGRAM"):
            members = collect_members(obj, False)
            if members:
                text = join_members(text, members, info[0])
        if info is None:
            log("SKIP   %s : 対象外（メソッド・アクションなど）" % name)
            continue
        folder = os.path.join(st_dir, FOLDERS[info[0]])
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = os.path.join(folder, name + ".st")
        write_text(path, text)
        log("EXPORT %s → %s" % (name, FOLDERS[info[0]]))
        count += 1
        # 前に別の場所（フォルダ分けする前の直下など）に書き出した同じ名前の .st を消す
        for old in existing:
            if (os.path.splitext(os.path.basename(old))[0].lower() == name.lower()
                    and os.path.normcase(os.path.abspath(old)) != os.path.normcase(os.path.abspath(path))
                    and os.path.exists(old)):
                os.remove(old)
                log("REMOVE %s（%s に移動）" % (os.path.relpath(old, st_dir), os.path.relpath(path, st_dir)))
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
