"""按 labelId 或日期下载单条高驰活动的 FIT,落到 coros-fit/。

用法:
  COROS_EMAIL=xxx COROS_PASSWORD=yyy python3 -m coros.download_one list
  COROS_EMAIL=xxx COROS_PASSWORD=yyy python3 -m coros.download_one list --date 2026-08-15
  COROS_EMAIL=xxx COROS_PASSWORD=yyy python3 -m coros.download_one label 479467173039867057
  COROS_EMAIL=xxx COROS_PASSWORD=yyy python3 -m coros.download_one 2026-08-15  # 兼容:裸日期 = list
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]
PARENT_DIR = os.path.split(CURRENT_DIR)[0]
sys.path.append(PARENT_DIR)

from coros.coros_client import CorosClient  # noqa: E402
from config import COROS_FIT_DIR  # noqa: E402


def _parse_ts(v):
    """activity list 里的时间字段格式不一,统一解成 datetime(本地时区)。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000.0) if v > 10_000_000_000 else datetime.fromtimestamp(v)
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    return None


def list_recent(client, date_str=None, days=7):
    """列出某日期前后 N 天的活动,默认 2026-08-15 前后 1 天。"""
    acts = client.getAllActivities()
    if not acts:
        print("高驰没有返回任何活动")
        return []
    if date_str:
        target = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        target = datetime.now()
    lo = target - timedelta(days=days)
    hi = target + timedelta(days=days)
    out = []
    for a in acts:
        ts = _parse_ts(a.get("startTime") or a.get("start_time") or a.get("time") or a.get("createTime"))
        if ts is None:
            print(f"  [跳过] 无法解析时间: {a}")
            continue
        if lo <= ts <= hi:
            out.append((ts, a))
    out.sort(key=lambda x: x[0])
    return out


def cmd_list(args):
    email = os.getenv("COROS_EMAIL")
    pwd = os.getenv("COROS_PASSWORD")
    if not email or not pwd:
        print("需要 COROS_EMAIL / COROS_PASSWORD 环境变量", file=sys.stderr)
        sys.exit(2)
    cli = CorosClient(email, pwd)
    cli.login()
    print(f"登录成功: userId={cli.userId} regionId={cli.regionId}")
    acts = list_recent(cli, args.date, args.days)
    if not acts:
        print(f"日期 {args.date} 前后 {args.days} 天无活动,试试 --days 调大或去掉 --date")
        return
    print(f"\n找到 {len(acts)} 条活动:")
    print(f"  {'startTime':<20} {'labelId':<22} {'sport':<10} {'dist(km)':>8} {'dur':>8}  name")
    print(f"  {'-'*20} {'-'*22} {'-'*10} {'-'*8} {'-'*8}  ----")
    for ts, a in acts:
        lid = a.get("labelId") or a.get("id")
        sport = a.get("sportType") or a.get("sport")
        dist = a.get("distance", 0)
        try:
            dist_km = float(dist) / 1000.0
        except (TypeError, ValueError):
            dist_km = 0
        dur = a.get("duration") or a.get("totalTime") or a.get("time")
        name = a.get("name") or a.get("title") or ""
        print(f"  {ts.strftime('%Y-%m-%d %H:%M:%S'):<20} {str(lid):<22} {str(sport):<10} "
              f"{dist_km:>8.2f} {str(dur):>8}  {name}")


def cmd_download(args):
    email = os.getenv("COROS_EMAIL")
    pwd = os.getenv("COROS_PASSWORD")
    if not email or not pwd:
        print("需要 COROS_EMAIL / COROS_PASSWORD 环境变量", file=sys.stderr)
        sys.exit(2)
    cli = CorosClient(email, pwd)
    cli.login()
    label_id = args.label
    sport_type = args.sport if args.sport is not None else 0
    print(f"下载 labelId={label_id} sportType={sport_type} ...")
    resp = cli.downloadActivitie(label_id, sport_type)
    os.makedirs(COROS_FIT_DIR, exist_ok=True)
    out = os.path.join(COROS_FIT_DIR, f"{label_id}.fit")
    with open(out, "wb") as fb:
        fb.write(resp.data)
    print(f"已落到 {out} ({os.path.getsize(out)} bytes)")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列某日期前后 N 天的活动,定位 labelId")
    p_list.add_argument("--date", default=None, help="YYYY-MM-DD,默认今天")
    p_list.add_argument("--days", type=int, default=1, help="前后浮动天数")
    p_list.set_defaults(func=cmd_list)

    p_dl = sub.add_parser("label", help="按 labelId 下载 FIT")
    p_dl.add_argument("label", help="活动 labelId")
    p_dl.add_argument("--sport", type=int, default=0, help="sportType,跑步填 0")
    p_dl.set_defaults(func=cmd_download)

    # 兼容:python -m coros.download_one 2026-08-15  (裸日期 -> list --date <date>)
    if len(sys.argv) == 2 and sys.argv[1].count("-") == 2 and len(sys.argv[1]) == 10:
        sys.argv[1:1] = ["list", "--date"]
    elif len(sys.argv) >= 2 and sys.argv[1] not in ("list", "label"):
        sys.argv[1:1] = ["list"]

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()