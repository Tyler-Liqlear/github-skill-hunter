# 分发 github-skill-hunter

这是一个符合 [agentskills.io](https://agentskills.io) 开放规范的 Agent Skill。
它本身不依赖 WorkBuddy 专有 API（两个脚本都是 Python 标准库 + 网络请求），
因此可以放到任何支持该规范的 AI 客户端里使用。

---

## 方式一：直接复制目录（最简单，跨平台通用）

把整个 `github-skill-hunter/` 目录复制到对方 AI 的 skills 目录即可。

| AI 客户端 | 用户级（全局）位置 | 项目级位置 |
|-----------|-------------------|-----------|
| **WorkBuddy** | `~/.workbuddy/skills/github-skill-hunter/` | `<工作区>/.workbuddy/skills/github-skill-hunter/` |
| **Claude Code** | `~/.claude/skills/github-skill-hunter/` | `<项目>/.claude/skills/github-skill-hunter/` |
| **其他兼容 agentskills.io 的客户端** | 查阅该客户端文档中的 "skills 目录" | 同上 |

> `~` 在 Windows 上通常是 `C:\Users\你的用户名\`。

---

## 方式二：用本 zip 包

1. 解压 `github-skill-hunter.zip`
2. 得到 `github-skill-hunter/` 目录和本说明文件
3. 按"方式一"的表格放到对应的 skills 目录

---

## 方式三：推到 GitHub，让别人用本 skill 自己拉（最"自动"）

这个 skill 本身就是干这个的——对方装上后，只需运行：

```bash
# 列出你仓库里的所有 skill
python github-skill-hunter/scripts/fetch_skill.py <你的用户名>/<你的仓库> --list

# 拉取并审计某一个
python github-skill-hunter/scripts/fetch_skill.py <你的用户名>/<你的仓库> --path github-skill-hunter --dry-run
```

推送步骤（需要 `git`，仓库里无敏感信息）：

```bash
cd github-skill-hunter
git init && git add -A && git commit -m "add github-skill-hunter skill"
# 在 GitHub 建好空仓库后：
git remote add origin https://github.com/<你的用户名>/<你的仓库>.git
git push -u origin main
```

---

## 安装后验证

```bash
# 用 skill 自带的校验器做自举体检（应 0 个 P0）
python github-skill-hunter/scripts/fetch_skill.py github-skill-hunter --local
```

预期输出包含"安全审计：0 个 P0"之类，说明结构合法、无高危风险。

---

## 兼容性注意

- **运行时**：需要 Python 3.8+，以及能访问 `api.github.com` 的网络。
- **GITHUB_TOKEN**（可选但推荐）：匿名 GitHub API 限速 60 次/小时，配置 token 后升到 5000 次/小时，
  且能解锁按 SKILL.md 内容精确搜索。在运行环境里 `export GITHUB_TOKEN=xxx` 即可。
- **无专有依赖**：脚本不引用任何 WorkBuddy 内部接口，换到 Claude Code / 其他客户端同样可用。
- **触发词**：SKILL.md 的 `description` 已按"抗欠触发"原则写好中英文触发词。换平台后，
  如果该客户端支持 `description` 触发，用户说"找技能 / 搜 skill / 安装技能 / 写技能"等即可唤起。

---

## 进阶：上架到 WorkBuddy 推荐市场（BuiltinMarket）

如果想让更多人通过 WorkBuddy 内置的"安装技能"一句话安装，可把仓库提交到
WorkBuddy 的技能市场源（需遵循平台的提交/审核流程）。这超出纯文件分发范畴，
属于发布行为，按需再做。
