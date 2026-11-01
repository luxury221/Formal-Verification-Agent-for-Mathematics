# 单定理表单编辑验收

日期：2026-10-26。设计：[ADR-018](../adr/0018-guided-paper-annotations.md)。

## 交付

论文审查新增原文/版本/变量/明确前提/结论/行号/作者步骤/检查计划表单，支持中缀公式、有行号原文预览、新建、显式带入上方草稿及精确JSON导出。编辑清除已准备计划和确认，非法公式不提交旧AST，移除步骤不悄悄改变引用。JSON与表单须逐字段无损，不能表示的标注保留原文。

表单是人工标注入口；原文不自动转换成公式，不扩展数学范围，不改服务端审查或知识证据政策，也不测量真实模型效果。准备/确认/真实Lean及独立重放流程均沿用既有实现。没有新增依赖、模型调用、权重/外部论文下载或训练。

## 实测

- **9项公式转换测试通过**：优先级、截断减法左结合、反向比较、不等式、逻辑/量词、全部作者示例无损转换、歧义/类型拒绝、预算及额外AST字段保留。
- **6项工作区HTTP回归通过**；JS语法与source gate72通过。未重跑既有全部Lean回归，后端Python、Nat编译器/模板/证据门禁保持上一交付摘要。
- **实际Edge14项流程检查通过**，另2项实际视图检查通过；共7截图已目视，桌面1440和手机390无横向溢出。JS异常、console/CSP/资源错误0。长截屏中的固定导航叠层是捕获方式产物，另留实际视口截图展示页面。
- 由表单新建 `n + 3 > n`、两步原证明及 `s2 -> s1` 引用，真实提交 `paper-f3c1cb2a97a34e1997a3e72aa98b5424`。3项义务均为PROVED/E4，命题与原证明均PROVED。候选/独立重放PID分别为 1420/1483, 1571/1625, 1710/1767，精确目标匹配。示例由项目作者编写，不代表一般论文或模型自动能力。
- 18条页面/资源均200，逐字节匹配最终源码并保留CSP/no-store；四账本integrity ok，无RUNNING/QUEUED任务；旧私有知识5条仍有效，模型未配置、worker无错误。
- 初次浏览器运行连接到启动时缓存的旧前端，辅助脚本因找不到新增表单节点退出。原记录保留 `annotation-editor-browser-before-service-reload.json`；核对空闲服务实际命令与目录后正常重启，最终完整流程通过，无产品代码改动。

## 工件

`artifacts/annotation-editor-validation.json` 保存9+6测试、语法/source gate及源码前后摘要；`annotation-editor-browser-qa.json` 保存14项流程、精确提交和实际结果；`annotation-editor-visual-qa.json` 保存补充视口；`annotation-editor-execution-evidence.json` 保存真实候选/重放与文件摘要；`annotation-editor-service-health.json`、`annotation-editor-final-runtime.json`、`annotation-editor-service-start.json` 保存服务与账本状态；`annotation-editor-delivery-check.json` 汇总源码/文档/证据/截图摘要。

当前入口：<http://127.0.0.1:8765/papers#single-theorem-review>。实际示例：<http://127.0.0.1:8765/papers#paper-f3c1cb2a97a34e1997a3e72aa98b5424>。服务采用既有4GiB内存/512MiB swap scope，无开机自启。未提交/推送/发布或运行远端CI。
