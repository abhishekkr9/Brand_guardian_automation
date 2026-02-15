import os
import glob
import logging
from google.cloud import storage
from dotenv import load_dotenv

load_dotenv()

# Configure Logger
logger = logging.getLogger("uploader")

# Configuration
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")  # e.g. "brand-guidelines-test"
SOURCE_FOLDER = os.path.join(os.path.dirname(__file__), "../../data")

def upload_pdfs():
    """
    Simply uploads all PDFs from local /data folder to the GCS Bucket.
    Vertex AI Agent Builder will automatically pick them up from there.
    """
    if not BUCKET_NAME:
        logger.error("Error: GCS_BUCKET_NAME is missing in .env")
        return

    # Initialize Client
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)

    # Find PDFs
    pdf_files = glob.glob(os.path.join(SOURCE_FOLDER, "*.pdf"))
    
    if not pdf_files:
        logger.warning(f"--- [Uploader] No PDFs found in {SOURCE_FOLDER} ---")
        return

    logger.info(f"--- [Uploader] Found {len(pdf_files)} PDFs. Uploading to gs://{BUCKET_NAME}... ---")

    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        blob = bucket.blob(filename) # Save at root of bucket

        # Optional: Check if exists to avoid re-uploading
        if blob.exists():
            logger.info(f"--- [Uploader] Skipping {filename} (Already exists) ---")
            continue

        logger.info(f"--- [Uploader] Uploading: {filename}... ---")
        blob.upload_from_filename(pdf_path)

    logger.info("="*40)
    logger.info("--- [Uploader] Upload Complete. ---")
    logger.info("Go to Vertex AI Agent Builder console to verify the sync.")
    logger.info("="*40)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upload_pdfs()