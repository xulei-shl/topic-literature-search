"""维普爬虫操作步骤演示脚本。

这个脚本演示了完整的操作流程，每一步都带有详细的注释说明。
可用于：
- 理解爬虫的工作流程
- 调试和验证每一步的选择器是否正确
- 作为编写自动化测试的参考
"""

import logging
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright
from vp_search.scripts.browser import BrowserManager
from vp_search.scripts.config import VpSearchConfig

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("vp-demo")


def demo_full_workflow():
    """演示完整工作流程：从打开首页到导出结果。"""

    # ========== 第 0 步：准备配置 ==========
    print("\n" + "="*60)
    print("步骤 0：初始化配置")
    print("="*60)

    config = VpSearchConfig(
        headless=False,          # 显示浏览器（False），方便观察
        geoip=False,             # 禁用 GeoIP（国内访问）
        proxy=None,              # 不使用代理
        language="zh-CN",        # 中文界面
        output_dir=Path("./outputs/demo"),  # 输出目录
        save_results=True,       # 保存 JSON 结果
        json_only=False,         # 同时打印可读信息
    )
    print(f"输出目录: {config.output_dir}")
    print(f"目标网站: {config.home_url}")

    # ========== 第 1 步：启动浏览器 ==========
    print("\n" + "="*60)
    print("步骤 1：启动浏览器")
    print("="*60)

    browser_manager = BrowserManager(config)
    page = browser_manager.start()
    print(f"✓ 浏览器已启动")
    print(f"  - User-Agent: {page.evaluate('() => navigator.userAgent')[:50]}...")
    print(f"  - 视口大小: {page.viewport_size}")

    try:
        # ========== 第 2 步：打开维普首页 ==========
        print("\n" + "="*60)
        print("步骤 2：打开维普首页")
        print("="*60)

        print(f"导航到: {config.home_url}")
        page.goto(config.home_url, timeout=config.page_timeout * 1000)
        page.wait_for_load_state("domcontentloaded")
        print(f"✓ 页面已加载")
        print(f"  当前 URL: {page.url}")
        print(f"  页面标题: {page.title()}")

        # 检查是否成功加载
        if "cqvip.com" not in page.url:
            raise Exception("未成功访问维普网站")

        # ========== 第 3 步：恢复/验证登录状态 ==========
        print("\n" + "="*60)
        print("步骤 3：检查登录状态")
        print("="*60)

        # 尝试加载之前保存的 cookies（如果存在）
        cookies_loaded = browser_manager.load_cookies()
        if cookies_loaded:
            print("✓ 已加载保存的 cookies")
            # 刷新页面使 cookies 生效
            page.reload()
            page.wait_for_load_state("domcontentloaded")
        else:
            print("⚠ 未找到保存的会话，可能需要手动登录")

        # 检查是否已登录（通过查找用户头像或登录按钮）
        login_btn = page.locator("a:has-text('登录')").first
        if login_btn.count() > 0:
            print("⚠ 检测到未登录状态")
            print("  提示：请手动登录或运行 login 命令")
        else:
            print("✓ 已处于登录状态")

        # ========== 第 4 步：进入高级检索页面 ==========
        print("\n" + "="*60)
        print("步骤 4：进入高级检索页面")
        print("="*60)

        # 方法 A：点击导航菜单
        print("尝试点击'高级检索'链接...")
        advanced_link = page.locator("a:has-text('高级检索')").first
        if advanced_link.count() > 0:
            advanced_link.click()
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            print(f"✓ 已点击导航菜单进入")
        else:
            # 方法 B：直接访问 URL
            print("未找到导航链接，直接访问高级检索 URL...")
            page.goto(config.advanced_search_url, timeout=30000)
            page.wait_for_load_state("domcontentloaded")

        print(f"✓ 当前 URL: {page.url}")
        print(f"  页面标题: {page.title()}")

        # 验证是否真的是高级检索页
        selectors = [
            "input[name='advSearchKeywords']",
            "#basic_beginYear",
            "#basic_endYear",
            "button.behavior-advancesearch",
        ]
        for sel in selectors:
            count = page.locator(sel).count()
            print(f"  - {sel}: {'✓ 存在' if count > 0 else '✗ 未找到'}")

        # ========== 第 5 步：填写检索表单 ==========
        print("\n" + "="*60)
        print("步骤 5：填写高级检索表单")
        print("="*60)

        # 检索信息
        query = "人工智能"
        year_from = "2020"
        year_to = "2024"
        core_only = True

        print(f"检索词: {query}")
        print(f"年份范围: {year_from} - {year_to}")
        print(f"核心期刊: {'是' if core_only else '否'}")

        # 5.1 填写第一条检索条件（题名或关键词）
        print("\n  [条件 1] 题名或关键词 = '人工智能'")
        input1 = page.locator("input[name='advSearchKeywords']").first
        if input1.count() > 0:
            input1.fill(query)
            print(f"  ✓ 已填写: '{query}'")
        else:
            raise Exception("未找到检索词输入框")

        # 5.2 设置检索逻辑为"精确匹配"
        print("\n  [选项] 精确匹配")
        exact_dropdown = page.locator(".sel-c .layui-form-select").first
        if exact_dropdown.count() > 0:
            try:
                exact_dropdown.click()
                time.sleep(0.5)
                exact_option = page.locator("dd[lay-value='1']").first
                if exact_option.count() > 0:
                    exact_option.click()
                    print("  ✓ 已选择'精确匹配'")
            except Exception as e:
                print(f"  ⚠ 精确匹配选项设置失败: {e}")

        # 5.3 填写第二条检索条件（摘要，OR 逻辑）
        print("\n  [条件 2] 摘要 OR '人工智能'")
        input2 = page.locator("input[name='advSearchKeywords']").nth(1)
        if input2.count() > 0:
            input2.fill(query)
            print(f"  ✓ 已填写: '{query}'")

            # 设置逻辑为"或"
            print("  [逻辑] 设置为 OR（或）")
            logic_dropdown = page.locator("dd[lay-value='2']").first  # 2=OR
            if logic_dropdown.count() > 0:
                logic_dropdown.click()
                print("    ✓ 已选择'或'")

        # 5.4 设置年份范围
        print(f"\n  [年份] 起始: {year_from}, 结束: {year_to}")
        year_from_select = page.locator("#basic_beginYear")
        year_to_select = page.locator("#basic_endYear")

        if year_from_select.count() > 0:
            # 使用 evaluate 直接设置 select 的值（更稳定）
            page.evaluate(f"document.querySelector('#basic_beginYear').value = '{year_from}'")
            page.evaluate("document.querySelector('#basic_beginYear').dispatchEvent(new Event('change'))")
            print(f"    ✓ 起始年份已设置")

        if year_to_select.count() > 0:
            page.evaluate(f"document.querySelector('#basic_endYear').value = '{year_to}'")
            page.evaluate("document.querySelector('#basic_endYear').dispatchEvent(new Event('change'))")
            print(f"    ✓ 结束年份已设置")

        # 5.5 核心期刊筛选（可选）
        if core_only:
            print("\n  [核心期刊] 仅限核心来源")
            core_titles = [
                "北大核心期刊",
                "EI来源期刊",
                "SCIE期刊",
                "CAS来源期刊",
                "CSCD期刊",
                "CSSCI期刊",
            ]

            # 先取消"全部期刊"
            all_journals = page.locator("input[name='basic_journalRange'][title='全部期刊']")
            if all_journals.count() > 0:
                all_journals.uncheck()
                print("    ✓ 已取消'全部期刊'")

            # 勾选核心期刊列表
            for title in core_titles:
                checkbox = page.locator(f"input[name='basic_journalRange'][title='{title}']")
                if checkbox.count() > 0:
                    checkbox.check()
                    print(f"    ✓ 已勾选'{title}'")

        # 截图保存表单状态
        screenshot_path = config.output_dir / "step5_form_filled.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_path))
        print(f"\n  📸 表单截图已保存: {screenshot_path}")

        # ========== 第 6 步：提交检索 ==========
        print("\n" + "="*60)
        print("步骤 6：提交检索")
        print("="*60)

        search_btn = page.locator("button.behavior-advancesearch").first
        if search_btn.count() > 0:
            search_btn.click()
            print("✓ 已点击'检索'按钮")
        else:
            raise Exception("未找到检索提交按钮")

        # ========== 第 7 步：等待结果页加载 ==========
        print("\n" + "="*60)
        print("步骤 7：等待结果页加载")
        print("="*60)

        print("等待页面加载完成（domcontentloaded）...")
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        time.sleep(2)  # 额外等待确保元素渲染

        # 验证是否进入结果页
        result_selectors = ["#headerpager", "#footerpager", ".search-result"]
        is_results_page = any(page.locator(s).count() > 0 for s in result_selectors)

        if is_results_page:
            print("✓ 已进入搜索结果页")
        else:
            print("⚠ 页面结构异常，可能未正确加载")

        # ========== 第 8 步：解析结果概要 ==========
        print("\n" + "="*60)
        print("步骤 8：解析结果概要信息")
        print("="*60)

        # 提取总数
        total_elem = page.locator("#hidShowTotalCount").first
        if total_elem.count() > 0:
            total_value = total_elem.get_attribute("value") or "0"
            print(f"总结果数: {total_value}")
        else:
            print("⚠ 未找到总结果数元素")

        # 提取当前页/总页数
        try:
            current_page_text = page.locator(".layui-laypage-curr").first.inner_text()
            print(f"当前页码: {current_page_text}")
        except:
            print("当前页码: 未检测到")

        # ========== 第 9 步：切换每页显示 50 条 ==========
        print("\n" + "="*60)
        print("步骤 9：设置每页显示 50 条结果")
        print("="*60)

        page_size_select = page.locator("#selectPageSize")
        if page_size_select.count() > 0:
            # 通过 JS 直接设置 select 值
            page.evaluate("document.querySelector('#selectPageSize').value = '50'")
            page.evaluate("document.querySelector('#selectPageSize').dispatchEvent(new Event('change'))")
            print("✓ 已设置页面大小为 50 条/页")
            time.sleep(1)  # 等待页面刷新
        else:
            print("⚠ 未找到分页大小选择器")

        # ========== 第 10 步：选择结果复选框 ==========
        print("\n" + "="*60)
        print("步骤 10：批量选择结果复选框")
        print("="*60)

        # 查找所有结果复选框
        checkbox_selectors = [
            "input[name='selectArticle']",
            "input[data-name='selectArticle']",
            ".search-list input[type='checkbox']",
        ]

        selected_count = 0
        for selector in checkbox_selectors:
            checkboxes = page.locator(selector)
            count = checkboxes.count()
            if count > 0:
                print(f"找到 {count} 个复选框: {selector}")

                # 逐个勾选
                for i in range(count):
                    cb = checkboxes.nth(i)
                    if cb.count() > 0 and not cb.is_checked():
                        cb.check()
                        selected_count += 1

                print(f"✓ 已勾选 {selected_count} 个结果")
                break

        if selected_count == 0:
            print("⚠ 未找到可勾选的复选框，或已全部选中")

        # ========== 第 11 步：点击"导出题录" ==========
        print("\n" + "="*60)
        print("步骤 11：点击'导出题录'入口")
        print("="*60)

        export_selectors = [
            "a.behavior-exporttitle",
            "a[data-key='export']",
            "a:has-text('导出题录')",
        ]

        export_clicked = False
        for selector in export_selectors:
            export_link = page.locator(selector).first
            if export_link.count() > 0:
                print(f"找到导出入口: {selector}")
                export_link.click()
                print("✓ 已点击导出题录")
                export_clicked = True
                break

        if not export_clicked:
            raise Exception("未找到'导出题录'按钮")

        # ========== 第 12 步：等待导出页打开 ==========
        print("\n" + "="*60)
        print("步骤 12：等待导出页面打开")
        print("="*60)

        # 导出会打开新页面，等待新页面
        time.sleep(2)
        pages = page.context.pages
        print(f"当前浏览器中有 {len(pages)} 个页面")

        # 找到最新的页面（导出页）
        export_page = pages[-1]
        print(f"✓ 导出页 URL: {export_page.url}")

        # ========== 第 13 步：在导出页选择格式 ==========
        print("\n" + "="*60)
        print("步骤 13：选择导出格式")
        print("="*60)

        # 选择"表格"格式（excel）
        excel_tab = export_page.locator("li[data-type='excel']").first
        if excel_tab.count() > 0:
            excel_tab.click()
            print("✓ 已选择'表格'格式（Excel）")
            time.sleep(0.5)

        # 选择"参考文献"格式（abstract）
        abstract_tab = export_page.locator("li[data-type='abstract']").first
        if abstract_tab.count() > 0:
            abstract_tab.click()
            print("✓ 已选择'参考文献'格式（TXT）")
            time.sleep(0.5)

        # ========== 第 14 步：点击导出确认按钮 ==========
        print("\n" + "="*60)
        print("步骤 14：点击导出按钮并捕获下载")
        print("="*60)

        export_btn_selectors = ["#exportbtn", "a#exportbtn", ".export-op #exportbtn"]

        export_btn = None
        for selector in export_btn_selectors:
            btn = export_page.locator(selector).first
            if btn.count() > 0:
                export_btn = btn
                print(f"找到导出按钮: {selector}")
                break

        if export_btn is None:
            raise Exception("未找到导出确认按钮")

        # 开始监听下载事件
        print("等待下载开始...")
        with export_page.expect_download(timeout=60000) as download_info:
            export_btn.click()

        download = download_info.value
        print(f"✓ 下载已触发")
        print(f"  建议文件名: {download.suggested_filename}")
        print(f"  下载 URL: {download.url[:80]}...")

        # ========== 第 15 步：保存文件 ==========
        print("\n" + "="*60)
        print("步骤 15：保存下载文件")
        print("="*60)

        output_dir = Path("./outputs/demo-batch001")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存 Excel 文件
        excel_path = output_dir / (download.suggested_filename or "vp-export.xls")
        download.save_as(str(excel_path))
        print(f"✓ 文件已保存: {excel_path}")

        # ========== 第 16 步：清理导出页 ==========
        print("\n" + "="*60)
        print("步骤 16：关闭导出页，返回结果页")
        print("="*60)

        export_page.close()
        print("✓ 导出页已关闭")
        print(f"当前活动页面: {page.url}")

        # ========== 第 17 步：保存会话 ==========
        print("\n" + "="*60)
        print("步骤 17：保存当前会话状态")
        print("="*60)

        browser_manager.save_session(page_type="results", last_query=query)
        print("✓ 会话已保存到 .vp-search/session/")

        # ========== 完成 ==========
        print("\n" + "="*60)
        print("✓✓✓ 操作流程演示完成！")
        print("="*60)
        print(f"\n输出文件:")
        print(f"  - 元数据: {excel_path}")
        print(f"  会话目录: .vp-search/session/")
        print(f"\n下一步:")
        print(f"  1. 使用 export_processor.py 清理 Excel")
        print(f"  2. 如需更多批次，继续翻页并重复导出")
        print(f"  3. 最后合并所有批次文件")

    except Exception as e:
        logger.exception("流程执行失败")
        print(f"\n✗ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # ========== 清理资源 ==========
        print("\n" + "="*60)
        print("清理：关闭浏览器")
        print("="*60)

        browser_manager.close()
        print("✓ 浏览器已关闭")


if __name__ == "__main__":
    demo_full_workflow()
