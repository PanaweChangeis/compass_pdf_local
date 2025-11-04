"""
Textract Throttle Utility

Simple quota management for AWS Textract API calls to prevent exceeding service limits.
"""

import time
import random
import os
import boto3
import logging
from typing import Dict, Any, Callable, Optional
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class TextractThrottleConfig:
    """Configuration for Textract API throttling"""
    
    def __init__(self):
        # TPS limits with optimized safe margins (closer to actual limits)
        self.START_TPS_LIMIT = float(os.getenv('TEXTRACT_START_TPS_LIMIT', '1.8'))  # Optimized: 90% of 2 TPS
        self.GET_TPS_LIMIT = float(os.getenv('TEXTRACT_GET_TPS_LIMIT', '4.5'))     # Optimized: 90% of 5 TPS  
        self.DETECT_TPS_LIMIT = float(os.getenv('TEXTRACT_DETECT_TPS_LIMIT', '0.9'))  # Optimized: 90% of 1 TPS
        
        # Concurrent job limits
        self.MAX_CONCURRENT_JOBS = int(os.getenv('TEXTRACT_MAX_CONCURRENT_JOBS', '90'))  # Conservative: 90% of 100
        
        # Retry configuration
        self.MAX_RETRIES = int(os.getenv('TEXTRACT_MAX_RETRIES', '5'))
        self.BASE_DELAY = float(os.getenv('TEXTRACT_BASE_DELAY', '1.0'))
        self.MAX_DELAY = float(os.getenv('TEXTRACT_MAX_DELAY', '60.0'))
        
        # DynamoDB table for job tracking
        self.DDB_DOCUMENTS_TABLE = os.getenv('DDB_DOCUMENTS_TABLE')


class TextractThrottler:
    """Simple throttling mechanism for Textract API calls"""
    
    def __init__(self, config: Optional[TextractThrottleConfig] = None):
        self.config = config or TextractThrottleConfig()
        self._last_call_times = {}
        
    def _wait_for_rate_limit(self, operation_type: str, tps_limit: float):
        """Wait to respect TPS limits"""
        current_time = time.time()
        last_call_time = self._last_call_times.get(operation_type, 0)
        
        # Calculate minimum time between calls
        min_interval = 1.0 / tps_limit
        time_since_last_call = current_time - last_call_time
        
        if time_since_last_call < min_interval:
            sleep_time = min_interval - time_since_last_call
            # Add small jitter to avoid thundering herd
            sleep_time += random.uniform(0, 0.1)
            logger.info(f"Throttling {operation_type}: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        self._last_call_times[operation_type] = time.time()
    
    def _exponential_backoff_retry(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with exponential backoff retry on throttling errors"""
        delay = self.config.BASE_DELAY
        
        for attempt in range(self.config.MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
                
            except ClientError as e:
                error_code = e.response['Error']['Code']
                
                if error_code in ['ThrottlingException', 'LimitExceededException', 'TooManyRequestsException']:
                    if attempt < self.config.MAX_RETRIES:
                        # Add jitter to prevent thundering herd
                        jitter = random.uniform(0.5, 1.5)
                        sleep_time = min(delay * jitter, self.config.MAX_DELAY)
                        
                        logger.warning(f"Throttling error on attempt {attempt + 1}, retrying in {sleep_time:.2f}s: {error_code}")
                        time.sleep(sleep_time)
                        delay *= 2  # Exponential backoff
                        continue
                    else:
                        logger.error(f"Max retries exceeded for throttling error: {error_code}")
                        raise
                else:
                    # Non-throttling error, don't retry
                    raise
            except Exception as e:
                # Non-ClientError exceptions, don't retry
                raise
        
        # Should never reach here
        raise Exception(f"Unexpected end of retry loop")
    
    def throttled_start_document_text_detection(self, textract_client: boto3.client, **kwargs) -> Dict[str, Any]:
        """Throttled version of start_document_text_detection"""
        self._wait_for_rate_limit('start_document_text_detection', self.config.START_TPS_LIMIT)

        def _call():
            return textract_client.start_document_text_detection(**kwargs)

        return self._exponential_backoff_retry(_call)

    def throttled_start_document_analysis(self, textract_client: boto3.client, **kwargs) -> Dict[str, Any]:
        """Throttled version of start_document_analysis"""
        self._wait_for_rate_limit('start_document_analysis', self.config.START_TPS_LIMIT)

        def _call():
            return textract_client.start_document_analysis(**kwargs)

        return self._exponential_backoff_retry(_call)

    def throttled_get_document_analysis(self, textract_client: boto3.client, **kwargs) -> Dict[str, Any]:
        """Throttled version of get_document_analysis"""
        self._wait_for_rate_limit('get_document_analysis', self.config.GET_TPS_LIMIT)

        def _call():
            return textract_client.get_document_analysis(**kwargs)

        return self._exponential_backoff_retry(_call)
    
    def throttled_get_document_text_detection(self, textract_client: boto3.client, **kwargs) -> Dict[str, Any]:
        """Throttled version of get_document_text_detection"""
        self._wait_for_rate_limit('get_document_text_detection', self.config.GET_TPS_LIMIT)
        
        def _call():
            return textract_client.get_document_text_detection(**kwargs)
        
        return self._exponential_backoff_retry(_call)
    
    def throttled_detect_document_text(self, textract_client: boto3.client, **kwargs) -> Dict[str, Any]:
        """Throttled version of detect_document_text"""
        self._wait_for_rate_limit('detect_document_text', self.config.DETECT_TPS_LIMIT)
        
        def _call():
            return textract_client.detect_document_text(**kwargs)
        
        return self._exponential_backoff_retry(_call)


class ConcurrentJobTracker:
    """Track concurrent Textract jobs using CloudWatch metrics to avoid exceeding limits"""
    
    def __init__(self, config: Optional[TextractThrottleConfig] = None):
        self.config = config or TextractThrottleConfig()
        self.textract_client = boto3.client('textract')
        self.cloudwatch_client = boto3.client('cloudwatch')
        # Cache for metrics to avoid excessive CloudWatch calls
        self._metrics_cache = {}
        self._cache_ttl = 60  # Cache metrics for 60 seconds
        
    def _get_cached_metric(self, cache_key: str) -> Optional[float]:
        """Get cached metric value if still valid"""
        if cache_key in self._metrics_cache:
            timestamp, value = self._metrics_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                return value
        return None
    
    def _cache_metric(self, cache_key: str, value: float):
        """Cache metric value with timestamp"""
        self._metrics_cache[cache_key] = (time.time(), value)
    
    def get_current_concurrent_jobs(self) -> int:
        """Get current number of concurrent Textract jobs from CloudWatch metrics"""
        try:
            cache_key = "concurrent_jobs"
            cached_value = self._get_cached_metric(cache_key)
            if cached_value is not None:
                logger.debug(f"Using cached concurrent jobs count: {cached_value}")
                return int(cached_value)
            
            # Calculate concurrent jobs by looking at job start/completion metrics
            # We'll query the last 5 minutes and calculate the difference
            end_time = time.time()
            start_time = end_time - 300  # 5 minutes ago
            
            # Get metrics for jobs started (both text detection and analysis)
            started_text_response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/Textract',
                MetricName='UserRequestCount',
                Dimensions=[
                    {
                        'Name': 'Operation',
                        'Value': 'StartDocumentTextDetection'
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,  # 5 minute period
                Statistics=['Sum']
            )

            started_analysis_response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/Textract',
                MetricName='UserRequestCount',
                Dimensions=[
                    {
                        'Name': 'Operation',
                        'Value': 'StartDocumentAnalysis'
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=['Sum']
            )

            # Get metrics for jobs completed (both text detection and analysis)
            completed_text_response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/Textract',
                MetricName='UserRequestCount',
                Dimensions=[
                    {
                        'Name': 'Operation',
                        'Value': 'GetDocumentTextDetection'
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=['Sum']
            )

            completed_analysis_response = self.cloudwatch_client.get_metric_statistics(
                Namespace='AWS/Textract',
                MetricName='UserRequestCount',
                Dimensions=[
                    {
                        'Name': 'Operation',
                        'Value': 'GetDocumentAnalysis'
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=['Sum']
            )

            # Calculate approximate concurrent jobs (sum across both text and analysis)
            jobs_started = 0
            if started_text_response['Datapoints']:
                jobs_started += sum(dp['Sum'] for dp in started_text_response['Datapoints'])
            if started_analysis_response['Datapoints']:
                jobs_started += sum(dp['Sum'] for dp in started_analysis_response['Datapoints'])

            jobs_polled = 0
            if completed_text_response['Datapoints']:
                jobs_polled += sum(dp['Sum'] for dp in completed_text_response['Datapoints'])
            if completed_analysis_response['Datapoints']:
                jobs_polled += sum(dp['Sum'] for dp in completed_analysis_response['Datapoints'])
            
            # Very rough approximation: assume jobs take ~30 seconds average
            # and GetDocument is called ~10 times per job on average
            estimated_completed = jobs_polled / 10 if jobs_polled > 0 else 0
            estimated_concurrent = max(0, jobs_started - estimated_completed)
            
            # Apply conservative factor since this is an approximation
            estimated_concurrent = min(estimated_concurrent * 1.5, jobs_started)
            
            self._cache_metric(cache_key, estimated_concurrent)
            logger.info(f"Estimated concurrent Textract jobs: {estimated_concurrent} (started: {jobs_started}, polled: {jobs_polled})")
            
            return int(estimated_concurrent)
            
        except Exception as e:
            logger.warning(f"Error querying CloudWatch metrics for concurrent jobs: {e}")
            # Fallback: use conservative time-based approach
            return self._fallback_job_estimate()
    
    def _fallback_job_estimate(self) -> int:
        """Fallback method when CloudWatch metrics are unavailable"""
        # Conservative fallback: assume some baseline concurrent jobs
        # This prevents false positives when metrics are unavailable
        fallback_estimate = min(10, self.config.MAX_CONCURRENT_JOBS // 4)
        logger.info(f"Using fallback concurrent job estimate: {fallback_estimate}")
        return fallback_estimate
    
    def can_start_new_job(self) -> bool:
        """Check if we can start a new Textract job without exceeding concurrent limits"""
        try:
            current_jobs = self.get_current_concurrent_jobs()
            can_start = current_jobs < self.config.MAX_CONCURRENT_JOBS
            
            if can_start:
                logger.info(f"Job submission allowed: {current_jobs}/{self.config.MAX_CONCURRENT_JOBS} concurrent jobs")
            else:
                logger.info(f"Job submission blocked: {current_jobs}/{self.config.MAX_CONCURRENT_JOBS} concurrent jobs (at limit)")
            
            return can_start
            
        except Exception as e:
            logger.warning(f"Error in job throttling logic, allowing job: {e}")
            # If we can't check, allow the job to proceed (fail open)
            return True
    
    def wait_for_job_slot(self, max_wait_time: int = 300, check_interval: int = 30) -> bool:
        """Wait for an available job slot, returns True if slot available, False if timeout"""
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            if self.can_start_new_job():
                return True
            
            logger.info(f"No job slots available, waiting {check_interval}s...")
            time.sleep(check_interval)
        
        logger.warning(f"Timeout waiting for job slot after {max_wait_time}s")
        return False


# Convenience functions for easy integration
def create_throttled_textract_client() -> tuple[boto3.client, TextractThrottler]:
    """Create a Textract client with throttling wrapper"""
    client = boto3.client('textract')
    throttler = TextractThrottler()
    return client, throttler


def get_job_tracker() -> ConcurrentJobTracker:
    """Get a concurrent job tracker instance"""
    return ConcurrentJobTracker()
