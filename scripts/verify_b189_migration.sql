-- 批189 M1 迁移后断言（V1–V10；V12 Redis 由发布脚本留证）
\set ON_ERROR_STOP on
DO $$
DECLARE n int; v_val bigint; v_txt text;
BEGIN
  SELECT val INTO v_val FROM b189_counts WHERE k='md_n';
  SELECT count(*) INTO n FROM market_interface;
  IF n<>v_val THEN RAISE EXCEPTION 'V1 md % <> %', n, v_val; END IF;

  SELECT val INTO v_val FROM b189_counts WHERE k='td_n';
  SELECT count(*) INTO n FROM trade_interface;
  IF n<>v_val THEN RAISE EXCEPTION 'V1 td % <> %', n, v_val; END IF;

  SELECT count(*) INTO n FROM (SELECT code FROM market_interface UNION ALL SELECT code FROM trade_interface) x
   WHERE code !~ '^[a-z][a-z0-9-]{1,30}$';
  IF n>0 THEN RAISE EXCEPTION 'V2 非法 code %', n; END IF;

  SELECT val INTO v_val FROM b189_counts WHERE k='live_task_nn';
  SELECT count(*) INTO n FROM live_task WHERE account_code IS NOT NULL;
  IF n<>v_val THEN RAISE EXCEPTION 'V3 live_task % <> %', n, v_val; END IF;

  SELECT val INTO v_val FROM b189_counts WHERE k='account_snapshot_nn';
  SELECT count(*) INTO n FROM account_snapshot WHERE account_code IS NOT NULL;
  IF n<>v_val THEN RAISE EXCEPTION 'V3 account_snapshot % <> %', n, v_val; END IF;

  SELECT val INTO v_val FROM b189_counts WHERE k='order_log_nn';
  SELECT count(*) INTO n FROM order_log WHERE account_code IS NOT NULL;
  IF n<>v_val THEN RAISE EXCEPTION 'V3 order_log % <> %', n, v_val; END IF;

  SELECT count(*) INTO n FROM live_task WHERE account_code IS NOT NULL
    AND account_code NOT IN (SELECT code FROM trade_interface);
  IF n>0 THEN RAISE EXCEPTION 'V4 live_task 孤儿 %', n; END IF;

  SELECT count(*) INTO n FROM order_log WHERE account_code IS NOT NULL
    AND account_code NOT IN (SELECT code FROM trade_interface);
  IF n>0 THEN RAISE EXCEPTION 'V4 order_log 孤儿 %', n; END IF;

  SELECT count(*) INTO n FROM account_permission p
    WHERE p.login_key NOT IN (SELECT login_key FROM trade_interface
                              UNION SELECT login_key FROM market_interface);
  IF n>0 THEN RAISE EXCEPTION 'V4 权限孤儿 %', n; END IF;

  SELECT val INTO v_val FROM b189_counts WHERE k='usage_matched';
  SELECT count(*) INTO n FROM data_source_usage WHERE interface_code IS NOT NULL;
  IF n<>v_val THEN RAISE EXCEPTION 'V5 usage % <> %', n, v_val; END IF;

  SELECT count(*) INTO n FROM b189_td_mapping_expect e
    WHERE NOT EXISTS (SELECT 1 FROM td_md_map m WHERE m.td_code=e.td_code);
  IF n>0 THEN RAISE EXCEPTION 'V6 应有映射缺失 %', n; END IF;

  SELECT count(*) INTO n FROM td_md_map m JOIN trade_interface t ON t.code=m.td_code WHERE t.is_virtual;
  IF n>0 THEN RAISE EXCEPTION 'V6 虚拟行有映射 %', n; END IF;

  SELECT count(*) INTO n FROM (
    VALUES ('fk_live_task_account','r'),('fk_position_snapshot_account','c'),
           ('fk_position_refresh_account','c'),('fk_account_snapshot_account','c'),
           ('fk_order_log_account','n'),('fk_trade_log_account','n'),
           ('fk_dsu_interface_code','n')) e(name, act)
   WHERE NOT EXISTS (SELECT 1 FROM pg_constraint c
                     WHERE c.conname=e.name AND c.contype='f' AND c.confdeltype::text=e.act);
  IF n>0 THEN RAISE EXCEPTION 'V7 FK 语义不符 %', n; END IF;

  SELECT count(*) INTO n FROM pg_trigger
   WHERE tgname='trg_live_task_account_guard'
     AND pg_get_triggerdef(oid) LIKE '%account_code%'
     AND pg_get_triggerdef(oid) LIKE '%UPDATE OF account_code, mode%';
  IF n<>1 THEN RAISE EXCEPTION 'V8 触发器不符'; END IF;

  IF to_regclass('trading_account') IS NOT NULL OR to_regclass('data_source_old') IS NOT NULL THEN
    RAISE EXCEPTION 'V9 旧表未删'; END IF;
  SELECT count(*) INTO n FROM information_schema.columns
   WHERE table_schema='public' AND column_name IN ('account_id','interface_id','quote_account_id')
     AND table_name NOT LIKE 'b189_%';
  IF n>0 THEN RAISE EXCEPTION 'V9 旧列残留 %', n; END IF;

  SELECT string_agg(a.attname, ',' ORDER BY a.attnum) INTO v_txt
    FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
   WHERE c.conrelid='provider_session_slot'::regclass AND c.contype='p';
  IF v_txt<>'login_key,provider,role,terminal_id' THEN RAISE EXCEPTION 'V10 slot PK=%', v_txt; END IF;

  RAISE NOTICE 'V1-V10 全绿';
END $$;
