# autofw 代码说明文档（CODE_OVERVIEW）

> 版本：v1.0（2026-08-15）｜本文档与当前代码逐一比对验证，路径/函数/命令以实际代码为准
> 配套文档：《通用多App自动化框架架构设计.md》《执行文档-Agent执行指南.md》（详见 §9）

---

## 1. 项目概览

**autofw** 是一个通用的多 App 自动化框架：基于 MuMu 12 模拟器 + adb + 图像识别，按 `pipeline.json` 编排顺序执行多个 App 的任务，具备流水线切换、断点续跑（三态决策）、容灾恢复（前台拉回/黑屏唤醒/断连重连）能力。

**设计定位**：通用地基——核心（core/）不含任何具体业务，业务全部以"适配器"模块形式接入（adapters/），新增一个 App 只需：新适配器文件 + 注册表登记 + 编排配置，核心零改动。

**运行环境**：Windows + Python 3.12+（实测 3.13.12）+ MuMu 12 模拟器（5.23.0.3181 / Android 12）。

## 2. 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 语言 | Python 3.12+ | 实测 3.13.12 |
| 图像处理 | OpenCV 4.14（cv2） | 截图解码、模板匹配（matchTemplate） |
| 数值 | numpy 2.5 | 帧处理、像素差异 |
| 图像裁剪 | pillow 11.3 | 模板采集脚本用 |
| 设备通道 | adb（MuMu 自带）+ MuMuManager 官方 CLI | 截图/输入/前台校验/应用启停 |
| 持久化 | JSON 文件（state/） | checkpoint 原子写双副本 + 意图日志 |

## 3. 目录结构

```
autofw/                          # 仓库根
├── main.py                      # 【主入口】argparse：加载配置→gate 自检→断点恢复→Pipeline.run
├── pipeline.json                # 编排配置（有序任务列表；recovery/checkpoint 参数）
├── m3_config.json               # 单 App 测试配置（M3 容灾注入用）
├── README.md                    # 快速接入说明（3 步）
├── requirements.txt             # 依赖：opencv-python / numpy / pillow
├── core/                        # 【核心模块】框架机制（无业务）
│   ├── pipeline.py              # 流水线调度：FIFO+单线程+控制流异常；切换协议；恢复三态；tasks 校验
│   ├── adapter.py               # AppAdapter 抽象 + load_templates 默认实现
│   ├── task.py                  # BaseTask 幂等执行单元 + TaskContext（截图/匹配/点击）
│   ├── device.py                # BaseDevice/EmulatorDevice：adb+MuMuManager 双通道；三级心跳；重启
│   ├── recovery.py              # 容灾：前台双确认+单前台协议+熔断+降级链+看门狗
│   ├── checkpoint.py            # 断点持久化：原子写双副本+意图日志（schema 见 §7）
│   ├── gate.py                  # 环境自检门：保活放行/adb 就绪/分辨率
│   └── exceptions.py            # 异常分级（TaskEnd/TaskDisabled/Retryable/AppStuck/EmulatorDown/HumanTakeover）
├── adapters/                    # 【业务模块】每 App 一个插件
│   ├── __init__.py              # ADAPTERS 注册表（包名→类，唯一登记点）
│   ├── example_app.py           # 示例适配器（模板匹配最小实现）
│   ├── np_manager.py            # NP管理器（open_menu：点☰→侧边栏确认）
│   ├── uncompress.py            # 解压缩全能王（open_compress）
│   ├── cherry_tale.py           # Cherry Tale 游戏（enter_game，公告弹窗未闭环）
│   └── templates/<pkg>/*.png    # 模板图（文件名即模板名；main_scene 必填）
└── state/                       # 【运行期数据】checkpoint.a/b.json + intent.log + app.log + error_frame.png
```

## 4. 主入口与启动流程

**主入口：`main.py`**（唯一启动点；判定依据：argparse 命令行入口，所有运行路径必经 `main()`）。

```
python main.py [--config pipeline.json] [--no-gate] [--no-resume]
```

启动流程（main.py main() 顺序）：
1. 解析参数（--config 指定编排文件，默认 pipeline.json；--no-gate 跳过环境自检；--no-resume 从头跑）
2. **环境自检门**：`Gate().check()` —— 保活状态检测（开启=放行进入单前台模式，仅提示）、adb 连通（先 connect 再 get-state）、分辨率（wm size）；不符则拒绝启动并列出问题（--no-gate 跳过）
3. **组装运行时**：从配置读取 recovery/checkpoint 参数 → `EmulatorDevice` + `Checkpoint` + `Watchdog/CircuitBreaker/Recovery`
4. **断点恢复**：`checkpoint.load()`（双副本容错读取）；--no-resume 或 resume_on_start=false 时清空已完成任务
5. **跑流水线**：`Pipeline(config, ADAPTERS, device, checkpoint, recovery, resume).run()`
6. **日志**：控制台 + `state/app.log`（RotatingFileHandler 10MB×3）

## 5. 核心模块职责

| 模块 | 职责 | 关键类/函数 |
|---|---|---|
| core/pipeline.py | 流水线编排与执行 | `Pipeline.run()`（逐 App 执行）；`_run_app()`（切换协议：启动→wait_ready→任务→close_app）；`_wait_ready()`（黑屏检测+前台拉回+特征画面就绪）；`_run_task_with_retry()`（重试+容灾+恢复三态）；`_resume_decision()`（R7 三态：skip/continue/pause，resume_auto 可配）；`_save_evidence()`（错误帧留证） |
| core/adapter.py | 适配器抽象 | `AppAdapter`（package/activity/ensure_foreground/ensure_home/login_recovery/build_tasks/verify_state）；`load_templates()`（从 templates/<pkg>/ 自动加载 PNG） |
| core/task.py | 任务执行单元 | `BaseTask`（task_id/precheck/run/postcheck/execute）；`TaskContext`（frame 缓存/find 模板匹配/tap_template） |
| core/device.py | 设备通道 | `EmulatorDevice`：shell/screencap/tap/swipe/key；MuMuManager 通道（active_app/app_state/mm_close_app）；双通道前台校验（dumpsys window + active）；close_app（force-stop 主+官方兜底+app info 确认）；health_check 三级心跳；emulator_restart；reconnect；**地址四级自愈**（08-28：_adb_ok → _probe_addr(MuMuManager/adb devices) → kill-server 复位 → 再探测；shell/screencap 失败换址重试） |
| core/recovery.py | 容灾 | `Recovery.assert_foreground`（前台双确认+单前台协议：拉回→等4s→连续2帧）；`handle_failure` 降级链（重试→adb重连→重启app→重启模拟器→告警）；`CircuitBreaker`；`Watchdog`（超时分级+点击熔断） |
| core/checkpoint.py | 断点持久化 | `Checkpoint`：save（意图日志先行+原子写双副本）；load（JSONDecodeError 容错+双副本交替）；mark_completed/is_completed/update_resource |
| core/gate.py | 环境自检门 | `Gate.check()`：保活（放行单前台模式）/adb/分辨率 |
| core/exceptions.py | 异常分级 | 6 级异常类（见 §6） |

## 6. 关键数据流

### 6.1 主执行流（单 App）

```
Pipeline.run()
  └─ _run_app(entry)
      ├─ load_templates()                # 适配器模板加载
      ├─ start_app(pkg, activity)        # am start 或 app launch
      ├─ _wait_ready()                   # 轮询截图找 main_scene（黑屏→WAKEUP；前台被占→拉回）
      ├─ login_recovery()                # 登录/公告恢复（App 启动时一次）
      └─ 逐任务：
          _resume_decision()             # 三态：已完成→skip / 未完成→continue / 无法确认→暂停三选
          assert_foreground()            # 前台双确认（dumpsys+active+视觉 2 帧）
          task.execute()                 # precheck → run（截图→模板匹配→tap）→ postcheck
          checkpoint.mark_completed()    # 幂等落盘
      └─ close_app()                     # force-stop 主 + 官方兜底 + app info 确认 stopped
```

### 6.2 识别-操作链路

```
adb screencap → numpy BGR 帧 → cv2.matchTemplate(模板, TM_CCOEFF_NORMED)
  → 相似度 ≥0.8 命中 → 中心坐标 → adb shell input tap x y → 截图确认（postcheck）
```

### 6.3 断点三态（verify_state 语义）

| 返回值 | 语义 | 调度行为 |
|---|---|---|
| True | 确认未完成 | 从该子任务 precheck 继续 |
| False | 确认已完成 | 跳过（幂等） |
| None | 无法确认 | 暂停：1=手动接管后继续 2=跳过 3=暂停退出（resume_auto 可预置） |

## 7. 数据存储与追踪

| 项 | 位置 | 说明 |
|---|---|---|
| 断点状态 | `state/checkpoint.a.json` / `checkpoint.b.json` | 原子写双副本；字段：schema_version/pipeline_index/app/task_id/completed[]/resources{}/env{}/updated_at |
| 意图日志 | `state/intent.log` | 每次状态变更先写 `{unix_timestamp} {event}` 再落盘（崩溃可重放、可审计） |
| 运行日志 | `state/app.log` | RotatingFileHandler 10MB×3；事件格式见架构设计 §4.10 |
| 失败证据 | `state/error_frame.png` | HumanTakeover/任务失败时保存错误帧截图 |
| 模拟器配置 | `D:\MuMuPlayer\vms\MuMuPlayer-12.0-0\configs\customer_config.json` | 只读检测（UTF-8/GBK 双解码），**禁止写入** |

登录态：由各适配器 login_recovery 处理（Cherry Tale 已实测登录态保持；公告弹窗处理未闭环——见 §8 风险）。

## 8. 构建/运行步骤与已知风险

### 构建与运行（Windows）

```bat
:: 1) 安装依赖（Python 3.12+；numpy>=2.0 适配 3.13）
cd autofw
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

:: 2) 校验（可选）
.venv\Scripts\python -m compileall -q core adapters main.py

:: 3) 运行（前置：MuMu 12 模拟器运行中，adb 127.0.0.1:16384 可达）
.venv\Scripts\python main.py
:: 调试选项：--no-gate 跳过自检 / --no-resume 忽略断点
```

### 已知风险与注意事项

| 风险 | 说明 | 处置 |
|---|---|---|
| MuMu 保活模式黑屏/前台抢占 | force-stop 后冷启动 App 常经历"无焦点黑屏"；其他 App 会抢占最前端 tab | 框架已内建黑屏 WAKEUP + wait_ready 周期拉回；影响是任务耗时增加 |
| Cherry Tale 公告弹窗 | 启动后公告弹窗遮挡主界面，enter_game 闭环未完成 | 需采集公告关闭按钮模板（未决项 U-1） |
| 容灾参数 | 重试 5/看门狗 60-180s/熔断 12 为建议值 | M4 48h 实测后校准回写 |
| 无用户级鉴权 | 仅 gate 环境自检（本地访问控制） | 若未来接远程能力必须先补鉴权 |
| 残留测试文件 | `_test_pipeline.json`（单元测试残留）、`np_manager.py.bak`（安全守卫拒绝删除） | 无功能影响，可手动清理 |
| 分辨率固定 | 模板与坐标按 1920×1080@280dpi 校准 | 禁止改模拟器分辨率；改动需重采模板 |

## 9. 文档地图

- 本文档（代码说明）：本文件 CODE_OVERVIEW.md
- 架构设计（为什么这么设计）：仓库外文档 `docs/design/通用多App自动化框架架构设计.md`
- 执行文档（怎么做，M0-M4 全流程）：`docs/design/执行文档-Agent执行指南.md`
- 实施与审查报告：`docs/design/实施报告-M0至M3.md`、`docs/design/框架达标审查报告.md`

## 10. 变更记录（文档版本）

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-15 | 初始版本（与当前代码逐项比对） |
