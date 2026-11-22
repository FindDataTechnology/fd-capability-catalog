# fd-capability-catalog

> 联邦能力目录：把各语义系统（concept / 指标 / 图 / 数据集）已有清单汇总成一份只读投影，供上层按授权裁剪。跑在 [`fd-ontology-kernel`](../fd-ontology-kernel) 的纪律下——唯一写路径、词表闭集闸、身份=(作用域+kind+外部键)、append-only 事件。

Change: `establish-capability-plane`（capability-catalog 能力）。

## 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 跨语义系统的**索引**（一条记录 = 一个可被引用的语义能力） | 数据本体（不复制各系统的数据） |
| 跑在 ontology-kernel 纪律下的一个 **ontology 域** | 一套平行于 kernel 的新机制 |
| 供装配面/运行时按授权裁剪的**源** | 供 agent 直接翻的全集 |

## 六端口

`fd_capability_catalog/adapter.py` 用 SQLite 实现 kernel 的六端口：
`SQLiteUnitStore` / `SQLiteJournal` / `SQLiteProposalBox` / `SQLitePublicationStore` /
`CapabilityVocabularySource` /（distribution 在 S0 未提供，声明 absent）。

`kind` 闭集：`concept` / `indicator` / `graph` / `dataset`——闭集外的值被 kernel 词表闸拒。

## 用法

```python
from fd_capability_catalog.adapter import build_kernel
k = build_kernel("/path/to/catalog.db")
k.write(scope_id="org:finddata", kind="concept", external_key="price.close",
        body="收盘价", attrs={"served_by": ["fd-open-data-mcp"], "status": "verified"},
        actor="projector", via="federation-sync")
```

## 符合性

```bash
python -m fd_ontology_kernel.conformance.gate --adapter fd-capability-catalog
# PASS: 14 checks, 2 skipped (no clause kinds, no distribution)
```

## 模块

| 模块 | 作用 |
|---|---|
| `adapter.py` | 六端口 SQLite 实现（units/events/proposals/publications/vocab）|
| `projector.py` | 从各系统 list 端点拉取、写目录；幂等、失败隔离、漂移标「暂不可核」 |
| `source_open_data.py` | fd-open-data-mcp 真源适配器（concept+indicator，分页拉全，完整身份+serving 边）|
| `source_business.py` | fd-find-data-business-mcp 真源（同 Concept 库；read/read_series/read_range 边）|
| `source_graph.py` | text-core KG verticals 作 graph kind（legal_v2/gov_v3；kg 工具边）|
| `resolution.py` | 三层求交 agent ∧ owner ∧ pack（能力 id 级，取最严）|
| `access.py` | 模型 C：registry `server_access`(工具级) + serving map(工具↔能力) → 能力级有效面 |

| `readers.py` | 三种读者：装配（全量）/ 常驻（pack 所用）/ 检索（分页+有效面）|

## 测试

```bash
PYTHONPATH=../fd-ontology-kernel:. uv run --with pytest python -m pytest tests/
# 34 passed (含 model C + 跨系统合并 + 真数据整链 + graph kind)
```
