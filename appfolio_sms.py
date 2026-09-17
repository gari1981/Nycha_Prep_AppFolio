"""Read a fresh verification SMS without logging its contents."""
import logging
import re
import time

SMS_SPREADSHEET_ID = '1_E8TYxNzWjQljkZwe5_RIn_pGeHqBDyLt7bQWn3RXYU'


def first_six_digits(value):
    digits = ''.join(re.findall(r'[0-9]', str(value)))
    if len(digits) < 6:
        raise ValueError('Latest SMS cell has fewer than six digits.')
    return digits[:6]


def latest_sms(service):
    metadata = service.spreadsheets().get(
        spreadsheetId=SMS_SPREADSHEET_ID,
        fields='sheets(properties(title,gridProperties(rowCount)))',
    ).execute(num_retries=0)
    sheet = next((x['properties'] for x in metadata.get('sheets', [])
                  if x['properties']['title'] == 'SMS'), None)
    if sheet is None:
        raise RuntimeError('SMS tab not found.')
    end = sheet['gridProperties']['rowCount']
    while end > 1:
        start = max(2, end - 199)
        rows = service.spreadsheets().values().get(
            spreadsheetId=SMS_SPREADSHEET_ID,
            range=f"'SMS'!A{start}:Z{end}", valueRenderOption='FORMATTED_VALUE',
        ).execute(num_retries=0).get('values', [])
        for offset in range(len(rows) - 1, -1, -1):
            row = rows[offset]
            if any(str(value).strip() for value in row):
                return start + offset, tuple(str(v) for v in row), str(row[2]) if len(row) > 2 else ''
        end = start - 1
    return 0, (), ''


def visible(locator):
    for item in locator.all():
        if item.is_visible():
            return item
    return None


class SmsVerification:
    def __init__(self, service):
        self.service = service
        self.requested = False
        self.submitted = False
        self.code = None
        self.before = None
        self.next_read = 0
        self.deadline = 0
        self.warned = False

    def poll(self, page):
        if self.submitted:
            return
        if not self.requested:
            button = visible(page.get_by_role('button', name='Send Verification Code', exact=True))
            if button is None:
                return
            self.before = latest_sms(self.service)
            sms_option = visible(page.get_by_label(re.compile(r'Receive code via SMS', re.I)))
            if sms_option is not None:
                sms_option.check()
            button.click(no_wait_after=True)
            self.requested = True
            self.deadline = time.monotonic() + 120
            logging.info('SMS REQUESTED Verification code; waiting 5 seconds before reading SMS tab.')
            page.wait_for_timeout(5000)
        if time.monotonic() >= self.deadline:
            raise RuntimeError('SMS verification timed out: no fresh code or recognizable verification form within 120 seconds.')
        if self.code is None and time.monotonic() >= self.next_read:
            latest = latest_sms(self.service)
            self.next_read = time.monotonic() + 5
            # Never reuse the code that existed before clicking Send.
            if latest != self.before and latest[0] and latest[2] != self.before[2]:
                try:
                    self.code = first_six_digits(latest[2])
                except ValueError:
                    return
                for handler in logging.getLogger().handlers:
                    if hasattr(handler, 'secrets'):
                        handler.secrets.append(self.code)
                logging.info('SMS RECEIVED Six digits read from column C of the latest new row; value hidden.')
        if self.code is None:
            return
        code_input = visible(page.get_by_role('textbox', name=re.compile(r'^(?:verification code|security code|authentication code|code|enter (?:the )?(?:verification )?code)[: *]*$', re.I)))
        if code_input is None:
            code_input = visible(page.get_by_label(re.compile(r'Verification\s+Code', re.I)))
        if code_input is None:
            code_input = visible(page.locator('input[autocomplete="one-time-code"]'))
        if code_input is None:
            # Some verification pages display a label without associating it
            # with the input. Only use a unique editable input on this screen.
            heading = visible(page.get_by_text('2-Step Verification', exact=True))
            label = visible(page.get_by_text('Verification Code', exact=True))
            if heading is not None and label is not None:
                candidates = [item for item in page.locator(
                    'input:not([type]), input[type="text"], input[type="tel"], input[type="number"]'
                ).all() if item.is_visible() and item.is_enabled()]
                if len(candidates) == 1:
                    code_input = candidates[0]
        confirm = visible(page.get_by_role('button', name=re.compile(r'^(?:Verify|Verify Code|Verify & Log In|Verify and Log In|Submit|Continue|Log in|Sign in)$', re.I)))
        if code_input is None or confirm is None:
            if not self.warned:
                logging.warning('SMS CODE READY but verification input/button not recognized; waiting for the verification form.')
                self.warned = True
            return
        if code_input.is_enabled():
            # Log in may be disabled until all six digits are entered.
            code_input.fill(self.code)
            if not confirm.is_enabled():
                return
            confirm.click(no_wait_after=True)
            self.code = None
            self.submitted = True
            logging.info('SMS SUBMITTED Verification code; waiting for AppFolio home page.')
