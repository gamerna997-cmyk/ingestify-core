import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Initialize FastAPI app
app = FastAPI(title="Ingestify Core API", version="2.0.0")

# Database setup
DATABASE_URL = "enterprises.db"

def init_db():
    """Initialize the SQLite database and create enterprises table if it doesn't exist."""
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()

    # Create enterprises table with specified schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS enterprises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            api_secret_token TEXT NOT NULL,
            credit_balance INTEGER NOT NULL DEFAULT 0,
            active_status BOOLEAN NOT NULL DEFAULT 1
        )
    """)

    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# In-memory rate limiter storage
# Format: {ip_address: [timestamp1, timestamp2, ...]}
rate_limit_store: Dict[str, list] = {}

def check_rate_limit(client_ip: str) -> bool:
    """
    Check if the client has exceeded the rate limit.
    Returns True if allowed, False if rate limited.
    Limit: 10 requests per 60 seconds rolling window.
    """
    now = time.time()
    window_start = now - 60  # 60 seconds ago

    # Clean old timestamps and get recent ones
    if client_ip in rate_limit_store:
        rate_limit_store[client_ip] = [
            ts for ts in rate_limit_store[client_ip] if ts > window_start
        ]
    else:
        rate_limit_store[client_ip] = []

    # Check if limit exceeded
    if len(rate_limit_store[client_ip]) >= 10:
        return False

    # Add current request timestamp
    rate_limit_store[client_ip].append(now)
    return True

# Pydantic model for request validation
class IngestPayload(BaseModel):
    raw_text: str
    source_platform: str

def get_enterprise_by_id(enterprise_id: int) -> Optional[dict]:
    """Fetch enterprise details from database by ID."""
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM enterprises WHERE id = ?", (enterprise_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None

def update_credit_balance(enterprise_id: int, amount: int) -> bool:
    """
    Update the credit balance for an enterprise.
    Returns True if successful, False if enterprise not found.
    """
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE enterprises SET credit_balance = credit_balance + ? WHERE id = ?",
        (amount, enterprise_id)
    )

    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()

    return affected_rows > 0

@app.post("/api/v2/ingest/{enterprise_id}")
async def ingest_data(
    enterprise_id: int,
    payload: IngestPayload,
    request: Request,
    x_enterprise_token: Optional[str] = Header(None)
):
    """
    Enterprise data ingestion webhook endpoint.

    Logic flow:
    1. Validate incoming payload with Pydantic model
    2. Check enterprise exists and is active
    3. Validate X-ENTERPRISE-TOKEN header matches database token
    4. Check and deduct credits (1 point per request)
    5. Enforce rate limiting (10 requests/minute/IP)
    6. Log successful processing
    7. Return success response
    """

    # Get client IP for rate limiting
    client_ip = request.client.host if request.client else "unknown"

    # Step 1: Rate limiting check
    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded: maximum 10 requests per 60 seconds"
        )

    # Step 2: Validate enterprise exists and is active
    enterprise = get_enterprise_by_id(enterprise_id)
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    if not enterprise['active_status']:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise is not active"
        )

    # Step 3: Validate security token
    if not x_enterprise_token or x_enterprise_token != enterprise['api_secret_token']:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing enterprise token"
        )

    # Step 4: Check and deduct credits
    if enterprise['credit_balance'] <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits. Payment required."
        )

    # Deduct 1 credit
    if not update_credit_balance(enterprise_id, -1):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update credit balance"
        )

    # Step 5: Log the processed request
    print(f"[{datetime.now().isoformat()}] Processing payload for enterprise: {enterprise['company_name']}")
    print(f"  Source Platform: {payload.source_platform}")
    print(f"  Raw Text Length: {len(payload.raw_text)} characters")
    print(f"  Remaining Credits: {enterprise['credit_balance'] - 1}")

    # Step 6: Return success response
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "processed": True,
            "message": "Payload authorized and routed to core sorting engine"
        }
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    # Run the server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )