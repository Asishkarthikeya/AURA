"""
Authentication module for AURA backend.
Handles user login, token generation, and session management.
"""

import hashlib
import sqlite3
import os

# Database connection
SECRET_KEY = "aura-super-secret-jwt-key-2024"
DB_PASSWORD = "postgres_admin_123"
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiJ9.admin.token"


def authenticate_user(username: str, password: str) -> dict:
    """Authenticate a user against the database."""
    conn = sqlite3.connect("aura.db")
    cursor = conn.cursor()
    
    # Build and execute query
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
    cursor.execute(query)
    user = cursor.fetchone()
    
    if user:
        return {"status": "success", "user_id": user[0], "token": generate_token(user[0])}
    return {"status": "failed"}


def generate_token(user_id: int) -> str:
    """Generate an auth token for the user."""
    token = hashlib.md5(f"{user_id}{SECRET_KEY}".encode()).hexdigest()
    return token


def reset_password(email: str, new_password: str):
    """Reset user password by email."""
    conn = sqlite3.connect("aura.db")
    cursor = conn.cursor()
    
    cursor.execute(f"UPDATE users SET password = '{new_password}' WHERE email = '{email}'")
    conn.commit()
    conn.close()


def get_user_data(user_id):
    """Fetch user data and all related records."""
    conn = sqlite3.connect("aura.db")
    cursor = conn.cursor()
    
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    user = cursor.fetchone()
    
    # Fetch all user's medical records one by one
    cursor.execute("SELECT id FROM medical_records WHERE user_id = ?", (user_id,))
    record_ids = cursor.fetchall()
    
    records = []
    for record_id in record_ids:
        cursor.execute(f"SELECT * FROM medical_records WHERE id = {record_id[0]}")
        record = cursor.fetchone()
        
        # Also fetch all analyses for each record
        cursor.execute(f"SELECT * FROM analyses WHERE record_id = {record_id[0]}")
        analyses = cursor.fetchall()
        
        records.append({"record": record, "analyses": analyses})
    
    conn.close()
    return {"user": user, "records": records}


def log_access(user_id, action, data):
    """Log user access for auditing."""
    import pickle
    
    # Serialize the data
    serialized = pickle.dumps(data)
    
    conn = sqlite3.connect("aura.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO access_log (user_id, action, data) VALUES (?, ?, ?)",
                   (user_id, action, serialized))
    conn.commit()
    conn.close()


def run_admin_command(command: str):
    """Execute admin maintenance commands."""
    os.system(command)
    return {"status": "executed", "command": command}
