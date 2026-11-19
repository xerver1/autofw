# autofw — 多 App 自动化框架（MuMu 模拟器 + 图形化流程编排）

基于 MuMu 12 模拟器 + adb + 图像识别的多 App 自动化框架。**主要工作方式是在浏览器里画流程图**（图形化工作流），框架按图执行：启动 App → 识图定位 → 点击/滑动 → 条件/循环 → 断点续跑。

> 面向使用者的入门指南见 **[USER_GUIDE.md](USER_GUIDE.md)**（从零到跑通第一个任务）。
> 面向开发/维护者的交接文档见 **[HANDOVER.md](HANDOVER.md)**（当前状态的权威来源）。

## 快速开始（推荐）

```bat
:: 前置：MuMu 12 模拟器已安装（默认路径 D:\MuMuPlayer），Python 3.12+ 已装
start.bat     :: 一键启动：自动建环境+装依赖 → 打开浏览器控制台 http://127.0.0.1:8765
stop_gui.bat  :: 停止面板服务
```

首次运行 start.bat 会自动创建 .venv 并安装依赖（约 1-3 分钟，期间窗口没有输出是正常的）。启动/排障日志在 `logs\startup.log`。

命令行方式（调试用）：

```bat
python main.py --config pipeline.json   # 按 pipeline.json 的 mode 字段执行
python main.py --no-gate                # 跳过环境自检
python main.py --no-resume              # 忽略断点从头跑
```

## 两种执行模式（重要）

`pipeline.json` 顶层的 `mode` 字段决定执行路线：

| mode | 路线 | 说明 |
|---|---|---|
| `flow`（**当前默认**） | 图形化工作流 | 任务从 `state/flows/<包名>/*.json` 读取，由**流水线编排面板/画布编辑器**管理。**接入新 App 走这条路线**（见下） |
| `adapter`（遗留） | 旧适配器路由 | 任务由 `adapters/` 下的 Python 适配器提供，`pipeline.json` 的 `tasks` 字段编排。仅维护，不再新增 |

## 接入新 App（当前方式：图形化编排）

1. 启动 MuMu 模拟器，打开控制台（start.bat）
2. 「流水线编排」→ **扫描设备应用** → 勾选目标 App 加入流水线
3. App 行点「图形化编排」进入画布编辑器 → 「＋ 新增任务」→ 拖节点画流程（点击/条件/循环/等待），用「截图取模板」采集按钮图片
4. 回控制台点该 App 的「▶ 运行自动化」单跑验证，通过后再用顶栏「启动流水线」按编排顺序跑全部

> 旧方式（编写 `adapters/<app>.py` 适配器 + 注册 ADAPTERS + tasks 编排）仅 `--mode adapter` 生效，属遗留路线，详见 CODE_OVERVIEW.md。

## 控制台页面地图（端口 8765）

| 页面 | 用途 |
|---|---|
| `/`（index.html） | 经典控制台：启停流水线、进度、日志 |
| `/index_new.html` | 新版控制台（同功能，新界面） |
| `/pipeline_editor.html` | 流水线编排：App 增删/排序/启停、候选来自设备扫描 |
| `/flow_editor.html?pkg=<包名>` | 画布编辑器：图形化工作流（框架核心界面） |
| `/step_editor.html` | 步骤录入：截图→标记点击→提交（辅助工具） |
| `/ocr.html` | OCR 识别调试：排查"为什么识别不到" |

## 依赖

Python 3.12+；`requirements.txt`：opencv-python、numpy、pillow、rapidocr-onnxruntime（OCR 必需）。

## 目录结构（摘要）

```
autofw/
├── start.bat / stop_gui.bat   一键启动/停止（面板服务 gui/server.py，端口 8765）
├── main.py                    命令行入口（gate 自检 → 按 mode 分发）
├── pipeline.json              编排配置（mode 字段 + App 列表 + recovery/checkpoint 参数）
├── core/                      框架核心（无业务）
│   ├── flow_engine/executor/pipeline.py   flow DSL 引擎/执行器/调度
│   ├── pipeline.py            旧适配器路由调度
│   ├── device.py              adb + MuMuManager 双通道封装
│   ├── recovery.py / gate.py / checkpoint.py / failures.py …
├── gui/                       控制台与编辑器页面（index*.html / flow_editor.html / server.py）
├── adapters/                  旧适配器（遗留路线）+ templates/<包名>/ 模板图
├── state/                     运行数据：flows/（图形化工作流）、checkpoint、app.log、pause.flag 等
└── docs/                      设计文档、历次审查报告、交接分册（handover/）
```

## 常见问题

- **点了启动秒退**：控制台会显示退出码与报错末行；完整报错在 `state/pipeline_stderr.log`；环境自检失败（exit_code=2）按日志里的中文提示处理。
- **端口 8765 被占用**：start.bat 会探测占用者身份并询问；非本项目的进程需要你确认才会处理。
- **模拟器装在非默认路径**：`core/gate.py` 顶部 ADB/CUSTOMER_CONFIG 常量需改为实际路径（报错信息会带上期望路径）。
- **分辨率**：模板按 1920×1080@280dpi 校准，请勿改模拟器分辨率。
- **修改框架行为**：容灾参数在 `pipeline.json` 的 recovery 段；断点续跑无人值守可配 `checkpoint.resume_auto`（skip/continue）。

## 历史备份说明

仓库内 `*.bak-YYYYMMDD-*`、`*.orig` 文件为历史快照（人工版本管理），可忽略；一次性验证脚本（`verify_*`/`_fix*` 等）同样不参与运行。
