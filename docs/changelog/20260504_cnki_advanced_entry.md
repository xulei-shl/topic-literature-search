# 开发变更记录
- **日期**: 2026-05-04
- **对应设计文档**: [需求.md](/E:/Desk/xinqingnian/docs/需求.md)

## 1. 变更摘要
调整 CNKI 高级检索入口逻辑，后续检索流程必须从首页点击“高级检索”进入，不再使用固定高级检索 URL 作为兜底跳转。

## 2. 文件清单
- `cnki-search/scripts/interactor.py`: 修改高级检索入口打开逻辑
- `docs/changelog/20260504_cnki_advanced_entry.md`: 新增变更记录

## 3. 测试结果
- [ ] 单元测试通过
- [ ] 核心路径验证通过

补充说明：本次仅执行了 `py_compile` 语法校验，未实际联通 CNKI 页面做端到端验证。
