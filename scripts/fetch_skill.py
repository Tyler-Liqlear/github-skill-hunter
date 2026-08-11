#!/usr/bin/env python3
"""
从 GitHub 拉取 Agent Skill 到本地，并做规范校验 + 安全审计。

采用 tarball 一次性下载整仓快照(走 codeload，不消耗 GitHub API 配额)，
比逐文件调 API 快几十倍，也不会因匿名限流卡死。

用法:
    # 列出仓库里所有 skill (推荐先做这步)
    python fetch_skill.py anthropics/skills --list

    # 只审计不落盘
    python fetch_skill.py anthropics/skills --path skills/pdf --dry-run

    # 审计通过后安装到对方 AI 的 skills 目录
    # (Claude Code 用 ~/.claude/skills；WorkBuddy 用 ~/.workbuddy/skills)
    python fetch_skill.py anthropics/skills --path skills/pdf --out ~/.claude/skills

安全: 本工具永不执行被拉取的代码，只做静态读取与模式审计。
安装前请务必阅读"安全审计"段落。
"""
import argparse
import io
import json
import os
import re
import sys
import tarfile
import urllib.error
import urllib.request

CODELOAD = "https://codeload.github.com"
API = "https://api.github.com"
UA = "github-skill-hunter/1.0"

TEXT_EXT = re.compile(r"\.(py|sh|bash|js|mjs|ts|md|json|ya?ml|txt|toml|cfg|ini)$", re.I)

# 高风险模式。命中不等于恶意，但必须人工过目。
RISK_PATTERNS = [
    (r"curl[^\n|]*\|\s*(ba)?sh", "P0", "管道执行远程脚本 (curl | sh)"),
    (r"wget[^\n|]*\|\s*(ba)?sh", "P0", "管道执行远程脚本 (wget | sh)"),
    (r"\brm\s+-rf\s+[~/$]", "P0", "递归删除家目录或根路径"),
    (r"base64\s+(-d|--decode)|b64decode", "P0", "base64 解码(常见于混淆载荷)"),
    (r"eval\s*\(\s*(requests|urllib|fetch|open)", "P0", "对外部内容执行 eval"),
    (r"exec\s*\(\s*(requests|urllib|fetch)", "P0", "对外部内容执行 exec"),
    (r"\.ssh/id_(rsa|ed25519|dsa)", "P1", "访问 SSH 私钥"),
    (r"(aws|gcp|azure)[_-]?(secret|credential)|\.aws/credentials", "P1", "读取云厂商凭据"),
    (r"(GITHUB_TOKEN|API_KEY|SECRET_KEY|PASSWORD)\s*=\s*[\"'][^\"']{8,}", "P1", "疑似硬编码密钥"),
    (r"history\s*-c|\.bash_history", "P1", "操作 shell 历史记录"),
    (r"os\.environ(\.get)?\s*[\(\[][\"'][^\"']*(TOKEN|KEY|SECRET|PASSWORD)", "P2", "读取凭据类环境变量"),
    (r"subprocess\.(run|call|Popen)[^\n]*shell\s*=\s*True", "P2", "shell=True 执行子进程"),
    (r"requests\.(post|put)\s*\(|fetch\([^\n]*method:\s*[\"']POST", "P2", "向外部发送数据"),
    (r"pip\s+install|npm\s+i(nstall)?\s|uv\s+pip\s+install", "P2", "安装外部依赖"),
]

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_CACHE = {}


def download_tarball(repo, branch=None, token=None):
    """下载仓库快照。返回 tarfile 对象。走 codeload，不吃 API 配额。"""
    key = (repo, branch)
    if key in _CACHE:
        return _CACHE[key], None

    branches = [branch] if branch else ["main", "master"]
    last = None
    for br in branches:
        url = f"{CODELOAD}/{repo}/tar.gz/refs/heads/{br}"
        headers = {"User-Agent": UA}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            tf = tarfile.open(fileobj=io.BytesIO(data))
            _CACHE[key] = tf
            return tf, None
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} (分支 {br})"
            continue
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
    return None, f"下载仓库快照失败: {last}。请确认仓库存在，或用 --branch 指定分支。"


def read_tree(tf):
    """把 tarball 读成 {相对路径: 内容} 字典，跳过超大文件和二进制。"""
    tree = {}
    for m in tf.getmembers():
        if not m.isfile():
            continue
        parts = m.name.split("/", 1)
        if len(parts) < 2:
            continue
        rel = parts[1]
        # 防路径穿越
        if ".." in rel.split("/") or rel.startswith("/"):
            continue
        if m.size > 500_000:
            continue
        if not TEXT_EXT.search(rel) and not rel.endswith("SKILL.md"):
            tree[rel] = None  # 记录存在但不读内容(如图片等资产)
            continue
        try:
            f = tf.extractfile(m)
            if f is None:
                continue
            tree[rel] = f.read().decode("utf-8", "replace")
        except Exception:
            continue
    return tree


def parse_frontmatter(text):
    """解析 SKILL.md 的 YAML frontmatter，不依赖第三方库。"""
    if not text.startswith("---"):
        return None, text, "缺少 YAML frontmatter (文件未以 --- 开头)"
    end = text.find("\n---", 3)
    if end == -1:
        return None, text, "frontmatter 未正确闭合"
    fm_raw = text[3:end].strip("\n")
    body = text[end + 4:]

    fm, current = {}, None
    lines = fm_raw.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # 嵌套映射项 (如 metadata: 下的子键)
        if line[:1] in (" ", "\t") and current:
            m = re.match(r"\s+([\w.-]+)\s*:\s*(.*)", line)
            if m and isinstance(fm.get(current), dict):
                fm[current][m.group(1)] = m.group(2).strip().strip("\"'")
            i += 1
            continue

        m = re.match(r"([\w.-]+)\s*:\s*(.*)", line)
        if not m:
            i += 1
            continue

        k, v = m.group(1), m.group(2).strip()
        current = k

        # YAML 块标量: | |- |+ > >- >+
        if re.match(r"^[|>][+-]?$", v):
            folded = v.startswith(">")
            block, i = [], i + 1
            base_indent = None
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and not nxt[:1].isspace():
                    break
                if nxt.strip():
                    ind = len(nxt) - len(nxt.lstrip())
                    base_indent = ind if base_indent is None else min(base_indent, ind)
                block.append(nxt)
                i += 1
            base_indent = base_indent or 0
            cleaned = [b[base_indent:] if len(b) >= base_indent else b.strip() for b in block]
            while cleaned and not cleaned[-1].strip():
                cleaned.pop()
            fm[k] = (" ".join(s.strip() for s in cleaned if s.strip())
                     if folded else "\n".join(cleaned))
            continue

        fm[k] = {} if v == "" else v.strip("\"'")
        i += 1
    return fm, body, None


def validate(fm, body, dir_name=None):
    """按 agentskills.io 官方规范校验。"""
    errors, warns = [], []
    if fm is None:
        return ["无法解析 frontmatter"], []

    name = fm.get("name")
    if not name:
        errors.append("缺少必填字段 name")
    elif not isinstance(name, str):
        errors.append("name 必须是字符串")
    else:
        if len(name) > 64:
            errors.append(f"name 超过 64 字符上限 (当前 {len(name)})")
        if not NAME_RE.match(name):
            errors.append(f"name '{name}' 不合规: 仅允许小写字母/数字/单连字符，"
                          "不可首尾为连字符或出现连续连字符")
        if dir_name and name != dir_name:
            warns.append(f"name '{name}' 与目录名 '{dir_name}' 不一致(规范要求一致)")

    desc = fm.get("description")
    if not desc:
        errors.append("缺少必填字段 description")
    elif isinstance(desc, str):
        n = len(desc)
        if n > 1024:
            errors.append(f"description 超过 1024 字符上限 (当前 {n})")
        if n < 40:
            warns.append(f"description 仅 {n} 字符，过短会显著降低触发率")
        low = desc.lower()
        if not any(w in low for w in ("use when", "use this", "when the user",
                                      "when you", "使用", "当用户", "何时")):
            warns.append("description 缺少明确的『何时使用』线索，容易欠触发")

    compat = fm.get("compatibility")
    if isinstance(compat, str) and len(compat) > 500:
        errors.append(f"compatibility 超过 500 字符上限 (当前 {len(compat)})")

    lines = body.count("\n") + 1
    if lines > 500:
        warns.append(f"正文 {lines} 行，超出推荐的 500 行，建议下沉到 references/")
    tok = int(len(body) / 3.5)
    if tok > 5000:
        warns.append(f"正文约 {tok} tokens，超出推荐的 5000，会挤占上下文")
    if not body.strip():
        errors.append("SKILL.md 正文为空")

    return errors, warns


_DEMOTE = {"P0": "P2", "P1": "P2", "P2": "P2"}


def _in_inline_code(line, col):
    """判断某列是否落在 markdown 行内反引号代码片段中(该列前反引号数为奇数)。"""
    return line[:col].count("`") % 2 == 1


# 匹配 RISK_PATTERNS 那样的三元组规则定义行。
# 安全工具扫描自身规则库时必然命中自己的模式串，属自指现象，需降级。
_RULE_DEF = re.compile(r"""^\s*\(\s*r["'].*["']\s*,\s*["']P[012]["']\s*,""")


def _is_rule_definition(line):
    return bool(_RULE_DEF.match(line))


def audit(tree, prefix):
    """
    对 skill 目录下所有文本文件做安全审计。

    对 .md 文件做上下文感知：文档在"讨论"某个危险模式(写在行内反引号里)，
    和脚本真的"执行"它，是两回事。前者降级，否则安全文档自己会被判定为恶意。
    """
    hits = []
    for rel, content in tree.items():
        if content is None or not rel.startswith(prefix):
            continue
        short = rel[len(prefix):].lstrip("/") or rel
        is_md = rel.lower().endswith(".md")
        lines = content.split("\n")

        # 标记 fenced code block 内的行：文档里的代码块仍按原级别处理
        in_fence, fenced = False, set()
        if is_md:
            for i, ln_txt in enumerate(lines):
                if ln_txt.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    fenced.add(i)

        for pat, level, why in RISK_PATTERNS:
            for m in re.finditer(pat, content, re.I):
                ln = content[:m.start()].count("\n")
                line_txt = lines[ln] if ln < len(lines) else ""
                col = m.start() - (content.rfind("\n", 0, m.start()) + 1)

                lvl, reason = level, why
                if is_md and ln not in fenced and _in_inline_code(line_txt, col):
                    lvl = _DEMOTE[level]
                    reason = f"{why}（文档引述，非执行）"
                elif _is_rule_definition(line_txt):
                    lvl = _DEMOTE[level]
                    reason = f"{why}（安全规则表定义，非执行）"

                hits.append((lvl, reason, short, ln + 1, line_txt.strip()[:110]))
    return hits


def find_skills(tree):
    """扫描整棵树，找出所有含 SKILL.md 的目录。"""
    out = []
    for rel in tree:
        if rel.endswith("SKILL.md") or rel.endswith("skill.md"):
            d = rel.rsplit("/", 1)[0] if "/" in rel else ""
            content = tree.get(rel) or ""
            fm, _, _ = parse_frontmatter(content)
            out.append({
                "path": d,
                "name": (fm or {}).get("name", "?"),
                "description": (fm or {}).get("description", "")[:120],
            })
    return sorted(out, key=lambda x: x["path"])


def read_local(root):
    """把本地 skill 目录读成 {相对路径: 内容}，用于离线自检。"""
    tree = {}
    root = os.path.abspath(os.path.expanduser(root))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       (".git", "__pycache__", "node_modules", ".venv")]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            try:
                if os.path.getsize(fp) > 500_000:
                    tree[rel] = None
                    continue
            except OSError:
                continue
            if not TEXT_EXT.search(rel) and not rel.endswith("SKILL.md"):
                tree[rel] = None
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    tree[rel] = f.read()
            except Exception:
                tree[rel] = None
    return tree


def main():
    ap = argparse.ArgumentParser(description="拉取并审计 GitHub 上的 Agent Skill")
    ap.add_argument("repo", help="owner/repo，如 anthropics/skills；配合 --local 时为本地目录路径")
    ap.add_argument("--local", action="store_true",
                    help="校验本地 skill 目录而非 GitHub 仓库(写完 skill 后自检用)")
    ap.add_argument("--path", default="", help="skill 所在目录，如 skills/pdf")
    ap.add_argument("--branch", default="", help="分支名(默认自动试 main/master)")
    ap.add_argument("--out", default="", help="安装目录(默认 ./skills)")
    ap.add_argument("--dry-run", action="store_true", help="只审计，不写入任何文件")
    ap.add_argument("--list", action="store_true", help="列出仓库中所有 skill")
    ap.add_argument("--json", metavar="FILE", help="把报告写成 JSON")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    if args.local:
        root = os.path.abspath(os.path.expanduser(args.repo))
        if not os.path.isdir(root):
            print(f"错误: 目录不存在 {root}")
            return 1
        tree = read_local(root)
        args.dry_run = True  # 本地模式恒为只读
        if not args.path and "SKILL.md" not in tree:
            found = find_skills(tree)
            if len(found) == 1:
                args.path = found[0]["path"]
        print(f"本地校验: {root} ({len(tree)} 个文件)\n")
    else:
        print(f"拉取 {args.repo} 快照中…")
        tf, err = download_tarball(args.repo, args.branch or None, token)
        if err:
            print("错误:", err)
            return 1
        tree = read_tree(tf)
        print(f"快照就绪: {len(tree)} 个文件\n")

    if args.list:
        found = find_skills(tree)
        if not found:
            print("未发现任何 SKILL.md")
            return 1
        print(f"发现 {len(found)} 个技能:\n")
        for s in found:
            loc = s["path"] or "(仓库根目录)"
            print(f"  {s['name']:<28} --path {loc}")
            if s["description"]:
                print(f"  {'':<28} {s['description']}")
        return 0

    prefix = args.path.strip("/")
    skill_md_key = f"{prefix}/SKILL.md" if prefix else "SKILL.md"
    if skill_md_key not in tree:
        print(f"错误: 未找到 {skill_md_key}")
        print("提示: 先运行 --list 查看该仓库有哪些 skill")
        return 1

    content = tree[skill_md_key]
    if prefix:
        dir_name = prefix.split("/")[-1]
    elif args.local:
        dir_name = os.path.basename(os.path.abspath(os.path.expanduser(args.repo)))
    else:
        dir_name = None
    fm, body, fm_err = parse_frontmatter(content)
    errors, warns = validate(fm, body, dir_name)
    if fm_err:
        errors.insert(0, fm_err)

    if prefix:
        files = {k: v for k, v in tree.items()
                 if k == skill_md_key or k.startswith(prefix + "/")}
    else:
        # 根目录型 skill：整棵树都属于它(含 scripts/ references/ 等子目录)
        files = dict(tree)
    risks = audit(files, prefix)

    name = (fm or {}).get("name") or dir_name or "unnamed-skill"
    print(f"技能: {name}")
    print(f"来源: {args.repo}/{prefix or '(根)'}")
    d = (fm or {}).get("description") or ""
    print(f"描述: {d[:220]}{'…' if len(d) > 220 else ''}")
    rels = sorted(k[len(prefix):].lstrip('/') if prefix else k for k in files)
    print(f"文件: {len(rels)} 个 -> {', '.join(rels[:8])}{' …' if len(rels) > 8 else ''}")

    print("\n--- 规范校验 (agentskills.io) ---")
    if errors:
        for e in errors:
            print(f"  [不合规] {e}")
    else:
        print("  通过: frontmatter 符合 Agent Skills 规范")
    for w in warns:
        print(f"  [提醒]   {w}")

    print("\n--- 安全审计 ---")
    if not risks:
        print("  未命中已知高风险模式")
    else:
        order = {"P0": 0, "P1": 1, "P2": 2}
        tag = {"P0": "严重", "P1": "警告", "P2": "注意"}
        for lvl, why, fn, ln, sn in sorted(risks, key=lambda x: order.get(x[0], 9))[:25]:
            print(f"  [{lvl} {tag[lvl]}] {why}")
            print(f"           {fn}:{ln}  {sn}")
        n0 = sum(1 for r in risks if r[0] == "P0")
        n1 = sum(1 for r in risks if r[0] == "P1")
        if n0:
            print(f"\n  >>> 命中 {n0} 个 P0 严重风险，不建议直接安装，请先逐行人工审阅。")
        elif n1:
            print(f"\n  >>> 命中 {n1} 个 P1 警告，安装前请确认这些行为符合预期。")

    installed = None
    if not args.dry_run:
        if any(r[0] == "P0" for r in risks):
            print("\n已中止安装: 存在 P0 风险。确认无误后可加 --dry-run 复查，或手动复制文件。")
            return 2
        out_dir = args.out or os.path.join(os.getcwd(), "skills")
        target = os.path.join(os.path.expanduser(out_dir), name)
        os.makedirs(target, exist_ok=True)
        cnt = 0
        for k, v in files.items():
            if v is None:
                continue
            rel = k[len(prefix):].lstrip("/") if prefix else k
            fp = os.path.join(target, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8", newline="\n") as f:
                f.write(v)
            cnt += 1
        installed = target
        print(f"\n已安装 {cnt} 个文件到: {target}")
    else:
        print("\n(dry-run: 未写入任何文件)")

    if args.json:
        rep = {
            "repo": args.repo, "path": prefix, "name": name,
            "description": d, "files": rels,
            "errors": errors, "warnings": warns,
            "risks": [{"level": l, "why": w, "file": f, "line": n, "snippet": s}
                      for l, w, f, n, s in risks],
            "installed_to": installed,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.json}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
