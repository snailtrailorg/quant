#!/usr/bin/env python3
"""#35 全链路集成验证脚本（服务器端运行）。

验证 strategy_runner XTP + 飞书模块可导入 + 配置完整。
跑在服务器（不需要交易时段，仅验证模块加载和配置存在性）。

用法：
  cd /data/websites/snailtrail.cc/quant/server
  LD_LIBRARY_PATH=$PWD/vendor/xtp/lib QT_QPA_PLATFORM=offscreen venv/bin/python scripts/verify_integration.py
"""
import os
import sys
import json

# 将 project root 加入 sys.path（scripts/ 的上一级）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(description: str, ok: bool, detail: str = ""):
    """打印检查结果。"""
    icon = "✅" if ok else "❌"
    print(f"  {icon} {description}")
    if detail:
        for line in detail.strip().split("\n"):
            print(f"     {line}")


def verify_strategy_runner():
    """验证 strategy_runner 模块可导入。"""
    print("\n=== 1. strategy_runner ===")
    try:
        from src.strategy_runner.main import _build_xtp_setting, _warmup_history, main
        check("模块导入", True, "src.strategy_runner.main 成功导入")
    except ImportError as e:
        check("模块导入", False, str(e))
        return

    # 验证 _build_xtp_setting 能运行（不连真实 broker DB）
    try:
        from src.strategy_framework.broker import get_broker
        broker = get_broker("xtp")
        if broker:
            check("Broker DB XTP 配置存在", True, f"type={type(broker).__name__}")
        else:
            check("Broker DB XTP 配置", False, "get_broker('xtp') 返回 None（.env 备选）")
    except Exception as e:
        check("Broker DB 查询", False, str(e))


def verify_xtp_sdk():
    """验证 XTP SDK 可加载。"""
    print("\n=== 2. XTP SDK ===")
    try:
        from vnpy_xtp import XtpGateway
        check("XtpGateway 可导入", True)
    except ImportError as e:
        check("XtpGateway 可导入", False,
              f"需要 LD_LIBRARY_PATH=vendor/xtp/lib\n  {e}")
    except Exception as e:
        check("XtpGateway 加载", False, str(e))


def verify_feishu():
    """验证飞书模块可导入 + 配置存在。"""
    print("\n=== 3. 飞书 ===")
    try:
        from src.feishu_bot.bot import FeishuClient, process_message_async, verify_signature
        check("feishu_bot.bot 导入", True)
    except ImportError as e:
        check("feishu_bot.bot 导入", False, str(e))
        return

    # 验证 DB 飞书配置
    from src.data_platform.db import get_conn
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT id, name, app_id, enabled, role FROM feishu_config ORDER BY id")
            rows = cur.fetchall()
            if rows:
                check("飞书配置存在", True,
                      f"{len(rows)} 条: " + "; ".join(f"id={r[0]} name={r[1]} role={r[4]} enabled={r[3]}" for r in rows))
            else:
                check("飞书配置", False, "feishu_config 表为空，需扫码配置")
    except Exception as e:
        check("飞书 DB 查询", False, str(e))


def verify_server_endpoints():
    """验证 Web API 关键端点可访问。"""
    print("\n=== 4. Web API 端点 ===")
    import httpx
    base = "http://127.0.0.1:8001"

    endpoints = [
        ("/health", "Health"),
        ("/lark/test", "飞书测试"),
    ]

    for path, label in endpoints:
        try:
            resp = httpx.get(f"{base}{path}", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                check(f"{label} ({path})", True, f"status={resp.status_code} {json.dumps(data)[:100]}")
            else:
                check(f"{label} ({path})", False, f"HTTP {resp.status_code}")
        except httpx.ConnectError as e:
            check(f"{label} ({path})", False, f"连接失败: {e}")
        except Exception as e:
            check(f"{label} ({path})", False, str(e))


def verify_broker_config():
    """验证 broker_config 表有 XTP 配置。"""
    print("\n=== 5. broker_config 配置 ===")
    from src.data_platform.db import get_conn
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT id, name, provider, enabled FROM broker_config WHERE provider='xtp'")
            rows = cur.fetchall()
            if rows:
                check("XTP broker 配置", True,
                      "; ".join(f"id={r[0]} name={r[1]} enabled={r[3]}" for r in rows))
            else:
                check("XTP broker 配置", False, "broker_config 无 xtp 记录（用 .env 备选）")
    except Exception as e:
        check("broker_config 查询", False, str(e))


def verify_services():
    """验证 systemd 服务状态。"""
    print("\n=== 6. 服务状态 ===")
    import subprocess

    services = [
        "quant-web-api@quant",
        "quant-celery-worker@quant",
        "quant-celery-beat@quant",
    ]

    # 动态获取 feishu bot 实例
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "quant-feishu-bot@*", "--no-legend", "--type=service"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                svc = line.split()[0]
                services.append(svc)
    except Exception:
        pass

    for svc in services:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            active = result.stdout.strip() == "active"
            check(f"{svc}", active, result.stdout.strip())
        except Exception as e:
            check(f"{svc}", False, str(e))


def main():
    print("=" * 50)
    print("  #35 全链路集成验证")
    print(f"  {__file__}")
    print(f"  Python {sys.version}")
    print("=" * 50)

    verify_strategy_runner()
    verify_xtp_sdk()
    verify_feishu()
    verify_server_endpoints()
    verify_broker_config()
    verify_services()

    print("\n" + "=" * 50)
    print("  验证完成")
    print("=" * 50)


if __name__ == "__main__":
    main()