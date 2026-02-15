import os
import logging
import google.cloud.logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

# Configure a specific logger for telemetry events
logger = logging.getLogger("brand-guardian-telemetry")

def setup_telemetry():
    """
    Initializes Google Cloud Operations Suite (formerly Stackdriver).
    
    1. Cloud Logging: Connects Python's standard logging to GCP Logs Explorer.
    2. Cloud Trace: Connects OpenTelemetry to GCP Trace for latency tracking.
    """
    
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    
    # ========== PART 1: GOOGLE CLOUD LOGGING ==========
    # This captures all your print() and logger.info() statements
    # and sends them to the GCP Console -> Logs Explorer.
    if os.getenv("CLOUD_LOGGING_ENABLED", "false").lower() == "true":
        try:
            # Instantiates a client
            client = google.cloud.logging.Client()
            
            # Retrieves a Cloud Logging handler based on the environment
            # and integrates it with the standard Python logging module
            client.setup_logging()
            
            logger.info(f"✅ Google Cloud Logging enabled for project: {project_id}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to setup Cloud Logging: {e}")

    # ========== PART 2: GOOGLE CLOUD TRACE ==========
    # This traces request latency (e.g., how long 'audit_video' took)
    if os.getenv("CLOUD_TRACE_ENABLED", "false").lower() == "true":
        try:
            # Set up the Tracer Provider (the factory for tracers)
            tracer_provider = TracerProvider()
            trace.set_tracer_provider(tracer_provider)

            # Create the Exporter that sends traces to Google Cloud
            cloud_trace_exporter = CloudTraceSpanExporter(
                project_id=project_id
            )

            # Add the exporter to the provider (Batch = sends in background to avoid lag)
            tracer_provider.add_span_processor(
                BatchSpanProcessor(cloud_trace_exporter)
            )
            
            logger.info("✅ Google Cloud Trace enabled.")
            
        except Exception as e:
            logger.error(f"⚠️ Failed to setup Cloud Trace: {e}")
            
    if not project_id:
        logger.warning("No GOOGLE_CLOUD_PROJECT found. Telemetry might rely on local defaults.")