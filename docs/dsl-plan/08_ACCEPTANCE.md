# 机械验收清单（ACC）

Plan-Doc-Version: v3.0
BUDGET: 150 行正文
前置：P4P5 done
规则：逐条命令**逐字执行**，输出与预期不一致 = 该项 FAIL = 整体验收 FAIL。
禁止改脚本/断言/期望值；期望值可疑 → BLOCKED 上报。

## A 结构与预算（4 条）

```
A1  node tools/check_plan.js split      预期: 全部 [OK], 无 [FAIL]
A2  node tools/check_plan.js budget     预期: 每个文件 "正文 x/y 行"
A3  node tools/check_plan.js errata     预期: "勘误条数一致: 25 条", 无空列 FAIL
A4  node tools/check_plan.js version    预期: 全部版本一致 v3.0
```

## B 勘误落地残留（2 条，对源文档执行）

```
B1  node tools/check_plan.js grep "指向循环外|指向循环体外" ../../混合编辑器实施计划.md
    预期: 施工完成后该两模式在【新落地的 dsl_*.js 与 flow_editor.html 改动区】0 命中;
         对源文档本身的命中属预期内(源文档是历史底稿), 以 B1 输出中不含 gui/dsl_*.js 路径为准
B2  node tools/check_plan.js grep '"hash"' gui\dsl_to_flow.js gui\dsl_parser.js gui\flow_to_dsl.js
    预期: 0 命中 (统一 graphHash, 勘误 #1)
```

## C 功能验收（5 条）

```
C1  node gui/test_dsl_parser.js          预期: "PASS n/n" (T1+T2+T3+T3' 全过)
C2  node gui/test_roundtrip.js           预期: T4 PASS 5/5 且 T5 PASS (数值=F-A6 回填版)
C3  node -e "const p=require('./gui/dsl_parser.js');console.log(typeof p.parseProgram)"
    预期: function
C4  node tools/check_plan.js inline      预期: "0 外部引用" (R3 用例全文内联)
C5  Select-String -Path gui\flow_editor.html -Pattern "addCommand|keydown"
    预期: 两行均命中 (勘误 #24)
```

## D 运行接线冒烟（2 条，需 GUI 服务在线：`.venv\Scripts\python.exe gui\server.py`）

```
D1  POST /api/flows/validate with P0-A1 同一探针 JSON → 预期: 不出现 5xx; 错误码 ∈ 已知集
D2  浏览器打开 flow_editor.html → DSL 面板粘贴附录 A DSL → 点"DSL→图" →
    预期: 图渲染成功且节点数与 C2 的 nodes 断言一致; 截图存 docs/dsl-plan/facts/F-ACC-D2.png
```

## E 进度收口（2 条）

```
E1  node tools/check_plan.js progress   预期: DATA 行 done=全部卡数, blocked=0
E2  Get-ChildItem gui\dsl_*.js | Measure-Object
    预期: Count=4 (dsl_parser/dsl_to_flow/flow_to_dsl/export_md)
```

## 判定

- 全部 PASS → 在 PROGRESS.md 写 `| ACC | done | <日期> <C2 实测数值> |`；
- 任一 FAIL → 按 01_PROTOCOL §6 SOP 处置；两次不过 → BLOCKED。
