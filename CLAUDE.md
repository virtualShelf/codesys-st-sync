# codesys-st-sync

フォルダの `.st` ファイルと CODESYS プロジェクトを import / export で同期する道具。どのリポジトリからでも使う。

## 構成

- `st_sync.py` : CODESYS 内で動く本体（CODESYS Scripting。CODESYS の Python で動くので、新しい構文を使わない）
- `st_sync_runner.py` : 画面版のロジック（CODESYS の起動、実行前チェック、結果の判定、Git の差分）
- `st_sync_ui.py` / `st-sync-ui.cmd` : 画面版（Streamlit）
- `st-sync.cmd` : コマンド版。`st-sync.cmd import|export "<project>" "<ST フォルダ>"`。CODESYS.exe とプロファイルは先頭で設定
- `tests/` : 偽 CODESYS（`fake_codesys.py`）でのテスト。テスト用の ST は `tests/fixtures/`

## 検証

- 変更したら `python -m unittest discover -s tests -v` を実行し、全部通るまで直す（偽 CODESYS を使うので CODESYS は不要）
- 画面のテストには streamlit が必要（`pip install -r requirements.txt`）。入っていないとスキップされるので、スキップを合格扱いにしない
- 本物の CODESYS での確認は `python tests/check_real_export.py "<.project のパス>" "<ST フォルダ>"`。エクスポートだけを一時フォルダに出して ST フォルダと比べる（プロジェクトは変更しない）。実行前に CODESYS でプロジェクトを閉じてもらう
- 本物のプロジェクトへの import は、ユーザーに確認してから行う

## 守ること

- 特定のリポジトリの構成を前提にしない。プロジェクトと ST フォルダは、画面の入力か `st-sync.cmd` の引数で受け取る
- 画面版の ST フォルダの初期値は「プロジェクトと同じ階層の `src`」（`codesys\<名前>.project` と `codesys\src` の構成に合わせる）

## Git

- 変更がひとまとまり終わったら、その単位でコミットする（1コミット = 1つの変更理由）
- コミットメッセージは日本語で、1行目に「何を・なぜ」を短く書く（例: `st_sync: FB のメソッドも書き出すように変更`）
- push は頼まれたときだけ
