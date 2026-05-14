# Lessons Learned

## 2026-05-13: weipu-search 模块创建

### 关于 Layui 复选框操作

**问题**：vp-search 中全选复选框一直出错。原代码尝试点击 Layui 的 `div.layui-form-checkbox` 包装层来触发全选，但翻页后 Layui 的内部 class 状态与真实 input 状态不同步，导致点击包装层后有时不生效、有时反而取消选中。

**根因**：Layui 的复选框状态机通过 CSS class `layui-form-checked` 控制视觉表现，但`input`的 `checked` 属性才是框架事件的真正触发源。点击包装层间接触发的 `change` 事件在翻页/清空操作后可能丢失监听绑定。

**解决方案**：对新模块 `weipu-search`，直接使用 `input.checked = true` + `dispatchEvent(new Event('change', {bubbles: true}))` JS 作为第一方法。这跳过了 Layui 包装层的 class 状态机，直接操作原生 input，可靠性更高。若全选无效，立即降级为逐条 JS 勾选。

### 关于 CloakBrowser 迁移

- `from cloakbrowser import launch` 返回标准 Playwright `Browser` 对象
- 不需要手动管理 `sync_playwright()` 生命周期
- Session 持久化（cookies/localStorage）与标准 Playwright API 完全兼容
- `humanize=True` 参数提供了额外的人性化行为模拟

### 关于 Mixin 多继承设计

- 跨模块复用时，Mixin 之间的方法冲突需要仔细通过 MRO 检查
- 工具方法（如 `_action_poll_interval_seconds`）应当只在一个 Mixin 中定义
- 进度管理和导出管理的 `_prepare_progress_store`、`_resolve_output_dir` 等重叠方法应放在一个 Mixin（`WpProgressMixin`）中，避免冲突

## 2026-05-14: 批量导出降级策略

### 问题
`_click_export_entry_after_batch_action()` 中下拉菜单（behavior-allDowns）的"导出题录"点击后直接 `return True`，不验证导出页是否真的打开。当该点击因页面状态原因未生效时，`_open_export_page()` 空等至超时（~3 分钟），导致整批失败。

### 解决方案
1. **验证后返回**：下拉菜单"导出题录"点击后，通过检测新页面/URL 变化/导出页就绪来验证是否生效
2. **自动降级**：验证失败时主动点击 `a[href='javascript:batch();']` 切换为模态弹窗方式，由同一个循环找到"导出全部"按钮
3. **弹窗清理**：导出完成后通过 `_dismiss_batch_modal_if_present()` 自动关闭模态弹窗，避免遮罩层阻塞后续翻页/勾选操作
