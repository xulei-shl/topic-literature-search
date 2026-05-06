# 开发变更记录
- **日期**: 2026-05-06
- **对应设计文档**: [vp_search_resume_jump_restore_20260506.md](../design/vp_search_resume_jump_restore_20260506.md)

## 1. 变更摘要
优化维普高级检索 `--progress-file` 的续跑恢复逻辑。

- 恢复结果页时，优先使用分页区“到第 X 页”输入框直接跳转
- 输入框跳转失败后，继续尝试当前分页条中可见的数字页按钮
- 两种跳页方式都不可用时，再回退到原有“下一页”逻辑

## 2. 文件清单
- `vp-search/scripts/interactor.py`: 修改，新增输入框跳页、可见数字页跳页和三段式恢复逻辑
- `tests/test_vp_interactor.py`: 修改，补充输入框优先、可见数字页回退和候选页选择测试

## 3. 测试结果
- [x] 单元测试通过
- [ ] 核心路径验证通过

已执行：

- `python -m pytest tests/test_vp_interactor.py`
- `python -m compileall vp-search/scripts`
