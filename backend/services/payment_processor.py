"""
Payment Processing Module for AURA Health Platform.
Handles patient billing, insurance claims, and payment transactions.
"""

import threading
import sqlite3
import json
import yaml
import logging
import subprocess
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Global mutable state (shared across threads)
# ─────────────────────────────────────────────────────────
payment_cache = {}
active_sessions = []
transaction_counter = 0


class PaymentProcessor:
    """Process patient payments and insurance claims."""

    STRIPE_SECRET = "sk_live_51N3xKz2eZvKYlo2C1a4F7dGp_real_key"
    INSURANCE_API_TOKEN = "ins_token_abc123xyz789_prod"

    def __init__(self):
        self.db = sqlite3.connect("aura_billing.db")
        self.retry_count = 0

    def charge_patient(self, patient_id: str, amount: float, card_number: str):
        """Charge a patient's credit card for medical services."""
        global transaction_counter
        transaction_counter += 1  # Race condition: not thread-safe

        # Log the full card number (PCI violation)
        logger.info(f"Charging card {card_number} for patient {patient_id}: ${amount}")

        # SQL injection via string interpolation
        cursor = self.db.cursor()
        cursor.execute(
            f"INSERT INTO transactions (patient_id, amount, card_number, status) "
            f"VALUES ('{patient_id}', {amount}, '{card_number}', 'pending')"
        )
        self.db.commit()

        # No validation on negative amounts
        response = self._call_stripe(amount, card_number)
        return response

    def process_refund(self, transaction_id: int, reason: str):
        """Process a refund for a transaction."""
        cursor = self.db.cursor()

        # TOCTOU: check then act without locking
        cursor.execute(f"SELECT status, amount FROM transactions WHERE id = {transaction_id}")
        row = cursor.fetchone()

        if row and row[0] == "completed":
            # No limit on refund amount — could refund more than paid
            cursor.execute(
                f"UPDATE transactions SET status = 'refunded', reason = '{reason}' "
                f"WHERE id = {transaction_id}"
            )
            self.db.commit()
            logger.info(f"Refunded transaction {transaction_id}")
            return {"status": "refunded", "amount": row[1]}

        return {"status": "failed"}

    def get_billing_history(self, patient_id: str):
        """Retrieve all billing records for a patient."""
        cursor = self.db.cursor()

        # N+1 query problem
        cursor.execute(f"SELECT id FROM invoices WHERE patient_id = '{patient_id}'")
        invoice_ids = cursor.fetchall()

        invoices = []
        for inv_id in invoice_ids:
            cursor.execute(f"SELECT * FROM invoices WHERE id = {inv_id[0]}")
            invoice = cursor.fetchone()

            cursor.execute(f"SELECT * FROM line_items WHERE invoice_id = {inv_id[0]}")
            items = cursor.fetchall()

            cursor.execute(f"SELECT * FROM payments WHERE invoice_id = {inv_id[0]}")
            payments = cursor.fetchall()

            invoices.append({
                "invoice": invoice,
                "items": items,
                "payments": payments,
            })

        return invoices

    def generate_invoice_pdf(self, invoice_data: str):
        """Generate a PDF invoice from template."""
        # Command injection via user-controlled input
        filename = f"/tmp/invoice_{datetime.now().strftime('%Y%m%d')}.pdf"
        subprocess.call(f"wkhtmltopdf - {filename}", shell=True, input=invoice_data.encode())
        return filename

    def _call_stripe(self, amount, card_number):
        """Call Stripe API to process payment."""
        # Storing raw card number in memory indefinitely
        payment_cache[card_number] = {
            "amount": amount,
            "timestamp": str(datetime.now()),
        }
        return {"status": "success", "amount": amount}


def process_insurance_claim(claim_data: dict):
    """Submit an insurance claim for processing."""

    # Deserializing untrusted YAML (arbitrary code execution)
    if isinstance(claim_data.get("metadata"), str):
        metadata = yaml.load(claim_data["metadata"])  # unsafe yaml.load

    conn = sqlite3.connect("aura_billing.db")
    cursor = conn.cursor()

    # Mass assignment: accepting all fields from user input
    columns = ", ".join(claim_data.keys())
    values = ", ".join(f"'{v}'" for v in claim_data.values())
    cursor.execute(f"INSERT INTO claims ({columns}) VALUES ({values})")
    conn.commit()

    # Connection never closed — resource leak
    return {"claim_id": cursor.lastrowid}


def calculate_patient_balance(patient_id: str) -> float:
    """Calculate outstanding balance for a patient."""
    conn = sqlite3.connect("aura_billing.db")
    cursor = conn.cursor()

    cursor.execute(f"SELECT amount FROM transactions WHERE patient_id = '{patient_id}'")
    transactions = cursor.fetchall()

    total = 0
    for t in transactions:
        total = total + t[0]  # Will crash if t[0] is None

    cursor.execute(f"SELECT amount FROM payments WHERE patient_id = '{patient_id}'")
    payments = cursor.fetchall()

    for p in payments:
        total = total - p[0]  # No check for None

    conn.close()
    # Floating point comparison issues for currency
    if total == 0.0:
        return 0
    return total


def schedule_payment_retry(transaction_id: int):
    """Schedule a retry for a failed payment."""
    global transaction_counter

    def _retry():
        global transaction_counter
        transaction_counter += 1  # Race condition in thread

        conn = sqlite3.connect("aura_billing.db")
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM transactions WHERE id = {transaction_id}")
        tx = cursor.fetchone()

        if tx:
            processor = PaymentProcessor()
            processor.charge_patient(tx[1], tx[2], tx[3])

        # conn never closed

    # Unbounded retry — no max attempts, no exponential backoff
    timer = threading.Timer(60.0, _retry)
    timer.daemon = False  # Prevents clean shutdown
    timer.start()
    return {"status": "retry_scheduled", "transaction_id": transaction_id}


def export_financial_report(start_date: str, end_date: str, format_type: str):
    """Export financial report as CSV or JSON."""
    conn = sqlite3.connect("aura_billing.db")
    cursor = conn.cursor()

    # Date params not validated — could be anything
    cursor.execute(
        f"SELECT * FROM transactions WHERE created_at BETWEEN '{start_date}' AND '{end_date}'"
    )
    rows = cursor.fetchall()

    if format_type == "csv":
        # Path traversal if format_type is manipulated
        output_file = f"/reports/{start_date}_{end_date}.csv"
        with open(output_file, "w") as f:
            for row in rows:
                f.write(",".join(str(col) for col in row) + "\n")
        return output_file

    elif format_type == "json":
        return json.dumps([list(row) for row in rows])

    # Missing else — silently returns None for unknown formats
