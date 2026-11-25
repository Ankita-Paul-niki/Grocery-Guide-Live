import os
import requests
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

@deconstructible
class SupabaseStorage(Storage):
    def __init__(self):
        self.project_id = 'nstzoicxnjbvcmqdbkst'
        self.bucket = 'media'
        self.key = os.environ.get('SUPABASE_SERVICE_KEY')
        self.base_url = f'https://{self.project_id}.supabase.co/storage/v1'

    def _save(self, name, content):
        url = f'{self.base_url}/object/{self.bucket}/{name}'
        headers = {
            'Authorization': f'Bearer {self.key}',
            'apikey': self.key,
            'Content-Type': getattr(content, 'content_type', 'application/octet-stream'),
            'x-upsert': 'true',
        }
        requests.post(url, headers=headers, data=content.read())
        return name

    def exists(self, name):
        return False

    def url(self, name):
        return f'{self.base_url}/object/public/{self.bucket}/{name}'
