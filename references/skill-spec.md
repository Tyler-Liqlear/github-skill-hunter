# Agent Skills 规范精要

来源：[agentskills.io/specification](https://agentskills.io/specification) 官方规范 +
[anthropics/skills](https://github.com/anthropics/skills) 官方 `skill-creator` 方法论。
本文是压缩后的执行版，用于编写与审查 skill。

---

## 1. Frontmatter 硬性规格

| 字段 | 必填 | 约束 |
|---|---|---|
| `name` | 是 | ≤64 字符；仅小写字母、数字、连字符；不可首尾为 `-`；不可出现 `--`；**必须与所在目录名一致** |
| `description` | 是 | 1–1024 字符；需同时说明「做什么」与「何时用」 |
| `license` | 否 | 许可证名或捆绑许可文件名 |
| `compatibility` | 否 | ≤500 字符；仅当有环境依赖时才写 |
| `metadata` | 否 | string→string 映射；键名建议加前缀避免冲突 |
| `allowed-tools` | 否 | 空格分隔的预批准工具，如 `Bash(git:*) Read`；实验性 |

最小合法形态：

```yaml
---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
---
```

命名反例：`PDF-Processing`（大写）、`-pdf`（首连字符）、`pdf--processing`（连续连字符）。

> 实测提醒：`description` 使用 YAML 块标量（`|-` / `>-`）时容易写超长。
> Anthropic 官方 `claude-api` skill 就因此达到 1068 字符，**超出 1024 上限**。写完务必数一遍。

---

## 2. 渐进式披露：三级加载模型

这是 skill 架构的核心。理解它才能决定内容该放哪。

| 层级 | 内容 | 何时进入上下文 | 预算 |
|---|---|---|---|
| L1 元数据 | `name` + `description` | **始终常驻**，所有 skill 都在 | ~100 tokens |
| L2 指令 | `SKILL.md` 正文 | 技能被触发时全量载入 | **<5000 tokens / <500 行** |
| L3 资源 | `scripts/` `references/` `assets/` | 按需，agent 自行判断 | 实质无上限 |

推论：

- L1 决定**能否被用上**——描述写砸了，再好的 skill 也永远不触发。
- L2 每一句都在花常驻预算，触发即全量载入。写之前先问：这句值这些 token 吗？
- L3 是免费额度。**详细内容一律下沉到 L3**，L2 只留流程骨架和跳转指引。
- `scripts/` 里的代码可以被**执行而不必读入上下文**，是最省 token 的形态。

---

## 3. description 决定一切：抗「欠触发」

官方 `skill-creator` 明确指出：**Claude 目前倾向于「欠触发」（undertrigger）**——
该用 skill 的时候不用。对策是把描述写得**主动一点、推一把**。

对照：

```yaml
# 弱：只说了做什么
description: Helps with PDFs.

# 弱：描述准确但被动
description: How to build a fast dashboard for internal data.

# 强：做什么 + 何时用 + 具体触发词 + 覆盖用户没明说的场景
description: >-
  Extracts text and tables from PDFs, fills forms, merges files.
  Use this skill whenever the user mentions PDFs, forms, or document
  extraction, even if they don't explicitly ask for extraction.
```

要点：

1. **前半句说能力，后半句说触发场景**，缺一不可。
2. 塞入用户真实会说的**关键词**（同义词、中英文都放）。
3. 加上「即使用户没有明确要求 X」这类兜底句，显著提升召回。
4. 用第三人称写（"Use this skill when..."），不要写"当你想要…"。
5. 所有「何时使用」的信息**只放 description**，不要放正文——正文只在触发后才被读到，那时判断已经做完了。

补充机制：Claude 只在任务**自己搞不定**时才去查 skill。
"读一下这个 PDF" 这种一步到位的简单请求，描述写得再好也可能不触发——这是正常行为，不是缺陷。
所以设计测试用例时，要用足够复杂的多步任务，否则测不出描述质量。

---

## 4. 目录组织

```
skill-name/
├── SKILL.md          # 必需
├── scripts/          # 可执行代码：确定性任务、重复逻辑
├── references/       # 按需载入的文档：schema、API 文档、领域知识
└── assets/           # 产出物用的素材：模板、图标、字体
```

三类资源的判断标准：

- **`scripts/`** — 同一段代码被反复重写，或需要确定性结果时。收益：省 token、结果稳定、可不读入上下文直接执行。
- **`references/`** — agent 工作时需要查阅的资料。单文件保持聚焦；**超过 1 万字要在 SKILL.md 里给出 grep 检索模式**。
- **`assets/`** — 不进上下文，只用于最终产出的文件。

引用规则：用相对路径，**保持一层深度**，避免多级跳转链。

```markdown
See [the reference guide](references/REFERENCE.md) for details.
Run: scripts/extract.py
```

**避免重复**：同一信息只存在于 SKILL.md 或引用文件之一，不要两边都写。
除非确实是技能核心，否则一律下沉——这样 SKILL.md 保持精简，信息又仍可被发现。

---

## 5. 正文写作模式

- **用祈使句**。"Extract the table" 而不是 "You should extract the table"。
- **给出显式的 Do / Don't 列表**。AI 有固定的失败模式（滥用 div、内联样式、生成占位文本），
  明确禁止比正面描述更有效。
- **3–5 个精选示例**足以确立模式，不必堆砌。
- **给工作流清单**（`- [ ] 步骤一`）来约束多步任务。
- **自由度要匹配任务**：
  - 固定任务 → 用脚本消除歧义（低自由度）
  - 创造性任务 → 用伪代码或文字引导（高自由度）

---

## 6. 安全红线（Principle of Lack of Surprise）

官方原则：**skill 的内容不应让用户感到意外**。如果如实描述它做的事，用户不会吃惊。

禁止：恶意代码、漏洞利用、误导性技能、协助未授权访问或数据外泄。
（角色扮演类技能本身是可以的。）

从 GitHub 安装第三方 skill 前必查：

| 级别 | 模式 | 处置 |
|---|---|---|
| P0 | `curl \| sh`、`rm -rf ~`、base64 解码执行、对网络内容 `eval` | 拒绝安装 |
| P1 | 读取 SSH 私钥 / 云凭据、硬编码密钥、清除 shell 历史 | 逐行人工审阅 |
| P2 | `shell=True`、外发 POST 请求、安装依赖 | 确认符合预期 |

注意 P1/P2 常有误报——文档里的 `export API_KEY="your-api-key"` 只是占位符。
命中不等于恶意，但**必须人工过目**。

---

## 7. 验证

官方提供参考实现：

```bash
skills-ref validate ./my-skill
```

仓库：<https://github.com/agentskills/agentskills/tree/main/skills-ref>

本技能的 `scripts/fetch_skill.py` 内置了等效校验（frontmatter 规则 + 行数/token 预算 + 安全审计），
无需额外安装即可对任意 GitHub skill 做体检。

---

## 8. 实测得到的高频问题

对官方与社区 skill 批量体检后，最常见的四类问题：

1. **description 缺「何时使用」线索** — 最普遍。连官方 `webapp-testing` 也只写了能力没写触发场景。
2. **description 超 1024 上限** — 多见于用块标量写长描述时，如官方 `claude-api`（1068 字符）。
3. **正文严重超预算** — 把该进 `references/` 的内容全塞在 SKILL.md，动辄 2 万 token。
4. **name 与目录名不一致** — 复制改名时最容易漏掉。
