"""
删除 Excel 中三列均不包含关键词「新青年」的行
列名：「题 名」「关键词」「文 摘」（注意空格）
"""

import pandas as pd
import sys

def filter_excel(input_path, output_path, keyword="新青年"):
    # 读取 Excel 文件
    try:
        df = pd.read_excel(input_path)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return

    # 检查所需列是否存在（列名可能与显示不完全一致，可打印提示）
    required_cols = ["题 名", "关键词", "文 摘"]
    for col in required_cols:
        if col not in df.columns:
            print(f"错误：Excel 中缺少列「{col}」，实际列名：{list(df.columns)}")
            return

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
    except Exception as e:
        print(f"保存文件失败: {e}")

if __name__ == "__main__":
    # 你可以直接修改下面的文件名，或通过命令行参数传入
    if len(sys.argv) == 3:
        input_file = sys.argv[1]
        output_file = sys.argv[2]
    else:
        input_file = "input.xlsx"   # 默认输入文件名
        output_file = "output.xlsx" # 默认输出文件名
        print(f"使用默认文件名：{input_file} -> {output_file}")
        print("提示：你也可以通过命令「python script.py 输入文件.xlsx 输出文件.xlsx」指定文件")

    filter_excel(input_file, output_file, keyword="新青年")