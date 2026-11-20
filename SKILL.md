# knowledge-neaten（知识库只读体检 → 清洗整理建议）

> 本技能对一个目录做**只读体检**，输出**建议清单**；**不改输入、不删不移不改名**。
> 默认扫的目录通常是 `knowledge-desensitization` 的脱敏工作副本（数据衔接，代码互不调用）。

## 核心不变量
1. **只读**：除 `out/` 报告外，对扫描目录无任何写操作。
2. **只出建议**：AI 永不下「删除」，只 `suspected / likely_valid / manual`；无 AI 时只列命中的确定信号。
3. **不泄正文**：报告只引用「路径 + 命中信号 + 置信度」，默认不含正文片段（`excerpt_included:false`）。
4. **两段式**：确定性（空/重复/断链/mtime/入度）不碰 AI；AI 仅用于失效复核与归类，且默认关闭、默认不读全文。

## 何时使用
- 「这个知识库哪些是空/废/重复，能清掉？」
- 「帮我把没归类的笔记建议归类」（AI 段，第二期）
- 知识库瘦身 / 迁移前的只读体检
- 脱敏副本生成后，再做一次「可清理」体检（接在 desensitization 之后）

## 两段式：确定性扫描 vs AI 辅助
| 子能力 | 算法 | 用 AI |
|---|---|---|
| A 空/近空 | 字节 + 去 frontmatter 后正文长度 | 否 |
| B 重复 | 归一化内容哈希（near 可选 simhash） | 否 |
| C 疑似过期/失效 | mtime 超期 AND（入度0 OR 含过去日期）→ 候选；候选 AI 三档复核 | 复核用 |
| D 断链 | 解析 `[[链接]]`/`[x](y)` 比对标题索引 | 否 |
| E 归类建议 | 正文语义 → taxonomy 候选 | 是 |

## 配置与目录（用户级两级配置 + neaten 子命名空间）
- `<KM_CONFIG_HOME>` = `%USERPROFILE%\.knowledge-manager-skills`
- `neaten/settings.yaml`（全局） + `neaten/sources/<id>.yaml`（每源，可覆盖 defaults）
- 规则在 `config/stale-rules.yaml`（从 `.example` 复制），类目在 `config/taxonomy.yaml`
- 产物落 `neaten/out/<id>/neaten-report.md` + `suggestions.json`

## 标准流水线（本期只接确定性）
```bash
# 确定性扫描（本期唯一可跑段）
python scripts/scan.py --source <目录> [--rules config/stale-rules.yaml] --out out/<id>
# 第二期：
#   python scripts/pipeline.py --id <id> [--ai]  # scan → assess → report
```

## 过期/无效判定规则
见 `config/stale-rules.example.yaml` 与 `references/stale-rules.md`（待建）。要点：
- 「过期」不是单条规则，而是 `stale_days AND (orphan OR scan_dates)` 命中后进候选。
- 候选在 `--no-ai` 下只标 `manual`（"信号命中、需人工确认"），不擅自判 suspected。
- root/索引/模板页豁免孤立判定。

## 误报治理
- `config/whitelist.txt`：`规则名|相对路径(glob)`，永久跳过。
- `stale.min_confidence`：低于此不进报告（仅约束 AI 结论）。

## AI 读正文的隐私开关
- 默认 `--no-ai`；`ai.full_text: false`（即便开 AI 也只喂元数据+引用图+首尾上下文）。
- 要审真实正文，请确保 `--source` 指向**脱敏副本**。

## 与 desensitization 的边界
独立 skill、互不 import、互不调用；仅共享「脱敏工作副本」这一目录约定（脱敏产物 = neaten 默认输入）。

## 安全红线
- 绝不写/删/移扫描目录内任何文件。
- 报告绝不粘正文原文。
- 真凭据只可能存在于原始真源；故默认输入 = 脱敏副本。

## 参考文档
- `README.md`（用户视角）
- `config/stale-rules.example.yaml`（可调阈值）
- `references/`（stale-rules / commands / false-positives，待建）
