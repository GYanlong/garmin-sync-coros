"""_parse_activity_ts_ms / _filter_activities_since 单元测试

覆盖路径:
  - 数字时间戳(s:实测形状 2026-10)
  - 数字时间戳(ms:理论形状)
  - 字符串时间戳(4 种格式)
  - 字符串数字时间戳(同时含单位歧义)
  - 字段缺失/无法解析(保留而非丢弃)
  - since_iso 为空(关闭)
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许从仓库根目录直接执行:python scripts/tests/coros/coros_sync_filter_test.py
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.coros.coros_sync_garmin import (  # noqa: E402
    _parse_activity_ts_ms,
    _filter_activities_since,
    _MS_THRESHOLD,
)


CUTOFF = "2026-10-15"
CUTOFF_MS = int(datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp() * 1000)


def _expect(label, got, want):
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, want {want!r}")


# ---------- _parse_activity_ts_ms ----------

# 1) 秒级 int(2026-10-20 01:33:14 UTC = 1792459994 s)
_act_seconds = {"labelId": "x", "startTime": 1792459994}
_ms = _parse_activity_ts_ms(_act_seconds)
_expect("seconds int", _ms, 1792459994000)

# 2) 毫秒级 int(同一时刻 ms 表示)
_act_ms = {"labelId": "x", "startTime": 1792459994000}
_expect("milliseconds int", _parse_activity_ts_ms(_act_ms), 1792459994000)

# 3) 浮点秒
_act_seconds_float = {"labelId": "x", "startTime": 1792459994.0}
_expect("seconds float", _parse_activity_ts_ms(_act_seconds_float), 1792459994000)

# 4) 字符串时间戳 4 种格式
for fmt_key, fmt_str, expected_ms in [
    ("Y-m-d H:M:S", "2026-10-20 01:33:14", 1792459994000),
    ("Y-m-dTH:M:S", "2026-10-20T01:33:14", 1792459994000),
    ("Y-m-dTH:M:S.f", "2026-10-20T01:33:14.500000", 1792459994500),
    ("Y/m/d H:M:S", "2026/10/20 01:33:14", 1792459994000),
]:
    _expect(f"str {fmt_key}", _parse_activity_ts_ms({"startTime": fmt_str}), expected_ms)

# 5) 字符串数字时间戳(毫秒级)
_expect("str ms", _parse_activity_ts_ms({"startTime": "1792459994000"}), 1792459994000)

# 6) 字段缺失
_expect("missing field", _parse_activity_ts_ms({"labelId": "x"}), None)

# 7) 全部字段都不可解析
_expect("unparseable", _parse_activity_ts_ms({"startTime": "not a date"}), None)

# 8) 阈值边界:恰好 _MS_THRESHOLD 应视为秒级
_expect("threshold = ms?",
        _parse_activity_ts_ms({"startTime": _MS_THRESHOLD}),
        _MS_THRESHOLD * 1000)

# ---------- _filter_activities_since ----------

_acts = [
    {"labelId": "s_on_cutoff", "startTime": CUTOFF_MS // 1000},        # 秒级,正好 = cutoff
    {"labelId": "ms_on_cutoff", "startTime": CUTOFF_MS},              # 毫秒级,正好 = cutoff
    {"labelId": "s_after",      "startTime": CUTOFF_MS // 1000 + 1},  # 秒级 +1s
    {"labelId": "str_after",    "startTime": "2026-10-20 10:00:00"},  # 字符串
    {"labelId": "s_before",     "startTime": CUTOFF_MS // 1000 - 1},  # 秒级 -1s
    {"labelId": "ms_before",    "startTime": CUTOFF_MS - 1},          # 毫秒级 -1ms
    {"labelId": "no_time"},                                              # 无时间 -> 保留
]
kept, skipped = _filter_activities_since(_acts, CUTOFF)
_kept_ids = sorted(a["labelId"] for a in kept)
_expect("filter kept", _kept_ids,
        sorted(["s_on_cutoff", "ms_on_cutoff", "s_after", "str_after", "no_time"]))
_expect("filter skipped", skipped, 2)

# since_iso 为空 -> 不过滤
kept2, skipped2 = _filter_activities_since(_acts, "")
_expect("empty since kept", len(kept2), len(_acts))
_expect("empty since skipped", skipped2, 0)

# since_iso 含时区(应是 UTC 当日 00:00)
kept3, skipped3 = _filter_activities_since(
    [{"startTime": "2026-10-15 00:00:00"}], "2026-10-15")
_expect("on-cutoff str kept", len(kept3), 1)
_expect("on-cutoff str skipped", skipped3, 0)

print("PASS - all _parse_activity_ts_ms / _filter_activities_since cases")