"""平台使用条款（单一真相源：注册页 /api/terms 拉取 + 开通邮件引用，避免双份维护）。

访问权限原则：受邀用户仅 Viewer（只读）/ 可能 Analyst（研究）；
交易（Trader）与管理（Admin）权限不向受邀用户开放（运营方持有）。
"""

TERMS_ZH = """平台使用条款（最后更新：2026 年 8 月）
一、接受条款
首次登录或使用本平台即视为您已阅读并同意本条款；如不同意，请停止使用。
二、服务性质
本平台为人工智能与量化策略的学习与研究工具，提供市场数据分析、策略回测及行情信息查看等功能，供账户持有人个人学习与研究使用。本平台非面向公众开放的服务，不接受公开注册，账户通过管理员邀请方式开通。
三、访问权限
受邀账户默认授予 Viewer（只读）权限，可查看持仓、盈亏、策略状态等信息；经管理员评估可能升级为 Analyst（研究）权限，用于策略与数据分析。交易（Trader）及管理（Admin）权限不向受邀用户开放，仅由平台运营方持有。
四、使用范围
本平台按个人、有限规模提供，不适合多人共享或商业用途。如需完整功能、更高频或更大规模的使用，您应自行部署独立服务器实例（运营方可提供软件），本平台不保证满足上述重度使用需求。
五、账户与安全
1. 请妥善保管账号与密码，因泄露或共享造成的后果由您自行承担。
2. 账号仅供本人使用，不得转让、出借或出售。
3. 您的可操作范围以所授角色为准；管理员有权调整、限制或收回权限，以及停用或注销账户。
六、投资风险提示
本平台展示的持仓、盈亏、策略表现等信息仅供学习参考，不构成任何投资建议。如您据此自行做出投资决策，相关风险与盈亏由您自行承担，本平台及运营方不保证盈利。
七、禁止行为
不得将本平台用于任何违法违规活动；不得滥用资源或对服务造成过载；不得对系统进行逆向工程或攻击；不得将账号用于商业转售或向第三方有偿提供访问。违者将被立即限制或终止使用。
八、服务变更与终止
本平台按"现状"提供，运营方可能随时变更功能、暂停服务或注销账户。鉴于资源有限，服务的可用性与性能不作保证。
九、免责声明
在法律允许的最大范围内，运营方不对因使用或无法使用本平台造成的任何直接或间接损失（包括利润损失、数据丢失等）承担责任。
十、隐私
平台可能记录您的操作与使用日志，用于运维、安全审计与服务改进。
十一、条款变更
运营方保留随时修订本条款的权利，修订后的条款自公布之日起生效。"""


TERMS_EN = """Terms of Use (Last updated: Aug 2026)
1. Acceptance
By logging in or using the platform for the first time, you agree to these terms. If you do not agree, stop using it.
2. Nature of Service
This platform is a learning and research tool for AI and quantitative strategies, providing market data analysis, strategy backtesting, and market information viewing for the account holder's personal study and research. It is not a public service, does not offer public registration, and accounts are opened via administrator invitation.
3. Access Permissions
Invited accounts are granted Viewer (read-only) permission by default, allowing you to view positions, PnL, and strategy status. At the administrator's discretion, this may be upgraded to Analyst (research) permission for strategy and data analysis. Trading (Trader) and administrative (Admin) permissions are not available to invited users and are held only by the operator.
4. Scope of Use
The platform is provided for personal, limited-scale use and is not suitable for multi-user sharing or commercial use. For full-featured, higher-frequency, or larger-scale use, you should deploy your own server instance (software can be provided by the operator). The platform does not guarantee it will meet such heavy-use demands.
5. Account & Security
1. Keep your credentials safe; you are responsible for consequences of disclosure or sharing.
2. Accounts are for personal use only and may not be transferred, lent, or sold.
3. Your available actions depend on your assigned role; administrators may adjust, restrict, or revoke permissions, and may disable or terminate accounts.
6. Investment Risk Notice
Positions, PnL, and strategy performance shown on the platform are for learning reference only and do not constitute investment advice. If you make investment decisions based on them, you bear the related risk and gains or losses; the platform and operator do not guarantee profit.
7. Prohibited Conduct
You may not use the platform for any illegal activity; abuse resources or overload the service; reverse-engineer or attack the system; or use accounts for commercial resale or paid access to third parties. Violators will be immediately restricted or terminated.
8. Changes & Termination
The platform is provided "as is"; the operator may change features, suspend service, or terminate accounts at any time. Given limited resources, service availability and performance are not guaranteed.
9. Disclaimer
To the maximum extent permitted by law, the operator is not liable for any direct or indirect losses (including loss of profit or data) arising from use of or inability to use the platform.
10. Privacy
The platform may log your operations and usage for operations, security audit, and service improvement.
11. Changes to Terms
The operator reserves the right to revise these terms at any time; revised terms take effect upon publication."""


def get_terms() -> dict:
    """返回中英文条款（注册页 + 开通邮件共用）。"""
    return {"zh": TERMS_ZH, "en": TERMS_EN}
