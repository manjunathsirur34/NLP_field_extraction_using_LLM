from typing import List
from pathlib import Path
from helper import all_helpers
import logging, os
# ---------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("EOBPipeline")
# ---------------------------------------------------------------------


helper = all_helpers()
# ---------------------------------------------------------------------

def process_pdf(pdf_key, filename):
    '''
    Process a PDF document using the OCR and LLM pipelines.

    args:
        pdf_key: Key of the PDF document in S3.
        filename: Name of the PDF document.

    returns:
        Dict[str, Any]: Records containing the processed data.
    '''
    # -------------------------------------
    try:
        token_count = 0
        jsons: List[str] = []
        context_json = ""
        records = {
            "Records": [],
            "TotalTokens": 0,
            "WarningCodes": [],
            "Error": ""
        }
    except Exception as e:  
        logger.error(f"Failed to initialize variables [token_count, jsons, context_json, records]: {e}")
    # -------------------------------------
       
    try:
        presigned_url = helper.presign_s3url(pdf_key)
    except Exception as e:
        logger.error(f"Failed to presign S3 URL: {e}")
        records["Error"] = f"Failed to presign S3 URL: {e}"
        return records
    # -------------------------------------
    
    try:
        payloads = helper.get_text_and_tables_from_url(presigned_url)
    except Exception as e:
        logger.error(f"Failed to get text and tables from URL: {e}")
        records["Error"] = f"Failed to get text and tables from URL: {e}"
        return records
    # -------------------------------------
    
    if payloads is None:
        logger.error(f"Generated payload out of get_text_and_tables_from_url is None")
        records["Error"] = f"Generated payload out of get_text_and_tables_from_url is None"
        return records
    # -------------------------------------

    for page_number, text in enumerate(payloads, start=1):
        token_count += helper.count_tokens(text)
        try:
            logger.info(f"Processing page via llm_processing for page: {page_number}")
            output = helper.llm_processing(context_json, text)
        except Exception as e:
            logger.error(f"Failed to process page via llm_processing for page: {page_number}: {e}")
            records["Error"] = f"Failed to process page via llm_processing for page: {page_number}: {e}"
            return records
        if output is None:
            continue
        jsons.append(output)
        context_json = output
    # -------------------------------------

    try:
        combined_json = helper.combine_records(jsons)
    except Exception as e:
        logger.error(f"Failed to combine records: {e}")
        records["Error"] = f"Failed to combine records: {e}"
        return records
    records["Records"] = combined_json.get("Records", [])
    records['TotalTokens'] = token_count + 4000

    return records
    
# ---------------------------------------------------------------------
# Testing
# url = "eobs/testing_EOB_series/Mutual of Omaha1.pdf"
# result = process_pdf(url, "Mutual of Omaha1.pdf")
# print(result)

# ---------------------------------------------------------------------