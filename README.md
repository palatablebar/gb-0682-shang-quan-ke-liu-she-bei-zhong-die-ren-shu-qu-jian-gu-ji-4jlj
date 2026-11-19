# 商圈客流设备重叠与人数区间估计

面向商场经营数据分析员，处理采信、判定、核算、复核与归档。

## 启动

```bash
docker compose up --build -d
```

访问 http://localhost:18251 。初次启动自动运行脏样例，可切换 clean.csv 与 legacy.csv。顶部“运行批次”会重新计算，同源输入复用批次。

## 业务模块

- 设备采信：`/m1_ingest`、`/api/m1_ingest`
- 区域映射：`/m2_mapping`、`/api/m2_mapping`
- 观测分桶：`/m3_versions`、`/api/m3_versions`
- 重叠约束：`/m4_segments`、`/api/m4_segments`
- 人数估界：`/m5_calculate`、`/api/m5_calculate`
- 日汇对账：`/m6_reconcile`、`/api/m6_reconcile`
- 营业日报：`/api/daily_reports`、`/api/daily_reports/recompute`
- 报告归档：`/m7_archive`、`/api/m7_archive`

## 营业日边界

营业日边界跟随**营业场次**（开门/闭店营业事实），不采用固定凌晨切点或自然日零点：一场开门到闭店为一个营业日，标签取营业时区下的开门日，夜场跨零点的小时桶仍归开门日。默认按相邻小时桶空档（超 `gap_hours`）推断场次；临时闭店或提前开门可在“边界重算”补录 `open`/`close` 事实，补录优先。换时区或补录边界只重算归属变化的营业日，同一批重跑按 `recompute_id` 幂等去重不重复累计，切版失败回滚并保留旧版正式日报。

## 核心规则

无法识别个人时只输出满足区域交集约束的人数上下界；矛盾观测隔离而非伪造精确人数。完整数值、边界和异常定义见 `docs/DATA-DICTIONARY.md`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

原始样例在 `sample_data/`。测试在 `tests/`。封存文件是单份CSV，含运行清单、计算证据、隔离原因与对账。复核需提交当前版本，陈旧复核返回409。

## 数据与限制

SQLite保存在records命名卷，普通重启与构建不会清除。确认可丢弃本项目数据后，运行 `docker compose down -v` 再启动可重置。单文件上限2MB；不接入外部业务系统；历史规则由本项目样例定义，不代表实际行业标准。
