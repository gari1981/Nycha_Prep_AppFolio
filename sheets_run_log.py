"""Append sanitized application progress to Google Sheets and local JSONL."""
import os
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SPREADSHEET_ID = os.getenv('LOG_SPREADSHEET_ID', '')
SHEET = 'NYCHA PREP APPFOLIO'
HEADERS = ['Time (UTC)', 'Run ID', 'Level', 'Step', 'Message']


class SheetsRunLog(logging.Handler):
    def __init__(self, directory):
        super().__init__(logging.INFO)
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        self.service = None
        self.pending = []
        self.secrets = []
        self.busy = False
        self.last_send = 0
        self.retry_after = 0
        self.sequence = 0
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (self.run_id + '.jsonl')

    def redact(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'https?://[^\s<>]+', '[URL REDACTED]', text)
        text = re.sub(r'(?i)((?:password|refresh_token|access_token|client_secret|authorization)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
        return text[:40000]

    def bind(self, service):
        # Verify the dedicated tab/header without altering any existing cells.
        header = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"'{SHEET}'!A1:E1"
        ).execute(num_retries=0).get('values', [])
        if header != [HEADERS]:
            raise RuntimeError('Logging tab is missing or its headers differ from the expected format.')
        self.service = service
        self.flush()

    def emit(self, record):
        if self.busy or record.name != 'root':
            return
        try:
            message = record.getMessage()
            if record.exc_info:
                message += '\n' + logging.Formatter().formatException(record.exc_info)
            message = self.redact(message)
            self.sequence += 1
            row = [datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='seconds'),
                   self.run_id, record.levelname, str(self.sequence), message]
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            self.pending.append(row)
            if (len(self.pending) >= 8 or time.monotonic() - self.last_send >= 3
                    or record.levelno >= logging.WARNING or message.startswith('WAITING')):
                self.flush()
        except Exception as exc:
            print('LOCAL LOG WARNING: ' + type(exc).__name__, file=sys.stderr)

    def flush(self):
        if (not self.service or not self.pending or self.busy
                or time.monotonic() < self.retry_after):
            return
        self.busy = True
        try:
            # RAW prevents report names/error text being interpreted as formulas.
            batch = self.pending[:200]
            self.service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID, range=f"'{SHEET}'!A:E",
                valueInputOption='RAW', insertDataOption='INSERT_ROWS',
                body={'values': batch},
            ).execute(num_retries=0)
            del self.pending[:len(batch)]
            self.last_send = time.monotonic()
        except Exception as exc:
            self.retry_after = time.monotonic() + 30
            print('SHEETS LOG WARNING: ' + type(exc).__name__ +
                  '; progress remains in ' + str(self.path), file=sys.stderr)
        finally:
            self.busy = False

    def finish(self):
        self.retry_after = 0
        while self.service and self.pending:
            before = len(self.pending)
            self.flush()
            if len(self.pending) == before:
                break
        if self.pending:
            print(f'SHEETS LOG: {len(self.pending)} unsent rows retained in {self.path}', file=sys.stderr)
