"""
Correlation context management for document processing pipeline.
"""

import logging
import threading
from typing import Optional, Dict, Any
from aws_xray_sdk.core import xray_recorder

logger = logging.getLogger(__name__)

class CorrelationContext:
    """Thread-local context for maintaining correlation across service calls."""
    
    _local = threading.local()
    
    @classmethod
    def set_correlation_id(cls, correlation_id: str) -> None:
        """Set the correlation ID for the current execution context."""
        cls._local.correlation_id = correlation_id
        
        # Add to X-Ray trace
        try:
            xray_recorder.put_annotation('correlation_id', correlation_id)
            xray_recorder.put_annotation('document_id', correlation_id)
        except Exception as e:
            logger.warning(f"Failed to add correlation_id to X-Ray trace: {e}")
    
    @classmethod
    def get_correlation_id(cls) -> Optional[str]:
        """Get the correlation ID from the current execution context."""
        return getattr(cls._local, 'correlation_id', None)
    
    @classmethod
    def set_document_metadata(cls, metadata: Dict[str, Any]) -> None:
        """Set document metadata for the current execution context."""
        cls._local.document_metadata = metadata
        
        # Add to X-Ray trace
        try:
            xray_recorder.put_metadata('document', metadata, 'processing')
        except Exception as e:
            logger.warning(f"Failed to add document metadata to X-Ray trace: {e}")
    
    @classmethod
    def get_document_metadata(cls) -> Dict[str, Any]:
        """Get document metadata from the current execution context."""
        return getattr(cls._local, 'document_metadata', {})
    
    @classmethod
    def set_processing_stage(cls, stage: str) -> None:
        """Set the current processing stage."""
        cls._local.processing_stage = stage
        
        # Add to X-Ray trace
        try:
            xray_recorder.put_annotation('processing_stage', stage)
        except Exception as e:
            logger.warning(f"Failed to add processing_stage to X-Ray trace: {e}")
    
    @classmethod
    def get_processing_stage(cls) -> Optional[str]:
        """Get the current processing stage."""
        return getattr(cls._local, 'processing_stage', None)
    
    @classmethod
    def clear(cls) -> None:
        """Clear all context data."""
        for attr in ['correlation_id', 'document_metadata', 'processing_stage']:
            if hasattr(cls._local, attr):
                delattr(cls._local, attr)
    
    @classmethod
    def get_context_dict(cls) -> Dict[str, Any]:
        """Get all context data as a dictionary."""
        return {
            'correlation_id': cls.get_correlation_id(),
            'document_metadata': cls.get_document_metadata(),
            'processing_stage': cls.get_processing_stage()
        }


def propagate_correlation_id(message_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add correlation ID to message body for propagation across services.
    
    Args:
        message_body: The message body to modify
        
    Returns:
        Modified message body with correlation ID
    """
    correlation_id = CorrelationContext.get_correlation_id()
    if correlation_id:
        message_body['correlation_id'] = correlation_id
        message_body['document_id'] = correlation_id  # Ensure backward compatibility
    
    return message_body


def extract_correlation_from_event(event: Dict[str, Any]) -> Optional[str]:
    """
    Extract correlation ID from various AWS event types.
    
    Args:
        event: AWS Lambda event
        
    Returns:
        Correlation ID if found, None otherwise
    """
    # Direct event
    if 'correlation_id' in event:
        return event['correlation_id']
    
    if 'document_id' in event:
        return event['document_id']
    
    # SQS Records
    if 'Records' in event:
        for record in event['Records']:
            # SQS message body
            if 'body' in record:
                try:
                    import json
                    body = json.loads(record['body'])
                    if 'correlation_id' in body:
                        return body['correlation_id']
                    if 'document_id' in body:
                        return body['document_id']
                except:
                    pass
            
            # SQS message attributes
            if 'messageAttributes' in record:
                attrs = record['messageAttributes']
                for key in ['correlation_id', 'document_id']:
                    if key in attrs and 'stringValue' in attrs[key]:
                        return attrs[key]['stringValue']
            
            # SNS message (when SQS subscribes to SNS)
            if 'Sns' in record:
                sns_message = record['Sns'].get('Message', '')
                try:
                    import json
                    msg_data = json.loads(sns_message)
                    if 'JobTag' in msg_data:  # Textract job tag
                        return msg_data['JobTag']
                    if 'correlation_id' in msg_data:
                        return msg_data['correlation_id']
                    if 'document_id' in msg_data:
                        return msg_data['document_id']
                except:
                    pass
    
    return None


def setup_correlation_context(event: Dict[str, Any], processing_stage: str = None) -> Optional[str]:
    """
    Set up correlation context from Lambda event.
    
    Args:
        event: AWS Lambda event
        processing_stage: Current processing stage name
        
    Returns:
        Correlation ID if found and set
    """
    correlation_id = extract_correlation_from_event(event)
    
    if correlation_id:
        CorrelationContext.set_correlation_id(correlation_id)
        logger.info(f"Set correlation ID: {correlation_id}")
    else:
        logger.warning("No correlation ID found in event")
    
    if processing_stage:
        CorrelationContext.set_processing_stage(processing_stage)
        logger.info(f"Set processing stage: {processing_stage}")
    
    return correlation_id


def create_message_attributes(additional_attrs: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Create SQS message attributes with correlation context.
    
    Args:
        additional_attrs: Additional attributes to include
        
    Returns:
        Message attributes dictionary
    """
    attributes = {}
    
    correlation_id = CorrelationContext.get_correlation_id()
    if correlation_id:
        attributes['correlation_id'] = {
            'StringValue': correlation_id,
            'DataType': 'String'
        }
        attributes['document_id'] = {  # Backward compatibility
            'StringValue': correlation_id,
            'DataType': 'String'
        }
    
    processing_stage = CorrelationContext.get_processing_stage()
    if processing_stage:
        attributes['processing_stage'] = {
            'StringValue': processing_stage,
            'DataType': 'String'
        }
    
    if additional_attrs:
        for key, value in additional_attrs.items():
            if isinstance(value, str):
                attributes[key] = {
                    'StringValue': value,
                    'DataType': 'String'
                }
            elif isinstance(value, (int, float)):
                attributes[key] = {
                    'StringValue': str(value),
                    'DataType': 'Number'
                }
    
    return attributes
