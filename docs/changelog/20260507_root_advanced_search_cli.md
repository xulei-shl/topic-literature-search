# 开发变更记录
- **日期**: 2026-05-07
- **对应设计文档**: [docs/design/root_advanced_search_cli_20260507.md](../design/root_advanced_search_cli_20260507.md)

## 1. 变更摘要
新增仓库根目录 `cli.py` 作为统一高级检索入口，支持按目标选择并发调用 `cnki`、`vp`、`wanfang` 三个现有脚本。

本次实现保持顶层入口只做调度与参数透传，不进入各模块具体业务逻辑；同时保证某一路失败不会中断其他并发任务。

## 2. 文件清单
- `cli.py`: 新增顶层高级检索调度入口
- `tests/test_root_cli.py`: 新增 5 个顶层 CLI 测试用例
- `docs/design/root_advanced_search_cli_20260507.md`: 补充设计文档留痕

## 3. 测试结果
- [x] 单元测试通过
- [x] 核心路径验证通过

已执行：
- `python -m pytest tests/test_root_cli.py`
- `python -m pytest tests/test_search_timeout_config.py`
