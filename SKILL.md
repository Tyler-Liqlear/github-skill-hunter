---
name: github-skill-hunter
description: >-
  Searches GitHub for Agent Skills, scores and audits them, then installs the
  good ones locally. Also validates and writes SKILL.md against the official
  agentskills.io spec. Use this skill whenever the user wants to find, search,
  discover, evaluate, install, import, or borrow an agent skill from GitHub, or
  asks to write, review, fix, or optimize a SKILL.md, skill description, or
  skill directory structure. Also use it when the user says 找技能, 搜索 skill,
  安装技能, 导入技能, 写技能, 技能规范, or asks why a skill is not triggering.
  Use it even when they only vaguely mention wanting some existing skill for a
  task, rather than explicitly asking to search GitHub.
  Also hunts for ANY valuable open-source project to reuse or fork: evaluate
  whether it is still maintained, how active the community is (stars/forks),
  how hard it is to deploy, which features are reusable, and whether its tech
  stack fits the user's need. Use it whenever the user asks 找开源项目, 搜索项目,
  这个项目能不能直接用, 有没有现成的轮子, reuse / fork / build-from-scratch
  decision, or wants a simplest MVP plan for an idea.
license: MIT
compatibility: Requires Python 3.8+ and internet access. GITHUB_TOKEN env var optional but recommended.
metadata:
  author: WorkBuddy
  version: "1.1"
---

# GitHub Skill Hunter

在 GitHub 上找到可用的 Agent Skill，审计它，装下来；或者按官方规范写一个新的。

三个脚本，零第三方依赖：

- `scripts/search_skills.py` — 搜索 Agent Skill 并按可用性评分
- `scripts/fetch_skill.py` — 列举、校验、安全审计、安装
- `scripts/scout_projects.py` — 侦察任何有价值的开源项目，做五维评估（维护/活跃/部署/复用/技术栈）

规范速查见 `references/skill-spec.md`（frontmatter 约束、渐进式披露、触发优化、安全红线）。

---

## 何时做什么

| 用户意图 | 动作 |
|---|---|
| 想找某类 skill | 走「搜索」流程 |
| 给了具体仓库 | 直接跳「审计与安装」 |
| 想写/改 skill | 读 `references/skill-spec.md`，走「编写」流程 |
| skill 不触发 | 走「诊断触发问题」 |
| 想找能复用的开源项目 / 判断该不该自研 | 走「侦察可复用的开源项目」 |

---

## 搜索

```bash
python scripts/search_skills.py "关键词" --limit 15
python scripts/search_skills.py "pdf 表单" --json results.json
python scripts/search_skills.py --topics-only --limit 30   # 泛览生态
```

评分同时考虑 star（取对数，避免大仓库碾压）、最近推送时间、名称/topic 的 skill 信号、
关键词相关性，以及**仓库结构实探**——即真的去看有没有 `SKILL.md` 或 `skills/` 目录。
最后一项最关键：它把「蹭关键词的大项目」和「真正的 skill 仓库」区分开。

结构标记的含义：

- `单技能 (根 SKILL.md)` — 直接可装
- `技能集合 (skills/)` — 先 `--list` 看里面有哪些
- `插件包 (.claude/.agents)` — 多为 marketplace 形态，需要挑
- `仅文档 / 清单` — 通常是 awesome 列表，去它的 README 里找链接

**匿名调用只有 60 次/小时**。设置 `GITHUB_TOKEN` 提到 5000 次/小时并解锁 SKILL.md 内容搜索。
遇到 403 时脚本会明确告知重置时间，不会静默失败。

---

## 侦察可复用的开源项目

不止能找 skill——任何「想偷懒不重复造轮子」的需求都可以用 `scout_projects.py` 侦察一遍。
它把 GitHub 搜索结果跑一遍**五维评估**，直接回答用户最关心的五个问题：

| 维度 | 字段 | 怎么算 |
|---|---|---|
| 1. 是否还在维护 | `maintenance` | 按最后 push 时间分档（≤30天=100；归档=0） |
| 2. Star / 社区活跃度 | `popularity` + `activity` | star 取对数压缩，避免大仓库碾压；活跃度看近期提交 |
| 3. 部署是否麻烦 | `deploy_ease` | 按语言生态给基准分（go/rust 高，java/c++ 低）；`--deep` 检测到 Dockerfile 再加分 |
| 4. 哪些功能可复用 | `reuse` + README `Features` 提取 | 按 license 友好度给分（MIT/Apache 高，无 license=15 风险分） |
| 5. 技术栈是否合适 | `stack_fit` | 用 `--stack` 指定期望栈，命中语言/topic/描述给分 |

每个仓库最后落一个 verdict 标签：

- ✅ **推荐直接复用 / 二次开发** — 维护活跃 + license 友好 + 技术栈吻合
- 🟡 **可基于现有项目修改** — 维护尚可 + license 可用，但需要改
- 🔴 **建议参考后自研** — 维护停滞或技术栈不符，只值得当参考
- ⚠ **已归档 / license 受限** — 归档项目或 GPL/AGPL/无 license，复用需谨慎

```bash
# 基础侦察：你的项目想法 + 期望技术栈
python scripts/scout_projects.py "personal portfolio website" --stack html,javascript --min-stars 200

# 深探：额外检查 Dockerfile、抓取 README 的 Features 列表
python scripts/scout_projects.py "tactical shooter game" --stack html,javascript --limit 8 --deep

# 机器可读：导出 JSON 留档
python scripts/scout_projects.py "ai agent framework" --stack python --json scout.json
```

输出末尾的「综合建议」会直接给出 **直接用 / 基于修改 / 自建** 的项目计数与首选推荐，
正好对应「应该直接用、基于现有项目修改，还是自己开发」的决策问题。

**给用户的 MVP 建议写法**（用脚本结论驱动，不要凭空编）：

1. 看 verdict 里第一个 ✅/🟡 仓库——它就是「能直接复用」的现成轮子，先 `git clone` 跑起来当基线。
2. 若 ✅/🟡 都没有，看 🟡 里最接近的一个，做「最小改动 fork」：只改入口/配置/你自己的内容。
3. 只有 🔴 时，说明没有合适的开源基础，按「最简单 MVP」自建：先实现 1 个核心闭环（能跑、能看、能交互），再迭代。
4. MVP 永远从「用户能感知的最小可用」起步，不要在第一版就补齐全功能。

---

## 审计与安装

顺序固定：**先列举 → 再 dry-run 审计 → 最后才装**。跳过审计直接装第三方代码是不可接受的。

```bash
# 1. 看这个仓库里有什么
python scripts/fetch_skill.py anthropics/skills --list

# 2. 审计，不落盘
python scripts/fetch_skill.py anthropics/skills --path skills/pdf --dry-run

# 3. 确认无误后安装
python scripts/fetch_skill.py anthropics/skills --path skills/pdf --out ~/.workbuddy/skills
```

拉取走 codeload tarball 快照，**不消耗 API 配额**，几秒完成整仓下载。

安装位置：

- 用户级 `~/.workbuddy/skills/` — 跨项目通用（默认选这个）
- 项目级 `<workspace>/.workbuddy/skills/` — 只在当前项目生效

### 审计结果怎么读

脚本输出两段。**两段都要看**。

规范校验：`[不合规]` 必须修复；`[提醒]` 影响质量与触发率。

安全审计分三级：

- **P0** — `curl | sh`、`rm -rf ~`、base64 解码执行等。脚本会**直接拒绝安装**。要装只能人工逐行读完再手动复制。
- **P1** — 读 SSH 私钥、云凭据、硬编码密钥。逐行审阅后再决定。
- **P2** — `shell=True`、外发请求、装依赖。确认符合技能声称的用途即可。

误报很常见：文档里的 `export API_KEY="your-api-key"` 只是占位符。
**命中不等于恶意，但必须人工过目。**向用户汇报时要说清楚是真风险还是占位符。

---

## 编写新技能

先读 `references/skill-spec.md`，再动手。核心约束：

- `name` 必须与目录名完全一致，只能小写字母/数字/单连字符
- `description` ≤1024 字符，且**必须同时写清「做什么」和「何时用」**
- SKILL.md 正文 <500 行 / <5000 tokens，详细内容一律下沉到 `references/`
- 用祈使句；给显式的 Do / Don't；固定任务用脚本、创造性任务用文字引导

写完立刻自检：

```bash
python scripts/fetch_skill.py <owner>/<repo> --path <dir> --dry-run
```

本地未提交的 skill 也可以直接看规格表逐条对照。

---

## 诊断触发问题

skill 不触发，**99% 是 description 的问题**，不是正文的问题。

排查顺序：

1. **描述里有没有「何时使用」？** 只写了能力没写场景 → 必然欠触发。
2. **有没有用户真会说的关键词？** 包括同义词、中英文两套。
3. **够不够「推」？** Claude 天然倾向欠触发。加上
   "Use this skill whenever the user mentions X, even if they don't explicitly ask for Y"
   这类兜底句，召回会明显改善。
4. **是不是任务太简单？** Claude 只在自己搞不定时才查 skill。
   "读一下这个文件"这类一步请求不触发是**正常行为**，别为此改坏描述。
5. **「何时用」是不是误写进正文了？** 正文只在触发后才被读到，那时判断已经做完了。全部信息必须在 description 里。

---

## 红线

- **永不执行**拉取到的代码来「测试」它。审计是纯静态读取。
- 存在 P0 风险时，**不要为了完成任务而绕过拦截**。
- 安装前把审计结论如实告诉用户，包括你判断为误报的项及理由。
- 不制作或安装意图具有误导性的技能。
