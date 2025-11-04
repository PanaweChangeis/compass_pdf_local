"""
X-Ray tracer setup and utilities for Lambda functions.
"""

import os
import logging
from typing import Optional, Dict, Any, Callable
from functools import wraps

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.models import subsegment
from aws_xray_sdk import global_sdk_config

logger = logging.getLogger(__name__)

class XRayTracer:
    """X-Ray tracer utility class for document processing pipeline."""
    
    def __init__(self):
        self._initialized = False
        self._recorder = xray_recorder
    
    def setup(self) -> None:
        """Initialize X-Ray tracing for the Lambda function."""
        if self._initialized:
            return
            
        try:
            # For Lambda, AWS automatically manages X-Ray setup when tracing is enabled
            # We just need to configure basic settings and patch AWS services
            
            # Configure context missing behavior
            self._recorder.configure(
                context_missing='LOG_ERROR'
            )
            
            # Patch AWS services for automatic instrumentation
            from aws_xray_sdk.core import patch_all
            patch_all()
            
            self._initialized = True
            logger.info("X-Ray tracing initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize X-Ray tracing: {e}")
            # Don't raise exception - allow function to continue without tracing
    
    def create_subsegment(self, name: str, namespace: str = 'local') -> subsegment.Subsegment:
        """Create a custom subsegment for detailed tracing."""
        return self._recorder.begin_subsegment(name, namespace=namespace)
    
    def annotate(self, key: str, value: Any) -> None:
        """Add annotation to current segment, using subsegment if needed."""
        try:
            current_segment = self._recorder.current_segment()
            if self._is_facade_segment(current_segment):
                # Create subsegment for annotations when facade segment is immutable
                with self._recorder.in_subsegment('annotations'):
                    self._recorder.put_annotation(key, value)
            else:
                self._recorder.put_annotation(key, value)
        except Exception as e:
            logger.warning(f"Failed to add annotation {key}={value}: {e}")
    
    def add_metadata(self, key: str, value: Dict[str, Any], namespace: str = 'default') -> None:
        """Add metadata to current segment, using subsegment if needed."""
        try:
            current_segment = self._recorder.current_segment()
            if self._is_facade_segment(current_segment):
                # Create subsegment for metadata when facade segment is immutable
                with self._recorder.in_subsegment('metadata'):
                    self._recorder.put_metadata(key, value, namespace)
            else:
                self._recorder.put_metadata(key, value, namespace)
        except Exception as e:
            logger.warning(f"Failed to add metadata {key}: {e}")
    
    def _is_facade_segment(self, segment) -> bool:
        """Check if the current segment is an immutable facade segment."""
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
    
    def capture_lambda_handler(self, func: Callable) -> Callable:
        """Decorator to automatically capture Lambda handler execution."""
        @wraps(func)
        def wrapper(event, context):
            # Setup tracing if not already done
            self.setup()
            
            # Extract correlation ID from event if present
            correlation_id = self._extract_correlation_id(event)
            if correlation_id:
                self.annotate('correlation_id', correlation_id)
                self.annotate('document_id', correlation_id)  # Assuming correlation_id is document_id
            
            # Add function metadata
            self.add_metadata('lambda_context', {
                'function_name': context.function_name,
                'function_version': context.function_version,
                'memory_limit': context.memory_limit_in_mb,
                'remaining_time': context.get_remaining_time_in_millis()
            }, 'lambda')
            
            # Add event metadata (sanitized)
            self.add_metadata('event', self._sanitize_event(event), 'request')
            
            try:
                result = func(event, context)
                self.annotate('status', 'success')
                return result
            except Exception as e:
                self.annotate('status', 'error')
                self.annotate('error_type', type(e).__name__)
                self.add_metadata('error', {
                    'message': str(e),
                    'type': type(e).__name__
                }, 'error')
                raise
        
        return wrapper
    
    def capture_method(self, method_name: str = None):
        """Decorator to capture method execution in a subsegment."""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs):
                segment_name = method_name or f"{func.__module__}.{func.__name__}"
                
                with self.create_subsegment(segment_name):
                    self.annotate('method', func.__name__)
                    try:
                        result = func(*args, **kwargs)
                        self.annotate('method_status', 'success')
                        return result
                    except Exception as e:
                        self.annotate('method_status', 'error')
                        self.annotate('method_error', str(e))
                        raise
            
            return wrapper
        return decorator
    
    def _extract_correlation_id(self, event: Dict[str, Any]) -> Optional[str]:
        """Extract correlation ID from various event types."""
        # SQS event
        if 'Records' in event:
            for record in event['Records']:
                if 'body' in record:
                    import json
                    try:
                        body = json.loads(record['body'])
                        if 'document_id' in body:
                            return body['document_id']
                    except:
                        pass
                
                # Check message attributes
                if 'messageAttributes' in record:
                    attrs = record['messageAttributes']
                    if 'document_id' in attrs:
                        return attrs['document_id'].get('stringValue')
        
        # SNS event
        if 'Records' in event:
            for record in event['Records']:
                if record.get('EventSource') == 'aws:sns' and 'Sns' in record:
                    message = record['Sns'].get('Message', '')
                    try:
                        import json
                        msg_data = json.loads(message)
                        if 'JobTag' in msg_data:
                            return msg_data['JobTag']
                    except:
                        pass
        
        # Direct invocation
        if 'document_id' in event:
            return event['document_id']
        
        return None
    
    def _sanitize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize event data for metadata storage."""
        # Remove sensitive data and limit size
        sanitized = {}
        
        # Copy safe fields
        safe_fields = ['Records', 'source', 'detail-type', 'time', 'region']
        for field in safe_fields:
            if field in event:
                sanitized[field] = event[field]
        
        # Limit Records to prevent large payloads
        if 'Records' in sanitized and len(sanitized['Records']) > 5:
            sanitized['Records'] = sanitized['Records'][:5]
            sanitized['_truncated'] = True
        
        return sanitized


# Global tracer instance
tracer = XRayTracer()

def setup_xray_tracing() -> XRayTracer:
    """Setup X-Ray tracing and return tracer instance."""
    tracer.setup()
    return tracer

def capture_lambda_handler(func: Callable) -> Callable:
    """Decorator to capture Lambda handler with X-Ray tracing."""
    return tracer.capture_lambda_handler(func)

def capture_method(method_name: str = None):
    """Decorator to capture method execution with X-Ray tracing."""
    return tracer.capture_method(method_name)
