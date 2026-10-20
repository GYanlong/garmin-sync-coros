import os
import sys
import zipfile
import io
from datetime import datetime, timezone

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))  # scripts/coros/
config_path = os.path.dirname(CURRENT_DIR)                # scripts/
sys.path.append(config_path)
sys.path.append(CURRENT_DIR)

from coros_client import CorosClient
from config  import DB_DIR, COROS_FIT_DIR
from coros_db import CorosDB
from garmin.garmin_client import GarminClient


SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 0,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
    # 只同步此日期(含)之后的活动;ISO 格式 YYYY-MM-DD,空字符串表示不过滤。
    # 配合 GitHub Actions 时可在 workflow env 覆盖,本地调试直接 export 即可。
    'GARMIN_SYNC_SINCE': '2026-10-15',
}


# 阈值:数字时间戳 > 该值视为毫秒级,否则视为秒级。
# 1e10 = 2286-11-20 的秒级 epoch;同年之前的秒级都 ≤ 1e10,而 2001-09-09 之后的
# 毫秒级 epoch 都 ≥ 1e12。两者差距 100 倍,在合理时间区间内不会混淆。
_MS_THRESHOLD = 10_000_000_000


def _parse_activity_ts_ms(activity):
    """从高驰活动对象里抽时间戳(毫秒);字段名不统一,逐个试。

    高驰 activity list 常见字段:startTime / start_time / time / createTime
    实测 (2026-10) startTime 是秒级 int,endTime 也是秒级 int;
    旧版本/其他端点偶尔返回毫秒级 int 或字符串时间。
    """
    for key in ('startTime', 'start_time', 'time', 'createTime'):
        v = activity.get(key) if isinstance(activity, dict) else None
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return int(v) if v > _MS_THRESHOLD else int(v * 1000)
        if isinstance(v, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(v, fmt)
                    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
                except ValueError:
                    continue
            try:
                n = int(v)
                return n if n > _MS_THRESHOLD else n * 1000
            except ValueError:
                continue
    return None


def _filter_activities_since(activities, since_iso):
    """按 since_iso(YYYY-MM-DD,UTC) 过滤活动;返回 (保留列表, 跳过条数)。"""
    if not since_iso:
        return activities, 0
    cutoff_dt = datetime.fromisoformat(since_iso).replace(tzinfo=timezone.utc)
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)
    kept, skipped = [], 0
    for act in activities:
        ts_ms = _parse_activity_ts_ms(act)
        if ts_ms is None or ts_ms >= cutoff_ms:
            kept.append(act)
        else:
            skipped += 1
    return kept, skipped


def init(coros_db):
    ## 判断RQ数据库是否存在
    print(os.path.join(DB_DIR, coros_db.coros_db_name))
    if not os.path.exists(os.path.join(DB_DIR, coros_db.coros_db_name)):
        ## 初始化建表
        coros_db.initDB()
    if not os.path.exists(COROS_FIT_DIR):
        os.mkdir(COROS_FIT_DIR)


if __name__ == "__main__":
  # 首先读取 面板变量 或者 github action 运行变量
  for k in SYNC_CONFIG:
      if os.getenv(k):
          v = os.getenv(k)
          SYNC_CONFIG[k] = v

  COROS_EMAIL = SYNC_CONFIG["COROS_EMAIL"]
  COROS_PASSWORD = SYNC_CONFIG["COROS_PASSWORD"]
  corosClient = CorosClient(COROS_EMAIL, COROS_PASSWORD)

  GARMIN_EMAIL = SYNC_CONFIG["GARMIN_EMAIL"]
  GARMIN_PASSWORD = SYNC_CONFIG["GARMIN_PASSWORD"]
  GARMIN_AUTH_DOMAIN = SYNC_CONFIG["GARMIN_AUTH_DOMAIN"]
  GARMIN_NEWEST_NUM = SYNC_CONFIG["GARMIN_NEWEST_NUM"]

  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)


  ## db 名称
  db_name = "coros.db"
  ## 建立DB链接
  coros_db = CorosDB(db_name)
  ## 初始化DB位置和下载文件位置
  init(coros_db)

  all_activities = corosClient.getAllActivities()
  if all_activities == None or len(all_activities) == 0:
      exit()

  # 只同步 GARMIN_SYNC_SINCE 之后的活动
  all_activities, skipped_old = _filter_activities_since(
      all_activities, SYNC_CONFIG["GARMIN_SYNC_SINCE"],
  )
  if skipped_old:
      print(f"[filter] 跳过 {skipped_old} 条 {SYNC_CONFIG['GARMIN_SYNC_SINCE']} 之前的活动")
  if not all_activities:
      exit()

  for activity in all_activities:
      activity_id = activity["labelId"]
      sport_type = activity["sportType"]
      coros_db.saveActivity(activity_id, sport_type)



  un_sync_list = coros_db.getUnSyncActivity()
  if un_sync_list == None or len(un_sync_list) == 0:
      exit()
  for un_sync in un_sync_list:
    try:
      id = un_sync["id"]
      sport_type = un_sync["sportType"]
      file = corosClient.downloadActivitie(id, sport_type)
      raw = file.data
      buf = io.BytesIO(raw)
      if zipfile.is_zipfile(buf):
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
          fit_names = [n for n in zf.namelist() if n.endswith('.fit')]
          statuses = []
          for fit_name in fit_names:
            fit_path = os.path.join(COROS_FIT_DIR, f"{id}-{os.path.basename(fit_name)}")
            with open(fit_path, "wb") as fb:
              fb.write(zf.read(fit_name))
            s = garminClient.upload_activity(fit_path)
            print(f"{fit_name} upload status {s}")
            statuses.append(s)
          upload_status = "SUCCESS" if any(s == "SUCCESS" for s in statuses) else statuses[0] if statuses else "UPLOAD_EXCEPTION"
      else:
        file_path = os.path.join(COROS_FIT_DIR, f"{id}.fit")
        with open(file_path, "wb") as fb:
          fb.write(raw)
        upload_status = garminClient.upload_activity(file_path)
        print(f"{id}.fit upload status {upload_status}")
      if upload_status in ("SUCCESS", "DUPLICATE_ACTIVITY"):
        coros_db.updateSyncStatus(id)
      
    except Exception as err:
      print(err)
      coros_db.updateExceptionSyncStatus(id)
  # exit()