# Obsidian 知识层 PoC 报告（2026-09-12）

> 对应《双底座优化与知识库融入架构方案》§6.4 Step 1（PoC）——本机已部署 Obsidian，
> 知识层接入由"llm_wiki 桌面应用"路径调整为 **Obsidian Vault 直接消费发布件 Markdown**（更轻、零额外安装）。

## 一、PoC 结论

✅ **打通**：双底座发布件 → Markdown（5000 份）→ `D:\DeMon KB\监管法规库`（Obsidian vault 子目录），全量 36 秒，SHA256 增量可重复执行。

## 二、执行与实测

| 步骤 | 结果 |
|---|---|
| 探测 vault | `%APPDATA%\obsidian\obsidian.json` → vault = `D:\DeMon KB`（已打开） |
| dry-run | 新增 5000 / 更新 0 |
| 实同步 | **新增 4985 / 更新 15 / 累计 4985**，耗时 **36s**（scope=all） |
| 产出 | 外部 4043（五源全量）+ 内部 957（含条文正文）；每文件 YAML frontmatter + 截断声明 |
| vault 校验 | 4985 md + `.wiki_sync_manifest.json`；样本 frontmatter 字段齐全（rfn/record_id/文号/日期/时效/来源/url/body_len_full/body_truncated） |
| 使用说明 | `监管法规库/00_使用说明.md`（vault 内，含 Dataview 示例与边界声明） |

## 三、同步机制（复用既有工具，零改造）

`tools/sync_wiki_sources.py`（2026-09-12 已建）：
- 命名 `<RFN/IPN>__<标题>.md`（保编号溯源）；
- frontmatter：rfn/ipn、文号、发布日期、时效、来源、url、主题、`body_len_full`/`body_truncated`（F-L08）；
- **SHA256 增量**：`.wiki_sync_manifest.json` 记录指纹，仅复制变化文件（与底座 `source diff` 变更监听互补）。

**增量复跑命令**（写入手册 §五归集/知识层）：

```bash
python tools/sync_wiki_sources.py --out "D:\DeMon KB\监管法规库" --scope all
```

## 四、与规划架构的对应（§6.2/6.3 映射落地）

| 方案项 | 本 PoC 落地 |
|---|---|
| 只喂文本件、结构化字段不进 wiki | ✅ frontmatter 仅溯源字段；权威字段（时效）标注"以底座为准" |
| 命名/溯源约定 | ✅ `<RFN/IPN>__<标题>.md` |
| 增量（双层去重） | ✅ 我方 SHA256 manifest；Obsidian 侧天然只读变化文件 |
| `concepts/`（主题聚合）/图谱 | ⏭ 后续：Dataview 插件 + 主题 MOC 页（方案 §6.3 映射，数据面已全）|
| 变更监听闭环 | ✅ 已有 `cli source diff`（F-O02）作为变更信号；Obsidian 为消费端 |

## 五、后续（登记下一批）

1. **主题 MOC 页**：按 T1–T10 生成 `concepts/` 索引页（发布件 `theme` 字段已备，脚本可批量生成）；
2. **条款级分发**：内部制度可增发条文级 md（`external_clauses`/`internal clauses` 发布件已备，PoC 未铺以免文件爆炸——当前每制度正文已含条文拼接）；
3. **llm_wiki 原路径**：若后续需要语义检索/两步 CoT 摄取，再安装 llm_wiki 指向本目录即可（本 PoC 已把"源文件夹"准备好）。

> 编制：CodeBuddy · 2026-09-12
