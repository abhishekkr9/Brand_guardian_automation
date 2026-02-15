import os
import logging
import re
from google.cloud import videointelligence
from google.cloud import storage
import yt_dlp

# Configure Logger
logger = logging.getLogger("video-indexer")

class VideoIntelligenceService:
    def __init__(self):
        # GCP Configuration
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-southeast1")
        self.bucket_name = os.getenv("GCS_BUCKET_NAME")
        
        # Initialize Clients (Automatic Auth via ADC)
        self.storage_client = storage.Client()
        self.video_client = videointelligence.VideoIntelligenceServiceClient() # Uses default endpoint

    # --- FUNCTION: Download from YouTube (Unchanged) ---
    def download_youtube_video(self, url, output_path="temp_video.mp4"):
        """Downloads a YouTube video to a local file."""
        logger.info(f"--- [Download] Starting for URL: {url} ---")
        
        ydl_opts = {
            # Flexible format selection - try multiple options
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best',
            'outtmpl': output_path,
            'quiet': False,
            'no_warnings': False,
            'merge_output_format': 'mp4',
            # Try multiple player clients for better compatibility
            'extractor_args': {'youtube': {'player_client': ['web', 'android', 'ios']}},
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            logger.info(f"--- [Download] Success: {output_path} ---")
            return output_path
        except Exception as e:
            logger.error(f"--- [Download] Failed: {str(e)} ---")
            raise Exception(f"YouTube Download Failed: {str(e)}")

    # --- FUNCTION: Upload Local File to GCS ---
    def upload_video(self, video_path, video_name):
        """
        Uploads a local file to Google Cloud Storage.
        Returns: The gs:// URI needed by the Video API.
        """
        try:
            logger.info(f"--- [Upload] Starting upload to gs://{self.bucket_name} ---")
            bucket = self.storage_client.bucket(self.bucket_name)
            # Create a path inside the bucket (e.g., videos/my_video.mp4)
            destination_blob_name = f"videos/{video_name}.mp4"
            blob = bucket.blob(destination_blob_name)

            # Upload with timeout settings for large files
            blob.upload_from_filename(video_path, timeout=300)
            
            # Construct URI
            gcs_uri = f"gs://{self.bucket_name}/{destination_blob_name}"
            logger.info(f"--- [Upload] Success: {gcs_uri} ---")
            return gcs_uri

        except Exception as e:
            logger.error(f"--- [Upload] Failed: {str(e)} ---")
            raise Exception(f"GCS Upload Failed: {str(e)}")

    # --- FUNCTION: Annotate (Trigger & Wait) ---
    def annotate_video(self, gcs_uri):
        """
        Triggers Video Intelligence API and waits for completion.'.
        """
        logger.info(f"--- [Analysis] Starting Video Intelligence for: {gcs_uri} ---")

        # 1. Configure Features
        features = [
            videointelligence.Feature.SPEECH_TRANSCRIPTION,
            videointelligence.Feature.TEXT_DETECTION,  # OCR
            videointelligence.Feature.LABEL_DETECTION,
            videointelligence.Feature.SHOT_CHANGE_DETECTION
        ]


        transcript_config = videointelligence.SpeechTranscriptionConfig(
            language_code="en-US",
            enable_automatic_punctuation=True,
            enable_speaker_diarization=True,
            diarization_speaker_count=2,
        )
        video_context = videointelligence.VideoContext(
            speech_transcription_config=transcript_config
        )

        # 3. Start Operation (Async)
        operation = self.video_client.annotate_video(
            request={
                "features": features,
                "input_uri": gcs_uri,
                "video_context": video_context,
            }
        )

        logger.info(f"--- [Analysis] Operation ID: {operation.operation.name} ---")
        logger.info("--- [Analysis] Waiting for processing... ---")

        # 4. Wait for Result (Polling handled internally)
        # Timeout set to 600s (10 mins)
        result = operation.result(timeout=600)
        
        logger.info("--- [Analysis] Processing Complete ---")
        return result

    # --- FUNCTION: Extract Data ---
    def extract_data(self, analysis_result):
        """
        Parses the GCP 'AnnotateVideoResponse' object into our State format.
        """
        logger.info("--- [Extract] Parsing analysis results ---")
        # Get the first result (usually only one video processed per request)
        if not analysis_result.annotation_results:
            logger.warning("--- [Extract] No annotation results found ---")
            return {"transcript": "", "ocr_text": []}
            
        annotations = analysis_result.annotation_results[0]
        
        # 1. Extract Transcript
        transcript_parts = []
        # Speech transcriptions are often broken into segments
        if hasattr(annotations, 'speech_transcriptions') and annotations.speech_transcriptions:
            logger.info(f"--- [Extract] Found {len(annotations.speech_transcriptions)} speech segments ---")
            for speech_transcription in annotations.speech_transcriptions:
                # Each segment has alternatives, the first is the most confident
                if speech_transcription.alternatives:
                    best_alternative = speech_transcription.alternatives[0]
                    transcript_parts.append(best_alternative.transcript)
        else:
             logger.warning("--- [Extract] No speech detected. Video likely has music/no-dialogue. ---")
        
        full_transcript = " ".join(transcript_parts).strip()
        
        # Fallback: Start using OCR as transcript if speech is missing
        if not full_transcript and annotations.text_annotations:
             logger.info("--- [Extract] Falling back to OCR text for transcript ---")
             
             # 1. Join all OCR text blocks
             raw_ocr = " ".join([t.text for t in annotations.text_annotations])
             
             # 2. Clean up non-ASCII 'garbage' text (often misread logos/graphics)
             # This regex keeps alphanumeric chars, whitespace, and basic punctuation
             cleaned_ocr = re.sub(r'[^\x00-\x7F]+', ' ', raw_ocr)
             
             # 3. Collapse multiple spaces
             cleaned_ocr = re.sub(r'\s+', ' ', cleaned_ocr).strip()
             
             full_transcript = cleaned_ocr

        # 2. Extract OCR
        ocr_lines = []
        for text_annotation in annotations.text_annotations:
            ocr_lines.append(text_annotation.text)
        
        if ocr_lines:
             logger.info(f"--- [Extract] Found {len(ocr_lines)} OCR text blocks ---")

        # 3. Extract Metadata (Duration)
        # Duration is often found in segment_label_annotations or shot_label_annotations
        # converting protobuf Duration object to seconds
        duration_sec = 0
        if annotations.segment_label_annotations:
            # Use the last segment's end time as approx duration
            last_segment = annotations.segment_label_annotations[0].segments[-1]
            start = last_segment.segment.start_time_offset.total_seconds()
            end = last_segment.segment.end_time_offset.total_seconds()
            duration_sec = end
        
        logger.info(f"--- [Extract] Video Duration: {duration_sec}s ---")

        return {
            "transcript": full_transcript,
            "ocr_text": ocr_lines,
            "video_metadata": {
                "duration": duration_sec,
                "platform": "youtube",
                "source_uri": f"gs://{self.bucket_name}/..."
            }
        }