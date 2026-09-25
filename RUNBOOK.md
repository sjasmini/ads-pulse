# Ads Pulse — scheduled run

A scheduled run is started with: **"Read RUNBOOK.md and follow it in DAILY mode"** (or **WEEKLY** mode).
Follow these steps in order. Nobody is watching the run, so don't ask questions: follow the hard rules and stop when one says to.

## Hard rules

1. **Never email the team if a required step fails, the totals don't reconcile, or the newest data date is not yesterday (IST).** Send `recipients.me` a short failure note instead (see step 8) and stop.
2. **Never retype data.** Every Windsor / Drive result is copied into `raw/` with `tools/save_tool_result.py` or `tools/save_drive_csv.py`, which read the exact tool output from the session transcript (or the file the harness saved when the result was too large). Never write numbers into a file yourself, and never hand-copy base64.
3. **Never commit `raw/`** (it is in `.gitignore`; check before committing).
4. Team emails go out only when `settings.json → recipients.team_mode` is `true`. When it is `false`, every email goes to `recipients.me` only. Don't change this flag yourself.
5. Before any Windsor pull, call `get_fields` for `facebook` and `google_ads` with the fields below and use only field ids it returns. If a field is gone, stop and send the failure note.
6. The emails never include images or attachments (the Gmail connector strips `<img>` and turns inline images into unreadable attachments).

## 0. Setup

- `cd` into the repo root. Run `git pull` so `history/` has yesterday's snapshot.
- Read `settings.json`: `meta_accounts` (ids), `google_accounts` (ids), `recipients`, `dashboard_url`, `sheets`.
- `rm -f raw/*.json raw/sheets/*.csv` so no stale file can be used.

## 1. Windsor pulls (connector ids: `facebook`, `google_ads`)

Use `accounts` = every id in `meta_accounts` (Meta) or `google_accounts` (Google). After **each** call, save it
immediately with the command shown (`--match` strings pick the right call out of the transcript):

| # | connector | fields | date_preset | filters | save with |
|---|---|---|---|---|---|
| 1 | facebook | account_name, campaign, date, spend, actions_lead, ctr, frequency | last_11d | – | `python3 tools/save_tool_result.py --out raw/campaign_days.json --match '"frequency"'` |
| 2 | facebook | account_name, date, spend, actions_lead | last_11d | – | `python3 tools/save_tool_result.py --out raw/totals.json --match '"fields": ["account_name", "date", "spend", "actions_lead"]'` |
| 3 | facebook | account_name, campaign, campaign_status, campaign_daily_budget, adset_name, adset_effective_status, adset_daily_budget | last_1d | – | `python3 tools/save_tool_result.py --out raw/status.json --match '"campaign_daily_budget", "adset_name"'` |
| 3b | facebook | account_name, campaign, adsset_optimization_goal, adset_destination_type, spend | last_11d | `[["spend","gt",0]]` | `python3 tools/save_tool_result.py --out raw/goals.json --match '"adset_destination_type", "spend"]' --match last_11d` |
| 4 | facebook | account_name, campaign, date, spend, actions_lead, actions_landing_page_view, actions_offsite_conversion_fb_pixel_lead, actions_offsite_conversion_fb_pixel_complete_registration, actions_offsite_conversion_fb_pixel_custom, actions_onsite_conversion_messaging_conversation_started_7d | last_11d | `[["spend","gt",0]]` | `python3 tools/save_tool_result.py --out raw/messaging.json --match '"actions_landing_page_view"'` |
| 5 | facebook | account_name, campaign, publisher_platform, platform_position, spend, impressions, actions_link_click, actions_lead | last_7d | `[["spend","gt",0]]` | `python3 tools/save_tool_result.py --out raw/placement.json --match '"platform_position"'` |
| 6 | facebook | account_name, campaign, adset_id, adset_name, adset_effective_status, adset_daily_budget, adset_bid_strategy, adset_bid_amount, adsset_optimization_goal, adset_destination_type, adset_targeting, adset_updated_time, ad_id, ad_name, status, ad_created_time, spend | last_1d | – | `python3 tools/save_tool_result.py --out raw/config.json --match '"adset_targeting"'` |
| 7 | facebook | account_name, campaign, adset_id, adset_name, ad_id, ad_name, date, spend, impressions, actions_link_click, actions_lead | last_14d | `[["spend","gt",0]]` | `python3 tools/save_tool_result.py --out raw/ad_days.json --match '"adset_name", "ad_id"' --match last_14d` |
| G1 | google_ads | account_name, account_id, campaign, campaign_status, advertising_channel_type, bidding_strategy_type, date, cost, conversions, clicks, impressions, budget_amount | last_11d | `[["cost","gt",0]]` | `python3 tools/save_tool_result.py --out raw/google_campaign_days.json --match '"advertising_channel_type", "bidding_strategy_type"'` |
| G2 | google_ads | account_name, date, cost, conversions | last_11d | – | `python3 tools/save_tool_result.py --out raw/google_totals.json --match '"fields": ["account_name", "date", "cost", "conversions"]'` |
| F1 | facebook | account_name, campaign, spend, actions_lead | last_60d | `[["spend","gt",0]]` | `python3 tools/save_tool_result.py --out raw/spend_60d.json --match '"actions_lead"]' --match last_60d` |
| F2 | google_ads | account_name, campaign, cost, conversions | last_60d | `[["cost","gt",0]]` | `python3 tools/save_tool_result.py --out raw/google_spend_60d.json --match '"cost", "conversions"]' --match last_60d` |

Notes: `adsset_optimization_goal` has a double "s". Status (#3) only returns delivering entities, so a missing campaign
means "not delivering". Large results are saved by the harness to a file; the save script finds and copies that file.
If a save command prints "no matching tool result", repeat the pull once, then treat it as a failed required step.

## 2. Sheets (optional)

Only if `settings.json → sheets.funnel_file_id` / `creative_file_id` is set (the funnel sheet is the PII-free LeadSquared extract; F1/F2 above give the matching 60-day spend): call Google Drive `download_file_content`
with that `fileId` and `exportMimeType: "text/csv"` (first tab only), then
`python3 tools/save_drive_csv.py --name funnel --match '<fileId>'` (or `--name creative`).
If the helper refuses (personal-data columns), the sheet shows #REF!/#ERROR! instead of a header row, or the download fails, skip it and say so in the log and in the email's Update section ("Lead funnel sheet not readable today") — the funnel step is optional and must never block the email.
Never open tabs with names, phone numbers or emails.

## 3. Pipeline

`python3 run_pipeline.py daily` (or `weekly`). It must print **PIPELINE OK**.
It exits non-zero if totals don't reconcile (spend ±0.5, Meta leads exact, Google conversions ±0.01) or the newest data
date isn't yesterday → rule 1. Warnings about optional steps are fine; mention them in the log.

## 4. Republish the dashboard (same URL every run)

`settings.json → dashboard_url` is the artifact. Call Artifact `action: "read"` on that URL first (a scheduled
session has not seen it yet), then Artifact publish with `url` = dashboard_url and `file_path` = `out/dashboard.html`.
Omit `icon` and `capabilities` so the page keeps its icon and its `sample` capability. If publishing fails, keep going
(the email still goes) and note it in the log and the email is sent without a changed link.

## 5. Send the email (Gmail connector)

Read `out/email.json`: `subject`, `html_file`, `to`, `cc`. Send ONE message from the connected Gmail with the contents of
`html_file` as the **HTML body** (not an attachment), subject exactly as given, to/cc exactly as given. `email.json`
already applies `team_mode`; never add recipients.

## 6. Log

Append one line to `history/run_log.md`:
`- <IST date time> <MODE> data <data_date> | PIPELINE OK | emailed <to> | dashboard <published/failed> | <warnings>`

## 7. Commit and push

```
git add history/ reports/*.json settings.json
git status --short | grep '^A.*raw/' && exit 1   # never commit raw/
git commit -m "Ads Pulse <MODE> run for <data_date>"
git push
```
Change detection needs yesterday's snapshot, so a run that skips this breaks tomorrow's change scoring.

## 8. Failure note (instead of the team email)

To `recipients.me` only, subject `Ads Pulse <MODE> run did not send (<today IST>)`, plain short body:
which step failed, the error line, the newest data date seen, and "No team email was sent." Then log it (step 6)
and commit `history/run_log.md` only.
