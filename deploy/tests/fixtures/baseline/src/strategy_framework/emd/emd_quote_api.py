# 沙箱 importsmoke 占位（批 133 沙箱回归补）——真身 = 同名 .so（批 127 EMQ 惰性绑定）。
# 沙箱 src 切片 exclude_so ⇒ 发布树里没有 .so；无此占位则第七入口恒 ModuleNotFound。
# 生产发布树用真 .so，本文件不进 repo 的 server/src（只住 fixtures），无遮蔽风险。
