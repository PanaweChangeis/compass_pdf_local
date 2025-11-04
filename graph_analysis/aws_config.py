"""
AWS Configuration Discovery
===========================

Automatically discover AWS resources (S3 buckets, DynamoDB tables, region)
based on naming patterns and the current AWS profile.
"""

import boto3
import os
import logging
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


class AWSConfigDiscovery:
    """Discover AWS configuration from current profile"""
    
    def __init__(self, profile_name: Optional[str] = None):
        """
        Initialize AWS configuration discovery.
        
        Args:
            profile_name: AWS profile name (uses default if not specified)
        """
        self.profile_name = profile_name
        self.session = self._create_session()
        self.region = self.session.region_name or 'us-east-1'
        
        logger.info(f"Initialized AWS session with profile: {profile_name or 'default'}")
        logger.info(f"Region: {self.region}")
    
    def _create_session(self) -> boto3.Session:
        """Create boto3 session with profile"""
        if self.profile_name:
            return boto3.Session(profile_name=self.profile_name)
        else:
            return boto3.Session()
    
    def discover_processed_bucket(self) -> Optional[str]:
        """
        Discover the processed documents S3 bucket.
        
        Looks for buckets matching pattern:
        - Contains 'processed' or 'document'
        - CDK stack output pattern
        
        Returns:
            Bucket name or None if not found
        """
        try:
            s3_client = self.session.client('s3')
            response = s3_client.list_buckets()
            
            # Look for processed/document buckets
            candidates = []
            for bucket in response['Buckets']:
                name = bucket['Name']
                name_lower = name.lower()
                
                # Match patterns
                if any(pattern in name_lower for pattern in [
                    'processeddocuments',
                    'processed-documents',
                    'document-processed'
                ]):
                    candidates.append(name)
                    logger.info(f"Found candidate bucket: {name}")
            
            if candidates:
                # Return the first match (or most recent if multiple)
                selected = candidates[0]
                logger.info(f"Selected processed bucket: {selected}")
                return selected
            
            logger.warning("No processed documents bucket found")
            return None
            
        except Exception as e:
            logger.error(f"Error discovering S3 bucket: {e}")
            return None
    
    def discover_documents_table(self) -> Optional[str]:
        """
        Discover the documents DynamoDB table.
        
        Looks for tables matching pattern:
        - Contains 'documents' or 'Documents'
        - Has document_id and document_name keys
        
        Returns:
            Table name or None if not found
        """
        try:
            dynamodb_client = self.session.client('dynamodb')
            response = dynamodb_client.list_tables()
            
            # Look for documents tables
            candidates = []
            for table_name in response['TableNames']:
                name_lower = table_name.lower()
                
                # Match patterns
                if 'documents' in name_lower or 'document' in name_lower:
                    # Verify table structure
                    try:
                        table_desc = dynamodb_client.describe_table(TableName=table_name)
                        key_schema = table_desc['Table']['KeySchema']
                        
                        # Check for document_id and document_name keys
                        keys = [key['AttributeName'] for key in key_schema]
                        if 'document_id' in keys or 'document_name' in keys:
                            candidates.append(table_name)
                            logger.info(f"Found candidate table: {table_name}")
                    except Exception as e:
                        logger.debug(f"Error checking table {table_name}: {e}")
                        continue
            
            if candidates:
                # Return the first match
                selected = candidates[0]
                logger.info(f"Selected documents table: {selected}")
                return selected
            
            logger.warning("No documents table found")
            return None
            
        except Exception as e:
            logger.error(f"Error discovering DynamoDB table: {e}")
            return None
    
    def discover_all(self) -> Dict[str, Optional[str]]:
        """
        Discover all AWS resources.
        
        Returns:
            Dict with bucket, table, and region
        """
        config = {
            'bucket': self.discover_processed_bucket(),
            'table': self.discover_documents_table(),
            'region': self.region,
            'profile': self.profile_name
        }
        
        logger.info(f"Discovered configuration: {config}")
        return config
    
    @staticmethod
    def get_current_profile() -> Optional[str]:
        """
        Get the current AWS profile from environment.
        
        Returns:
            Profile name or None if using default
        """
        return os.environ.get('AWS_PROFILE')
    
    @staticmethod
    def validate_credentials() -> bool:
        """
        Validate that AWS credentials are available.
        
        Returns:
            True if credentials are valid
        """
        try:
            sts = boto3.client('sts')
            sts.get_caller_identity()
            return True
        except Exception as e:
            logger.error(f"AWS credentials validation failed: {e}")
            return False


def discover_aws_config(profile_name: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Convenience function to discover AWS configuration.
    
    Args:
        profile_name: AWS profile name (uses default if not specified)
    
    Returns:
        Dict with bucket, table, region, and profile
    """
    discovery = AWSConfigDiscovery(profile_name)
    return discovery.discover_all()


def get_aws_config_from_env() -> Dict[str, Optional[str]]:
    """
    Get AWS configuration from environment variables or auto-discover.
    
    Priority:
    1. Environment variables (S3_PROCESSED_BUCKET, DDB_DOCUMENTS_TABLE)
    2. Auto-discovery based on current AWS profile
    
    Returns:
        Dict with bucket, table, region, and profile
    """
    # Check environment variables first
    bucket = os.getenv('S3_PROCESSED_BUCKET')
    table = os.getenv('DDB_DOCUMENTS_TABLE')
    region = os.getenv('AWS_REGION', 'us-east-1')
    profile = os.getenv('AWS_PROFILE')
    
    # If not in env, auto-discover
    if not bucket or not table:
        logger.info("Auto-discovering AWS resources...")
        discovered = discover_aws_config(profile)
        
        bucket = bucket or discovered['bucket']
        table = table or discovered['table']
        region = discovered['region']
        profile = discovered['profile']
    
    return {
        'bucket': bucket,
        'table': table,
        'region': region,
        'profile': profile
    }
