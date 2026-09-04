import logging
import re
import sys


class SensitiveDataFilter(logging.Filter):
    """
    Filter that masks sensitive data such as passwords, auth tokens,
    and unmasked bank account numbers from logs.
    """
    # Regex to match potential bank account numbers (9 to 18 digits)
    ACCOUNT_REGEX = re.compile(r'(?i)(account[_\s]?number["\':\s=]+)(\d{5,18})')
    # Regex to match passwords or secrets
    PASSWORD_REGEX = re.compile(r'(?i)(password|secret|auth_token|token|api_key)(["\':\s=]+)(["\']?[^"\'\s,]+["\']?)')
    # Regex for bolt/neo4j uri passwords
    URI_PASSWORD_REGEX = re.compile(r'(neo4j(?:\+s)?:\/\/|bolt:\/\/)([^:]+):([^@]+)@')

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self.sanitize(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self.sanitize(str(arg)) for arg in record.args)
        return True

    @classmethod
    def sanitize(cls, text: str) -> str:
        if not isinstance(text, str):
            return text
        
        # Mask bank account numbers, keeping last 4 digits
        def mask_account(match):
            prefix = match.group(1)
            num = match.group(2)
            masked_num = "*" * (len(num) - 4) + num[-4:] if len(num) > 4 else "****"
            return f"{prefix}{masked_num}"

        text = cls.ACCOUNT_REGEX.sub(mask_account, text)

        # Mask passwords / credentials
        text = cls.PASSWORD_REGEX.sub(r'\1\2"******"', text)
        
        # Mask URI credentials
        text = cls.URI_PASSWORD_REGEX.sub(r'\1\2:******@', text)

        return text


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("case_graph_backend")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Remove existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z"
    )
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveDataFilter())
    
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = setup_logging()

