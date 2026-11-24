# Google Ads search terms review

Your assistant reviews the search-term detail rows in your Google Ads export, groups them by likely intent and reports historical spend on proposed exclusions when the costs share a confirmed currency. The scripts recompute totals and compare proposed negatives with the supplied keywords and protected searches using literal text. Each proposed exclusion includes a reason and a match type. You decide what to add in Google Ads. Use this skill in an assistant that can run Python on your computer. The bundled scripts use Python's standard library and make no network calls. Your assistant's provider processes the export under its data terms. The skill needs no separate account or Google Ads connection.

## Install

Python 3.10+ must be on the path for the commands shown here, or your assistant can use the interpreter’s full path. If local Python cannot run, quote available searches in source order under **Unchecked** for manual review. Give no intent labels, totals or negative keywords.

- **Claude Code.** Copy the entire `skills/google-ads-search-terms-review/` folder into `.claude/skills/` in your project or `~/.claude/skills/` for your own projects. Use `/google-ads-search-terms-review` or ask Claude to review the report.
- **Other assistants that read skills.** Copy or import that same folder into the assistant's skills location. Keep `SKILL.md`, `references/` and `scripts/` together. Use a host that can read the export and run local Python 3.10 or later.

In Google Ads, open **Campaigns → Insights and reports → Search terms**. Export a CSV or Excel .xlsx file, or paste the full comma-, semicolon- or tab-separated text. Save older .xls files as .xlsx or CSV first. Use English report headings. The reader accepts periods or commas for decimals when the number format is clear. Your assistant asks you to confirm the column mapping before reporting figures. Include a `Search term` column, and Cost, Clicks, Conversions, Campaign, Ad group and Keyword if available. For `.xlsx`, supply one report sheet. The `Keyword` column identifies reported matches; for a paste-ready list, supply or confirm the full keyword inventory for the campaigns or ad groups where you intend to apply it. Tell the assistant what you sell and where.

## Example: made-up account

The [example export](tests/example-made-up-account.csv) shows 8 searches costing $1,420.50 in October. Two job searches cost $90. The phrase negative `"jobs"` covers both. The assistant could also suggest `[free plumbing video]`. The checker rejects `"free"` because it literally matches the keyword and converting search for a free estimate. The assistant leaves “pipe help” for a person to check because it could mean advice or a service call. For this example, pass `--row-type 13=summary --row-type 14=summary --row-type 15=summary` after checking the three summary records.

## What the review can tell

Show the source record number and search text for every skipped summary. Check each against the original report. If its type is uncertain, withhold full-export totals and paste-ready negatives until it is confirmed. Pass the same confirmed `--row-type` decisions to every subsequent totals, row-listing and conflict-check run. The scripts ignore reviewer-decision columns in the export. Clear uploaded decisions in a working copy before the first run. Ask the user to confirm disputed records if the original report is inconclusive. Supply each confirmed decision as `--row-type RECORD=detail` or `--row-type RECORD=summary`. Repeat the option for additional decisions and use the row IDs from the confirmed run.

Read the column mapping from the confirmed report header once. A search whose text is `search term` remains a detail row. Check the displayed currency of every Excel cost against its currency code before reporting total, bucket or exclusion spend, spend share or a 30-day figure. Report spend only after confirming that the costs being added share one currency. Check both currency codes and symbols in the source. If they conflict or leave a row’s currency unresolved, report row counts and withhold the combined spend, spend share and 30-day figure. Request a separate export for each currency.

Confirm the date order for slash-formatted dates or request ISO dates (`YYYY-MM-DD`). If a supplied detail date is unreadable, ambiguous or outside the stated window, identify the row and withhold period-specific spend and the 30-day figure until the date and window are reconciled.

The assistant reads the words and judges likely intent. The scripts total the detail rows, check proposed negatives for literal conflicts with protected searches and supplied keywords, and require a nonempty reason for each exclusion. The assistant judges whether the reason fits the search and business context.

The export is a snapshot. It cannot show who became a customer, live bids, budgets, change history or searches and keywords outside the file and any list you supplied. The assistant knows about your business from what you tell it. It can misread words and miss searches worth excluding. Check high-spend rows before excluding them.

Pipeyield offers a free web review. https://pipeyield.com
