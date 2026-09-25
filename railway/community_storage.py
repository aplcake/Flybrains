"""Object storage boundary. S3 credentials stay in the API process."""
import os, hashlib

class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config
        self.bucket=os.environ['S3_BUCKET']
        self.client=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],
            region_name=os.environ.get('S3_REGION','auto'),
            aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4',s3={'addressing_style':os.environ.get('S3_ADDRESSING_STYLE','virtual')},connect_timeout=10,read_timeout=45,retries={'max_attempts':3}))
    def head(self,key):
        from botocore.exceptions import ClientError
        try:return self.client.head_object(Bucket=self.bucket,Key=key)
        except ClientError as e:
            if e.response['ResponseMetadata']['HTTPStatusCode']==404:return None
            raise
    def upload_ticket(self,record):
        headers={'Content-Type':record['mime'],'Content-MD5':record['md5']}
        url=self.client.generate_presigned_url('put_object',Params={'Bucket':self.bucket,'Key':record['key'],
            'ContentType':record['mime'],'ContentMD5':record['md5'],'ContentLength':record['bytes']},ExpiresIn=900)
        return {'url':url,'headers':headers}
    def verify(self,record):
        response=self.client.get_object(Bucket=self.bucket,Key=record['key']);body=response['Body'];h=hashlib.sha256();size=0
        try:
            for block in iter(lambda:body.read(1024*1024),b''):
                size+=len(block)
                if size>record['bytes']:raise ValueError('Stored media exceeds declared size')
                h.update(block)
        finally:body.close()
        if size!=record['bytes'] or h.hexdigest()!=record['sha256']:raise ValueError('Stored media checksum mismatch')
        return response['ETag']
    def read_url(self,key):
        return self.client.generate_presigned_url('get_object',Params={'Bucket':self.bucket,'Key':key},ExpiresIn=3600)
