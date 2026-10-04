#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 GitHub 上搜索"有价值的开源项目"，并围绕「能否直接使用 / 二次开发」做五维评估。

五维评估（对应需求清单）:
  1. 是否还在维护   -> maintenance  (最后提交时间 / archived)
  2. Star/社区活跃度 -> popularity + activity (stars/forks/近期提交)
  3. 部署是否麻烦   -> deploy_ease  (语言生态 / Dockerfile / CI)
  4. 哪些功能可复用 -> reuse + README 的 Features 段落提取
  5. 技术栈是否合适 -> stack_fit    (用户用 --stack 指定期望栈)

输出每个项目的 verdict 标签:
  ✅ 推荐直接复用/二次开发 | 🟡 可基于现有项目修改 | 🔴 建议参考后自研 | ⚠ 已归档/license 受限

用法:
    python scout_projects.py "ai agent framework"
    python scout_projects.py "tactical shooter game" --stack c#,unity --limit 12
    python scout_projects.py "personal website" --stack html,javascript --deep --json out.json

认证(可选但推荐):
    设置 GITHUB_TOKEN 可将限速从 60次/小时 提升到 5000次/小时。
"""
import argparse
import base64
import json
import math
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

# 语言 -> 部署简易度基准分 (编译型单文件/解释型易部署给高分, 重运行时给低分)
DEPLOY_BASE = {
    "go": 85, "rust": 78, "python": 65, "javascript": 62, "typescript": 62,
    "c#": 55, "php": 52, "ruby": 52, "java": 45, "kotlin": 48,
    "c++": 40, "cpp": 40, "c": 40, "swift": 50, "dart": 55,
}
# 友好 license -> 高复用分
FRIENDLY_LIC = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause",
                "Unlicense", "ISC", "MPL-2.0", "0BSD", "CC0-1.0"}
WEAK_LIC = {"GPL-3.0", "LGPL-3.0", "EUPL-1.2"}
STRONG_LIC = {"AGPL-3.0"}


def _req(url, token=None, accept="application/vnd.github+json", retries=3):
    headers = {"Accept": accept, "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
                return json.loads(body), None
        except urllib.error.HTTPError as e:
            if e.code == 403:
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
                return {"__error__": f"403 速率限制{hint}，建议设置 GITHUB_TOKEN"}, None
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


def _days_since(iso):
    if not iso:
        return 9999
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 9999


def search_repos(query, token=None, per_page=30, sort="stars"):
    q = urllib.parse.quote(query)
    url = f"{API}/search/repositories?q={q}&sort={sort}&order=desc&per_page={per_page}"
    data, _ = _req(url, token)
    if "__error__" in data:
        return [], data["__error__"]
    return data.get("items", []), None


def _bar(score, width=10):
    filled = max(0, min(width, round(score / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def extract_features(readme_text, max_items=6):
    """从 README 提取 Features / 功能 段落里的列表项。"""
    # 找 ## Features / ## 功能 / ## 特性 段
    m = re.search(r"##\s*(?:features?|功能|特性|what's included|highlights)\b.*?\n(.*?)(?=\n##\s|\Z)",
                 readme_text, re.I | re.S)
    if not m:
        # 退化: 取前 40 行里的列表项
        chunk = "\n".join(readme_text.splitlines()[:40])
    else:
        chunk = m.group(1)
    items = re.findall(r"^\s*[-*]\s+(.+)$", chunk, re.M)
    out = []
    for it in items:
        it = re.sub(r"`{1,3}", "", it).strip()
        it = re.sub(r"\[(.+?)\]\(.*?\)", r"\1", it)  # 去 markdown 链接
        if 3 < len(it) < 90:
            out.append(it)
        if len(out) >= max_items:
            break
    return out


def evaluate(repo, stack_keywords):
    stars = repo.get("stargazers_count", 0)
    forks = repo.get("forks_count", 0)
    lang = (repo.get("language") or "").lower()
    topics = [t.lower() for t in (repo.get("topics") or [])]
    lic = None
    if repo.get("license"):
        lic = (repo.get("license") or {}).get("spdx_id")
    archived = repo.get("archived", False)
    pushed = repo.get("pushed_at") or repo.get("updated_at")
    days = _days_since(pushed)
    desc = (repo.get("description") or "").lower()

    # 1) 维护状态
    if archived:
        maint = 0
    elif days <= 30:
        maint = 100
    elif days <= 90:
        maint = 85
    elif days <= 180:
        maint = 70
    elif days <= 365:
        maint = 50
    elif days <= 730:
        maint = 30
    else:
        maint = 10

    # 2) 流行度 + 活跃度
    pop = min(100, math.log10(stars + 1) * 25 + math.log10(forks + 1) * 10)
    if days <= 30:
        act = 90
    elif days <= 90:
        act = 75
    elif days <= 180:
        act = 55
    elif days <= 365:
        act = 35
    else:
        act = 15

    # 3) 部署简易度 (基础按语言, deep 模式用 Dockerfile 修正)
    deploy = DEPLOY_BASE.get(lang, 50)

    # 4) 复用度 (license)
    if lic in FRIENDLY_LIC:
        reuse = 95
    elif lic in WEAK_LIC:
        reuse = 60
    elif lic in STRONG_LIC:
        reuse = 40
    elif lic is None:
        reuse = 15  # 无 license, 法律上的复用风险
    else:
        reuse = 70

    # 5) 技术栈匹配
    if stack_keywords:
        sk = [s.strip().lower() for s in stack_keywords if s.strip()]
        hit = 0
        for s in sk:
            if s == lang:
                hit += 2
            elif s in topics:
                hit += 2
            elif s in desc:
                hit += 1
        fit = 50 if not sk else (min(100, int(hit / len(sk) * 100)) if hit else 10)
    else:
        fit = 50  # 未指定栈, 中性, 交给调用方判断

    # 综合 verdict
    if maint == 0:
        verdict = "⚠ 已归档, 仅作参考"
    elif reuse < 30:
        verdict = "⚠ license 受限, 复用需谨慎"
    elif maint >= 70 and reuse >= 80 and fit >= 60:
        verdict = "✅ 推荐直接复用 / 二次开发"
    elif maint >= 50 and reuse >= 70:
        verdict = "🟡 可基于现有项目修改"
    else:
        verdict = "🔴 建议参考后自研"

    return {
        "maintenance": maint, "popularity": round(pop), "activity": act,
        "deploy_ease": deploy, "reuse": reuse, "stack_fit": fit,
        "verdict": verdict, "license": lic, "language": lang or "未知",
        "days_since_push": days, "forks": forks,
    }


def deep_enrich(repos, token, max_n=8):
    """deep 模式: 检查 Dockerfile / 抓 README 的 Features 列表。"""
    for r in repos[:max_n]:
        full = r["full_name"]
        ev = r.setdefault("_eval", {})
        # Docker / compose
        has_docker = False
        for f in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Dockerfile.dev"):
            d, _ = _req(f"{API}/repos/{full}/contents/{f}", token)
            if isinstance(d, dict) and "name" in d:
                has_docker = True
                ev["deploy_ease"] = min(100, ev.get("deploy_ease", 50) + 30)
                break
        r["_docker"] = has_docker
        # README
        rd, _ = _req(f"{API}/repos/{full}/readme", token)
        if isinstance(rd, dict) and "content" in rd:
            try:
                txt = base64.b64decode(rd["content"]).decode("utf-8", "replace")
                r["_features"] = extract_features(txt)
            except Exception:
                pass
    return repos


def run(query, limit, token, stack, sort, deep, min_stars):
    items, err = search_repos(query, token, per_page=min(limit * 2, 50), sort=sort)
    if err:
        return [], [err]
    seen = {}
    for it in items:
        if it.get("stargazers_count", 0) < min_stars:
            continue
        seen[it["full_name"]] = it
    repos = list(seen.values())
    for r in repos:
        r["_eval"] = evaluate(r, stack)
    repos.sort(key=lambda x: (-x["_eval"]["maintenance"],
                               -x["_eval"]["stack_fit"],
                               -x["_eval"]["popularity"]))
    if deep:
        repos = deep_enrich(repos, token, max_n=min(limit, 8))
        repos.sort(key=lambda x: (-x["_eval"]["maintenance"],
                                  -x["_eval"]["stack_fit"],
                                  -x["_eval"]["popularity"]))
    return repos[:limit], []


def to_row(r):
    ev = r.get("_eval", {})
    return {
        "full_name": r["full_name"],
        "url": r["html_url"],
        "stars": r.get("stargazers_count", 0),
        "eval": ev,
        "docker": r.get("_docker"),
        "features": r.get("_features", []),
        "topics": (r.get("topics") or [])[:5],
        "description": (r.get("description") or "")[:200],
    }


def main():
    ap = argparse.ArgumentParser(description="搜索并评估可复用的开源项目")
    ap.add_argument("query", help="搜索关键词, 如 'ai agent framework' 'tactical shooter game'")
    ap.add_argument("--limit", type=int, default=10, help="返回条数 (默认 10)")
    ap.add_argument("--stack", help="期望技术栈(逗号分隔), 如 'python,react' 'c#,unity'")
    ap.add_argument("--sort", default="stars", choices=["stars", "updated"],
                    help="排序: stars(热门) 或 updated(近期活跃)")
    ap.add_argument("--min-stars", type=int, default=0, help="过滤低于此星数的仓库")
    ap.add_argument("--deep", action="store_true", help="深探: 检查 Dockerfile + 抓 README 功能列表")
    ap.add_argument("--json", metavar="FILE", help="结果写入 JSON 文件")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    stack = args.stack.split(",") if args.stack else []

    print(f"侦察开源项目: 「{args.query}」")
    print(f"排序={args.sort}  期望栈={args.stack or '(未指定)'}  "
          f"认证={'是' if token else '否 (匿名 60次/小时)'}  deep={'是' if args.deep else '否'}\n")

    repos, errors = run(args.query, args.limit, token, stack,
                        args.sort, args.deep, args.min_stars)
    if not repos:
        print("未找到结果。")
        for e in errors:
            print("  !", e)
        return 1

    rank = 0
    for r in repos:
        rank += 1
        ev = r["_eval"]
        d = ev["days_since_push"]
        ago = f"{d}天前" if d < 9999 else "未知"
        lic = ev["license"] or "无"
        docker = "有" if r.get("_docker") else ("无" if args.deep else "—")
        print(f"\n#{rank} {ev['verdict']}")
        print(f"   {r['full_name']}  ★{r.get('stargazers_count',0)}  (语言: {ev['language']}, License: {lic})")
        print(f"   维护 {_bar(ev['maintenance'])} {ev['maintenance']:>3} | "
              f"活跃 {_bar(ev['activity'])} {ev['activity']:>3} | "
              f"部署 {_bar(ev['deploy_ease'])} {ev['deploy_ease']:>3} | "
              f"复用 {_bar(ev['reuse'])} {ev['reuse']:>3} | "
              f"栈匹配 {_bar(ev['stack_fit'])} {ev['stack_fit']:>3}")
        print(f"   最后更新: {ago} | Forks: {ev['forks']} | Docker: {docker} | 链接: {r['html_url']}")
        if r.get("description"):
            print(f"   简介: {r['description']}")
        if r.get("_features"):
            print(f"   可直接复用的功能:")
            for f in r["_features"][:5]:
                print(f"      - {f}")

    print("\n" + "=" * 70)
    print("综合建议 (基于以上评估):")
    rec = [r for r in repos if "✅" in r["_eval"]["verdict"]]
    mod = [r for r in repos if "🟡" in r["_eval"]["verdict"]]
    warn = [r for r in repos if "⚠" in r["_eval"]["verdict"]]
    print(f"  · 可直接复用/二次开发: {len(rec)} 个"
          + (f" → {rec[0]['full_name']}" if rec else ""))
    print(f"  · 可基于修改: {len(mod)} 个" + (f" → {mod[0]['full_name']}" if mod else ""))
    print(f"  · 需谨慎(归档/license): {len(warn)} 个")
    if not stack:
        print("  · 未指定 --stack, 技术栈匹配列为中性值; 请结合你的实际需求再判断。")
    print("\n下一步: 用 fetch_skill.py 把候选仓库拉到本地做安全审计, 或 git clone 后改造。")

    if args.json:
        payload = {
            "query": args.query, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "authenticated": bool(token), "results": [to_row(r) for r in repos],
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
