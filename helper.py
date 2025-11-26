from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient
import logging, os, boto3, json, tiktoken
from botocore.client import Config
from typing import Dict, List, Tuple, Any
from collections import defaultdict
from openai import AzureOpenAI
from pathlib import Path
from tool_config import tool_config
from dotenv import load_dotenv
load_dotenv()
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --------------------------------
# Loading system prompt

SYSTEM_PROMPT_PATH = Path(__file__).with_name("system_prompt.txt")
try:
    system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
except FileNotFoundError as exc:
    raise RuntimeError(f"Missing system prompt file at {SYSTEM_PROMPT_PATH}") from exc

# --------------------------------
# Setting up AWS region and name, S3 client and bucket name

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ENV_NAME = os.getenv("Env", "dev")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    config=Config(signature_version='s3v4')
)

S3_BUCKET = os.getenv("BUCKET_NAME", "XXXX")

# --------------------------------
# Setting up SSM client and prefix

ssm_client = boto3.client("ssm", region_name=AWS_REGION)
ssm_prefix = f"/XXXX/{ENV_NAME}"


def get_ssm_param(name: str, with_decryption: bool = True) -> str:
    try:
        response = ssm_client.get_parameter(Name=name, WithDecryption=with_decryption)
        value = response["Parameter"]["Value"]
        logger.info("Retrieved SSM parameter with ARN: %s", response["Parameter"]["ARN"])
        return value
    except ssm_client.exceptions.ParameterNotFound as exc:
        raise RuntimeError(f"SSM parameter with name {name} not found.") from exc

# --------------------------------
# Setting up OCR and LLM endpoints and keys

ocr_endpoint = get_ssm_param(f"{ssm_prefix}/ocr_endpoint")
ocr_key = get_ssm_param(f"{ssm_prefix}/ocr_subscription_key")

# --------------------------------
# Setting up LLM endpoint and key

llm_endpoint = get_ssm_param(f"{ssm_prefix}/llm_endpoint")
llm_subscription_key = get_ssm_param(f"{ssm_prefix}/llm_subscription_key")

# --------------------------------
# Setting up Document Analysis client and LLM client
document_analysis_client = DocumentAnalysisClient( endpoint=ocr_endpoint, credential=AzureKeyCredential(ocr_key))

api_version = "2024-12-01-preview"
deployment = "gpt-5-mini"
model_name = "gpt-5-mini"

client = AzureOpenAI(
    api_version=api_version,
    azure_endpoint=llm_endpoint,
    api_key=llm_subscription_key)

# --------------------------------
# Setting up logging

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("EOBPipeline")

# --------------------------------

class all_helpers():
    def count_tokens(self, text: str, model: str = "gpt-4o-mini") -> int:
        '''
        Count the number of tokens in a given text using the specified model.

        args:
            text: Text to count tokens for.
            model: Model to use for token counting.

        returns:
            int: Number of tokens in the text.
        '''
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    
    def presign_s3url(self, url):
        '''
        Generate a presigned URL for an S3 object.

        args:
            url: URL of the S3 object.

        returns:
            str: Presigned URL for the S3 object.
        '''
        url = s3.generate_presigned_url("get_object",
        Params={
        "Bucket": S3_BUCKET,
        "Key": url},
        ExpiresIn=300)
        return url
        
    @staticmethod
    def ocr_processing(url):
        '''
        Process an image using Azure Document Intelligence.

        args:
            url: URL of the image to process.

        returns:
            DocumentAnalysisResult: Result of the OCR processing.
        '''
        formUrl = url
        logger.info(f"OCR-Processing S3 URL: {formUrl}")
        poller = document_analysis_client.begin_analyze_document_from_url("prebuilt-receipt", formUrl)
        result = poller.result()
        return result
        
        
    @staticmethod 
    def _format_table(table, index: int) -> str:
        '''
        Create a printable string representation for an Azure Document Intelligence table.

        args:
            table: Azure Document Intelligence table.
            index: Index of the table.

        returns:
            str: Printable string representation of the table.
        '''
        cell_map = {
            (cell.row_index, cell.column_index): (cell.content or "").replace("\n", " ").strip()
            for cell in table.cells
        }
        rows: List[str] = []
        for row_idx in range(table.row_count):
            row_values: List[str] = []
            for col_idx in range(table.column_count):
                row_values.append(cell_map.get((row_idx, col_idx), ""))
            rows.append("|".join(row_values).strip("|"))
        header = f"Table {index} (rows={table.row_count}, cols={table.column_count})"
        return "\n".join([header, *rows]).strip()
        
        
        
    def get_text_and_tables_from_url(self, file_url: str):
        '''
        Use the OCR pipeline to fetch text + tables for the given document URL and return them per page.

        args:
            file_url: URL of the document to process.

        returns:
            Dict[int, str]: Text and tables for each page.
        '''
        result = self.ocr_processing(file_url)
        if result is None:
            return []

        pages = getattr(result, "pages", []) or []
        page_text: Dict[int, str] = {}
        for fallback_idx, page in enumerate(pages, start=1):
            words = getattr(page, "words", []) or []
            text = " ".join(word.content for word in words if getattr(word, "content", None)).strip()
            page_number = getattr(page, "page_number", fallback_idx)
            page_text[page_number] = text

        table_result = result
        if not getattr(table_result, "tables", []):
            try:
                poller = document_analysis_client.begin_analyze_document_from_url(
                    "prebuilt-layout", file_url
                )
                table_result = poller.result()
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[WARN] Failed to run layout analysis for tables: {exc}")

        tables = getattr(table_result, "tables", []) or []
        tables_by_page: Dict[int, List[Tuple[int, str]]] = defaultdict(list)
        for idx, table in enumerate(tables, start=1):
            page_number = 1
            if getattr(table, "bounding_regions", None):
                page_number = table.bounding_regions[0].page_number
            tables_by_page[page_number].append((idx, self._format_table(table, idx)))

        page_numbers = sorted(set(page_text.keys()) | set(tables_by_page.keys())) or [1]
        payloads: List[str] = []
        for page_number in page_numbers:
            segments: List[str] = [f"PAGE_NUMBER: {page_number}"]
            text_block = page_text.get(page_number, "").strip()
            if text_block:
                segments.append("TEXT:\n" + text_block)

            table_entries = tables_by_page.get(page_number, [])
            if table_entries:
                table_blob = "\n\n".join(table_repr for _, table_repr in table_entries)
                segments.append("TABLES:\n" + table_blob)

            payload = "\n\n".join(segments).strip()
            if payload:
                payloads.append(payload)
        return payloads
    
    
    def llm_processing(self, json_output, current_page_text):
        '''
        Process a document using Azure Document Intelligence.

        args:
            json_output: JSON output from OCR processing.
            current_page_text: Text and tables for current page.

        returns:
            Dict[str, Any]: Combined records.
        '''
        message=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"The json for reference is: \n{json_output}\n\n The text and tables for current page is: \n{current_page_text}"}]
        
        response = client.chat.completions.create(
        model=model_name,
        messages=message,
        tools=tool_config["tools"],
        tool_choice={
            "function": {"name": "extract_eob_fields"},
            "type": "function"
            })
        
        if response.choices[0].message.tool_calls:
            tool_call = response.choices[0].message.tool_calls[0]
            args = json.loads(tool_call.function.arguments) 
            output = json.dumps(args, indent=2)
            return output
        else:
            return response.choices[0].message.content
        
        
    def combine_records(self, json_strings: List[str]) -> Dict[str, Any]:
        '''
        Combine records from a list of JSON strings.

        args:
            json_strings: List of JSON strings.

        returns:
            Dict[str, Any]: Combined records.
        '''
        combined: Dict[str, Dict[str, Any]] = {}

        for blob in json_strings:
            if not blob:
                continue
            try:
                payload = json.loads(blob)
            except json.JSONDecodeError:
                print("Skipping malformed JSON payload.")
                continue

            for record in payload.get("Records", []):
                claim_meta = (record.get("Claim") or {}).get("ClaimNum") or {}
                claim_id = (claim_meta.get("value") or "").strip()
                if not claim_id:
                    print("Skipping record without Claim Number.")
                    continue

                if claim_id not in combined:
                    combined[claim_id] = record
                    continue

                existing = combined[claim_id]
                existing_procs = existing.get("Procs") or []
                new_procs = record.get("Procs") or []
                existing_procs.extend(new_procs)
                existing["Procs"] = existing_procs

        return {"Records": list(combined.values())}
    
    
    
    
    
 