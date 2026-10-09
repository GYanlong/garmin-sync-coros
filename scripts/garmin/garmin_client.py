import logging
import os
from enum import Enum, auto
import requests

import garth


from .garmin_url_dict import GARMIN_URL_DICT

logger = logging.getLogger(__name__)

# 持久化 garth token 目录;GitHub Actions 通过 actions/cache@v3 跨 run 复用。
# 本地运行时默认落到 ~/.garth,需要换位置只需改这里。
GARTH_TOKEN_DIR = os.path.expanduser("~/.garth")


def is_duplicate_activity(detailed_import_result):
  """Return whether Garmin's import response reports a duplicate activity."""
  if not isinstance(detailed_import_result, dict):
    return False

  failures = detailed_import_result.get("failures")
  if not isinstance(failures, list):
    return False

  for failure in failures:
    if not isinstance(failure, dict):
      continue
    messages = failure.get("messages")
    if not isinstance(messages, list):
      continue
    if any(
        isinstance(message, dict)
        and message.get("content") == "Duplicate Activity."
        for message in messages
    ):
      return True
  return False


class GarminClient:
  def __init__(self, email, password, auth_domain, newest_num):
        self.auth_domain = auth_domain
        self.email = email
        self.password = password
        self.garthClient = garth
        self.newestNum = int(newest_num)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.88 Safari/537.36",
            "origin": GARMIN_URL_DICT.get("SSO_URL_ORIGIN"),
            "nk": "NT"
        }
        # 单次 run 内只登录一次;装饰器据此短路
        self._logged_in = False

  ## 登录装饰器 — 优先复用持久化 token,缓存失效才走密码登录
  def login(func):
    def ware(self, *args, **kwargs):
      # 1) 本次 run 已经登录过 → 直接放行(garth 是模块级单例,登录一次就够)
      client = self.garthClient.client
      if self._logged_in and client.oauth2_token is not None:
        return func(self, *args, **kwargs)

      # 2) 尝试加载上次缓存的 oauth2 token(GH Actions 走 actions/cache 跨 run 复用)
      loaded = False
      if os.path.isdir(GARTH_TOKEN_DIR):
        try:
          # garth 0.4.38 没有模块级 garth.load(),必须走 garth.client.load()
          client.load(GARTH_TOKEN_DIR)
          loaded = client.oauth2_token is not None
          if loaded:
            logger.info("Loaded garth tokens from %s", GARTH_TOKEN_DIR)
        except Exception as load_err:
          logger.debug("Load cached garth tokens failed: %s", load_err)

      # 3) 没缓存或缓存失效 → 密码登录
      if not loaded:
        logger.warning(
          "Garmin is not logging in or the token has expired; "
          "falling back to password login."
        )
        if self.auth_domain and str(self.auth_domain).upper() == "CN":
          self.garthClient.configure(domain="garmin.cn")
        try:
          self.garthClient.login(self.email, self.password)
        except Exception as login_err:
          logger.error("Garmin password login failed: %s", login_err)
          raise

        # 4) 登录成功 → 把 token 落盘供下次 run 复用
        try:
          os.makedirs(GARTH_TOKEN_DIR, exist_ok=True)
          self.garthClient.save(GARTH_TOKEN_DIR)
          logger.info("Saved garth tokens to %s", GARTH_TOKEN_DIR)
        except Exception as save_err:
          logger.warning("Failed to save garth tokens: %s", save_err)

      # 5) 去掉 garth 默认 UA(没有就跳过),统一用我们自己的
      client.sess.headers.pop("User-Agent", None)

      self._logged_in = True
      return func(self, *args, **kwargs)
    return ware
  
  @login 
  def download(self, path, **kwargs):
     return self.garthClient.download(path, **kwargs)
  
  @login 
  def connectapi(self, path, **kwargs):
      return self.garthClient.connectapi(path, **kwargs)
     

  ## 获取运动
  def getActivities(self, start:int, limit:int):
     
     params = {"start": str(start), "limit": str(limit)}
     activities =  self.connectapi(path=GARMIN_URL_DICT["garmin_connect_activities"], params=params)
     return activities;

  # ## 获取所有运动
  # def getAllActivities(self): 
  #   all_activities = []
  #   start = 0
  #   limit=100
  #   if 0 < self.newestNum < 100:
  #     limit = self.newestNum
      
  #   while(True):
  #     activities = self.getActivities(start=start, limit=limit)
  #     if len(activities) > 0:
  #       all_activities.extend(activities)
        
  #       if 0 < self.newestNum < 100 or start > self.newestNum:
  #          return all_activities
  #     else:
  #        return all_activities
  #     start += limit

  ## 获取所有运动
  def getAllActivities(self): 
    all_activities = []
    start = 0
    while(True):
      activities = self.getActivities(start=start, limit=100)
      if len(activities) > 0:
         all_activities.extend(activities)
      else:
         return all_activities
      start += 100

  ## 下载原始格式的运动
  def downloadFitActivity(self, activity):
    download_fit_activity_url_prefix = GARMIN_URL_DICT["garmin_connect_fit_download"]
    download_fit_activity_url = f"{download_fit_activity_url_prefix}/{activity}"
    response = self.download(download_fit_activity_url)
    return response

  @login  
  def upload_activity(self, activity_path: str):
    """Upload activity in fit format from file."""
    # This code is borrowed from python-garminconnect-enhanced ;-)
    file_base_name = os.path.basename(activity_path)
    file_extension = file_base_name.split(".")[-1]
    allowed_file_extension = (
        file_extension.upper() in ActivityUploadFormat.__members__
    )

    if allowed_file_extension:
       status = "UPLOAD_EXCEPTION"
       try:
        with open(activity_path, 'rb') as file:
          file_data = file.read()
          fields = {
              'file': (file_base_name, file_data, 'text/plain')
          }

          url_path = GARMIN_URL_DICT["garmin_connect_upload"]
          upload_url = f"https://connectapi.{self.garthClient.client.domain}{url_path}"
          self.headers['Authorization'] = str(self.garthClient.client.oauth2_token)
          response = requests.post(upload_url, headers=self.headers, files=fields)
          res_code = response.status_code
          result = response.json()
          detailed_import_result = (
              result.get("detailedImportResult")
              if isinstance(result, dict)
              else None
          )
          uploadId = (
              detailed_import_result.get("uploadId")
              if isinstance(detailed_import_result, dict)
              else None
          )
          isDuplicateUpload = uploadId == None or uploadId == ''
          if res_code == 202 and not isDuplicateUpload:
              status = "SUCCESS"
          elif res_code == 409 and is_duplicate_activity(detailed_import_result):
              status = "DUPLICATE_ACTIVITY"
          else:
              print(f"  -> HTTP {res_code}: {result}")
       except Exception as e:
            print(f"  -> exception: {e}")
            status = "UPLOAD_EXCEPTION"
       finally:
            return status
    else:
        return "UPLOAD_EXCEPTION"
  

class ActivityUploadFormat(Enum):
  FIT = auto()
  GPX = auto()
  TCX = auto()

class GarminNoLoginException(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, status):
        """Initialize."""
        super(GarminNoLoginException, self).__init__(status)
        self.status = status
