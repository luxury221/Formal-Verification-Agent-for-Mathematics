# KaTeX 本地排版依赖

- 采用时间：2026-11-01；固定版本：`katex@0.19.1`。
- 上游：<https://github.com/KaTeX/KaTeX>；发行元数据：<https://registry.npmjs.org/katex/0.19.1>。
- 下载：<https://registry.npmjs.org/katex/-/katex-0.19.1.tgz>。
- 许可：MIT；保留上游 `LICENSE`，允许商用、修改与分发，按许可提供版权声明且不提供担保。
- npm SHA-512：`sha512-jG8uKntjSEcm2EpQOh0XturpPuuC9r3Sq5HLHdsFdWZT27E672SF19QEi+of0/YeOy15lDgRhKvr5614uRrEfA==`。
- npm SHA-1：`ba2685811cdbffc54dfb7d55bd440c6cc6bb8795`。下载后两项均核对；元数据、原始压缩包和核对记录保存在 `artifacts/article-audit-v1-20261101-01/frontend/`。

## 用途与边界

仅将上游发行包的 `dist/katex.min.js`、`dist/katex.min.css`、CSS 引用的字体和 `LICENSE` 保存到 `mra/web/vendor/katex/`，通过本站静态资源提供。不引入 npm 构建链、自动渲染插件、CDN 或外部字体请求。

用于论文完整引文和证明任务自然语言原文中的显式 TeX 公式阅读。它不解析论文 HTML、不执行原文 HTML、不修改存储内容、结构化请求、完整 Lean 目标或既有检查工件，也不参与数学判断。

应用层固定 `trust: false`、禁用用户宏定义、限制公式长度/数量/展开次数/尺寸，拒绝 HTML、链接、资源加载、文件操作和宏定义命令。未知命令、异常、超限和不闭合分隔符保留原文。每个已排版原文块保留可切换的完整原始文本。KaTeX 支持的语法不等于 Lean 语义；排版成功不构成形式化、证明或语义对应。

KaTeX HTML 输出使用布局用的内联样式；本站 CSP 需要允许其样式属性。脚本仍限定本站外部脚本，`trust: false` 且命令拒绝规则阻止原文注入可执行内容或请求外部资源。MathML 与完整原始文本保留用于辅助阅读及核对。

升级需重新核对官方元数据、完整性、许可、安全配置和浏览器回归；不自动升级。
