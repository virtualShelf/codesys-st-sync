(* ============================================================
   GVL_Modbus : Modbus TCP Slave の I/O マッピング先
   ・aToTS   → ModbusTCP Slave の「Input Registers」(%QW) にマッピング
   ・aFromTS → ModbusTCP Slave の「Holding Registers」(%IW) にマッピング
   ============================================================ *)
VAR_GLOBAL
    aToTS   : ARRAY[0..9] OF WORD;  (* PLC → TypeScript  (FC04 Read Input Registers) *)
    aFromTS : ARRAY[0..9] OF WORD;  (* TypeScript → PLC  (FC06 Write Single Register) *)
END_VAR
