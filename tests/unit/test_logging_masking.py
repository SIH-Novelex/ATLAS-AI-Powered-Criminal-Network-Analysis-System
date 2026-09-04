import logging
from backend.logging_config import SensitiveDataFilter


def test_logging_masking_bank_account():
    filt = SensitiveDataFilter()
    sample_text = "Processing transfer for account_number 987654321098 to target account_number 1234567890."
    sanitized = filt.sanitize(sample_text)
    assert "987654321098" not in sanitized
    assert "1098" in sanitized  # Keeps last 4 digits
    assert "********1098" in sanitized


def test_logging_masking_password_and_secret():
    filt = SensitiveDataFilter()
    sample_text = "Connecting to neo4j with password=my_super_secret_password and api_key='secret12345'"
    sanitized = filt.sanitize(sample_text)
    assert "my_super_secret_password" not in sanitized
    assert "secret12345" not in sanitized
    assert "******" in sanitized


def test_logging_masking_bolt_uri():
    filt = SensitiveDataFilter()
    sample_text = "Connecting to bolt://neo4j:superpassword123@localhost:7687"
    sanitized = filt.sanitize(sample_text)
    assert "superpassword123" not in sanitized
    assert "bolt://neo4j:******@localhost:7687" in sanitized

