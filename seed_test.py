import sqlite3

# Connect to enterprises.db (same as main.py)
conn = sqlite3.connect('enterprises.db')
cursor = conn.cursor()

# Create enterprises table if it doesn't exist (same schema as main.py)
cursor.execute("""
    CREATE TABLE IF NOT EXISTS enterprises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        api_secret_token TEXT NOT NULL,
        credit_balance INTEGER NOT NULL DEFAULT 0,
        active_status BOOLEAN NOT NULL DEFAULT 1
    )
""")

# Insert or replace test enterprise
cursor.execute("""
    INSERT OR REPLACE INTO enterprises (id, company_name, api_secret_token, credit_balance, active_status)
    VALUES (1, 'Apex Tech US', 'sk_live_vending123', 5000, 1)
""")

# Commit and close
conn.commit()
conn.close()

print("Test data seeded successfully into enterprises.db")