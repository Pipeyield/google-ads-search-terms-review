# Google Ads search terms review

Give your assistant a Google Ads search terms export. Your assistant flags searches that look off-target and gives a reason for each proposed exclusion. It suggests negative keywords with match types and checks them for literal conflicts with protected searches and supplied keywords. You decide what to add in Google Ads.

**Example (made-up plumbing account, October):** about $140 of $1,420.50 (9.9%) goes to 4 searches you can exclude, for example two job searches covered by one phrase negative `"jobs"`. The checker rejected the phrase negative `"free"` because it matched the keyword `"free estimate"` and the converting search `free estimate plumber`. The vague search "pipe help" is left for you to judge. See the [example export](tests/example-made-up-account.csv).

## Install (3 steps)

1. Copy the `skills/google-ads-search-terms-review/` folder into `.claude/skills/` (this project) or `~/.claude/skills/` (all projects). Other assistants that read skills: import the same folder and keep `SKILL.md`, `references/` and `scripts/` together.
2. In Google Ads, open **Campaigns → Insights and reports → Search terms**. Choose a date range, such as the last 90 days, and download a CSV or Excel `.xlsx` file.
3. Attach or paste the report and ask your assistant to review it. In Claude Code, you can also run `/google-ads-search-terms-review`. Tell it what you sell and where.

For a checked review, use an assistant that can run Python 3.10+ on your computer. The bundled scripts use only Python's standard library and make no network calls. The skill requires no Pipeyield account or sign-in and does not connect to your Google Ads account. Your assistant's provider processes the export under its own data terms.

## What it can't tell

The review uses your export, your keyword and keep lists, and the business details you provide. It cannot identify individual customers or inspect live bids, budgets, change history, or unreported searches. The assistant can misread a search. Check high-spend rows before you exclude them.

## Details: inputs

Export a CSV or Excel .xlsx file, or paste the full comma-, semicolon- or tab-separated text. Save older .xls files as .xlsx or CSV first. Use English report headings. The reader accepts periods or commas for decimals when the number format is clear. Your assistant asks you to confirm the column mapping before reporting figures. Include a `Search term` column, and Cost, Clicks, Conversions, Campaign, Ad group and Keyword if available. For `.xlsx`, supply one report sheet. The `Keyword` column identifies reported matches; for a paste-ready list, supply or confirm the full keyword inventory for the campaigns or ad groups where you intend to apply it. Python 3.10+ must be on the path for the commands shown here, or your assistant can use the interpreter’s full path. If local Python cannot run, quote available searches in source order under **Unchecked** for manual review. Give no intent labels, totals or negative keywords.

## Details: the example

The example export has 8 searches costing $1,420.50. The phrase negative `"jobs"` covers two job searches costing $90. The assistant could also suggest `[free plumbing video]`. For this example, pass `--row-type 13=summary --row-type 14=summary --row-type 15=summary` after checking the three summary records.

## Details: input checks and limits

Show the source record number and search text for every skipped summary. Check each against the original report. If its type is uncertain, withhold full-export totals and paste-ready negatives until it is confirmed. Pass the same confirmed `--row-type` decisions to every subsequent totals, row-listing and conflict-check run. The scripts ignore reviewer-decision columns in the export. Clear uploaded decisions in a working copy before the first run. Ask the user to confirm disputed records if the original report is inconclusive. Supply each confirmed decision as `--row-type RECORD=detail` or `--row-type RECORD=summary`. Repeat the option for additional decisions and use the row IDs from the confirmed run.

Read the column mapping from the confirmed report header once. A search whose text is `search term` remains a detail row. Check the displayed currency of every Excel cost against its currency code before reporting total, bucket or exclusion spend, spend share or a 30-day figure. Report spend only after confirming that the costs being added share one currency. Check both currency codes and symbols in the source. If they conflict or leave a row’s currency unresolved, report row counts and withhold the combined spend, spend share and 30-day figure. Request a separate export for each currency.

Confirm the date order for slash-formatted dates or request ISO dates (`YYYY-MM-DD`). If a supplied detail date is unreadable, ambiguous or outside the stated window, identify the row and withhold period-specific spend and the 30-day figure until the date and window are reconciled.

The assistant reads the words and judges likely intent. The scripts total the detail rows, check proposed negatives for literal conflicts with protected searches and supplied keywords, and require a nonempty reason for each exclusion. The assistant judges whether the reason fits the search and business context.

The export is a snapshot. It cannot show who became a customer, live bids, budgets, change history or searches and keywords outside the file and any list you supplied. The assistant knows about your business from what you tell it. It can misread words and miss searches worth excluding. Check high-spend rows before excluding them.

Pipeyield offers a free web review. https://pipeyield.com/review?utm_source=github&utm_content=skill
