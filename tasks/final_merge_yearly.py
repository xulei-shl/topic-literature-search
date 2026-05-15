"""合并所有年份的 final merged 文件，更新进度为完成"""
import json, re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vp-search" / "scripts"))

from export_processor import ExportResultProcessor
from src.utils.result_output import build_summary_output_filename

# ---- 1. 读取顶层进度 ----
progress_file = Path("outputs/vp-search/新青年/progress-新青年-fe7e08c1ae.json")
data = json.loads(progress_file.read_text(encoding="utf-8"))
r = data["runtime"]
s = data["search_params"]
output_dir = Path(r["output_dir"])

# ---- 2. 按年份取最新 merged 文件 ----
year_files: dict[str, Path] = {}
for fp in r["yearly_result_files"]:
    p = Path(fp)
    m = re.search(r"year-(\d{4})", str(p))
    if not m:
        continue
    year = m.group(1)
    if year not in year_files or p.stat().st_mtime > year_files[year].stat().st_mtime:
        year_files[year] = p

sorted_years = sorted(year_files.keys())
selected = [year_files[y] for y in sorted_years]
print(f"共 {len(selected)} 个年份，文件列表:")
for y, p in zip(sorted_years, selected):
    print(f"  {y}: {p.name}")

# ---- 3. 合并所有年份 ----
final_file = output_dir / build_summary_output_filename(query=s["query"], fallback="vp-yearly")
processor = ExportResultProcessor()
final_file_path = processor.merge_batch_excels(selected, final_file, check_reference_column=True)

# ---- 4. 生成汇总报告 ----
date_range = ""
if s.get("date_from") and s.get("date_to"):
    date_range = f"{s['date_from']}-{s['date_to']}"
core = "是" if s.get("core_only") else "否"
lines = [
    f"检索词: {s['query']}",
    "状态: success",
    f"总数: {r['exported_total']}",
    f"选中: {r['exported_total']}",
    f"导出: {r['exported_total']}",
    f"计划导出: {r['planned_download']}",
    f"批次数: {r['exported_batches']} / {r['batch_count']}",
]
if date_range:
    lines.append(f"日期范围: {date_range}")
lines.append(f"核心: {core}")
lines += [
    f"文件: {final_file_path}",
    f"进度文件: {str(progress_file.resolve())}",
]
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
report_path = output_dir / f"{timestamp}-{s['query'][:20]}-report.txt"
report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---- 5. 更新进度文件为完成 ----
data["status"] = "success"
r["final_file_path"] = final_file_path
r["current_year"] = ""
r["current_year_date_from"] = ""
r["current_year_date_to"] = ""
r["current_year_progress_file"] = ""
r["next_year_index"] = len(sorted_years)
data["updated_at"] = datetime.now().astimezone().isoformat()
if "last_error" in data:
    del data["last_error"]
progress_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\n合并完成: {final_file_path}")
print(f"报告生成: {report_path}")
print(f"进度已更新为 success")
