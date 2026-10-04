# 附录 A：T5 完整示例 DSL（重构版，D-R1d 裁决数据源）

Plan-Doc-Version: v3.0

> 本文件是勘误 #5/#6 的落点：《混合编辑器任务书.md》不存在、MEMORY.md 暂存版不可达，
> 故 T5 全文由 P0-A5 重建。下方为**骨架规格**；P0 完成后按 §3 原语库补全为可运行全文，
> 并把最终全文回填到本文件第 4 节（覆盖 `（待 P0 回填）` 占位行）。

<!-- APPENDIX-START -->

## 1. 目标结构（源文档 6.5 断言口径）

- 节点数 / 图数 / subgraph 键数：以 F-A6 推演实测为准回填（初值 29/6/5，允许修正）；
- 必须覆盖语句类型：赋值、tap/swipe/wait 类原语、if_else（含 else 分支非空）、
  for 或 loop 循环体（含 break）、switch ≥2 case + default、sub 定义 + 前向 call。

## 2. 骨架（P0-A5 的起点草案）

```
// main
n = 0
tap 540, 1800
wait "开始.png", timeout=10s
if n == 0
    swipe 300,1200,300,600, duration=300ms~500ms
else
    log "skip"
end
for i in 1~3
    tap 540, 1200
    if i == 2
        break
    end
end
switch n
case 1
    warn "one"
case 2
    log "two"
default
    log "other"
end
call cleanup

sub cleanup
    wait_gone "加载中.png", timeout=15s
end
```

## 3. 计数推演方法（F-A6 必须照此复核）

1. 每条原子语句 → 1 节点；if → 条件头节点 + :true/:false 两键各含 start/end；
2. for/loop → 头节点 + body 键含 start/end；break → 1 节点 + label=break 边；
3. switch → 头节点 + 每 case 一键 + default 键；sub/call → 定义块与调用点各计；
4. 图数 = main + 各非空子图键；键数按第 2 节子图键规则逐项清点。

## 4. 最终回填区（P0-A5 重建，对齐计划 §3 BNF + §6.5 结构断言）

> 注：本全文依计划 §6.5（29 节点/6 图/5 键）重建；键数口径见 F-A6（若严格按 §5.2 if→2 键则 7 键，待 P2 回填）。

```dsl
// 任务书 3.1 重构版（D-R1d 数据源）— 按键精灵风格
main
  Call 启动游戏()
  Call 关闭弹窗()
  Do
    Call 刷一关()
    If FindImage "done.png" threshold 0.9 Then
      Exit Do
    End If
  Loop
  TracePrint "全部完成"
end main

Sub 启动游戏()
  TapImage "main_btn.png" threshold 0.85
  Delay 1000
  WaitImage "home.png" timeout 10s
End Sub

Sub 关闭弹窗()
  Do While FindImage "close.png" threshold 0.8
    Tap 960, 60
    Delay 500
  Loop
End Sub

Sub 刷一关()
  Tap 500, 800
  Delay 500
  WaitImage "result.png" timeout 120s
  Tap 960, 540
  Delay 1000
End Sub
```

结构计数（§6.5 口径）：main(6) + main-loop-body(4) + sub启动游戏(5) + sub关闭弹窗(3) + sub关闭弹窗-while-body(4) + sub刷一关(7) = 29 节点 / 6 图 / 5 键。
