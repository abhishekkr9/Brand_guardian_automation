"""
Main Execution Entry Point for Brand Guardian AI (GCP Edition).

This file is the "control center" that starts and manages the entire 
compliance audit workflow. Think of it as the master switch that:
1. Sets up the audit request
2. Runs the AI workflow (Vertex AI Video & Gemini)
3. Displays the final compliance report
"""

# Standard library imports
import uuid      # Generates unique IDs
import json      # Handles JSON formatting
import logging   # Records execution logs
import warnings  # For suppressing noise
from pprint import pprint

# Suppress Pydantic V1 / LangChain deprecation warnings
warnings.filterwarnings("ignore", category=UserWarning, module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Load environment variables
from dotenv import load_dotenv
load_dotenv(override=True)

# Import the main workflow graph
# This "app" object now contains your GCP-converted nodes
from backend.src.graph.workflow import app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("brand-guardian-runner")


def run_cli_simulation():
    """
    Simulates a Video Compliance Audit request.
    
    Orchestration:
    - Generates Session ID
    - Downloads YouTube Video -> Uploads to Google Cloud Storage
    - Analyzes via Vertex AI Video Intelligence
    - Audits via Vertex AI Search (RAG) + Gemini
    """
    
    # ========== STEP 1: GENERATE SESSION ID ==========
    session_id = str(uuid.uuid4())
    logger.info(f"--- [Main] Starting GCP Audit Session: {session_id} ---")

    # ========== STEP 2: DEFINE INITIAL STATE ==========
    # "Intake form" for the workflow
    initial_inputs = {
        # The YouTube video to audit (Replace with any valid URL)
        "video_url": "https://youtu.be/dT7S75eYhcQ",
        
        # Unique Video ID for GCS filename (e.g. "vid_ce6c43bb")
        "video_id": f"vid_{session_id[:8]}",
        
        # Output placeholders
        "compliance_results": [],
        "errors": []
    }

    # ========== DISPLAY SECTION: INPUT SUMMARY ==========
    print("\n--- 1. INITIALIZING WORKFLOW (GCP BACKEND) ---")
    print(f"Input Payload: {json.dumps(initial_inputs, indent=2)}")

    # ========== STEP 3: EXECUTE GRAPH ==========
    try:
        # app.invoke() triggers the LangGraph workflow
        # Flow: START -> Indexer (Vertex Video) -> Auditor (Gemini) -> END
        final_state = app.invoke(initial_inputs)
        
        print("\n--- 2. WORKFLOW EXECUTION COMPLETE ---")
        
        # ========== STEP 4: OUTPUT RESULTS ==========
        print("\n=== COMPLIANCE AUDIT REPORT ===")
        
        print(f"Video ID:    {final_state.get('video_id')}")
        print(f"Status:      {final_state.get('final_status')}")
        
        # ========== VIOLATIONS SECTION ==========
        print("\n[ VIOLATIONS DETECTED ]")
        
        results = final_state.get('compliance_results', [])
        
        if results:
            for issue in results:
                # Format: [SEVERITY] Category: Description
                print(f"- [{issue.get('severity')}] {issue.get('category')}: {issue.get('description')}")
        else:
            print("No violations found. Video is compliant.")

        # ========== SUMMARY SECTION ==========
        print("\n[ FINAL SUMMARY ]")
        print(final_state.get('final_report'))

        # Optional: Print where the raw files are stored in GCP
        if final_state.get('gcs_uri'):
            print(f"\n[ ARTIFACTS ]")
            print(f"GCS Storage: {final_state.get('gcs_uri')}")

    except Exception as e:
        logger.error(f"Workflow Execution Failed: {str(e)}")
        # raise e # Don't raise, just exit gracefully after logging


if __name__ == "__main__":
    run_cli_simulation()