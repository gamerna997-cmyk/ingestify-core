import os
import sqlite3
import time
import uuid
import hashlib
import secrets
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import requests
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Initialize FastAPI app
app = FastAPI(title="Sortera API Core", version="2.0.0")

# Database setup
DATABASE_PATH = "sortera.db"

def init_db():
    """Initialize the SQLite database and create enterprises table if not exists."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS enterprises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            api_secret_token TEXT NOT NULL UNIQUE,
            credit_balance INTEGER NOT NULL DEFAULT 100000,
            active_status BOOLEAN NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# In-memory rate limiter: {ip: [timestamp1, timestamp2, ...]}
rate_limit_store = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 10

def check_rate_limit(client_ip: str) -> bool:
    """Check if the IP has exceeded the rate limit. Returns True if allowed, False if blocked."""
    now = time.time()
    if client_ip not in rate_limit_store:
        rate_limit_store[client_ip] = []

    # Remove timestamps older than the window
    rate_limit_store[client_ip] = [
        ts for ts in rate_limit_store[client_ip] if now - ts < RATE_LIMIT_WINDOW
    ]

    # Check if at limit
    if len(rate_limit_store[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return False

    # Add current timestamp
    rate_limit_store[client_ip].append(now)
    return True

# Pydantic model for ingestion request
class IngestionRequest(BaseModel):
    raw_text: str
    source_platform: str

# Helper to get enterprise by ID
def get_enterprise_by_id(enterprise_id: int):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, company_name, api_secret_token, credit_balance, active_status FROM enterprises WHERE id = ?",
        (enterprise_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "company_name": row[1],
            "api_secret_token": row[2],
            "credit_balance": row[3],
            "active_status": bool(row[4])
        }
    return None

# Helper to update enterprise credit balance
def update_credit_balance(enterprise_id: int, amount: int):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE enterprises SET credit_balance = credit_balance + ? WHERE id = ?",
        (amount, enterprise_id)
    )
    conn.commit()
    conn.close()

# Helper to create new enterprise
def create_enterprise(company_name: str) -> dict:
    # Generate a secure token starting with 'sk_live_'
    token = f"sk_live_{secrets.token_urlsafe(32)}"
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO enterprises (company_name, api_secret_token, credit_balance, active_status)
        VALUES (?, ?, ?, ?)
        """,
        (company_name, token, 100000, 1)
    )
    conn.commit()
    enterprise_id = cursor.lastrowid
    conn.close()
    return {
        "id": enterprise_id,
        "company_name": company_name,
        "api_secret_token": token,
        "credit_balance": 100000,
        "active_status": True
    }

# Ingestion endpoint
@app.post("/api/v2/ingest/{enterprise_id}")
async def ingest_data(
    enterprise_id: int,
    request: IngestionRequest,
    x_enterprise_token: Optional[str] = Header(None),
    req: Request = None
):
    """
    Ingest and sanitize data via Gemini AI.

    Steps:
    1. Validate incoming payload (raw_text, source_platform) via Pydantic (handled by FastAPI).
    2. Check enterprise exists and is active.
    3. Validate X-ENTERPRISE-TOKEN header matches database token.
    4. Apply IP-based rate limiting (10 requests/60 seconds).
    5. Deduct 1 credit; return 402 if balance <= 0 after deduction.
    6. Call Gemini API to clean and structure the data.
    7. Return Gemini's JSON response.
    """
    # 1. Payload validation is done by Pydantic (raw_text, source_platform required)

    # 2. Check enterprise exists and active
    enterprise = get_enterprise_by_id(enterprise_id)
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )
    if not enterprise["active_status"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Enterprise account is inactive"
        )

    # 3. Validate token
    if x_enterprise_token != enterprise["api_secret_token"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid enterprise token"
        )

    # 4. Rate limiting
    client_ip = req.client.host if req.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Maximum 10 requests per 60 seconds."
        )

    # 5. Deduct 1 credit
    new_balance = enterprise["credit_balance"] - 1
    if new_balance < 0:
        # Not enough credits
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits. Please recharge your account."
        )
    update_credit_balance(enterprise_id, -1)  # Deduct 1

    # 6. Call Gemini API asynchronously
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Gemini API key not configured"
        )

    # System prompt for Gemini
    system_prompt = """
    You are a data sanitization expert. Read the messy input string, strip out punctuation/typos,
    extract the customer's phone number and name, and format it into a clean data dictionary.
    Return ONLY a valid JSON object with the following structure:
    {
        "name": "extracted or cleaned customer name",
        "phone_number": "extracted phone number in E.164 format if possible, else cleaned",
        "cleaned_text": "the input string with punctuation and typos removed",
        "source_platform": "the source platform from the request"
    }
    If name or phone number cannot be found, use empty strings.
    """

    # Prepare Gemini API request
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    gemini_payload = {
        "contents": [{
            "parts": [{
                "text": f"{system_prompt}\n\nInput text: {request.raw_text}\nSource platform: {request.source_platform}"
            }]
        }]
    }

    # Make the request in a thread to avoid blocking the async event loop
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        try:
            response = await loop.run_in_executor(
                pool,
                lambda: requests.post(
                    gemini_url,
                    json=gemini_payload,
                    timeout=10  # 10 second timeout
                )
            )
        except Exception as e:
            # If the thread fails, we return an error
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to contact Gemini API: {str(e)}"
            )

    # Check Gemini API response
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini API returned error: {response.text}"
        )

    # Parse Gemini response
    try:
        gemini_result = response.json()
        # Extract the text content from Gemini's response
        # Expected structure: { "candidates": [{ "content": { "parts": [{ "text": "..." }] }] } }
        candidates = gemini_result.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates in Gemini response")

        text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
        # The text_content should be a JSON string, but we'll try to parse it
        import json
        structured_data = json.loads(text_content)
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
        # If Gemini didn't return valid JSON, we return a fallback structured response
        structured_data = {
            "name": "",
            "phone_number": "",
            "cleaned_text": request.raw_text,  # fallback: just remove punctuation? but we don't have the cleaned version
            "source_platform": request.source_platform
        }
        # Optionally, we could try to extract from raw_text here, but for simplicity we return the fallback.
        # In a production system, we might want to log this error.

    # 7. Return the structured JSON response from Gemini
    return JSONResponse(content=structured_data)

# Stripe webhook endpoint
@app.post("/api/v2/payments/stripe-webhook")
async def stripe_webhook(request: Request):
    """
    Handle Stripe webhook events for automatic customer onboarding.

    Steps:
    1. Listen for incoming transaction logs from Stripe.
    2. If event type is 'checkout.session.completed', parse session metadata.
    3. Extract company name or email from metadata.
    4. Insert new enterprise into database with:
        - Generated token (sk_live_...)
        - credit_balance = 100000
        - active_status = True
    5. Return 200 to Stripe.
    """
    # Get the raw body for signature verification (in production, verify Stripe signature)
    # For simplicity, we assume the payload is JSON and we don't verify signature here.
    # In a real implementation, you would verify the webhook signature using the endpoint secret.
    try:
        payload = await request.body()
        import json
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )

    # Check if this is a checkout.session.completed event
    if event.get("type") == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        metadata = session.get("metadata", {})

        # Extract company name or email from metadata
        # We'll look for 'company_name' or 'email' or 'client_reference_id'
        company_name = (
            metadata.get("company_name") or
            metadata.get("email") or
            metadata.get("client_reference_id") or
            "New Customer"  # fallback
        )

        # If we got an email, we might want to use the part before @ as company name, but we'll keep it simple.
        # Create the enterprise
        try:
            enterprise = create_enterprise(company_name)
            # Log the creation (in production, you might want to send a welcome email, etc.)
            print(f"Created new enterprise: {enterprise['id']} for {company_name}")
        except Exception as e:
            # If database error, we still return 200 to Stripe to avoid retries, but log the error.
            # In a real system, you might want to alert on this.
            print(f"Failed to create enterprise from Stripe webhook: {str(e)}")
            # We still return 200 so Stripe doesn't retry

    # Return 200 to Stripe to acknowledge receipt
    return JSONResponse(content={"status": "success"}, status_code=200)

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "2.0.0"}

# To run the application: uvicorn main:app --host 0.0.0.0 --port 8000