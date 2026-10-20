"""预览 GARMIN_SYNC_SINCE 过滤器对当前账号活动的命中情况 —— 只读、不上传。
用法:
  COROS_EMAIL=xxx COROS_PASSWORD=yyy python3 -m coros.preview_filter [--since YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]
PARENT_DIR = os.path.split(CURRENT_DIR)[0]
sys.path.insert(0, PARENT_DIR)

from coros.coros_client import CorosClient  # noqa: E402
from coros.coros_sync_garmin import (  # noqa: E402
    _parse_activity_ts_ms,
    _filter_activities_since,
    SYNC_CONFIG,
)


def _fmt_ts(ms):
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=SYNC_CONFIG["GARMIN_SYNC_SINCE"],
                    help="过滤截止日期 YYYY-MM-DD,默认 SYNC_CONFIG['GARMIN_SYNC_SINCE']")
    args = ap.parse_args()

    email = os.getenv("COROS_EMAIL")
    pwd = os.getenv("COROS_PASSWORD")
    if not email or not pwd:
        print("需要 COROS_EMAIL / COROS_PASSWORD 环境变量", file=sys.stderr)
        sys.exit(2)

    cli = CorosClient(email, pwd)
    cli.login()
    print(f"登录成功: userId={cli.userId} regionId={cli.regionId}")

    acts = cli.getAllActivities()
    if not acts:
        print("高驰返回空活动列表")
        return

    kept, skipped = _filter_activities_since(acts, args.since)
    print(f"\n截止 {args.since} (UTC) — 总 {len(acts)} 条, 保留 {len(kept)} 条, 跳过 {skipped} 条")

    if skipped:
        print("\n[跳过] 早于截止日期的活动:")
        print(f"  {'UTC 时间':<20} {'labelId':<22} {'sport':<6}  name")
        for a in acts:
            ts = _parse_activity_ts_ms(a)
            if ts is not None and ts < int(datetime.fromisoformat(args.since)
                    .replace(tzinfo=timezone.utc).timestamp() * 1000):
                print(f"  {_fmt_ts(ts):<20} {str(a.get('labelId')):<22} "
                      f"{str(a.get('sportType')):<6}  {a.get('name') or ''}")

    if kept:
        print(f"\n[保留] {len(kept)} 条(只展示前 20 条):")
        print(f"  {'UTC 时间':<20} {'labelId':<22} {'sport':<6}  name")
        for a in kept[:20]:
            ts = _parse_activity_ts_ms(a)
            print(f"  {_fmt_ts(ts):<20} {str(a.get('labelId')):<22} "
                  f"{str(a.get('sportType')):<6}  {a.get('name') or ''}")
        if len(kept) > 20:
            print(f"  ... 还有 {len(kept)-20} 条未列出")

    # 同时打印 1 条样本的原始字段,便于核对格式
    if acts:
        print("\n[样本] 第 1 条活动原始字段(只看前 8 个键):")
        sample = acts[0]
        for k in list(sample.keys())[:8]:
            print(f"  {k}: {sample.get(k)!r}")


if __name__ == "__main__":
    main()