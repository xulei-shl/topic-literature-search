"""
删除 Excel 中三列均不包含关键词「新青年」的行
列名：「题 名」「关键词」「文 摘」（注意空格）

处理逻辑：
1. 对输入文件创建备份（文件名添加 -backup 后缀）
2. 筛选后覆盖原始文件
3. 生成统计报告（Markdown 格式）

使用方法：
  python clean_vp.py [输入文件路径]

示例：
  python clean_vp.py outputs/vp-search/新青年/20260515-095815-新青年-merged.xlsx

说明：
  - 若提供输入文件参数，则处理该文件，自动创建备份并覆盖原文件
  - 若不提供参数，则使用默认文件名 input.xlsx
  - 备份文件名为：原文件名（添加 -backup 后缀），如 文件名-backup.xlsx
  - 统计报告文件名为：原文件名（添加 -stats 后缀），如 文件名-stats.md
"""

import pandas as pd
import sys
import shutil
import os

def generate_stats_report(input_path, original_count, filtered_count, keyword, output_report_path):
    """生成处理统计报告（Markdown格式）"""
    from datetime import datetime
    
    report_lines = [
        "# Excel 数据处理统计报告",
        "",
        "## 基本信息",
        f"- **处理时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **输入文件**: `{input_path}`",
        f"- **处理工具**: `clean_vp.py`",
        "",
        "## 处理逻辑",
        "本脚本用于筛选包含指定关键词「{0}」的数据行，具体规则如下：".format(keyword),
        "",
        "### 筛选列",
        "- `题 名`（标题）",
        "- `关键词`",
        "- `文 摘`（摘要）",
        "",
        "### 筛选条件",
        "保留**至少一列**中包含关键词「{0}」的行（OR 逻辑），删除三列均不包含该关键词的行。".format(keyword),
        "",
        "### 技术实现",
        "1. 将三列统一转为字符串类型，缺失值填充为空字符串",
        "2. 使用 `pandas.Series.str.contains()` 进行子串匹配（忽略大小写敏感）",
        "3. 通过布尔掩码筛选目标行",
        "",
        "## 数据统计",
        f"- **原始总行数**: `{original_count}` 行",
        f"- **保留行数**: `{filtered_count}` 行",
        f"- **删除行数**: `{original_count - filtered_count}` 行",
        f"- **保留比例**: `{filtered_count/original_count*100:.2f}%`" if original_count > 0 else "- **保留比例**: N/A (原始文件为空)",
        "",
        "## 文件产出",
        f"- **备份文件**: `{get_backup_path(input_path)}`",
        f"- **结果文件**: `{input_path}` (已覆盖)",
        f"- **统计报告**: `{output_report_path}`",
        "",
        "---",
        "*本报告由 clean_vp.py 自动生成*"
    ]
    
    report_content = "\n".join(report_lines)
    
    try:
        with open(output_report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)
        print(f"✓ 统计报告已生成：{output_report_path}")
        return True
    except Exception as e:
        print(f"警告：统计报告生成失败 - {e}")
        return False


def get_backup_path(input_path):
    """获取备份文件路径"""
    import os
    file_dir, file_name = os.path.split(input_path)
    name_without_ext, ext = os.path.splitext(file_name)
    backup_name = f"{name_without_ext}-backup{ext}"
    return os.path.join(file_dir, backup_name) if file_dir else backup_name


def get_stats_path(input_path):
    """获取统计报告文件路径"""
    import os
    file_dir, file_name = os.path.split(input_path)
    name_without_ext, ext = os.path.splitext(file_name)
    stats_name = f"{name_without_ext}-stats.md"
    return os.path.join(file_dir, stats_name) if file_dir else stats_name


def filter_excel(input_path, output_path, keyword="新青年"):
    # 读取 Excel 文件
    try:
        df = pd.read_excel(input_path)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return None, None

    # 检查所需列是否存在（列名可能与显示不完全一致，可打印提示）
    required_cols = ["题 名", "关键词", "文 摘"]
    for col in required_cols:
        if col not in df.columns:
            print(f"错误：Excel 中缺少列「{col}」，实际列名：{list(df.columns)}")
            return None, None

    # 将三列转为字符串，填充缺失值为空字符串，然后检查是否包含关键词
    # 注意：数字、日期等会先转为字符串进行子串匹配
    mask = (
        df["题 名"].fillna("").astype(str).str.contains(keyword, na=False) |
        df["关键词"].fillna("").astype(str).str.contains(keyword, na=False) |
        df["文 摘"].fillna("").astype(str).str.contains(keyword, na=False)
    )

    # 保留匹配成功（至少一列含关键词）的行
    filtered_df = df[mask]

    # 输出结果
    try:
        filtered_df.to_excel(output_path, index=False)
        print(f"处理完成！\n原始行数：{len(df)}\n保留行数：{len(filtered_df)}\n删除行数：{len(df)-len(filtered_df)}")
        print(f"结果已保存至：{output_path}")
        return len(df), len(filtered_df)
    except Exception as e:
        print(f"保存文件失败: {e}")
        return None, None

if __name__ == "__main__":
    # 参数说明：
    # 不传参数：使用默认 input.xlsx
    # 传1个参数：指定输入文件，输出覆盖该文件，自动创建 backup
    # 传2个参数：指定输入和输出文件（旧逻辑，仍可用）
    if len(sys.argv) >= 2:
        input_file = sys.argv[1]
        if len(sys.argv) >= 3:
            output_file = sys.argv[2]
        else:
            output_file = input_file  # 覆盖原文件
    else:
        input_file = "input.xlsx"
        output_file = "output.xlsx"
    
    # 生成备份文件名：在文件名最后一个点前插入 -backup
    file_dir, file_name = os.path.split(input_file)
    name_without_ext, ext = os.path.splitext(file_name)
    backup_name = f"{name_without_ext}-backup{ext}"
    backup_path = os.path.join(file_dir, backup_name) if file_dir else backup_name
    
    # 生成统计报告文件名
    stats_name = f"{name_without_ext}-stats.md"
    stats_path = os.path.join(file_dir, stats_name) if file_dir else stats_name
    
    # 检查输入文件是否存在
    if not os.path.exists(input_file):
        print(f"错误：输入文件不存在 - {input_file}")
        sys.exit(1)
    
    # 创建备份
    try:
        shutil.copy2(input_file, backup_path)
        print(f"✓ 备份已创建：{backup_path}")
    except Exception as e:
        print(f"警告：备份失败 - {e}")
    
    # 处理并直接覆盖原始文件
    original_count, filtered_count = filter_excel(input_file, input_file, keyword="新青年")
    
    # 生成统计报告
    if original_count is not None and filtered_count is not None:
        generate_stats_report(input_file, original_count, filtered_count, "新青年", stats_path)