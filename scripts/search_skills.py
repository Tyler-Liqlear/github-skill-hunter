#!/usr/bin/env python3
"""
在 GitHub 上自动搜索 Agent Skills，并按可用性评分排序。

用法:
    python search_skills.py "pdf 处理"
    python search_skills.py "code review" --limit 20
    python search_skills.py --topics-only --limit 30
    python search_skills.py "unity shader" --json out.json

认证 (可选但强烈推荐):
    设置环境变量 GITHUB_TOKEN 可将速率限制从 60次/小时 提升到 5000次/小时,
    并解锁 code search (按 SKILL.md 内容精确搜索)。
    Windows:  set GITHUB_TOKEN=ghp_xxx
    bash:     export GITHUB_TOKEN=ghp_xxx
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
UA = "github-skill-hunter/1.0"

# 已知的高质量 skill 聚合仓库 —— 命中这些来源额外加分
CURATED_SOURCES = {
    "anthropics/skills": 40,
    "composiohq/awesome-claude-skills": 25,
    "voltagent/awesome-agent-skills": 22,
    "addyosmani/agent-skills": 22,
    "hesreallyhim/awesome-claude-code": 18,
    "github/awesome-copilot": 18,
    "wshobson/agents": 15,
}

# 搜索这些 topic 通常能捞到真正的 skill 仓库
SKILL_TOPICS = [
    "agent-skills",
    "claude-skills",
    "claude-skill",
    "agent-skill",
]


def _req(url, token=None, accept="application/vnd.github+json", retries=3):
    """带重试和限流处理的 GitHub API 请求。"""
    headers = {"Accept": accept, "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                remaining = r.headers.get("X-RateLimit-Remaining")
                body = r.read().decode("utf-8", "replace")
                return json.loads(body), remaining
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # 触发二级限流，退避重试
                if attempt < retries - 1:
                    time.sleep(2 ** attempt * 3)
                    continue
                reset = e.headers.get("X-RateLimit-Reset")
                hint = ""
                if reset:
                    try:
                        dt = datetime.fromtimestamp(int(reset), tz=timezone.utc)
                        hint = f" (限额重置于 {dt.astimezone().strftime('%H:%M:%S')})"
                    except Exception:
                        pass
                return {"__error__": f"403 速率限制{hint}。建议设置 GITHUB_TOKEN 环境变量。"}, None
            if e.code == 422:
                return {"__error__": "422 查询语法无法被 GitHub 接受"}, None
            if e.code == 404:
                return {"__error__": "404 未找到"}, None
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return {"__error__": f"HTTP {e.code}"}, None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return {"__error__": f"{type(e).__name__}: {e}"}, None
    return {"__error__": "重试耗尽"}, None


def search_repos(query, token=None, per_page=30):
    """搜索仓库。"""
    q = urllib.parse.quote(query)
    url = f"{API}/search/repositories?q={q}&sort=stars&order=desc&per_page={per_page}"
    data, _ = _req(url, token)
    if "__error__" in data:
        return [], data["__error__"]
    return data.get("items", []), None


def search_code_skillmd(keyword, token):
    """
    Code search: 在 SKILL.md 文件内容里搜关键词。
    这是找到"真正 skill"最精确的方式，但必须要 token。
    """
    if not token:
        return [], "code search 需要 GITHUB_TOKEN"
    q = urllib.parse.quote(f"{keyword} filename:SKILL.md")
    url = f"{API}/search/code?q={q}&per_page=30"
    data, _ = _req(url, token)
    if "__error__" in data:
        return [], data["__error__"]
    return data.get("items", []), None


def _days_since(iso):
    if not iso:
        return 9999
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


def score_repo(repo, keyword=""):
    """
    对仓库打分。评分不只看 star —— 一个 500 星但三天前更新的专用 skill,
    比 10 万星但只是顺带提了一句 skill 的大项目更有价值。
    """
    stars = repo.get("stargazers_count", 0)
    full = (repo.get("full_name") or "").lower()
    name = (repo.get("name") or "").lower()
    desc = (repo.get("description") or "").lower()
    topics = [t.lower() for t in (repo.get("topics") or [])]
    kw = keyword.lower().strip()

    score = 0.0

    # 1. star 取对数，避免超大仓库碾压一切
    import math
    score += math.log10(stars + 1) * 10

    # 2. 活跃度：最近更新的加分，长期不动的扣分
    d = _days_since(repo.get("pushed_at") or repo.get("updated_at"))
    if d <= 7:
        score += 25
    elif d <= 30:
        score += 18
    elif d <= 90:
        score += 10
    elif d <= 365:
        score += 2
    else:
        score -= 15

    # 3. 明确的 skill 信号
    if "skill" in name:
        score += 20
    if any("skill" in t for t in topics):
        score += 15
    if re.search(r"\bskills?\b", desc):
        score += 8

    # 4. 精选来源加权
    for src, bonus in CURATED_SOURCES.items():
        if full == src:
            score += bonus
            break

    # 5. 关键词相关性
    if kw:
        parts = [p for p in re.split(r"[\s\-_]+", kw) if len(p) > 1]
        for p in parts:
            if p in name:
                score += 12
            elif p in desc:
                score += 6
            elif any(p in t for t in topics):
                score += 5

    # 6. 归档 / 疑似模板仓库扣分
    if repo.get("archived"):
        score -= 40
    if repo.get("is_template"):
        score -= 5

    return round(score, 1)


def enrich_has_skillmd(repos, token, max_check=12):
    """
    对 top 结果做一次实探: 仓库里到底有没有 SKILL.md / skills 目录。
    这是区分"真 skill 仓库"和"蹭关键词仓库"的关键一步。
    """
    for repo in repos[:max_check]:
        full = repo["full_name"]
        data, _ = _req(f"{API}/repos/{full}/contents/", token)
        repo["_layout"] = "?"
        if isinstance(data, dict) and "__error__" in data:
            continue
        if not isinstance(data, list):
            continue
        names = {it["name"].lower(): it["type"] for it in data}
        if "skill.md" in names:
            repo["_layout"] = "单技能 (根 SKILL.md)"
            repo["_score"] = repo.get("_score", 0) + 25
        elif names.get("skills") == "dir":
            repo["_layout"] = "技能集合 (skills/)"
            repo["_score"] = repo.get("_score", 0) + 20
        elif names.get(".claude") == "dir" or names.get(".agents") == "dir":
            repo["_layout"] = "插件包 (.claude/.agents)"
            repo["_score"] = repo.get("_score", 0) + 12
        elif "readme.md" in names:
            repo["_layout"] = "仅文档 / 清单"
    return repos


def run(keyword, limit, token, topics_only=False, deep=True):
    seen = {}
    errors = []

    queries = []
    if not topics_only and keyword:
        queries.append(f"{keyword} skill")
        queries.append(f"{keyword} in:name,description,topics skill")
    for t in SKILL_TOPICS:
        queries.append(f"topic:{t} {keyword}".strip())

    for q in queries:
        items, err = search_repos(q, token, per_page=30)
        if err:
            errors.append(f"[{q}] {err}")
            if "403" in err:
                break  # 限流了，别再打了
            continue
        for it in items:
            fn = it["full_name"]
            if fn not in seen:
                seen[fn] = it

    repos = list(seen.values())
    for r in repos:
        r["_score"] = score_repo(r, keyword)

    repos.sort(key=lambda x: -x["_score"])

    if deep and repos:
        repos = enrich_has_skillmd(repos, token, max_check=min(limit, 12))
        repos.sort(key=lambda x: -x.get("_score", 0))

    return repos[:limit], errors


def to_row(r):
    return {
        "full_name": r["full_name"],
        "url": r["html_url"],
        "stars": r.get("stargazers_count", 0),
        "score": r.get("_score", 0),
        "layout": r.get("_layout", "?"),
        "pushed_days_ago": _days_since(r.get("pushed_at")),
        "topics": r.get("topics", [])[:5],
        "description": (r.get("description") or "")[:160],
        "license": (r.get("license") or {}).get("spdx_id") if r.get("license") else None,
    }


def main():
    ap = argparse.ArgumentParser(description="在 GitHub 上搜索 Agent Skills")
    ap.add_argument("keyword", nargs="?", default="", help="主题关键词，如 'pdf' 'code review'")
    ap.add_argument("--limit", type=int, default=15, help="返回条数 (默认 15)")
    ap.add_argument("--json", metavar="FILE", help="同时把结果写入 JSON 文件")
    ap.add_argument("--topics-only", action="store_true", help="只按 skill topic 搜，不做关键词泛搜")
    ap.add_argument("--no-deep", action="store_true", help="跳过仓库结构实探(更快但更不准)")
    ap.add_argument("--code", action="store_true", help="额外做 SKILL.md 内容搜索(需 GITHUB_TOKEN)")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    if not args.keyword and not args.topics_only:
        args.topics_only = True

    print(f"搜索中… 关键词={args.keyword or '(全部)'}  认证={'是' if token else '否 (匿名，60次/小时)'}\n")

    repos, errors = run(args.keyword, args.limit, token,
                        topics_only=args.topics_only, deep=not args.no_deep)

    if not repos:
        print("没有找到结果。")
        for e in errors:
            print("  !", e)
        return 1

    print(f"{'评分':>6}  {'星标':>8}  {'更新':>7}  {'结构':<22} 仓库")
    print("-" * 100)
    for r in repos:
        row = to_row(r)
        d = row["pushed_days_ago"]
        ago = f"{d}天前" if d < 9999 else "未知"
        print(f"{row['score']:>6}  {row['stars']:>8}  {ago:>7}  {row['layout']:<22} {row['full_name']}")
        if row["description"]:
            print(f"{'':>48}{row['description']}")

    if errors:
        print("\n提示:")
        for e in errors[:3]:
            print("  !", e)

    if args.code and token:
        print("\n=== SKILL.md 内容匹配 ===")
        items, err = search_code_skillmd(args.keyword, token)
        if err:
            print("  !", err)
        for it in items[:15]:
            print(f"  {it['repository']['full_name']}  ->  {it['path']}")

    if args.json:
        payload = {
            "query": args.keyword,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "authenticated": bool(token),
            "results": [to_row(r) for r in repos],
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.json}")

    print("\n下一步: 用 fetch_skill.py 把选中的 skill 拉到本地并做规范校验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
