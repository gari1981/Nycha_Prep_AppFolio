# NYCHA Prep AppFolio

Download three saved AppFolio reports and replace their Google Sheets upload tabs using headless Chromium.
AppFolio credentials are read from `Login!A1:Z20` in a separate spreadsheet, using the same authorized Google account.
No NYCHA portal status check is performed. A refresh replaces live data and may partially complete if an upload fails.

## Setup

Upload this directory's contents, including `.github/workflows/appfolio.yml`, to the repository's default branch (`main` in the API examples).
Do not upload the ZIP itself. No local browser profile, downloaded report, OAuth client file or token belongs in source control.

In Settings > Secrets and variables > Actions, create a repository **secret**:

- `GOOGLE_TOKEN_JSON`: the complete contents of the working local `.secrets/google_token_new.json` file. This is the authorized-user token, not `google_oauth_client.json`. It must contain `refresh_token`, `client_id`, `client_secret` and `token_uri`.

Create these repository **variables**:

- `NYCHA_PREP_SPREADSHEET_ID`: the destination NYCHA PREP MASTERSHEET ID.
- `APPFOLIO_LOGIN_SPREADSHEET_ID`: the spreadsheet ID containing the `Login` tab.
- `APPFOLIO_URL`: your organization's full AppFolio URL (`https://...appfolio.com`).

The Google account must be able to read the Login spreadsheet and edit the destination spreadsheet.
The existing Google token already includes OAuth client information, so no second OAuth-client secret is required.
If Google revokes the refresh token, authorize locally again and replace the GitHub secret.

## Manual run

Open Actions > NYCHA Prep AppFolio > Run workflow. First select `validate`.
This checks Google access, tabs and credential layout; it does not test AppFolio login.
Select `refresh` to log into AppFolio, download reports and update the three tabs.
Only explicit dispatch runs this workflow; pushes do not trigger a refresh.

Each GitHub runner starts with a fresh browser profile. If AppFolio requires MFA, CAPTCHA or blocks runner access, the unattended login will fail rather than wait for input. This requires resolving the login policy or using an appropriately configured self-hosted runner; headless mode does not bypass it.
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
