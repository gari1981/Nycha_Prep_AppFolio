#!/usr/bin/env python3
"""Refresh the NYCHA PREP AppFolio upload tabs from three saved reports."""

from __future__ import annotations

import argparse
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import key_organizer_worker as appfolio_base


NYCHA_PREP_SPREADSHEET_ID = ""
APPFOLIO_LOGIN_SPREADSHEET_ID = os.getenv("APPFOLIO_LOGIN_SPREADSHEET_ID", "")
APPFOLIO_LOGIN_RANGE = "'Login'!A1:Z20"
CHECK_SHEET = "Appfolio+NYCHA"
CHECK_HEADER = "Check"
REPORT_JOBS = (
    appfolio_base.ReportJob("NYCHA Tenant Directory", "Tenant Directory upload"),
    appfolio_base.ReportJob(
        "NYCHA PREP Property Directory", "Property Directory upload"
    ),
    appfolio_base.ReportJob(
        "NYCHA PAYMENTS General Ledger", "General Ledger upload"
    ),
)
REPORT_REQUIRED_HEADERS = {
    REPORT_JOBS[0]: (
        "Tenant Tags",
        "Unit Tags",
        "Tenant",
        "Property Name",
        "Property Street Address 1",
        "Property Street Address 2",
        "First Name",
        "Last Name",
    ),
    REPORT_JOBS[1]: (
        "Description",
        "Property Name",
        "Market Rent",
        "Owner(s)",
    ),
    REPORT_JOBS[2]: (
        "Property Name",
        "Payee / Payer",
        "Credit",
    ),
}

# Start quickly, then re-enter more slowly if AppFolio drops characters or the
# result menu is delayed by a weak connection.
NYCHA_PREP_SEARCH_RETRY_STEPS = (
    (0, 12, 2),
    (50, 30, 5),
    (200, 60, 0),
)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Download three NYCHA PREP AppFolio reports, replace their upload "
            "tabs."
        )
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=os.getenv(
            "NYCHA_PREP_SPREADSHEET_ID", NYCHA_PREP_SPREADSHEET_ID
        ),
    )
    parser.add_argument(
        "--google-credentials-file",
        default=os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    )
    parser.add_argument(
        "--google-oauth-client-file", default=os.getenv("GOOGLE_OAUTH_CLIENT_FILE")
    )
    parser.add_argument(
        "--google-token-file",
        default=os.getenv(
            "GOOGLE_SHEETS_TOKEN_FILE",
            str(script_dir / ".secrets" / "google_token.json"),
        ),
    )
    parser.add_argument("--google-timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--appfolio-url",
        default=os.getenv("APPFOLIO_URL", appfolio_base.APPFOLIO_URL),
    )
    parser.add_argument("--appfolio-email", default=os.getenv("APPFOLIO_EMAIL", ""))
    parser.add_argument(
        "--appfolio-password", default=os.getenv("APPFOLIO_PASSWORD", "")
    )
    parser.add_argument(
        "--profile-dir",
        default=os.getenv(
            "NYCHA_PREP_APPFOLIO_PROFILE",
            str(script_dir / "chrome-profile-appfolio-nycha-prep"),
        ),
    )
    parser.add_argument(
        "--browser-channel",
        choices=("chromium", "chrome"),
        default=os.getenv("APPFOLIO_BROWSER_CHANNEL", "chromium").strip().lower(),
        help=(
            "Use Playwright's managed Chromium by default. Choose chrome only "
            "to opt back into the installed system Google Chrome build."
        ),
    )
    parser.add_argument(
        "--download-dir",
        default=os.getenv(
            "NYCHA_PREP_DOWNLOAD_DIR",
            str(script_dir / "downloads" / "nycha-prep"),
        ),
    )
    parser.add_argument(
        "--headless", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--non-interactive-login",
        action="store_true",
        help=(
            "Fail instead of pausing for manual Chrome input when automatic "
            "login fails."
        ),
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run one complete refresh. Required for safety.",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser.parse_args()


def normalize_login_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().casefold()).strip()


def parse_appfolio_login_grid(rows: list[list[Any]]) -> tuple[str, str]:
    """Parse a small Login tab without ever logging or returning labels."""
    username_labels = {
        "username", "user name", "email", "email address",
        "appfolio username", "appfolio user name", "appfolio email",
    }
    password_labels = {"password", "appfolio password"}

    # Horizontal header layout: Username | Password, with values below.
    for row_index, row in enumerate(rows[:-1]):
        labels = [normalize_login_label(value) for value in row]
        username_column = next(
            (index for index, label in enumerate(labels) if label in username_labels),
            None,
        )
        password_column = next(
            (index for index, label in enumerate(labels) if label in password_labels),
            None,
        )
        if username_column is not None and password_column is not None:
            value_row = rows[row_index + 1]
            username = str(value_row[username_column] if username_column < len(value_row) else "").strip()
            password = str(value_row[password_column] if password_column < len(value_row) else "")
            if username and password:
                return username, password

    # Vertical key/value layout: AppFolio Username | value.
    username = ""
    password = ""
    all_labels = username_labels | password_labels
    for row in rows:
        for column, value in enumerate(row):
            label = normalize_login_label(value)
            if label not in all_labels:
                continue
            candidate = str(row[column + 1] if column + 1 < len(row) else "")
            if not candidate or normalize_login_label(candidate) in all_labels:
                continue
            if label in username_labels:
                username = candidate.strip()
            elif label in password_labels:
                password = candidate
    if username and password:
        return username, password
    raise RuntimeError(
        "Login tab must contain AppFolio username/email and password in either "
        "a header row with values below or key/value rows."
    )


def load_appfolio_login(service, args: argparse.Namespace) -> None:
    response = appfolio_base.execute_google(
        service.spreadsheets().values().get(
            spreadsheetId=APPFOLIO_LOGIN_SPREADSHEET_ID,
            range=APPFOLIO_LOGIN_RANGE,
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    args.appfolio_email, args.appfolio_password = parse_appfolio_login_grid(
        response.get("values", [])
    )
    logging.info("SUCCESS Loaded AppFolio login from the Login tab.")


def normalize_header(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def column_letter(number: int) -> str:
    if number < 1:
        raise ValueError("Column numbers are one-based.")
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def required_header_row(
    job: appfolio_base.ReportJob, rows: list[list[Any]]
) -> int:
    """Return the one-based row containing every formula-required header."""
    required = {
        normalize_header(header) for header in REPORT_REQUIRED_HEADERS[job]
    }
    for row_number, row in enumerate(rows, start=1):
        present = {
            normalize_header(value)
            for value in row
            if normalize_header(value)
        }
        if required.issubset(present):
            return row_number
    missing = ", ".join(REPORT_REQUIRED_HEADERS[job])
    raise RuntimeError(
        f"{job.report_name} does not contain its required header row: {missing}."
    )


def destination_sheet_properties(
    service, spreadsheet_id: str, sheet_name: str
) -> dict[str, Any]:
    response = appfolio_base.execute_google(
        service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields=(
                "sheets(properties(sheetId,title,"
                "gridProperties(rowCount,columnCount)))"
            ),
        )
    )
    props = next(
        (
            sheet["properties"]
            for sheet in response.get("sheets", [])
            if sheet.get("properties", {}).get("title") == sheet_name
        ),
        None,
    )
    if not props:
        raise RuntimeError(f"Destination tab not found: {sheet_name}")
    return props


def unmerge_destination_sheet(service, spreadsheet_id: str, sheet_name: str) -> None:
    """Remove stale report merges before replacing raw worksheet values."""
    props = destination_sheet_properties(service, spreadsheet_id, sheet_name)
    grid = props.get("gridProperties", {})
    appfolio_base.execute_google(
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "unmergeCells": {
                            "range": {
                                "sheetId": props["sheetId"],
                                "startRowIndex": 0,
                                "endRowIndex": max(int(grid.get("rowCount", 1)), 1),
                                "startColumnIndex": 0,
                                "endColumnIndex": max(
                                    int(grid.get("columnCount", 1)), 1
                                ),
                            }
                        }
                    }
                ]
            },
        )
    )
    logging.info("SUCCESS Removed stale merged cells from %s.", sheet_name)


def verify_uploaded_report_headers(
    service,
    spreadsheet_id: str,
    job: appfolio_base.ReportJob,
    expected_header_row: int,
) -> None:
    response = appfolio_base.execute_google(
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=(
                f"{appfolio_base.quote_sheet_name(job.destination_sheet)}!"
                f"A1:Z{expected_header_row}"
            ),
            valueRenderOption="UNFORMATTED_VALUE",
        )
    )
    actual_header_row = required_header_row(job, response.get("values", []))
    if actual_header_row != expected_header_row:
        raise RuntimeError(
            f"{job.destination_sheet} header verification failed: expected row "
            f"{expected_header_row}, received row {actual_header_row}."
        )
    logging.info(
        "SUCCESS Verified required headers in %s row %s.",
        job.destination_sheet,
        expected_header_row,
    )


def check_column_number(service, spreadsheet_id: str) -> int:
    response = appfolio_base.execute_google(
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{appfolio_base.quote_sheet_name(CHECK_SHEET)}!1:1",
            valueRenderOption="UNFORMATTED_VALUE",
        )
    )
    rows = response.get("values", [])
    headers = rows[0] if rows else []
    matches = [
        index + 1
        for index, value in enumerate(headers)
        if normalize_header(value) == normalize_header(CHECK_HEADER)
    ]
    if not matches:
        raise RuntimeError(
            f"{CHECK_SHEET} row 1 does not contain the required {CHECK_HEADER!r} header."
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"{CHECK_SHEET} row 1 contains more than one {CHECK_HEADER!r} header."
        )
    return matches[0]


def validate_spreadsheet(service, args: argparse.Namespace) -> None:
    response = appfolio_base.execute_google(
        service.spreadsheets().get(
            spreadsheetId=args.spreadsheet_id,
            fields="properties(title),sheets(properties(sheetId,title))",
        )
    )
    titles = {
        sheet["properties"]["title"] for sheet in response.get("sheets", [])
    }
    required = {
        CHECK_SHEET,
        *(job.destination_sheet for job in REPORT_JOBS),
    }
    missing = sorted(required - titles)
    if missing:
        raise RuntimeError("Required NYCHA PREP tab(s) not found: " + ", ".join(missing))
    check_column_number(service, args.spreadsheet_id)
    logging.info(
        "SUCCESS Connected to Google Sheet: %s",
        response.get("properties", {}).get("title", args.spreadsheet_id),
    )


def _download_all_reports_once(args: argparse.Namespace) -> dict[Any, Path]:
    from playwright.sync_api import sync_playwright

    download_dir = Path(args.download_dir).expanduser().resolve()
    diagnostics_dir = download_dir / "diagnostics"
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    if args.browser_channel == "chromium" and not profile_dir.name.endswith(
        "-playwright"
    ):
        profile_dir = profile_dir.with_name(profile_dir.name + "-playwright")
    download_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    completed: dict[Any, Path] = {}

    logging.info("STARTED Launching persistent AppFolio Chrome profile.")
    prior_search_steps = appfolio_base.APPFOLIO_SEARCH_RETRY_STEPS
    appfolio_base.APPFOLIO_SEARCH_RETRY_STEPS = NYCHA_PREP_SEARCH_RETRY_STEPS
    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, Any] = {
                "user_data_dir": str(profile_dir),
                "headless": args.headless,
                "accept_downloads": True,
            }
            if args.browser_channel == "chrome":
                launch_options["channel"] = "chrome"
            logging.info(
                "STARTED Using %s for AppFolio.",
                "Playwright-managed Chromium"
                if args.browser_channel == "chromium"
                else "system Google Chrome",
            )
            context = playwright.chromium.launch_persistent_context(**launch_options)
            try:
                home_page = context.pages[0] if context.pages else context.new_page()
                appfolio_base.wait_for_appfolio_login(home_page, args)
                for job in REPORT_JOBS:
                    logging.info("STARTED Opening saved report %s.", job.report_name)
                    report_page = appfolio_base.open_saved_report(
                        home_page, job.report_name, diagnostics_dir
                    )
                    try:
                        completed[job] = appfolio_base.download_report(
                            report_page,
                            job.report_name,
                            download_dir,
                            diagnostics_dir,
                        )
                    finally:
                        if report_page is not home_page and not report_page.is_closed():
                            report_page.close()
            finally:
                context.close()
                logging.info("SUCCESS Closed AppFolio Chrome profile.")
    finally:
        appfolio_base.APPFOLIO_SEARCH_RETRY_STEPS = prior_search_steps
    return completed


def download_all_reports(args: argparse.Namespace) -> dict[Any, Path]:
    for attempt in range(1, 4):
        try:
            return _download_all_reports_once(args)
        except Exception as exc:
            if not appfolio_base.browser_target_closed_error(exc) or attempt == 3:
                raise
            pause_seconds = 5 * attempt
            logging.warning(
                "BROWSER RECOVERY AppFolio browser closed unexpectedly on "
                "attempt %s/3. Relaunching in %s second(s): %s",
                attempt,
                pause_seconds,
                exc,
            )
            time.sleep(pause_seconds)
    raise RuntimeError("AppFolio browser recovery exhausted unexpectedly.")


def perform_refresh(service, args: argparse.Namespace) -> None:
    downloads = download_all_reports(args)
    prepared = {
        job: appfolio_base.read_excel_values(path)
        for job, path in downloads.items()
    }
    try:
        header_rows = {
            job: required_header_row(job, prepared[job]) for job in REPORT_JOBS
        }
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc} The live upload tabs were not changed."
        ) from exc
    logging.info("SUCCESS All downloaded reports passed required-header checks.")

    # Do not clear live data until every report exists and every workbook can be
    # parsed. This makes a search/download/workbook failure non-destructive.
    for job in REPORT_JOBS:
        logging.info(
            "STARTED Uploading %s to %s.", job.report_name, job.destination_sheet
        )
        unmerge_destination_sheet(
            service,
            args.spreadsheet_id,
            job.destination_sheet,
        )
        appfolio_base.replace_sheet_values(
            service,
            args.spreadsheet_id,
            job.destination_sheet,
            prepared[job],
        )
        verify_uploaded_report_headers(
            service,
            args.spreadsheet_id,
            job,
            header_rows[job],
        )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        appfolio_base.require_dependencies()
        service = appfolio_base.build_sheets_service(args)
        validate_spreadsheet(service, args)
        load_appfolio_login(service, args)
        if args.validate_only:
            print(
                "NYCHA PREP AppFolio worker dependencies, Google access, "
                "destination tabs, Check header, and AppFolio Login values are valid."
            )
            return 0
        if not args.run_now:
            raise RuntimeError(
                "Refusing to run without --run-now. Pass --run-now to refresh reports."
            )
        perform_refresh(service, args)
        logging.info("SUCCESS NYCHA PREP AppFolio update completed.")
        return 0
    except Exception as exc:
        logging.exception("ERROR NYCHA PREP AppFolio update failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
