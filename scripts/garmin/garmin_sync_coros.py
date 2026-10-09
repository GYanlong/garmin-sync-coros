import os
import sys
from datetime import datetime, timezone

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]  # 当前目录
config_path = CURRENT_DIR.rsplit('/', 1)[0]  # 上三级目录
sys.path.append(config_path)

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB
from coros.coros_client import CorosClient
from oss.ali_oss_client import AliOssClient
from oss.aws_oss_client import AwsOssClient
from utils.md5_utils import calculate_md5_file

SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 10000,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
    # 只同步此日期(含)之后的活动;ISO 格式 YYYY-MM-DD,空字符串表示不过滤。
    # 配合 GitHub Actions 时可在 workflow env 覆盖,本地调试直接 export 即可。
    'GARMIN_SYNC_SINCE': '2026-10-01',
}


def _parse_activity_ts_ms(activity):
    """从佳明活动对象里抽时间戳(毫秒);字段名不统一,逐个试。

    佳明 Connect API 常见字段:startTimeGMT / startTimeLocal / startTime(秒)
    """
    for key in ('startTimeGMT', 'startTimeLocal', 'startTime'):
        v = activity.get(key) if isinstance(activity, dict) else None
        if v is None:
            continue
        if isinstance(v, (int, float)):
            # Garmin 通常返回毫秒整数;但 startTime(秒级)需要区分
            return int(v) if v > 10_000_000_000 else int(v * 1000)
        if isinstance(v, str):
            try:
                n = int(v)
                return n if n > 10_000_000_000 else n * 1000
            except ValueError:
                try:
                    dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
                    return int(dt.timestamp() * 1000)
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
    print(os.path.join(DB_DIR, coros_db.garmin_db_name))
    if not os.path.exists(os.path.join(DB_DIR, coros_db.garmin_db_name)):
        ## 初始化建表
        coros_db.initDB()
    if not os.path.exists(GARMIN_FIT_DIR):
        os.mkdir(GARMIN_FIT_DIR)

if __name__ == "__main__":

   # 首先读取 面板变量 或者 github action 运行变量
  for k in SYNC_CONFIG:
      if os.getenv(k):
          v = os.getenv(k)
          SYNC_CONFIG[k] = v

  ## db 名称
  db_name = "garmin.db"
  ## 建立DB链接
  garmin_db = GarminDB(db_name)
  ## 初始化DB位置和下载文件位置
  init(garmin_db)

  GARMIN_EMAIL = SYNC_CONFIG["GARMIN_EMAIL"]
  GARMIN_PASSWORD = SYNC_CONFIG["GARMIN_PASSWORD"]
  GARMIN_AUTH_DOMAIN = SYNC_CONFIG["GARMIN_AUTH_DOMAIN"]
  GARMIN_NEWEST_NUM = SYNC_CONFIG["GARMIN_NEWEST_NUM"]

  garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)

  COROS_EMAIL = SYNC_CONFIG["COROS_EMAIL"]
  COROS_PASSWORD = SYNC_CONFIG["COROS_PASSWORD"]
  corosClient = CorosClient(COROS_EMAIL, COROS_PASSWORD)
  corosClient.login()
  all_activities = garminClient.getAllActivities()
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
      activity_id = activity["activityId"]
      garmin_db.saveActivity(activity_id)



  un_sync_id_list = garmin_db.getUnSyncActivity()
  if un_sync_id_list == None or len(un_sync_id_list) == 0:
      exit()
  file_path_list = []

  for un_sync_id in un_sync_id_list:
    try:
      file = garminClient.downloadFitActivity(un_sync_id)
      file_path = os.path.join(GARMIN_FIT_DIR, f"{un_sync_id}.zip")
      with open(file_path, "wb") as fb:
          fb.write(file)

      un_sync_info = {
        "un_sync_id": un_sync_id,
        "file_path": file_path
      }

      file_path_list.append(un_sync_info)

    except Exception as err:
      print(err)
  sync_failed = False
  for un_sync_info in file_path_list:
    try:
      client = None
      ## 中国区使用阿里云OSS,STS 端点需登录 accessToken(2026-10-03 后免登录通道已下线)
      if corosClient.regionId == 2:
         client = AliOssClient(access_token=corosClient.accessToken)
      elif corosClient.regionId == 1 or corosClient.regionId == 3:
         from coros.region_config import REGIONCONFIG
         training_host = REGIONCONFIG[corosClient.regionId]["hostname"].replace("https://", "")
         client = AwsOssClient(
             access_token=corosClient.accessToken,
             training_host=training_host,
         )

      file_path = un_sync_info["file_path"]
      un_sync_id = un_sync_info["un_sync_id"]
      oss_obj = client.multipart_upload(file_path,  f"{corosClient.userId}/{calculate_md5_file(file_path)}.zip")
      size = os.path.getsize(file_path)
      upload_result = corosClient.uploadActivity(f"fit_zip/{corosClient.userId}/{calculate_md5_file(file_path)}.zip", calculate_md5_file(file_path), f"{un_sync_id}.zip", size)
      if upload_result:
          garmin_db.updateSyncStatus(un_sync_id)
    except Exception as err:
      print(err)
      garmin_db.updateExceptionSyncStatus(un_sync_id)
      sync_failed = True
  if sync_failed:
    sys.exit(1)
