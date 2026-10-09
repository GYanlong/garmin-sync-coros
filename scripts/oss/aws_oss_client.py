import urllib3
import json
import boto3
import certifi

from boto3.s3.transfer import TransferConfig


from oss.sts_token_error import StsTokenError
from utils.coros_oss_credients_utils import decode

class AwsOssClient:
  def __init__(self, bucket="eu-coros", service="aws", app_id="1660188068672619112", sign="877571111A1EE5316E4B590103D4B5B3", v=2, access_token=None, training_host="training.coros.com"):
    self.bucket = bucket
    self.service = service
    self.app_id = app_id
    self.sign = sign
    self.credentials = None
    self.access_key_id = None
    self.access_key_secret = None
    self.access_token = access_token
    self.training_host = training_host
    self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
    self.v = v
    self.client = None
    self.initClient()

  def initClient(self):
        base_query = f"bucket={self.bucket}&service={self.service}&v={self.v}"
        ## 2026-10-03 起 COROS 关闭免登录 STS 通道,改走 BFF 代理,
        ## 需带登录 accessToken 作 CPL-coros-token cookie;training 主机按 regionId 选(US/EU)
        sts_calls = []
        if self.access_token:
            sts_calls.append((
                f"https://{self.training_host}/api/proxy/oss/sts?{base_query}",
                {"Cookie": f"CPL-coros-token={self.access_token}"},
            ))
        ## 旧免登录通道保留作回退(当前已 404)
        sts_calls.append((
            f"https://faq.coros.com/openapi/oss/sts?{base_query}&app_id={self.app_id}&sign={self.sign}",
            {},
        ))

        sts_token_response = None
        for sts_token_url, extra_headers in sts_calls:
            response = self.req.request('GET', sts_token_url, headers=extra_headers)
            try:
                parsed = json.loads(response.data)
            except ValueError:
                continue
            if parsed.get("code") == 200 and parsed.get("data", {}).get("credentials"):
                sts_token_response = parsed
                break
        if sts_token_response is None:
            raise StsTokenError("Get AWS OSS STS Token Exception")

        credentials = sts_token_response["data"]["credentials"]
        v = sts_token_response["data"].get("v", self.v)
        self.credentials = credentials
        self.v = v
        credients_json = decode(credentials)
        self.client = boto3.client(
            "s3",
            aws_access_key_id=credients_json["AccessKeyId"],
            aws_secret_access_key=credients_json["SecretAccessKey"],
            aws_session_token=credients_json["SessionToken"],
            endpoint_url='https://s3.eu-central-1.amazonaws.com',
        )

  def multipart_upload(self, filePath, fileName):
      # 配置上传选项
      config = TransferConfig(
          multipart_threshold=1024 * 1024 * 5,  # 分片上传的阈值（5MB）
          max_concurrency=4,                   # 并发数
          multipart_chunksize=1024 * 1024 * 5,  # 分片大小（5MB）
          use_threads=True                     # 使用多线程
      )

      # 执行上传
      try:
          self.client.upload_file(
              filePath,
              Bucket=self.bucket,
              Key=f"fit_zip/{fileName}",
              Config=config
          )
          print(f"File {fileName} uploaded successfully!")
      except Exception as e:
          print(f"Upload failed: {e}")
