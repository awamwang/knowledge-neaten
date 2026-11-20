# knowledge-neaten

对本地知识库做**只读体检**，产出一份**清洗整理建议清单**：空/近空文件、重复、
疑似过期/失效、断链、可归类位置。**不改输入、不删、不移、不改名**——你拿清单自己处置。

- **真源/副本只读**：唯一写动作是生成 `reports_root/<id>/` 下的报告；扫过的目录一个字节都不改。
- **默认输入 = 脱敏工作副本**：neaten 不 import 脱敏脚本，只是约定扫的目录通常是
  `knowledge-desensitization` 的工作副本 `<data_root>/<id>`（已脱敏，AI 段读它没有真凭据泄露风险）。
  直扫原始真源也行，但带凭据的真源会让 AI 段有隐私风险，故默认推荐脱敏副本。
- **两段式**：确定性扫描（空/重复/断链/mtime/引用入度）**不碰 AI**；AI 只用于「疑似失效复核」「归类建议」，
  且**默认关闭**（`--no-ai`），即便开启也**默认不把完整正文送进模型**。
- **只出建议**：AI 永不下「删除」结论，只下 `suspected / likely_valid / manual`。无 AI 时只报命中的确定信号 + `manual`。
- **可配阈值**：何为「过期」全走 `config/stale-rules.yaml`，可调、可白名单排除。
- **不泄内容**：报告只引用「路径 + 命中信号 + 置信度」，不粘正文片段。

机制细节见 `SKILL.md` 与 `references/stale-rules.md`（待建）。

---

## 0. 当前状态

`scan.py`（确定性扫描）已是**可跑雏形**；`assess.py`（AI 段）、`pipeline.py`（串接）、
`guide.py`、`km_config.py`（两级配置读取）为下一期。`--no-ai` 不依赖未实现件，单文件即可出报告。

## 1. 安装依赖

| 依赖 | 是否必需 | 说明 |
|---|---|---|
| Python 3.10+ | 必需 | `scan.py` 仅用标准库 |
| PyYAML | 可选 | 读 `stale-rules.yaml`；缺省时脚本用内置默认阈值，仍可跑 |

## 2. 快速上手

```bash
# 只跑确定性扫描（最快、零隐私成本）：对一个目录出建议清单
python scripts/scan.py --source <脱敏工作副本目录> --out out/manual

# 指定规则文件（默认找 config/stale-rules.yaml，回退 .example.yaml，再回退内置默认）
python scripts/scan.py --source <目录> --rules config/stale-rules.yaml --out out/<id>
#   产出：out/<id>/neaten-report.md（人读） + suggestions.json（机器读）

# 误报治理：把已确认忽略的「路径 + 规则」写进 config/whitelist.txt
#   形如  stale|pages/旧计划-2024.md   （规则名|相对路径，glob 支持）
```

> 输出**只打印各分类计数与产物路径**，不打印正文。

## 3. 数据布局

```
<技能目录>/                       # 本仓库（进 Git：仅代码/模板/文档）
├── config/*.example.*            # 模板
└── out/<id>/                     # 直接跑时的产物根（已 .gitignore）

<KM_CONFIG_HOME>/neaten/          # 用户级配置根（不进 Git），复用隔壁根 + neaten 子命名空间
├── settings.yaml
├── sources/<id>.yaml
└── out/<id>/                     # 推荐的报告落点
```

`<KM_CONFIG_HOME>` = `%USERPROFILE%\.knowledge-manager-skills`（与脱敏共用根，`neaten/` 子目录隔离）。

## 4. 隐私边界

- **永不进 Git**：`out/`（含路径与主题）、真实 `sources/*.yaml`、`whitelist.txt`、`taxonomy.yaml`（若含私有类目）。
- **AI 段的隐私开关**：默认 `--no-ai`；`ai.full_text` 默认 `false`。要审真实正文请先确认输入是**脱敏副本**。

## 5. 与 knowledge-desensitization 的边界

| | knowledge-desensitization | knowledge-neaten |
|---|---|---|
| 管什么 | 做成可外分享副本 + 凭据入保险库 + 离线备份 | 这个库里哪些能清掉、哪些该归类 |
| 写不写 | 在工作副本上就地脱敏（写副本） | **只读，只出报告** |
| 衔接 | 产物 = 工作副本 | **默认输入 = 上面那个工作副本** |
| 代码调用 | 互相**不 import、不**调用 |

两件事，不重叠，不互相调用，仅共享同一条「脱敏副本」数据目录约定。

## 6. 许可

MIT，见 `LICENSE`。**许可范围不含任何运行数据**（`out/`、真实配置都是用户隐私）。
