(* ==========================================================================
   E_State : PLC_PRG の状態
   値は Modbus の IR0 にそのまま載り、web/src/config.ts の label（Idle/Run/Error）と
   対応しているので、番号を変えないこと
   ========================================================================== *)
{attribute 'qualified_only'}   (* CODESYS 独自: E_State.Run の形でしか書けないようにする *)
TYPE E_State :
(
    Idle  := 0,
    Run   := 1,
    Error := 2
) INT;
END_TYPE
