"""AWS Textract client (Blueprint §12.4)."""
import boto3
from app.core.config import settings

class TextractClient:
    def __init__(self):
        region = getattr(settings, "AWS_TEXTRACT_REGION", settings.S3_REGION or "us-east-1")
        self.client = boto3.client("textract", region_name=region,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY)

    def analyze_expense(self, document_bytes: bytes) -> dict:
        return self.client.analyze_expense(Document={"Bytes": document_bytes})

    def analyze_document(self, document_bytes: bytes) -> dict:
        return self.client.analyze_document(
            Document={"Bytes": document_bytes}, FeatureTypes=["TABLES", "FORMS"])
