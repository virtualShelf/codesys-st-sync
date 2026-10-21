# codesys-st-sync

フォルダの `.st` ファイルと CODESYS プロジェクトの中身を、コピペなしで同期する道具（CODESYS Scripting を使用）。
どのリポジトリからでも、プロジェクトのパスと ST フォルダを指定して使える。

- **export**：プロジェクト → ST フォルダ（`*.st` に書き出す）
- **import**：ST フォルダ → プロジェクト（POU / DUT / GVL を作成・上書きして保存）

対象は ST で書けるオブジェクト（PROGRAM / FUNCTION_BLOCK / FUNCTION / DUT / GVL）だけ。FB のメソッド・アクションやデバイスツリーの設定は対象外。

どちらの方法でも、**実行前に CODESYS の画面でプロジェクトを閉じる**（開いたままだとロックされて失敗する）。

## 画面版（Streamlit）

```powershell
pip install -r requirements.txt   # 最初の1回だけ
st-sync-ui.cmd                    # またはダブルクリック
```

ブラウザに画面が開く（初回だけターミナルでメールアドレスを聞かれることがあるが、Enter で飛ばせる）。

- CODESYS.exe とプロファイルの初期値は `st-sync.cmd` の値。プロジェクトは空なので、毎回「参照…」かパスの入力で指定する
- 「ST フォルダ」がエクスポート先とインポート元を兼ねる。初期値はプロジェクトファイルと同じ階層の `src` フォルダ（プロジェクトを指定すると自動で入る。無ければエクスポート時に作成）。手で書き換えた場合は、プロジェクトを変えても上書きしない
- インポートは ST フォルダの `.st` を読む。フォルダや `.st` が無いうちは押せない
- 「エクスポート」「インポート」ボタンで実行。インポートはプロジェクトを上書きするので、確認のチェックを入れると押せる
- 実行後、ログと ST フォルダの Git の変更（差分）が表示される。リポジトリは ST フォルダから自動で探す。コミットは自分で行う
- CODESYS でプロジェクトを開いたままだと、ボタンが押せない（ロックファイルを検出）

## コマンド版

1. `st-sync.cmd` の先頭2行を自分の環境に合わせて書き換える
   - `CODESYS_EXE` : CODESYS.exe のパス
   - `CODESYS_PROFILE` : プロファイル名（`CODESYS\Profiles` フォルダ内の `.profile.xml` のファイル名から拡張子を除いたもの）
2. プロジェクトのパスと ST フォルダを引数で渡して実行する

```bat
st-sync.cmd export "<プロジェクト>.project" "<ST フォルダ>"   :: プロジェクト → ST フォルダに書き出し
st-sync.cmd import "<プロジェクト>.project" "<ST フォルダ>"   :: ST フォルダ → プロジェクトに反映して保存
```

引数が足りないときは使い方を表示して終わる。結果は `src\st_sync.log` にも残る。

## ほかのリポジトリから使う

`codesys\<名前>.project` と、同じ階層の `codesys\src\*.st` という構成にしておくと、画面版ではプロジェクトを指定するだけで ST フォルダ（`codesys\src`）が自動で入る。

**codesys-elevator**

- 画面版：プロジェクトファイルに `...\codesys-elevator\codesys\Elevator.project` を指定する（ST フォルダは `...\codesys-elevator\codesys\src`）
- コマンド版：

```bat
cd C:\path\to\Projects   :: 3つのリポジトリを並べて置いたフォルダ
codesys-st-sync\st-sync.cmd export "codesys-elevator\codesys\Elevator.project" "codesys-elevator\codesys\src"
codesys-st-sync\st-sync.cmd import "codesys-elevator\codesys\Elevator.project" "codesys-elevator\codesys\src"
```

**codesys-ts-monitor**

ST は `codesys-ts-monitor\codesys\src`。CODESYS プロジェクトを `codesys-ts-monitor\codesys\` に置けば、画面版ではプロジェクトを指定するだけで済む。プロジェクトが別の場所にある場合は、「ST フォルダ」を `codesys-ts-monitor\codesys\src` に書き換えるか、コマンド版で指定する。

```bat
codesys-st-sync\st-sync.cmd export "C:\...\ModbusTCP.project" "codesys-ts-monitor\codesys\src"
```

## テスト

```powershell
python -m unittest discover -s tests -v                                              # 偽 CODESYS でのテスト（CODESYS 不要）
python tests\check_real_export.py "C:\...\YourProject.project" "C:\...\codesys\src"   # 本物の CODESYS で export だけ確認
```

- `tests/fake_codesys.py` が CODESYS Scripting の API を真似て、`st_sync.py` を実際に動かす
- テスト用の ST は `tests/fixtures/`
- 画面のテスト（`tests/test_st_sync_ui.py`）は Streamlit の AppTest を使う。streamlit が無いとスキップ
- `check_real_export.py` はプロジェクトを変更しない（一時フォルダに書き出して、指定した ST フォルダと比較）

## ファイル

```
codesys-st-sync\
  st-sync-ui.cmd        画面を開く（ダブルクリック）
  st-sync.cmd           コマンド版。CODESYS.exe とプロファイルの設定もここ
  requirements.txt      画面版に必要な Python パッケージ
  src\
    st_sync_ui.py       画面（Streamlit）
    st_sync_runner.py   画面から CODESYS を起動して結果を判定する処理
    st_sync.py          CODESYS 内で動く同期スクリプト（CODESYS Scripting）
  tests\               偽 CODESYS でのテスト、画面のテスト、本物の CODESYS での確認
    fixtures\          テスト用の ST
```

使う人が触るのはルートの `.cmd` 2つだけ。中身の Python は `src\` にまとめてある。
