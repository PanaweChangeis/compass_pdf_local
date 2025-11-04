"""
X-Ray SDK utilities for document processing pipeline tracing.

This module provides utilities for:
- Setting up X-Ray tracing in Lambda functions
- Propagating correlation IDs through the processing pipeline
- Adding custom annotations and metadata to traces
- Instrumenting AWS service calls
"""

from .tracer import XRayTracer, setup_xray_tracing, capture_lambda_handler, capture_method
from .correlation import (
    CorrelationContext, 
    propagate_correlation_id, 
    setup_correlation_context,
    extract_correlation_from_event,
    create_message_attributes
)
from .annotations import (
    add_document_annotations, 
    add_processing_metadata,
    add_textract_annotations,
    add_s3_annotations,
    add_dynamodb_annotations,
    add_performance_annotations,
    add_error_annotations
)

__all__ = [
    'XRayTracer',
    'setup_xray_tracing',
    'capture_lambda_handler',
    'capture_method',
    'CorrelationContext',
    'propagate_correlation_id',
    'setup_correlation_context',
    'extract_correlation_from_event',
    'create_message_attributes',
    'add_document_annotations',
    'add_processing_metadata',
    'add_textract_annotations',
    'add_s3_annotations',
    'add_dynamodb_annotations',
    'add_performance_annotations',
    'add_error_annotations'
]
