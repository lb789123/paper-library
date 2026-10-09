# 特性卡 Schema（`<id>/README.md`）

特性卡是**整个论文库的检索单元**。它必须做到两件事：

1. **在不打开全文的前提下，让人（和 Agent）判断"这篇跟我现在的问题有没有关系"**；
2. **可被纯文本 grep 精确命中**——不依赖任何向量库。

因此：frontmatter 存结构化字段（用于打分/过滤），正文存人读的展开（用于确认相关性）。

---

## 一、字段定义

`index.json` 与 `INDEX.md` 完全由这些字段派生。**README.md 的 frontmatter 是唯一真值来源。**

| 字段 | 必填 | 类型 | 说明 |
|------|:----:|------|------|
| `id` | ✅ | str | 目录名，全局唯一。用论文短名（`dualBrep`），不是标题全称 |
| `title` | ✅ | str | 论文完整标题 |
| `tags` | ✅ | list | **检索主键**。技术/领域名词，3–8 个。见下方命名规范 |
| `task` | ⭕ | list | 任务类型：`逆向工程` `条件生成` `表示学习` `网格重建`… |
| `problem` | ✅ | str | **它解决的具体痛点**，一句话。这是"我为什么需要它"的入口 |
| `core_idea` | ✅ | str | **核心思路**，一句话（≤60 字）。INDEX.md 一览表用的就是它 |
| `reuse_when` | ✅ | list | **什么时候该翻回这篇论文**。面向未来的自己写，2–5 条 |
| `key_mechanisms` | ⭕ | list | 关键机制，每条 `机制 → 换来了什么` |
| `results` | ⭕ | str | 关键量化结果 |
| `limitations` | ⭕ | list | 局限与失效边界 |
| `related` | ⭕ | list | 库内相关论文的 `id` |
| `analysis` | ⭕ | str | 深度分析文件名，默认 `analysis.md` |
| `source` | ⭕ | str | 原文文件名 |
| `authors` / `year` / `venue` | ⭕ | list/int/str | 元信息 |
| `status` | ✅ | str | `pending`（脚手架已建、内容未填）/ `summarized` / `stale` |
| `created` / `updated` | ⭕ | str | ISO 日期 |

> `→` 是字面字符，不是箭头语法糖；写成可读的短句即可。

## 二、取值规范（决定了检索能不能命中）

**tags 用名词、用你自己提问时会用的词。**

- ✅ `B-rep` `implicit-field` `CAD-generation` `GVD` `topology-encoding` `point-cloud`
- ❌ `novel` `deep-learning` `good-paper` —— 这类词在所有论文里都成立，等于没有区分度
- 中英文不要混：同一概念只固定一种写法（库里已有 `逆向工程`，就别再写 `reverse-engineering` 指同一件事）

**`problem` 写痛点，不写领域。**

- ✅ 「离散拓扑不可微，导致几何一致性与水密性无法端到端优化」
- ❌ 「CAD 表示学习很难」

**`reuse_when` 写触发场景，不写论文内容。**

- ✅ 「当我要把离散结构（图/序列）编码成可端到端优化的连续表示时」
- ❌ 「这篇论文提出了双场表示」——这是 `core_idea` 干的事

**`core_idea` 一句话说完"做了什么 + 靠什么 + 效果"，不堆名词。**

- ✅ 「用 SDF（几何）+ GVD-UDF（拓扑）两个标量场共享一份 latent，把 B-rep 学习搬到连续域」
- ❌ 「提出一种新颖的统一连续表示，结合多种先进技术，取得了优异效果」

## 三、手写容错

`library.py` 的 frontmatter 解析器同时接受两种写法，手改之后照样能 reindex：

```yaml
tags: ["B-rep", "GVD"]        # 流式（脚本生成的就是这种，JSON 数组也是合法 YAML）
tags:                         # 块式（手写更舒服）
  - B-rep
  - GVD
```

带 `:` 的字符串**必须加引号**，否则会被当成键值对分隔：`title: "DualBrep: A Dual-Field ..."`。

正文里出现的 `---` 单独成行会截断 frontmatter —— 分隔线请用 `***` 或留空。

**流式列表（`[` / `{` 开头）可以折行写**，解析器会攒到括号闭合再一次性当 JSON 解析：

```yaml
reuse_when: [
  "第一条触发场景",
  "第二条触发场景"
]
```

只有在**单行**时才会被原样写成单行；两种写法等价，按可读性选。

> 历史坑（2026-10-08 已修）：早期解析器只吃单行，多行 `reuse_when` 会被截成一个字符串 `"["`，
> 表现为 `INDEX.md` 里该论文的"何时可复用"只出现一个 `[`。若再见到，重新检查
> `split_frontmatter` 里的 `_flow_is_incomplete` 折行累积逻辑。
