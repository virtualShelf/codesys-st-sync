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


class Obj:
    def __init__(self, data):
        self.data = data
        self.has_textual_declaration = True
        self.has_textual_implementation = data["impl"] is not None
        self.textual_declaration = Text(data, "decl")
        self.textual_implementation = Text(data, "impl")
    def get_name(self): return self.data["name"]


class App:
    def __init__(self, objs): self.objs = objs
    def find(self, name, rec): return [Obj(o) for o in self.objs if o["name"] == name]
    def get_children(self, rec): return [Obj(o) for o in self.objs]
    def _add(self, name, impl):
        o = {"name": name, "decl": "", "impl": "" if impl else None}
        self.objs.append(o); return Obj(o)
    def create_pou(self, name, t, return_type=None): return self._add(name, True)
    def create_dut(self, name, t, *a): return self._add(name, False)
    def create_gvl(self, name): return self._add(name, False)


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
