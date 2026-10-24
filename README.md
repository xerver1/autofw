# autofw — 通用多 App 自动化框架

基于 **MuMu 12 模拟器 + ADB + OpenCV 图像识别** 的通用多 App 流水线自动化框架。
Python 后端 + 浏览器图形化流程编辑器，设计调研参考 MAA / ALAS / ok-script，并经 23 项事故场景反方评审。
主要面向mumu模拟器，你也可以进行二次开发以兼容更多模拟器

典型用途：在模拟器里自动执行 App 的日常任务——**启动 → 识图定位 → 点击 → 循环 → 断点恢复**，全程台账可追踪。

## 特性

- 🖱 **图形化流程编辑器**：拖拽节点画流程，8 种节点类型（动作 / 条件分支 / 循环 / 打印 / 子程序…），画布缩放、流程总览小地图（节点类型着色、运行联动）、批量操作、框选
- 👁 **三种识别方式**：OpenCV 模板匹配（default / Canny 边缘 / ORB 特征点）、区域匹配、OCR 文字识别（RapidOCR，控制台附「OCR 调试」页排查识别问题）
- 🔁 **可靠执行**：条件 / 循环 / 等待 / 重试 / 点击生效自检 / 断点续跑 / 看门狗
- 📋 **全程台账**：运行日志、步骤序列、失败归档，出问题可回放定位
- 🧩 **DSL 双向同步**：流程图 ↔ 类按键精灵 DSL 代码互相转换，既能拖画布也能写代码
- 🖥 **控制台面板**：经典 + 新版两套界面，App 管理、流水线编排、运行状态、MuMu/ADB 状态一键诊断
- ⏸ **运行控制**：批量执行与流水线（子进程）均支持暂停/继续（节点级安全点）、从任意子任务起跑、一键跳转画布跟随运行进度
- 📖 **新手友好**：内置开箱示例工作流、空状态自动生成示例流程、[USER_GUIDE.md](USER_GUIDE.md) 从零到跑通、破坏性操作二次确认、报错中文化并附行动指引

## 快速开始（3 步）

> 前置：Windows 10/11，安装 [MuMu 12 模拟器](https://mumu.163.com/) 与 [Python 3.12+](https://www.python.org/downloads/)（勾选 Add to PATH）

```bat
:: 1. 克隆仓库
git clone https://github.com/xerver1/autofw.git
cd autofw

:: 2. 一键启动（首次自动创建 venv 并安装依赖，约 1~3 分钟；
::    期间窗口没有输出属正常，请勿关闭窗口）
start.bat

:: 3. 浏览器打开控制台
http://127.0.0.1:8765
```

启动后先跑内置示例：控制台里对 **com.android.settings**（系统设置，自带演示工作流：打开设置 → 打印 → 循环 → 返回键）点「运行」，观察日志输出即表示全链路正常。完整上手步骤（添加 App → 画流程 → 运行 → 排错）见 [USER_GUIDE.md](USER_GUIDE.md)。

> 💡 MuMu 装在非默认位置时，框架会自动从注册表 / 常见目录探测；特殊环境可设置环境变量 `AUTOFW_MUMU_HOME` 指向安装根目录。
> 💡 依赖安装已由 start.bat 自动处理（`python -m pip --no-user` + 国内镜像）；仅手动安装时才需注意 `PIP_USER=1` 机器要带 `--no-user`（详见 requirements.txt 注释）。

## 做你自己的自动化

1. 控制台「添加 App」（扫描模拟器已装应用）→ 自动生成工作流脚手架
2. 打开图形编辑器，从节点库拖入动作：点击坐标 / 识图点击 / 等待 / 条件 / 循环……
3. 「截取模拟器画面」→ 圈选按钮区域 → 自动存为识别模板
4. 点「运行」单任务调试，稳定后加入流水线批量执行

DSL 规格与编辑器设计文档见 [docs/dsl-plan/](docs/dsl-plan/)；代码结构详解见 [CODE_OVERVIEW.md](CODE_OVERVIEW.md)。

## 目录结构

```
autofw/
├── main.py               # 命令行入口（--mode flow|adapter）
├── start.bat / stop_gui.bat  # 一键启动 / 停止
├── USER_GUIDE.md         # 新手使用指南（从零到跑通第一个任务）
├── core/                 # 执行引擎：设备层(ADB)、流程执行、断点、熔断、恢复
├── gui/                  # 控制台(server.py + 经典/新版界面) + 流程编辑器 + DSL 三件套(纯前端)
├── adapters/             # App 适配器（example_app.py 为示例）
├── docs/dsl-plan/        # DSL 语言规格与编辑器工单
└── state/                # 运行数据（首次启动自动创建；工作流 JSON 在 state/flows/<pkg>/）
```

## 参与贡献

欢迎 Issue 与 Pull Request！改代码请遵循：

- 改 `gui/server.py`、`core/*.py` 前先开 Issue 讨论（历史红线区域，牵一发动全身）
- E2E 测试必须用 `UX_TEST_` 前缀隔离包，不得对真实工作流做写操作
- 提交前跑回归：`node gui/test_roundtrip.js`（DSL 往返）+ `python -m py_compile` 全量语法检查
- 每次改动先备份原文件（`.bak-YYYYMMDD<后缀>`），保持可回滚

## License

[MIT](LICENSE) —— 可自由使用、修改、分发；游戏截图与商标版权归各自厂商，与本框架无关。

## tips
全程交给agent搭建，本人仅有编程基础
让agent上传上来的
同时存在编程界面与图形化编排界面（图形化编排界面足够好用了就没继续优化编程界面，也是自己不知道该怎么折腾编程界面）
欢迎各位提出修改意见
