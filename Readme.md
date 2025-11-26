# Dental EOB Field Extraction using LLM

This project is a FastAPI-based application designed to extract structured data from Dental Explanation of Benefits (EOB) PDF documents. It leverages a hybrid approach using **Azure Document Intelligence** for OCR and layout analysis, and **Azure OpenAI (GPT-4o/GPT-5-mini)** for intelligent field extraction. The processed data is standardized and stored in AWS S3, with optional integration to trigger downstream AWS Lambda functions.

## Features

- **PDF Processing**: Downloads EOB PDFs from AWS S3.
- **Hybrid Extraction Pipeline**:
  - **OCR**: Uses Azure Document Intelligence to extract text and tables.
  - **LLM Processing**: Uses Azure OpenAI to interpret unstructured text and map it to a structured JSON schema.
- **API Endpoints**:
  - Asynchronous (Non-blocking) processing with background tasks.
  - Synchronous (Blocking) processing for immediate results.
- **Integration**:
  - AWS S3 for input/output storage.
  - AWS Lambda trigger for post-processing.
  - AWS SSM Parameter Store for secure configuration management.

## Tech Stack

- **Language**: Python 3.9+
- **Framework**: FastAPI
- **Cloud Services**:
  - **AWS**: S3, Lambda, SSM Parameter Store
  - **Azure**: Document Intelligence (Form Recognizer), OpenAI
- **Libraries**: `boto3`, `azure-ai-formrecognizer`, `openai`, `pydantic`, `uvicorn`

## Prerequisites

Before running the application, ensure you have the following:

1.  **Python 3.9+** installed.
2.  **AWS Credentials** configured (via `~/.aws/credentials` or environment variables) with access to S3, SSM, and Lambda.
3.  **Azure Services** set up:
    - Azure Document Intelligence resource.
    - Azure OpenAI resource with a deployed model (e.g., GPT-4o).

## Installation

Since a `requirements.txt` file is not present, install the necessary dependencies manually:

```bash
pip install fastapi uvicorn boto3 azure-ai-formrecognizer azure-core openai tiktoken pydantic python-dotenv
```

## Configuration

The application relies on **Environment Variables** and **AWS SSM Parameter Store** for configuration.

### Environment Variables

Create a `.env` file in the root directory or set these variables in your environment:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `AWS_REGION` | AWS Region for S3 and SSM | `us-east-1` |
| `Env` | Environment name (e.g., dev, prod) | `dev` |
| `BUCKET_NAME` | S3 Bucket name for file storage | `dev-opendentalintegration-eob-upload` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `POSTPROCESS_LAMBDA` | Name of the Lambda function to trigger | `{Env}-OpenDental-EOBPostProcessFunction` |

### AWS SSM Parameters

The application fetches sensitive keys from AWS SSM Parameter Store. Ensure the following parameters exist:

| Parameter Path | Description |
| :--- | :--- |
| `/XXXX/{Env}/ocr_endpoint` | Azure Document Intelligence Endpoint |
| `/XXXX/{Env}/ocr_subscription_key` | Azure Document Intelligence Key |
| `/XXXX/{Env}/llm_endpoint` | Azure OpenAI Endpoint |
| `/XXXX/{Env}/llm_subscription_key` | Azure OpenAI API Key |

> **Note**: Replace `XXXX` with the actual project prefix used in `helper.py` (currently hardcoded as `XXXX`).

## 🚀 Usage

### 1. Start the Server

Run the FastAPI server using Uvicorn:

```bash
uvicorn app:app --reload
```

The server will start at `http://127.0.0.1:8000`.

### 2. API Endpoints

#### Health Check
- **GET** `/health`
- Returns the service status.

#### Asynchronous Processing
- **POST** `/eob`
- Starts processing in the background and returns immediately.
- Triggers the configured AWS Lambda function upon completion.

**Request Body:**
```json
{
  "eobId": "unique-eob-id",
  "uploadedDataPath": "path/to/input.pdf",
  "processedDataPath": "path/to/output/folder",
  "providerOverride": "optional-provider-id"
}
```

#### Synchronous Processing
- **POST** `/eob/sync`
- Blocks until processing is complete and returns the extracted data directly.

**Request Body:**
```json
{
  "eobId": "unique-eob-id",
  "uploadedDataPath": "path/to/input.pdf",
  "processedDataPath": "path/to/output/folder"
}
```

## Project Structure

```
.
├── app.py              # Main FastAPI application and route definitions
├── helper.py           # Helper class for Azure/AWS integrations and text processing
├── processor.py        # Core logic for PDF processing pipeline
├── system_prompt.txt   # System prompt for the LLM extraction logic
└── README.md           # Project documentation
```


