import os
import boto3
import json
import re
import uvicorn
import logging
import tempfile
from processor import process_pdf
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
import time

# ---------------------------
# Configuration / Environment
# ---------------------------
load_dotenv()

# Environment
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ENV_NAME = os.getenv("Env", "dev")

# S3 & Lambda config
S3_BUCKET = os.getenv("BUCKET_NAME", "dev-opendentalintegration-eob-upload")

# AWS clients
s3 = boto3.client("s3", region_name=AWS_REGION)

# ---------------- Logging ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("EOBPipeline")


def process_pdf_from_s3(input_s3_path: str, output_s3_path: str) -> dict:
    """
    Processes a PDF file stored in an S3 bucket, applies custom processing, and uploads the results as a JSON file to another S3 location.
    Args:
        input_s3_path (str): The S3 URI of the input PDF file (e.g., "s3://bucket/key").
        output_s3_path (str): The S3 URI where the output JSON file will be saved.
        config (dict): Configuration dictionary for PDF processing.
    Returns:
        dict: The processed payload extracted from the PDF.
    Raises:
        ValueError: If the input or output S3 path is invalid.
        RuntimeError: If any error occurs during the processing pipeline.
    """
    try:
        in_match = re.match(r"s3://([^/]+)/(.+)", input_s3_path)
        out_match = re.match(r"s3://([^/]+)/(.+)", output_s3_path)

        if not in_match:
            raise ValueError(f"Invalid S3 input path: {input_s3_path}")
        if not out_match:
            raise ValueError(f"Invalid S3 output path: {output_s3_path}")

        in_bucket, in_key = in_match.groups()
        out_bucket, out_key = out_match.groups()

        with tempfile.TemporaryDirectory(prefix="eob_proc_") as tmp_dir:
            start_time = time.time()
            local_pdf = os.path.join(tmp_dir, "input.pdf")
            logger.info(f"Downloading PDF from {input_s3_path} to {local_pdf}...")
            s3.download_file(in_bucket, in_key, local_pdf)

            final_payload = process_pdf(in_key, os.path.basename(in_key))
           
            local_json = os.path.join(tmp_dir, "output.json")
            with open(local_json, "w", encoding="utf-8") as f:
                json.dump(final_payload, f, indent=4, ensure_ascii=False)

            logger.info(f"Uploading results to {output_s3_path}...")
            s3.upload_file(local_json, out_bucket, out_key)

            end_time = time.time()
            elapsed = end_time - start_time
            logger.info(f"✅ Processed {input_s3_path} → {output_s3_path} in {elapsed:.2f} seconds")
            return final_payload

    except Exception as e:
        logger.error(f"Pipeline failed for {input_s3_path}: {e}")
        raise RuntimeError(f"Error processing PDF from S3: {e}")


# ==================== FASTAPI ENDPOINTS ====================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

LAMBDA_FUNC_NAME = os.getenv("POSTPROCESS_LAMBDA", f"{ENV_NAME}-OpenDental-EOBPostProcessFunction")
lambda_client = boto3.client("lambda", region_name=AWS_REGION)


class ProcessRequest(BaseModel):
    eobId: str
    uploadedDataPath: str
    processedDataPath: str
    providerOverride: Optional[str] = None


def run_pipeline_from_s3_and_return_result(eob_id: str, uploaded_path: str, processed_path: str) -> dict:
    """
    Central pipeline: download PDF bytes from S3 -> extract text/tables -> images -> call Bedrock -> upload JSON to S3.
    Returns the payload dict matching the desired format.
    """
    response = {
        "eventPayload": {
            "eobId": eob_id,
            "processedDataPath": processed_path,
            "processingStatus": "FAILED",
            "message": ""
        },
        "eobParsed": {}
    }
    try:
        logger.info(f"Running pipeline for EOB {eob_id}")
       
        uploaded_path_full = f"s3://{S3_BUCKET}/{uploaded_path}"
        processed_path_full = f"s3://{S3_BUCKET}/{processed_path}/eob-parsed.json"

        payload = process_pdf_from_s3(uploaded_path_full, processed_path_full)
        response["eventPayload"]["warningCodes"] = payload["WarningCodes"]

        if not payload["Records"] or payload["Error"]:
            response["eventPayload"]["processingStatus"] = "FAILED"
            response["eventPayload"]["message"] = payload["Error"]
            response["eobParsed"] = payload
        else:
            response["eventPayload"]["processingStatus"] = "SUCCESS"
            response["eventPayload"]["message"] = ""
            response["eobParsed"] = payload
    except Exception as e:
        logger.error(f"Pipeline failed for {eob_id}: {e}")
        response["eventPayload"]["processingStatus"] = "FAILED"
        response["eventPayload"]["message"] = str(e)
    return response


def background_worker_and_invoke_lambda(request: ProcessRequest):
    try:
        response = run_pipeline_from_s3_and_return_result(
            request.eobId,
            request.uploadedDataPath,
            request.processedDataPath,
        )
        payload = response["eventPayload"]
        try:
            lambda_client.invoke(
                FunctionName=LAMBDA_FUNC_NAME,
                InvocationType="Event",
                Payload=json.dumps(payload).encode("utf-8"),
            )
            logger.info(f"Lambda {LAMBDA_FUNC_NAME} invoked for EOB {request.eobId}")
        except Exception as e:
            logger.error(f"Lambda invocation failed for {request.eobId}: {e}")
    except Exception as e:
        logger.error(f"Background worker failed for {request.eobId}: {e}")

@app.get("/")
async def root():
    return {"message": "AI Processor Service (original pipeline wrapped) is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/eob")
async def eob_nonblocking(request: ProcessRequest, background_tasks: BackgroundTasks):
    """
    Non-blocking: immediately returns; processing happens in background.
    Lambda WILL be invoked at the end of processing with the payload.
    """
    logger.info(f"Received non-blocking /eob request for {request.eobId}")
    try:
        # Add background task (do not wait)
        background_tasks.add_task(background_worker_and_invoke_lambda, request)
        return JSONResponse(status_code=202, content={"status": "processing started"})
    except Exception as e:
        logger.exception("Failed to start background task")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/eob/sync")
async def eob_blocking(request: ProcessRequest):
    """
    Blocking: runs full pipeline synchronously and returns payload (no Lambda invocation).
    """
    logger.info(f"Received blocking /eob/sync request for {request.eobId}")
    try:
        payload = run_pipeline_from_s3_and_return_result(request.eobId, request.uploadedDataPath, request.processedDataPath)
        return JSONResponse(status_code=200, content=payload)
    except Exception as e:
        logger.exception("Synchronous processing failed")
        raise HTTPException(status_code=500, detail=str(e))
