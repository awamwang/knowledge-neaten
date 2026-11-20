#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""knowledge-neaten · 确定性扫描（可跑雏形，不碰 AI、不写输入目录）。

对 --source 目录做只读体检，输出 suggestions.json + neaten-report.md：
  A 空/近空   B 重复(归一化哈希)   C 疑似过期/失效(信号命中, 无 AI 时 verdict=manual)   D 断链
报告只引用 路径+命中信号+置信度，默认不粘正文（excerpt_included:false）。

用法:
  python scripts/scan.py --source <dir> [--rules config/stale-rules.yaml] [--out out/<id>]
默认即 --no-ai；--ai 为第二期占位（会提示并回退确定性）。
仅依赖标准库；装了 PyYAML 且能读到规则文件时按文件覆盖内置默认阈值。
"""
from __future__ import annotations
import argparse, datetime, fnmatch, hashlib, json, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = HERE.parent / "config"

# 内置默认阈值（与 config/stale-rules.example.yaml 同值；缺 yaml 时即用此）
DEFAULTS = {
    "empty": {"zero_byte": True, "min_body_chars": 10},
    "duplicate": {"exact": True, "near": False, "similarity_threshold": 0.90},
    "stale": {
        "stale_days": 365, "orphan": True, "scan_dates": True,
        "date_lookback_days": 0, "ai_review": True, "min_confidence": 0.6,
        "exempt_paths": ["**/index.md", "**/README.md", "pages/主页*", "**/日志/**"],
    },
    "broken_links": True,
    "exclude": [".git/**", ".obsidian/**", ".logseq/**", "node_modules/**",
                "__pycache__/**", "templates/**", "**/README.md"],
    "whitelist_file": "whitelist.txt",
}

FRONT_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)$", re.S)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# —— 链接解析：移植自 knowledge-desensitization/scripts/build_index.py 的成熟做法 ——
# LINK_RE 抓整条 wiki 目标（含 #blockref / |alias），ASSET_RE 的 <?...>? 覆盖尖括号 autolink。
LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
ASSET_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)>\s]+)>?\s*\)")
NON_MD = ("http://", "https://", "mailto:", "#", "file://", "obsidian://")
# 扫描时跳过的目录名（任一路径段命中即跳过；同隔壁 SKIP_DIR_NAMES）
SKIP_DIR_NAMES = {".km", "vault", ".git", "bak", ".recycle", "recycle",
                  "node_modules", "__pycache__", ".obsidian", ".logseq", "logseq"}
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
             ".pdf", ".mp4", ".webm", ".mov", ".mp3", ".wav", ".m4a", ".zip", ".epub"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def journal_alias(date_str: str) -> str:
    y, m, d = date_str.split("-")
    return f"{MONTHS[int(m) - 1]} {ordinal(int(d))}, {y}"


def cn_journal_alias(date_str: str) -> str:
    y, m, d = date_str.split("-")
    return f"{y}年{int(m)}月{int(d)}日"


def journal_date(stem: str):
    try:
        parts = stem.replace("_", "-").split("-")
        if len(parts) != 3:
            return None
        y, m, d = parts
        return f"{y}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return None


def should_skip_rel(rel: str) -> bool:
    return any(seg in SKIP_DIR_NAMES or seg.lower() in SKIP_DIR_NAMES
               for seg in rel.split("/")[:-1])


def asset_exists(base, low, asset_names, src):
    """链接未解析成页面时，回退判定它是否其实是个已存在的附件（文件名含逗号/括号会被截断）。
    移植自 build_index.extract_assets 的廉价反查：asset 名作为正文/路径片段出现即视为命中。"""
    if not asset_names:
        return False
    fn = low.split("assets/")[-1] if "assets/" in low else low.split("/")[-1]
    fn = fn.lstrip("./")
    if fn in asset_names or any(n.startswith(fn) or fn.startswith(n) for n in asset_names):
        return True
    return any(f"assets/{n}" in base for n in asset_names)


def path_exists(base, rel, src):
    """文件型链接（./ ../ 相对路径、附件、同名页）按页面所在目录与根目录各试一次，存在即有效。"""
    b = base.replace("\\", "/")
    for cand in (os.path.normpath(str(src / Path(rel).parent / b)),
                 os.path.normpath(str(src / b.lstrip("./")))):
        try:
            if Path(cand).exists():
                return True
        except OSError:
            pass
    return False


def load_rules(path: Path | None):
    rules = json.loads(json.dumps(DEFAULTS))  # 深拷贝
    cand = None
    if path:
        cand = Path(path)
    else:
        for n in ("stale-rules.yaml", "stale-rules.example.yaml"):
            p = CFG / n
            if p.exists():
                cand = p
                break
    if cand and cand.exists():
        try:
            import yaml  # 可选
            data = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
            _merge(rules, data)
            rules["_rules_file"] = str(cand)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 规则文件未能解析，使用内置默认: {e}", file=sys.stderr)
    return rules


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def glob_hit(rel: str, patterns):
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch("/" + rel, p) for p in patterns)


def parse_file(fp: Path):
    raw = fp.read_text(encoding="utf-8", errors="ignore")
    m = FRONT_RE.match(raw)
    fm, body = (m.group(1), m.group(2)) if m else ("", raw)
    # frontmatter 里的 title/aliases（行解析，不依赖 yaml）
    titles = {fp.stem}
    for line in fm.splitlines():
        line = line.strip()
        if line.lower().startswith(("title:", "aliases:")):
            val = line.split(":", 1)[1].strip().strip("[]\"' ")
            for part in re.split(r"[,\s]+", val):
                part = part.strip().strip("\"'")
                if part:
                    titles.add(part)
    return raw, body, titles


def outlinks(body, raw):
    """返回原始链接目标（wiki + markdown/嵌入链接）；分类与跳过在解析阶段统一做。
    LINK_RE 抓 `[[...]]`（含 #block / |alias）；ASSET_RE 抓 `![]()`/`[]()`/`<url>`。"""
    targets = [t.strip() for t in LINK_RE.findall(raw)]
    targets += [t.strip() for t in ASSET_RE.findall(raw)]
    return [t for t in targets if t]


def main():
    ap = argparse.ArgumentParser(description="knowledge-neaten deterministic scan")
    ap.add_argument("--source", required=True, help="要体检的目录（只读）")
    ap.add_argument("--rules", default=None, help="规则 yaml；缺省找 config/stale-rules{,.example}.yaml")
    ap.add_argument("--out", default=str(HERE.parent / "out" / "manual"), help="报告输出目录")
    ap.add_argument("--ai", action="store_true", help="第二期占位：本期忽略")
    ap.add_argument("--report", default="neaten-report.md")
    args = ap.parse_args()

    src = Path(args.source).resolve()
    if not src.is_dir():
        print(f"[error] --source 不是目录: {src}", file=sys.stderr)
        return 2
    if args.ai:
        print("[note] --ai 为第二期(assess.py)；本次仅确定性扫描。", file=sys.stderr)

    R = load_rules(Path(args.rules) if args.rules else None)
    today = datetime.date.today()
    lookback = today - datetime.timedelta(days=R["stale"]["date_lookback_days"])
    stale_cutoff = today - datetime.timedelta(days=R["stale"]["stale_days"])

    # 第一趟：枚举 + 解析 + 建可解析别名表
    files, resolve, stems = {}, {}, set()

    def add_alias(name, rel):
        name = (name or "").strip()
        if not name:
            return
        nk = name.lower()
        for v in (nk, nk.replace("___", "/"), nk.replace("/", "___"),
                  nk.removesuffix(".md")):
            resolve.setdefault(v, rel)

    for fp in sorted(src.rglob("*.md")):
        rel = fp.relative_to(src).as_posix()
        if should_skip_rel(rel) or glob_hit(rel, R["exclude"]):
            continue
        raw, body, titles = parse_file(fp)
        files[rel] = {"path": fp, "rel": rel, "body": body, "titles": titles,
                      "links": outlinks(body, raw), "size": fp.stat().st_size,
                      "mtime": fp.stat().st_mtime}
        stem = fp.stem
        stems.add(stem.lower())
        keys = set(titles) | {stem, rel.removesuffix(".md")}
        parts = rel.split("/")
        if parts[0] in ("pages", "journals"):
            keys.add("/".join(parts[1:]).removesuffix(".md"))
        for k in keys:
            add_alias(k, rel)
        if rel.startswith("journals/"):
            date = journal_date(stem)
            if date:
                for k in (date, journal_alias(date), cn_journal_alias(date)):
                    add_alias(k, rel)

    # 附件名集合（assets/ 下真实文件；用于把媒体链接与"真断链"区分开）
    asset_names = set()
    assets_dir = src / "assets"
    if assets_dir.is_dir():
        asset_names = {p.name.lower() for p in assets_dir.iterdir() if p.is_file()}

    # 入度 + 断链收集（外链/已存在附件/日期别名/层级页名 均不再误判为断链）
    in_deg = {rel: 0 for rel in files}
    broken = {rel: [] for rel in files}
    for rel, info in files.items():
        seen = set()
        for tgt in info["links"]:
            base = re.split(r"[#|]", tgt, maxsplit=1)[0].strip()
            # 处理嵌套方括号 / 残留 `[[..]]`（如 `[[[[激活函数]]]]` 抓到的目标）
            while base.startswith("[[") and base.endswith("]]"):
                base = base[2:-2].strip()
            if not base:
                continue
            low = base.lower()
            # 外链：含 ://（含畸形 xxhttps://、尖括号 autolink）或常见协议前缀 → 跳过
            if "://" in base or low.startswith(NON_MD) or base.startswith("//"):
                continue
            # 附件/媒体链接：assets 名命中或相对文件存在 → 有效；否则才记断链
            if "assets/" in low or Path(low).suffix in MEDIA_EXT:
                cand = low.split("assets/")[-1].lstrip("./")
                exists = cand in asset_names or low.rstrip("/").split("/")[-1] in asset_names
                if not exists:
                    try:
                        exists = (src / base.lstrip("./")).exists()
                    except OSError:
                        pass
                if not exists and (asset_exists(base, low, asset_names, src)
                                   or path_exists(base, rel, src)):
                    exists = True
                if not exists:
                    broken[rel].append(tgt)
                continue
            # 内部页面/日记链接
            key = low.replace("\\", "/").lstrip("./").removesuffix(".md")
            hit = (resolve.get(key) or resolve.get(key.replace("/", "___"))
                   or resolve.get(key.split("/")[-1]))
            if hit:
                if hit != rel and hit not in seen:
                    in_deg[hit] += 1
                    seen.add(hit)
            elif asset_exists(base, low, asset_names, src) or path_exists(base, rel, src):
                continue  # 文件名含逗号/括号被截断，或 ./../ 相对文件确在其目录 → 不算断链
            else:
                broken[rel].append(tgt)

    dup_norm = {}  # normalized hash -> [rel]
    suggestions = []
    wl = load_whitelist(R)

    def wl_hit(rule, rel):
        return any(rule == rr and glob_hit(rel, [g]) for rr, g in wl)

    for rel, info in files.items():
        body_ns = re.sub(r"\s+", "", info["body"])
        # A 空/近空
        cat = None
        if info["size"] == 0 and R["empty"]["zero_byte"]:
            cat = "empty"
            sig = ["zero_byte"]
        elif len(body_ns) < R["empty"]["min_body_chars"]:
            cat = "empty"
            sig = [f"body_chars={len(body_ns)}<{R['empty']['min_body_chars']}"]
        else:
            # B 重复
            if R["duplicate"]["exact"]:
                h = hashlib.sha256(body_ns.encode()).hexdigest()
                dup_norm.setdefault(h, []).append(rel)

        if cat == "empty" and not wl_hit("empty", rel):
            suggestions.append(_sug(rel, "empty", sig, "manual"))

        # C 疑似过期/失效（内置组合：stale_days AND (orphan OR scan_dates)）
        st = R["stale"]
        if not glob_hit(rel, st["exempt_paths"]) and not wl_hit("stale", rel):
            days = (today - datetime.date.fromtimestamp(info["mtime"])).days
            stale_days_hit = days >= st["stale_days"]
            orphan_hit = st["orphan"] and in_deg[rel] == 0
            dates = [d for d in DATE_RE.findall(info["body"])]
            dated_hit = st["scan_dates"] and any(
                datetime.date.fromisoformat(d) <= lookback for d in dates if _okdate(d))
            if stale_days_hit and (orphan_hit or dated_hit):
                sig = [f"mtime_age={days}d>= {st['stale_days']}"]
                if orphan_hit:
                    sig.append("inbound_refs=0")
                if dated_hit:
                    sig.append(f"contains_past_date(min={min(dates)})")
                suggestions.append(_sug(rel, "stale", sig, "manual"))

        # D 断链
        if R["broken_links"] and broken[rel] and not wl_hit("broken", rel):
            suggestions.append(_sug(rel, "broken", [f"broken_links={len(broken[rel])}",
                               "targets=" + ",".join(sorted(set(broken[rel]))[:5])], "manual"))

    # 汇总重复组
    if R["duplicate"]["exact"]:
        for h, group in dup_norm.items():
            if len(group) > 1 and not all(wl_hit("duplicate", r) for r in group):
                for r in group:
                    suggestions.append(_sug(r, "duplicate",
                                  [f"same_hash_group={len(group)}", "others=" + ",".join(
                                      [x for x in group if x != r][:3])], "manual"))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "suggestions.json").write_text(
        json.dumps(suggestions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / args.report).write_text(render_report(suggestions, files, R), encoding="utf-8")

    n = lambda c: sum(1 for s in suggestions if s["category"] == c)
    print(f"[neaten] scanned={len(files)}  empty={n('empty')} duplicate={n('duplicate')} "
          f"stale={n('stale')} broken={n('broken')}")
    print(f"[neaten] 报告: {out/args.report}")
    print(f"[neaten] 数据: {out/'suggestions.json'}  (只读扫描，未改动 {src})")
    return 0


def _okdate(d):
    try:
        datetime.date.fromisoformat(d)
        return True
    except ValueError:
        return False


def _sug(rel, cat, sig, verdict):
    return {"file": rel, "category": cat, "verdict": verdict,
            "confidence": None, "signals": sig,
            "suggested_action": {"empty": "review_then_delete",
                                  "duplicate": "review_then_merge_or_delete",
                                  "stale": "review_then_archive",
                                  "broken": "fix_or_delete_links"}[cat],
            "excerpt_included": False}


def load_whitelist(R):
    p = CFG / R.get("whitelist_file", "whitelist.txt")
    rules = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            rr, g = line.split("|", 1)
            rules.append((rr.strip(), g.strip()))
    return rules


def render_report(sug, files, R):
    today = datetime.date.today().isoformat()
    lines = [f"# neaten 体检报告（确定性扫描，只读）",
             f"\n生成: {today}  ·  规则: {R.get('_rules_file','内置默认')}",
             f"\n> 本报告只列『路径 + 命中信号 + 建议动作』，**不含正文**；neaten **未改动任何被扫文件**。\n",
             f"总计 {len(sug)} 条建议。处置方式（删/移/改）由你自行决定。\n"]
    order = ["empty", "duplicate", "stale", "broken"]
    titles = {"empty": "A 空/近空", "duplicate": "B 重复",
              "stale": "C 疑似过期/失效（需人工确认）", "broken": "D 断链"}
    for c in order:
        items = [s for s in sug if s["category"] == c]
        lines.append(f"## {titles[c]}  · {len(items)}")
        if not items:
            lines.append("_无_\n")
            continue
        for s in items[:200]:
            lines.append(f"- `{s['file']}` — {'; '.join(s['signals'])} → 建议: {s['suggested_action']}")
        if len(items) > 200:
            lines.append(f"- _… 另有 {len(items)-200} 条见 suggestions.json_")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
