(* ==========================================================================
   PLC_PRG
   目的 : Modbus 経由のデバッグ環境を試すためのテスト用状態遷移
          Idle → Run → Error（タイムアウト）→ Idle（bStart を落としてリセット）
   入力 : GVL_Modbus.aFromTS[0]   bStart（0/1）
   出力 : GVL_Modbus.aToTS[0..3]  状態・カウンタ・値・ハートビート
          （各レジスタの意味は README のレジスタマップ表を参照）
   履歴 : 2026-10-17  初版
          2026-10-17  codesys-st 規約を適用
                      - 状態を E_State（列挙型）に変更、CASE に ELSE を追加
                      - TON を毎サイクル呼ぶ形に変更
                      - マジックナンバーを定数化
                      - カウンタを DINT 化し、送信時に 16bit の範囲へ飽和
   ========================================================================== *)
PROGRAM PLC_PRG
VAR CONSTANT
    RUN_TIMEOUT : TIME := T#10S;   (* Run を続けられる上限。超えたら Error *)
    VALUE_STEP  : REAL := 0.1;     (* Run 中に 1 サイクルごとに足す量 *)
    VALUE_SCALE : REAL := 100.0;   (* REAL を INT レジスタに詰める倍率。config.ts の scale: 0.01 と対 *)
    INT_MAX     : DINT := 32767;   (* 符号付き 16bit レジスタに入る上限 *)
END_VAR
VAR
    bStart     : BOOL;
    eState     : E_State := E_State.Idle;
    nCounter   : DINT;             (* 停止→再開を繰り返すと累積するため、INT では溢れうる *)
    rValue     : REAL;
    wHeartbeat : WORD;
    tonRun     : TON;
END_VAR

(* ---- 入力 ---- *)
bStart := GVL_Modbus.aFromTS[0] <> 0;

(* ---- タイムアウト監視 ----
   毎サイクル呼ぶ。Run 以外では IN が FALSE になり、自動でリセットされる *)
tonRun(IN := eState = E_State.Run, PT := RUN_TIMEOUT);

(* ---- 状態遷移 ---- *)
CASE eState OF
    E_State.Idle:
        IF bStart THEN
            eState := E_State.Run;
        END_IF

    E_State.Run:
        nCounter := nCounter + 1;
        rValue   := rValue + VALUE_STEP;
        IF NOT bStart THEN
            eState := E_State.Idle;    (* 停止指令をタイムアウトより優先する *)
        ELSIF tonRun.Q THEN
            eState := E_State.Error;
        END_IF

    E_State.Error:
        IF NOT bStart THEN
            nCounter := 0;
            rValue   := 0.0;
            eState   := E_State.Idle;
        END_IF

ELSE
    (* 列挙外の値はメモリ破壊などの異常。安全側の Error に倒す *)
    eState := E_State.Error;
END_CASE

(* ---- 出力 ---- *)
wHeartbeat := wHeartbeat + 1;   (* 通信の生存確認用。WORD なので 65535 の次は 0 に戻ってよい *)

(* 16bit に入らない値を送ると TS 側で別の値に見えるので、範囲内に飽和させる *)
GVL_Modbus.aToTS[0] := INT_TO_WORD(eState);
GVL_Modbus.aToTS[1] := DINT_TO_WORD(LIMIT(0, nCounter, INT_MAX));
GVL_Modbus.aToTS[2] := DINT_TO_WORD(LIMIT(-INT_MAX, REAL_TO_DINT(rValue * VALUE_SCALE), INT_MAX));
GVL_Modbus.aToTS[3] := wHeartbeat;
