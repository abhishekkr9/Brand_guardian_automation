import json
import os
import logging
import re
import warnings
from typing import Dict, Any, List

# Suppress warnings
try:
    from langchain_core._api.deprecation import LangChainDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)
except ImportError:
    pass

warnings.filterwarnings("ignore", category=UserWarning, module="langchain_google_community.vertex_ai_search")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="langchain_core._api.deprecation")

# --- GCP IMPORTS ---
from langchain_google_vertexai import ChatVertexAI, VertexAIEmbeddings
from langchain_google_community import VertexAISearchRetriever
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

# Import the State schema
from backend.src.graph.state import VideoAuditState, ComplianceIssue

# Import the Service
from backend.src.services.video_indexer import VideoIntelligenceService

# Configure Logger
logger = logging.getLogger("brand-guardian")

# --- CONFIG ---
VERTEX_AI_LOCATION = os.getenv("VERTEX_AI_LOCATION", "asia-southeast1")
VERTEX_SEARCH_LOCATION = os.getenv("VERTEX_SEARCH_LOCATION", "global")

# --- NODE 1: THE INDEXER (GCP) ---
def index_video_node(state: VideoAuditState) -> Dict[str, Any]:
    """
    Downloads YouTube video, uploads to GCS, and extracts insights using Vertex AI.
    """
    video_url = state.get("video_url")
    video_id_input = state.get("video_id", "vid_demo")
    
    logger.info(f"--- [Indexer] Processing: {video_url} ---")
    
    local_filename = f"temp_{video_id_input}.mp4"
    
    try:
        # Initialize GCP Service
        vi_service = VideoIntelligenceService()
        
        # 1. DOWNLOAD
        if "youtube.com" in video_url or "youtu.be" in video_url:
            local_path = vi_service.download_youtube_video(video_url, output_path=local_filename)
        else:
            raise Exception("Please provide a valid YouTube URL for this test.")

        # 2. UPLOAD (Returns GCS URI gs://...)
        gcs_uri = vi_service.upload_video(local_path, video_name=video_id_input)
        logger.info(f"--- [Indexer] Upload Success. GCS URI: {gcs_uri} ---")
        
        # 3. CLEANUP
        if os.path.exists(local_path):
            os.remove(local_path)

        # 4. ANNOTATE (Trigger Video Intelligence API)
        raw_insights = vi_service.annotate_video(gcs_uri)
        
        # 5. EXTRACT
        clean_data = vi_service.extract_data(raw_insights)
        
        logger.info("--- [Indexer] Extraction Complete ---")
        return clean_data

    except Exception as e:
        logger.error(f"--- [Indexer] Failed: {e} ---")
        return {
            "errors": [str(e)],
            "final_status": "FAIL",
            "transcript": "", 
            "ocr_text": []
        }

# --- NODE 2: THE COMPLIANCE AUDITOR (GCP) ---
def audit_content_node(state: VideoAuditState) -> Dict[str, Any]:
    """
    Performs Retrieval-Augmented Generation (RAG) to audit the content.
    """
    logger.info("--- [Auditor] Querying Knowledge Base & LLM ---")
    
    transcript = state.get("transcript", "")
    
    if not transcript:
        logger.warning("--- [Auditor] No transcript available. Skipping Audit. ---")
        return {
            "final_status": "FAIL",
            "final_report": "Audit skipped because video processing failed (No Transcript)."
        }

    # --- 1. Initialize Vertex AI LLM (Using standard Vertex AI Client) ---
    llm = ChatVertexAI(
        model_name=os.getenv("VERTEX_AI_MODEL_NAME", "gemini-2.0-flash"),
        location=VERTEX_AI_LOCATION,
        temperature=0.0
    )

    # --- 2. Initialize Vertex AI Search (Retriever) ---
    # Note: Vertex AI Search (Agent Builder) is a Retriever, not just a VectorStore
    data_store_id = os.getenv("VERTEX_SEARCH_DATA_STORE_ID")
    search_loc = VERTEX_SEARCH_LOCATION
    logger.info(f"--- [Auditor] Initializing Retriever (Data Store: {data_store_id}) ---")
    
    retriever = VertexAISearchRetriever(
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location_id=search_loc,
        data_store_id=data_store_id,
        max_documents=3,
        engine_data_type=0, # 0 = Unstructured Data Store (Docs/PDFs) 
        get_extractive_answers=True # This often helps with getting meaningful content
    )
    
    # --- 3. RAG Retrieval ---
    ocr_text = state.get("ocr_text", [])
    query_text = f"{transcript} {' '.join(ocr_text)}"
    
    # Invoke retrieval (Equivalent to similarity_search)
    logger.info(f"--- [Auditor] Searching Knowledge Base... (Query len: {len(query_text)}) ---")
    docs = retriever.invoke(query_text)
    
    # Fallback: If no specific rules found, fetch general guidelines
    if not docs:
        logger.info("--- [Auditor] No specific rules found. Fetching General Guidelines. ---")
        docs = retriever.invoke("General Brand Safety Guidelines Compliance Rules")

    logger.info(f"--- [Auditor] Retrieved {len(docs)} documents. ---")
    retrieved_rules = "\n\n".join([doc.page_content for doc in docs])
    
    # --- 4. COMPLIANCE PROMPT ---
    system_prompt = f"""
    You are a Senior Brand Compliance Auditor.
    
    OFFICIAL REGULATORY RULES:
    {retrieved_rules}
    
    INSTRUCTIONS:
    1. Analyze the Transcript and OCR text below.
    2. Identify ANY violations of the rules.
    3. Return strictly JSON in the following format:
    
    {{
        "compliance_results": [
            {{
                "category": "Claim Validation",
                "severity": "CRITICAL",
                "description": "Explanation of the violation..."
            }}
        ],
        "status": "FAIL", 
        "final_report": "Summary of findings..."
    }}

    If no violations are found, set "status" to "PASS" and "compliance_results" to [].
    """

    user_message = f"""
    VIDEO METADATA: {state.get('video_metadata', {})}
    TRANSCRIPT: {transcript}
    ON-SCREEN TEXT (OCR): {ocr_text}
    """

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ])
        
        # --- Clean Markdown (Gemini often adds ```json) ---
        content = response.content
        if "```" in content:
            # Regex to find JSON inside code blocks
            match = re.search(r"```(?:json)?(.*?)```", content, re.DOTALL)
            if match:
                content = match.group(1)
            
        audit_data = json.loads(content.strip())
        
        return {
            "compliance_results": audit_data.get("compliance_results", []),
            "final_status": audit_data.get("status", "FAIL"),
            "final_report": audit_data.get("final_report", "No report generated.")
        }

    except Exception as e:
        logger.error(f"--- [Auditor] System Error: {str(e)} ---")
        logger.error(f"--- [Auditor] Raw LLM Response: {response.content if 'response' in locals() else 'None'} ---")
        return {
            "errors": [str(e)],
            "final_status": "FAIL"
        }