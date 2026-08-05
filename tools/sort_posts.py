# -*- coding: utf-8 -*-
"""按真实发表时间重新排序 咕咕镇论坛内帖子.md 已读帖子记录表。

用法:
    python tools/sort_posts.py
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD_PATH = os.path.join(ROOT, "咕咕镇资料", "咕咕镇论坛内帖子.md")
TIME_PATH = os.path.join(ROOT, "咕咕镇资料", "publish_time.json")

ROW_RE = re.compile(
    r"^\| ([\d-]+) \| (.+?) \| (https://bbs\.kfpromax\.com/read\.php\?tid=(\d+)&sf=[0-9a-f]+) \| ([^|]+?) \|$")


def main():
    times = json.load(open(TIME_PATH, encoding="utf-8"))
    lines = open(MD_PATH, encoding="utf-8").read().split("\n")

    rows = []
    missing = []
    in_table = False
    header_idx = None
    table_start = None
    for i, line in enumerate(lines):
        if line.startswith("| 日期 |"):
            in_table = True
            header_idx = i
            continue
        if in_table and line.strip() == "":
            in_table = False
            continue
        if in_table and table_start is None and not line.startswith("|------"):
            table_start = i
        if in_table:
            m = ROW_RE.match(line)
            if m:
                old_date, title, url, tid, status = m.groups()
                pt = times.get(tid, "")
                if pt:
                    new_date = pt[:10]  # YYYY-MM-DD
                else:
                    new_date = old_date
                    missing.append((tid, title))
                rows.append({
                    "date": new_date,
                    "time": pt or old_date + " 00:00",
                    "title": title,
                    "url": url,
                    "tid": tid,
                    "status": status,
                })

    print(f"表格行: {len(rows)}, 缺发表时间: {len(missing)}")
    missing_tids = {tid for tid, _ in missing}

    # 按发表时间倒序 (新→老); 缺时间的放最后(按原日期)
    rows.sort(key=lambda r: r["time"], reverse=True)

    new_lines = lines[: header_idx + 1]  # 保留表头之前的行 + 表头
    new_lines.append("|------|------|-----|------|")  # 分隔行
    for r in rows:
        flag = " ⚠️" if r["tid"] in missing_tids else ""  # 缺真实时间的标记
        new_lines.append(f"| {r['date']}{flag} | {r['title']} | {r['url']} | {r['status']} |")
    # 表格后的内容 (从表格结束后第一个空行后的下一行开始)
    # table_start 指向第一行数据, 表格在第一个空行结束
    idx = table_start
    while idx < len(lines) and lines[idx].strip() != "":
        idx += 1
    new_lines.extend(lines[idx:])  # 空行之后的内容 (包含空行前的? 不, 从空行开始)

    open(MD_PATH, "w", encoding="utf-8").write("\n".join(new_lines))
    print(f"完成: 表格重写, 共 {len(rows)} 行")


if __name__ == "__main__":
    main()
