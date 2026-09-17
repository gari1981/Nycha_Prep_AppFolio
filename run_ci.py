"""Run the AppFolio refresh without interactive authorization or login."""
import json
import logging
import os
from pathlib import Path
import tempfile
import sys

import nycha_prep_appfolio_worker as worker
from sheets_run_log import SheetsRunLog


def run(run_log):
    required = ("GOOGLE_TOKEN_JSON", "NYCHA_PREP_SPREADSHEET_ID",
                "APPFOLIO_LOGIN_SPREADSHEET_ID", "APPFOLIO_URL", "LOG_SPREADSHEET_ID")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("Missing GitHub Secrets/Variables: " + ", ".join(missing))
    try:
        token = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    except ValueError:
        raise RuntimeError("GOOGLE_TOKEN_JSON must contain the complete token JSON.") from None
    if not isinstance(token, dict) or not all(token.get(k) for k in
            ("refresh_token", "client_id", "client_secret", "token_uri")):
        raise RuntimeError("GOOGLE_TOKEN_JSON must be an authorized-user token with a refresh token, not an OAuth client JSON.")
    if token["token_uri"] != "https://oauth2.googleapis.com/token":
        raise RuntimeError("Unexpected Google token endpoint.")
    run_log.secrets.extend(str(token[k]) for k in
                           ('token', 'refresh_token', 'client_secret') if token.get(k))
    with tempfile.TemporaryDirectory(prefix="nycha-appfolio-") as directory:
        folder = Path(directory)
        token_path = folder / "google_token.json"
        token_path.write_text(json.dumps(token), encoding="utf-8")
        token_path.chmod(0o600)
        args = worker.parse_args()
        args.headless = True
        args.non_interactive_login = True
        args.google_credentials_file = None
        args.google_oauth_client_file = None
        args.google_token_file = str(token_path)
        args.profile_dir = str(folder / "browser-profile")
        args.download_dir = str(folder / "downloads")
        args.browser_channel = "chromium"
        worker.appfolio_base.require_dependencies()
        service = worker.appfolio_base.build_sheets_service(args)
        # Require log access before downloading or changing any report tabs.
        run_log.bind(service)
        logging.info("SUCCESS Connected to NYCHA PREP APPFOLIO progress log.")
        worker.validate_spreadsheet(service, args)
        worker.load_appfolio_login(service, args)
        run_log.secrets.extend([args.appfolio_email, args.appfolio_password])
        if args.validate_only:
            logging.info("SUCCESS Google access, destination tabs and Login values validated. AppFolio login not tested.")
            return
        if not args.run_now:
            raise RuntimeError("Pass --validate-only or --run-now.")
        worker.perform_refresh(service, args)
        logging.info("SUCCESS NYCHA PREP AppFolio refresh completed.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with tempfile.TemporaryDirectory(prefix='nycha-run-log-') as log_directory:
        run_log = SheetsRunLog(log_directory)
        logging.getLogger().addHandler(run_log)
        try:
            logging.info("STARTED GitHub Actions run %s; mode=%s", run_log.run_id,
                         'validate' if '--validate-only' in sys.argv else 'refresh')
            run(run_log)
            return 0
        except KeyboardInterrupt:
            logging.warning("STOPPED Run interrupted.")
            return 130
        except Exception as exc:
            # Keep authentication URLs and known credentials out of public logs.
            logging.error("FAILED %s: %s", type(exc).__name__, run_log.redact(str(exc)))
            return 1
        finally:
            run_log.finish()
            if run_log.pending:
                # Hosted runners are disposable: emit sanitized fallback rows.
                for row in run_log.pending:
                    print('UNSENT SHEETS LOG: ' + json.dumps(row, ensure_ascii=False), flush=True)
            logging.getLogger().removeHandler(run_log)
            run_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
