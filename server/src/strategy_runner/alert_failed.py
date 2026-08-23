"""OnFailure 告警钩子（SA3，F-13）。

systemd unit 配 `OnFailure=quant-task-failed@%i.service`，本模块作为 ExecStart 入口：
拿到失败单元名 → 走通知中心（站内铃铛 + 外部推送）→ 退出。任何异常只记日志。

用法: python -m src.strategy_runner.alert_failed "quant-live-task@4.service"
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("alert_failed")


def main() -> None:
    unit = sys.argv[1] if len(sys.argv) > 1 else "unknown-unit"
    title = f"实盘单元失败: {unit}"
    body = ("该 systemd 单元进入 Failed（StartLimit 5 次/5 分钟耗尽，或看门狗/显式失败）。"
            "SA4 reconciler 将在依赖健康且退避窗口（首次 5 分钟，指数翻倍封顶 1 小时）后自动"
            " reset-failed + start；若退出码为 78（EX_CONFIG 永久配置错误）不会自动重启，"
            "需人工 journalctl -u {unit} -n 50 定位后修复再 systemctl reset-failed + start。"
            ).format(unit=unit)
    try:
        from src.alert_notify.notify import notify
        nid = notify("critical", "system", title, body)
        if nid:
            logger.info("已发告警 notification_id=%s: %s", nid, title)
        else:
            logger.info("告警去重命中（1min 内同标题），跳过: %s", title)
    except Exception:
        logger.exception("告警发送失败: %s", title)


if __name__ == "__main__":
    main()
