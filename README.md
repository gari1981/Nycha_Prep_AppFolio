# NYCHA Prep AppFolio

Download three saved AppFolio reports and replace their Google Sheets upload tabs using headless Chromium.
AppFolio credentials are read exclusively from `username1` and `password1` in `Login!A1:Z20` in a separate spreadsheet, using the same authorized Google account.
No NYCHA portal status check is performed. A refresh replaces live data and may partially complete if an upload fails.

## Setup

Upload this directory's contents, including `.github/workflows/appfolio.yml`, to the repository's default branch (`main` in the API examples).
Do not upload the ZIP itself. No local browser profile, downloaded report, OAuth client file or token belongs in source control.

In Settings > Secrets and variables > Actions, create a repository **secret**:

- `GOOGLE_TOKEN_JSON`: the complete contents of the working local `.secrets/google_token_new.json` file. This is the authorized-user token, not `google_oauth_client.json`. It must contain `refresh_token`, `client_id`, `client_secret` and `token_uri`.

Create these repository **variables**:

- `NYCHA_PREP_SPREADSHEET_ID`: the destination NYCHA PREP MASTERSHEET ID.
- `APPFOLIO_LOGIN_SPREADSHEET_ID`: the spreadsheet ID containing the `Login` tab.
- `LOG_SPREADSHEET_ID`: the NDC LOG spreadsheet ID containing `NYCHA PREP APPFOLIO`.
- `APPFOLIO_URL`: your organization's full AppFolio URL (`https://...appfolio.com`).

The Google account must be able to read the Login spreadsheet and edit both the destination spreadsheet and the logging spreadsheet.
The existing Google token already includes OAuth client information, so no second OAuth-client secret is required.
If Google revokes the refresh token, authorize locally again and replace the GitHub secret.

## Progress logging

Both `validate` and `refresh` append progress to `NYCHA PREP APPFOLIO` in the configured logging spreadsheet.
The existing tab must have these headers in A1:E1: `Time (UTC)`, `Run ID`, `Level`, `Step`, `Message`.
Each execution has a unique Run ID. Existing history is preserved. Steps include Google access, login, report opening, Excel download, upload, verification, retries and completion/failure.
Logging access is checked before report changes. If a later log write fails, rows are retried and any remaining sanitized messages are printed to the Actions log before exit. No log artifact is uploaded. Authentication failures before Google access is available can only be recorded in Actions output.

This version uses a fresh non-persistent browser context (`launch` + `new_context`) and waits for `download.path()` before `save_as`, matching the successful local export flow. Headless login never waits for manual keyboard input. It can request an SMS code and complete a recognized verification form using the configured SMS spreadsheet.

## Manual run

Open Actions > NYCHA Prep AppFolio > Run workflow. First select `validate`.
This checks Google access, tabs and credential layout; it does not test AppFolio login.
Select `refresh` to log into AppFolio, download reports and update the three tabs.
Only explicit dispatch runs this workflow; pushes do not trigger a refresh.

Each GitHub runner starts with a fresh browser profile. SMS MFA is handled through the SMS spreadsheet as described below. If AppFolio requests another verification method, CAPTCHA or blocks runner access, the unattended login will fail rather than wait for input. This requires resolving the login policy or using an appropriately configured self-hosted runner; headless mode does not bypass it.
Concurrency prevents overlapping runs of this workflow but does not coordinate with external NYCHA jobs. GitHub may replace an older pending run when another is queued.
No reports, screenshots or browser profiles are uploaded as Actions artifacts. Temporary runtime data is cleaned up on normal exit and errors, and the hosted runner is disposable.

## API trigger

Use a fine-grained GitHub token scoped to this repository with Actions: write permission (keep it in the calling service's secret storage).
Send an authenticated POST to:

```text
https://api.github.com/repos/gari1981/Nycha_Prep_AppFolio/actions/workflows/appfolio.yml/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_TOKEN
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28
```

Body:

```json
{"ref":"main","inputs":{"mode":"refresh"}}
```

Use `validate` instead of `refresh` for a read-only Google validation run. The workflow file must exist on the default branch.
The dispatch response acknowledges the request; check Actions for the actual run result.
This is an authenticated API trigger, not an anonymous webhook. A service unable to supply headers and this JSON needs an intermediary.

References: [GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event), [Playwright Python CI](https://playwright.dev/python/docs/ci).

## SMS verification

The Google account must also be able to read the SMS Handler spreadsheet configured in appfolio_sms.py, tab SMS. On Send Verification Code, the script snapshots the last row, selects SMS if available, sends once and waits five seconds. It polls every five seconds for up to 120 seconds for a changed/new last row with changed column C. It takes the first six ASCII digits (preserving leading zeros), fills a recognized code input and clicks a recognized verification button once. Old messages are not reused, and codes are not written to logs. Concurrent SMS requests for other accounts must not be routed into this same unfiltered inbox during a run; no recipient/account column has been specified.

If the verification form cannot be recognized, check the log and provide its labels for a targeted selector update. Local interactive runs remain open for manual completion on an SMS automation error. Headless runs fail with a sanitized error. No live SMS send or login was executed during package preparation.
