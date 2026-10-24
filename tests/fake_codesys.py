#!/usr/bin/env python3
"""偽物の CODESYS.exe（テスト用）

--runscript の Python を、CODESYS Scripting の API を真似たオブジェクトを渡して実行する。
プロジェクトの中身は .project ファイル（JSON）に保存する。
受け付けるプロファイル名は環境変数 FAKE_CODESYS_PROFILE（既定: CODESYS V3.5 SP22 Patch 4）。
"""
import json, os, runpy, sys, types

args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
expected_profile = os.environ.get("FAKE_CODESYS_PROFILE", "CODESYS V3.5 SP22 Patch 4")
if args.get("--profile") != expected_profile:
    print("profile not found")  # プロファイルが違うときはスクリプトを動かさない想定
    sys.exit(1)
script = args["--runscript"]


class Text:
    def __init__(self, store, key): self.store, self.key = store, key
    @property
    def text(self): return self.store.get(self.key, "")
    def replace(self, t): self.store[self.key] = t


class Folder:
    """CODESYS の Application の下のフォルダ"""
    is_folder = True
    has_textual_declaration = False
    has_textual_implementation = False
    def __init__(self, app, name): self.app, self.name = app, name
    def get_name(self): return self.name
    @property
    def parent(self): return self.app
    # 本物と同じく、フォルダの中に直接オブジェクトを作れる
    def _in_here(self, obj): obj.data["folder"] = self.name; return obj
    def create_pou(self, name, t, return_type=None): return self._in_here(self.app.create_pou(name, t, return_type))
    def create_dut(self, name, t, *a): return self._in_here(self.app.create_dut(name, t, *a))
    def create_gvl(self, name): return self._in_here(self.app.create_gvl(name))
    def create_interface(self, name): return self._in_here(self.app.create_interface(name))


class Obj:
    """POU・DUT・GVL・インターフェースと、その中のメンバー（メソッド・プロパティ・アクション・Get/Set）。
    データは dict。"decl" が None なら宣言なし（アクション）、"impl" が None なら実装なし"""
    is_folder = False
    def __init__(self, app, data, owner=None):
        self.app = app
        self.data = data
        self.owner = owner  # メンバーなら、親（Obj）
        self.has_textual_declaration = data.get("decl") is not None
        self.has_textual_implementation = data["impl"] is not None
        self.textual_declaration = Text(data, "decl")
        self.textual_implementation = Text(data, "impl")
    def get_name(self): return self.data["name"]
    @property
    def parent(self):
        if self.owner is not None:
            return self.owner
        folder = self.data.get("folder")
        return Folder(self.app, folder) if folder else self.app
    def move(self, new_parent, index=None):
        if os.environ.get("FAKE_CODESYS_MOVE_AMBIGUOUS"):
            # 本物の CODESYS（IronPython）で起きたエラーの再現
            raise TypeError("Multiple targets could match: move(IExtendedObject[IScriptObject]), "
                            "move(IExtendedObject[IScriptProject])")
        self.data["folder"] = new_parent.get_name() if getattr(new_parent, "is_folder", False) else None
    # ---- メンバー ----
    def get_children(self, rec):
        children = [Obj(self.app, m, self) for m in self.data.get("members", [])]
        if rec:
            children += [g for c in list(children) for g in c.get_children(True)]
        return children
    def _has_body(self):
        """実装を持つ POU（FB / PRG）か。インターフェースのメンバーには実装がない"""
        o = self
        while o.owner is not None:
            o = o.owner
        return o.data["impl"] is not None
    def _add_member(self, m):
        self.data.setdefault("members", []).append(m)
        return Obj(self.app, m, self)
    def create_method(self, name, return_type=None):
        return self._add_member({"name": name, "member": "METHOD",
                                 "decl": "METHOD %s%s\n" % (name, " : " + return_type if return_type else ""),
                                 "impl": "" if self._has_body() else None})
    def create_property(self, name, return_type):
        # 本物と同じく、Get と Set を両方作る
        body = "" if self._has_body() else None
        return self._add_member({"name": name, "member": "PROPERTY",
                                 "decl": "PROPERTY %s : %s\n" % (name, return_type), "impl": None,
                                 "members": [{"name": "Get", "decl": "VAR\nEND_VAR\n", "impl": body},
                                             {"name": "Set", "decl": "VAR\nEND_VAR\n", "impl": body}]})
    def create_action(self, name):
        return self._add_member({"name": name, "member": "ACTION", "decl": None, "impl": ""})
    def _create_accessor(self, name):
        body = "" if self._has_body() else None
        self._add_member({"name": name, "decl": "VAR\nEND_VAR\n", "impl": body})
        return None  # 戻り値に頼らない（本物の create_folder と同じく None でも動くか確かめる）
    def create_get_accessor(self): return self._create_accessor("Get")
    def create_set_accessor(self): return self._create_accessor("Set")
    def remove(self):
        if self.owner is not None:
            self.owner.data["members"].remove(self.data)
        else:
            self.app.objs.remove(self.data)


class App:
    """プロジェクトの中身は JSON のリスト。フォルダは {"kind": "folder", "name": ...}、
    オブジェクトは {"name", "decl", "impl", "folder"}（folder は入っているフォルダ名。直下なら None）"""
    is_folder = False
    def __init__(self, objs): self.objs = objs
    def get_name(self): return "Application"
    parent = None
    def _items(self): return [o for o in self.objs if o.get("kind") != "folder"]
    def _folders(self): return [o["name"] for o in self.objs if o.get("kind") == "folder"]
    def find(self, name, rec): return [Obj(self, o) for o in self._items() if o["name"] == name]
    def get_children(self, rec):
        folders = [Folder(self, n) for n in self._folders()]
        items = [Obj(self, o) for o in self._items() if rec or not o.get("folder")]
        if rec:  # 本物と同じく、再帰ではメソッド・プロパティも返す
            items += [m for o in list(items) for m in o.get_children(True)]
        return folders + items
    def create_folder(self, name):
        if name in self._folders():
            raise ValueError("folder already exists: " + name)  # 本物も同じ名前は作れない
        self.objs.append({"kind": "folder", "name": name})
        return None  # 本物の CODESYS も、作ったフォルダを返さない
    def _add(self, name, impl):
        o = {"name": name, "decl": "", "impl": "" if impl else None, "folder": None}
        self.objs.append(o); return Obj(self, o)
    def create_pou(self, name, t, return_type=None): return self._add(name, True)
    def create_dut(self, name, t, *a): return self._add(name, False)
    def create_gvl(self, name): return self._add(name, False)
    def create_interface(self, name): return self._add(name, False)


class Proj:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as f: self.objs = json.load(f)
        self.active_application = App(self.objs)
    def save(self):
        with open(self.path, "w", encoding="utf-8") as f: json.dump(self.objs, f, ensure_ascii=False)
    def close(self): pass


class Projects:
    primary = None
    def open(self, path):
        self.primary = Proj(path); return self.primary


ns = types.SimpleNamespace
sys.argv = [script]
runpy.run_path(script, run_name="__main__", init_globals={
    "projects": Projects(),
    "PouType": ns(Program=1, FunctionBlock=2, Function=3),
    "DutType": ns(Enumeration=1, Structure=2, Union=3, Alias=4),
})
