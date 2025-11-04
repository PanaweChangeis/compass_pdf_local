"""
X-Ray annotations and metadata utilities for document processing pipeline.
"""

import logging
import time
from typing import Dict, Any, Optional
from aws_xray_sdk.core import xray_recorder

logger = logging.getLogger(__name__)

def _safe_put_annotation(key: str, value: Any) -> None:
    """Safely add annotation, using subsegment if current segment is immutable."""
    try:
        current_segment = xray_recorder.current_segment()
        if _is_facade_segment(current_segment):
            # Use subsegment for immutable facade segments
            with xray_recorder.in_subsegment('custom_annotations'):
                xray_recorder.put_annotation(key, value)
        else:
            xray_recorder.put_annotation(key, value)
    except Exception as e:
        logger.warning(f"Failed to add annotation {key}={value}: {e}")

def _safe_put_metadata(key: str, value: Dict[str, Any], namespace: str = 'default') -> None:
    """Safely add metadata, using subsegment if current segment is immutable."""
    try:
        current_segment = xray_recorder.current_segment()
        if _is_facade_segment(current_segment):
            # Use subsegment for immutable facade segments
            with xray_recorder.in_subsegment('custom_metadata'):
                xray_recorder.put_metadata(key, value, namespace)
        else:
            xray_recorder.put_metadata(key, value, namespace)
    except Exception as e:
        logger.warning(f"Failed to add metadata {key}: {e}")

def _is_facade_segment(segment) -> bool:
    """Check if the current segment is an immutable facade segment."""
    import os
    try:
        # Primary check: facade attribute (most reliable)
        if hasattr(segment, 'facade') and segment.facade:
            return True
        
        # Check if this is a Lambda function root segment
        if hasattr(segment, 'name') and segment.name:
            # Lambda facade segments typically have names starting with the function name
            # or contain specific patterns
            name_lower = segment.name.lower()
            if 'lambda_function' in name_lower or name_lower.startswith('aws:lambda'):
                return True
        
        # Check if this is the root segment and we're in Lambda environment
        if hasattr(segment, 'parent_id') and segment.parent_id is None:
            # This is a root segment - in Lambda, root segments are usually facades
            if os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
                return True
        
        # Check for Lambda-specific attributes that indicate facade segment
        if hasattr(segment, 'service') and hasattr(segment, 'origin'):
            service = getattr(segment, 'service', {})
            origin = getattr(segment, 'origin', '')
            if origin == 'AWS::Lambda::Function' or (service and service.get('name') == 'lambda'):
                return True
        
        # Check segment type - facade segments have specific types
        if hasattr(segment, 'type') and segment.type == 'subsegment':
            return False  # Subsegments are not facades
        
        # In Lambda environment, if we can't determine otherwise, 
        # assume root segments are facades for safety
        if os.environ.get('AWS_LAMBDA_FUNCTION_NAME') and not hasattr(segment, 'parent_id'):
            return True
            
        return False
        
    except Exception as e:
        logger.debug(f"Error detecting facade segment: {e}")
        # If we can't determine safely, assume it's a facade to use subsegments
        # This is safer than risking mutation errors
        return True

def add_document_annotations(
    document_id: str,
    document_name: str,
    bucket: str = None,
    key: str = None,
    file_size: int = None,
    processing_stage: str = None
) -> None:
    """
    Add document-related annotations to the current X-Ray segment.
    
    Args:
        document_id: Unique document identifier
        document_name: Human-readable document name
        bucket: S3 bucket name
        key: S3 object key
        file_size: Document file size in bytes
        processing_stage: Current processing stage
    """
    try:
        # Core document identifiers
        _safe_put_annotation('document_id', document_id)
        _safe_put_annotation('document_name', document_name)
        
        if processing_stage:
            _safe_put_annotation('processing_stage', processing_stage)
        
        if bucket:
            _safe_put_annotation('source_bucket', bucket)
        
        if file_size:
            _safe_put_annotation('file_size_bytes', file_size)
            # Categorize file size for easier filtering
            if file_size < 1024 * 1024:  # < 1MB
                size_category = 'small'
            elif file_size < 10 * 1024 * 1024:  # < 10MB
                size_category = 'medium'
            elif file_size < 100 * 1024 * 1024:  # < 100MB
                size_category = 'large'
            else:
                size_category = 'xlarge'
            _safe_put_annotation('file_size_category', size_category)
        
        # Extract file type from document name
        if '.' in document_name:
            file_extension = document_name.split('.')[-1].lower()
            _safe_put_annotation('file_type', file_extension)
        
    except Exception as e:
        logger.warning(f"Failed to add document annotations: {e}")


def add_processing_metadata(
    stage: str,
    start_time: float = None,
    end_time: float = None,
    status: str = 'success',
    error_message: str = None,
    metrics: Dict[str, Any] = None
) -> None:
    """
    Add processing metadata to the current X-Ray segment.
    
    Args:
        stage: Processing stage name
        start_time: Stage start timestamp
        end_time: Stage end timestamp
        status: Processing status (success, error, warning)
        error_message: Error message if status is error
        metrics: Additional metrics dictionary
    """
    try:
        metadata = {
            'stage': stage,
            'status': status,
            'timestamp': time.time()
        }
        
        if start_time and end_time:
            duration = end_time - start_time
            metadata['duration_seconds'] = duration
            _safe_put_annotation('duration', duration)
        
        if error_message:
            metadata['error_message'] = error_message
            _safe_put_annotation('error_occurred', True)
        else:
            _safe_put_annotation('error_occurred', False)
        
        if metrics:
            metadata['metrics'] = metrics
            
            # Add specific metrics as annotations for filtering
            for key, value in metrics.items():
                if isinstance(value, (int, float, str, bool)):
                    try:
                        _safe_put_annotation(f'metric_{key}', value)
                    except:
                        pass  # Skip invalid annotation values
        
        _safe_put_metadata('processing', metadata, 'pipeline')
        
    except Exception as e:
        logger.warning(f"Failed to add processing metadata: {e}")


def add_textract_annotations(
    job_id: str,
    job_status: str = None,
    page_count: int = None,
    block_count: int = None,
    word_count: int = None,
    confidence_score: float = None
) -> None:
    """
    Add Textract-specific annotations to the current X-Ray segment.
    
    Args:
        job_id: Textract job ID
        job_status: Job status (IN_PROGRESS, SUCCEEDED, FAILED)
        page_count: Number of pages processed
        block_count: Total number of blocks detected
        word_count: Number of word blocks detected
        confidence_score: Average confidence score
    """
    try:
        _safe_put_annotation('textract_job_id', job_id)
        
        if job_status:
            _safe_put_annotation('textract_status', job_status)
        
        if page_count:
            _safe_put_annotation('page_count', page_count)
        
        if block_count:
            _safe_put_annotation('block_count', block_count)
        
        if word_count:
            _safe_put_annotation('word_count', word_count)
        
        if confidence_score:
            _safe_put_annotation('confidence_score', confidence_score)
            
            # Categorize confidence for easier filtering
            if confidence_score >= 0.9:
                confidence_category = 'high'
            elif confidence_score >= 0.7:
                confidence_category = 'medium'
            else:
                confidence_category = 'low'
            _safe_put_annotation('confidence_category', confidence_category)
        
        # Add to metadata for detailed view
        textract_metadata = {
            'job_id': job_id,
            'status': job_status,
            'pages': page_count,
            'blocks': block_count,
            'words': word_count,
            'confidence': confidence_score
        }
        
        _safe_put_metadata('textract', textract_metadata, 'aws_service')
        
    except Exception as e:
        logger.warning(f"Failed to add Textract annotations: {e}")


def add_s3_annotations(
    operation: str,
    bucket: str,
    key: str,
    file_size: int = None,
    content_type: str = None,
    success: bool = True
) -> None:
    """
    Add S3 operation annotations to the current X-Ray segment.
    
    Args:
        operation: S3 operation (get_object, put_object, etc.)
        bucket: S3 bucket name
        key: S3 object key
        file_size: Object size in bytes
        content_type: Object content type
        success: Whether operation was successful
    """
    try:
        _safe_put_annotation('s3_operation', operation)
        _safe_put_annotation('s3_bucket', bucket)
        _safe_put_annotation('s3_success', success)
        
        if file_size:
            _safe_put_annotation('s3_object_size', file_size)
        
        # Add to metadata
        s3_metadata = {
            'operation': operation,
            'bucket': bucket,
            'key': key,
            'size': file_size,
            'content_type': content_type,
            'success': success
        }
        
        _safe_put_metadata('s3', s3_metadata, 'aws_service')
        
    except Exception as e:
        logger.warning(f"Failed to add S3 annotations: {e}")


def add_dynamodb_annotations(
    operation: str,
    table_name: str,
    item_count: int = None,
    success: bool = True
) -> None:
    """
    Add DynamoDB operation annotations to the current X-Ray segment.
    
    Args:
        operation: DynamoDB operation (put_item, update_item, etc.)
        table_name: DynamoDB table name
        item_count: Number of items processed
        success: Whether operation was successful
    """
    try:
        _safe_put_annotation('ddb_operation', operation)
        _safe_put_annotation('ddb_table', table_name)
        _safe_put_annotation('ddb_success', success)
        
        if item_count:
            _safe_put_annotation('ddb_item_count', item_count)
        
        # Add to metadata
        ddb_metadata = {
            'operation': operation,
            'table': table_name,
            'item_count': item_count,
            'success': success
        }
        
        _safe_put_metadata('dynamodb', ddb_metadata, 'aws_service')
        
    except Exception as e:
        logger.warning(f"Failed to add DynamoDB annotations: {e}")


def add_performance_annotations(
    memory_used: int = None,
    memory_limit: int = None,
    duration_ms: int = None,
    cold_start: bool = None
) -> None:
    """
    Add performance-related annotations to the current X-Ray segment.
    
    Args:
        memory_used: Memory used in MB
        memory_limit: Memory limit in MB
        duration_ms: Execution duration in milliseconds
        cold_start: Whether this was a cold start
    """
    try:
        if memory_used:
            _safe_put_annotation('memory_used_mb', memory_used)
        
        if memory_limit:
            _safe_put_annotation('memory_limit_mb', memory_limit)
            
            if memory_used:
                memory_utilization = (memory_used / memory_limit) * 100
                _safe_put_annotation('memory_utilization_pct', memory_utilization)
        
        if duration_ms:
            _safe_put_annotation('duration_ms', duration_ms)
        
        if cold_start is not None:
            _safe_put_annotation('cold_start', cold_start)
        
        # Add to metadata
        performance_metadata = {
            'memory_used': memory_used,
            'memory_limit': memory_limit,
            'duration_ms': duration_ms,
            'cold_start': cold_start
        }
        
        _safe_put_metadata('performance', performance_metadata, 'lambda')
        
    except Exception as e:
        logger.warning(f"Failed to add performance annotations: {e}")


def add_error_annotations(
    error_type: str,
    error_message: str,
    stack_trace: str = None,
    retry_count: int = None
) -> None:
    """
    Add error-related annotations to the current X-Ray segment.
    
    Args:
        error_type: Type of error (exception class name)
        error_message: Error message
        stack_trace: Full stack trace
        retry_count: Number of retries attempted
    """
    try:
        _safe_put_annotation('error_type', error_type)
        _safe_put_annotation('has_error', True)
        
        if retry_count:
            _safe_put_annotation('retry_count', retry_count)
        
        # Add to metadata
        error_metadata = {
            'type': error_type,
            'message': error_message,
            'stack_trace': stack_trace,
            'retry_count': retry_count,
            'timestamp': time.time()
        }
        
        _safe_put_metadata('error', error_metadata, 'exception')
        
    except Exception as e:
        logger.warning(f"Failed to add error annotations: {e}")
