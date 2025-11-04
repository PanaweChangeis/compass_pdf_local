"""
Document Lookup Module
----------------------

Query DynamoDB to find document processing information by document name.
Uses the document_name_index2 GSI to efficiently lookup documents.
"""

import boto3
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DocumentLookup:
    """Query DynamoDB for document processing information"""
    
    def __init__(self, table_name: str, region: str = 'us-east-1'):
        """
        Initialize DynamoDB connection.
        
        Args:
            table_name: Name of the DynamoDB table
            region: AWS region (default: us-east-1)
        """
        self.table_name = table_name
        self.region = region
        self.dynamodb = boto3.resource('dynamodb', region_name=region)
        self.table = self.dynamodb.Table(table_name)
        logger.info(f"Initialized DocumentLookup for table: {table_name}")
    
    def find_latest_processing(
        self, 
        document_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find the most recent processing for a document name.
        
        Uses the document_name_index2 GSI to query by document_name
        and returns the most recent processing (sorted by document_id descending).
        
        Args:
            document_name: Full filename (e.g., 'example.pdf')
        
        Returns:
            Dict with document_id, document_name, and all metadata
            None if not found
        """
        try:
            logger.info(f"Querying for document: {document_name}")
            
            # Query GSI by document_name
            response = self.table.query(
                IndexName='document_name_index2',
                KeyConditionExpression='document_name = :name',
                ExpressionAttributeValues={':name': document_name},
                ScanIndexForward=False,  # Sort descending (newest first)
                Limit=1  # Only need the most recent
            )
            
            if response['Items']:
                doc_info = response['Items'][0]
                logger.info(f"Found document_id: {doc_info.get('document_id')}")
                return doc_info
            
            logger.warning(f"No processing found for: {document_name}")
            return None
            
        except Exception as e:
            logger.error(f"Error querying DynamoDB: {e}")
            raise
    
    def find_all_processings(
        self,
        document_name: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Find all processings for a document name (up to limit).
        
        Args:
            document_name: Full filename
            limit: Maximum number of results (default: 10)
        
        Returns:
            List of processing records, sorted by most recent first
        """
        try:
            logger.info(f"Querying all processings for: {document_name}")
            
            response = self.table.query(
                IndexName='document_name_index2',
                KeyConditionExpression='document_name = :name',
                ExpressionAttributeValues={':name': document_name},
                ScanIndexForward=False,  # Sort descending (newest first)
                Limit=limit
            )
            
            items = response['Items']
            logger.info(f"Found {len(items)} processings")
            return items
            
        except Exception as e:
            logger.error(f"Error querying DynamoDB: {e}")
            raise
    
    def list_all_documents(self) -> List[str]:
        """
        List all unique document names in the table.
        
        Note: This scans the entire table, so it may be slow for large tables.
        Consider caching the results.
        
        Returns:
            Sorted list of unique document names
        """
        try:
            logger.info("Scanning for all document names")
            
            # Scan for unique document names
            response = self.table.scan(
                ProjectionExpression='document_name'
            )
            
            items = response['Items']
            
            # Handle pagination if needed
            while 'LastEvaluatedKey' in response:
                response = self.table.scan(
                    ProjectionExpression='document_name',
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
                items.extend(response['Items'])
            
            # Deduplicate and sort
            names = sorted(set(item['document_name'] for item in items))
            logger.info(f"Found {len(names)} unique documents")
            return names
            
        except Exception as e:
            logger.error(f"Error scanning DynamoDB: {e}")
            raise
    
    def get_document_info(
        self,
        document_id: str,
        document_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get specific document processing info by document_id and document_name.
        
        Args:
            document_id: Document ID (partition key)
            document_name: Document name (sort key)
        
        Returns:
            Document info dict or None if not found
        """
        try:
            response = self.table.get_item(
                Key={
                    'document_id': document_id,
                    'document_name': document_name
                }
            )
            
            return response.get('Item')
            
        except Exception as e:
            logger.error(f"Error getting item from DynamoDB: {e}")
            raise
    
    def get_processing_metadata(
        self,
        document_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get processing metadata for the latest version of a document.
        
        Returns useful information like:
        - document_id
        - Processing timestamps
        - Textract confidence
        - Number of pages
        - S3 locations
        
        Args:
            document_name: Full filename
        
        Returns:
            Metadata dict or None if not found
        """
        doc_info = self.find_latest_processing(document_name)
        
        if not doc_info:
            return None
        
        # Extract useful metadata
        metadata = {
            'document_id': doc_info.get('document_id'),
            'document_name': doc_info.get('document_name'),
            'textract_s3_key': f"{doc_info.get('document_id')}/textract_output_blocks.json",
            'page_images_prefix': f"{doc_info.get('document_id')}/pages/",
            'processed_pdf_key': f"{doc_info.get('document_id')}/{doc_info.get('document_name')}",
        }
        
        # Add any timestamp fields
        for key, value in doc_info.items():
            if 'datetime' in key.lower() or 'timestamp' in key.lower():
                metadata[key] = value
        
        return metadata
