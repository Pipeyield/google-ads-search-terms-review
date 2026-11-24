# Negative match types

| Match type | What it blocks | Negative | Example it blocks | Example it leaves |
| --- | --- | --- | --- | --- |
| Exact | The same words in the same order with no extra words | `[plumber jobs]` | `plumber jobs` | `local plumber jobs` |
| Phrase | The words together in order, with extra words allowed | `"trade school"` | `local trade school courses` | `school for a trade` |
| Broad | All the words anywhere in the search, in any order | `plumber jobs` | `jobs for a plumber` | `local plumber` |

These rules apply to Google Search campaigns. Google accounts for capitalization and misspellings. Check singulars, plurals and synonyms separately. For example, `job` can leave `jobs` eligible. Accents and ampersands can affect matching. Google may still serve an ad when the negative occurs after word 16. The local checker compares literal words across the full search. Review possible spelling conflicts manually and leave coverage that depends on words after position 16 out of the spend estimate.

The checker compares a negative to literal keyword text and observed searches, including converting and kept rows. Positive keyword targeting can reach other searches. The Keyword column and any supplied keyword list define which keywords the script can check. When the export includes multiple campaigns or ad groups, the checker scans all of them for a list that could be applied across the report.

Use plain text and a separate match type in the JSON plan. The script supplies the paste syntax. Check symbols and search operators in proposed negatives, supplied bid keywords and protected searches. If Google’s treatment of a symbol could make a proposal collide with protected text, show both strings for manual review and omit the proposal and its spend until resolved. If you propose different text, audit that exact text again.
