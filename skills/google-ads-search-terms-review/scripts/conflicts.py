"""Check proposed negatives and audit the assistant's row-by-row review."""

import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import BadZipFile

from totals import (InputError, detail_dates, emit, parse_date, read_export,
                    row_type_overrides, verified_records, window_conflicts)

BUCKETS = ("buyer", "off-target", "unsure")
MATCH_TYPES = ("exact", "phrase", "broad")
WORD_LIMIT = 16
LATE_MATCH_REASON = "The literal match depends on words after word 16."
LIMITATIONS = (
    "This review uses the export, keyword and keep lists, and business details you supplied. "
    "It cannot identify individual customers or inspect live bids, budgets, change history or "
    "unreported searches. The assistant can misread intent. Check high-spend rows and the "
    "intended campaign or ad group before applying a negative. The review lists the checks "
    "completed and any unresolved gaps."
)
TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*\+*|[&*]", re.UNICODE)
SEARCH_OPERATOR = re.compile(r"\bOR\b|(?<!\S)(?:-(?=\w)|site:)", re.IGNORECASE)
SYMBOL_REVIEW = "Manual review required. Google can ignore plus signs or search operators. Audit any replacement text again."


def words(text):
    normalized = unicodedata.normalize("NFC", text.lower()).replace(".", "").replace("’", "'")
    return tuple(TOKEN.findall(normalized))


def contains(sequence, fragment):
    return bool(fragment) and any(
        sequence[i:i + len(fragment)] == fragment
        for i in range(len(sequence) - len(fragment) + 1)
    )


def blocks(negative, match_type, query):
    if match_type == "exact":
        return query == negative
    if match_type == "phrase":
        return contains(query, negative)
    return set(negative).issubset(query)


def required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} must be nonempty text")
    return value.strip()


def keyword_text(keyword):
    if (keyword.startswith("[") and keyword.endswith("]")) or (
        keyword.startswith('"') and keyword.endswith('"')
    ):
        return keyword[1:-1]
    return keyword


def symbol_forms(text):
    """Possible readings for collision review only. Never use these to approve coverage."""
    if "+" not in text and not SEARCH_OPERATOR.search(text):
        return ()
    stripped = SEARCH_OPERATOR.sub(" ", text)
    omitted = re.sub(r"(?<!\S)(?:-\w|site:)\S*", " ", text, flags=re.IGNORECASE)
    readings = {words(text)}
    for reading in (stripped, SEARCH_OPERATOR.sub(" ", omitted)):
        readings.add(words(reading.replace("+", " ")))
        readings.add(words(reading.replace("+", "")))
    return tuple(form for form in readings if form)


def proposal(value):
    if not isinstance(value, dict):
        raise InputError("each negative must give negative and match_type")
    text = value.get("negative")
    match_type = required_text(value.get("match_type"), "match_type").lower()
    if match_type not in MATCH_TYPES:
        raise InputError(f"unknown negative match type {match_type!r}")
    if isinstance(text, str):
        if re.search(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]", text):
            return text, match_type, "The proposal contains a line break."
        for size, limit, unit in ((len(text.split()), 10, "whitespace-separated words"),
                                  (len(text), 80, "characters"),
                                  (len(text.encode("utf-8")), 255, "UTF-8 bytes")):
            if size > limit:
                return text, match_type, f"Manual review required. The proposal exceeds Google's limit of {limit} {unit}."
        if "+" in text or SEARCH_OPERATOR.search(text):
            return text, match_type, SYMBOL_REVIEW
        if len(words(text)) > 10:
            return text, match_type, ("Manual review required. The local punctuation check counts more than "
                                      "10 word tokens.")
    text = required_text(text, "negative")
    # Reject syntax, operators and characters whose matching is outside this review.
    if not words(text) or re.search(r'[^\w\s.&*+\-\'’]|_', text):
        raise InputError(f"use plain keyword text without match wrappers or operators: {text!r}")
    return text, match_type, None


def decision_map(plan, rows):
    if "rows" not in plan:
        return None
    decisions = {}
    if not isinstance(plan["rows"], list):
        raise InputError("rows must be a list")
    for item in plan["rows"]:
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise InputError("every row decision must give its integer id")
        row_id = item["id"]
        if row_id in decisions:
            raise InputError(f"duplicate decision for row {row_id}")
        if item.get("bucket") not in BUCKETS:
            raise InputError(f"row {row_id} needs one valid bucket")
        for key in ("keep_reason", "exclude_reason", "check_reason"):
            if key in item:
                required_text(item[key], f"row {row_id} {key}")
        decisions[row_id] = item
    expected = {row["id"] for row in rows}
    if decisions.keys() != expected:
        raise InputError(f"decisions must cover every row id; missing {sorted(expected - decisions.keys())}, "
                         f"unknown {sorted(decisions.keys() - expected)}")
    return decisions


def spend(rows, summary):
    if summary["cost"] is None:
        return None
    return sum((row["cost"] for row in rows), Decimal(0))


def report_period(plan, summary, rows):
    if "period" in plan:
        period = plan["period"]
        if not isinstance(period, list) or len(period) != 2 or not all(isinstance(day, str) for day in period):
            raise InputError("period must be [start, end] for the dates the person confirmed")
        start, end = map(parse_date, period)
    elif summary["date_range_source"] == "report header":
        start, end = map(date.fromisoformat, summary["date_range"])
    else:
        return None, []
    if end < start:
        raise InputError("review period ends before it starts")
    conflicts = []
    dates = detail_dates(rows, conflicts)
    conflicts.extend(window_conflicts(dates, start, end))
    if summary["date_range_source"] == "report header" and [start.isoformat(), end.isoformat()] != summary["date_range"]:
        first, last = summary["date_range"]
        conflicts.append(f"Confirmed window {start} to {end} differs from report header {first} to {last}.")
    if conflicts:
        return None, conflicts
    return (start.isoformat(), end.isoformat(), (end - start).days + 1), []


def audit(summary, rows, plan):
    if isinstance(plan, list):
        plan = {"negatives": plan}
    if not isinstance(plan, dict) or not isinstance(plan.get("negatives"), list):
        raise InputError("supply a list of negatives, or an object with a negatives list")
    decisions = decision_map(plan, rows)
    negatives = [proposal(value) for value in plan["negatives"]]
    supplied = plan.get("keywords", [])
    kept = plan.get("kept_searches", [])
    if not isinstance(supplied, list) or not isinstance(kept, list):
        raise InputError("keywords and kept_searches must be lists of text")
    keyword_values = defaultdict(list)
    for row in rows:
        if row.get("keyword"):
            keyword_values[row["keyword"]].append(row["id"])
    for value in supplied:
        keyword_values.setdefault(required_text(value, "keyword"), [])
    keywords = [(value, words(keyword_text(value)), ids) for value, ids in keyword_values.items()]
    if any(not tokens for _, tokens, _ in keywords):
        raise InputError("a keyword has no readable words; verify the keyword list")
    kept = [(required_text(value, "kept search"), words(value)) for value in kept]
    sources = [name for name, present in (
        ("Keyword column", any(keyword_values.values())), ("supplied keyword list", bool(supplied))
    ) if present]
    blockers = []
    if summary["ambiguous_records"]:
        blockers.append("Verify ambiguous summary records against the source before using totals or a paste list.")
    if not sources:
        blockers.append("No keyword inventory is available. Supply the Keyword column or a keyword list.")
    if "conversions" in summary["missing"] or any(
        row.get("conversions") is None for row in rows
    ):
        blockers.append("Conversion data is missing or unreadable in at least one row.")
    prepared = []
    # Preserve the source text and literal tokens alongside any uncertain readings.
    targets = [(tokens, symbol_forms(keyword_text(value)), {"kind": "keyword", "text": value, "row_ids": ids})
               for value, tokens, ids in keywords]
    for row in rows:
        tokens = words(row["search_term"])
        decision = decisions[row["id"]] if decisions is not None else {}
        protected = []
        if row.get("conversions") and row["conversions"] > 0:
            protected.append("Search recorded a conversion.")
        for keyword, keyword_tokens, _ in keywords:
            if contains(tokens, keyword_tokens):
                protected.append(f"Search contains keyword {keyword!r}.")
        if decision.get("bucket") == "buyer":
            protected.append("The assistant marked this as a buyer search.")
        if decision.get("keep_reason"):
            protected.append(decision["keep_reason"])
        if any(tokens and tokens == kept_tokens for _, kept_tokens in kept):
            protected.append("Search is on the supplied keep list.")
        if decisions is not None and not tokens:
            protected.append("The script found no readable words in this search.")
        reason = decision.get("exclude_reason")
        eligible = (decision.get("bucket") == "off-target" and bool(reason)
                    and not decision.get("check_reason") and not protected)
        prepared.append((row, tokens, decision, protected, eligible))
        if row.get("conversions") and row["conversions"] > 0:
            kind = "converting search"
        elif protected:
            kind = "kept search"
        elif decisions is not None and not eligible:
            kind = "unapproved search"
        else:
            continue
        search = row["search_term"]
        targets.append((tokens, symbol_forms(search), {
            "kind": kind, "text": search, "row_id": row["id"],
            "reasons": protected or [decision.get("check_reason") or "No eligible exclusion with a reason."],
        }))
    targets.extend((tokens, symbol_forms(value), {"kind": "kept search", "text": value}) for value, tokens in kept)

    checked = []
    covered = set()
    late_match_ids = set()
    rejection_by_row = {}
    for text, match_type, rejection in negatives:
        tokens = words(text)
        forms = symbol_forms(text)
        collisions = []
        for target_tokens, target_forms, source in targets:
            if (forms or target_forms) and any(
                candidate and blocks(candidate, match_type, target)
                for candidate in forms or (tokens,) for target in target_forms or (target_tokens,)
            ):
                collisions.append(dict(source, reason=SYMBOL_REVIEW))
                rejection = rejection or SYMBOL_REVIEW
            elif tokens and blocks(tokens, match_type, target_tokens):
                collisions.append(source)
        if rejection:
            for row, search_tokens, _, _, eligible in prepared:
                if eligible and tokens and blocks(tokens, match_type, search_tokens):
                    rejection_by_row[row["id"]] = rejection
            checked.append({
                "negative": text, "match_type": match_type, "approved": False, "reason": rejection,
                "covers": [], "spend": spend([], summary), "collisions": collisions, "uncertain_matches": [],
            })
            continue
        matching = []
        uncertain_matches = []
        for row, search_tokens, _, _, _ in prepared:
            if not blocks(tokens, match_type, search_tokens):
                continue
            if len(search_tokens) > WORD_LIMIT and not blocks(tokens, match_type, search_tokens[:WORD_LIMIT]):
                late_match_ids.add(row["id"])
                uncertain_matches.append({"id": row["id"], "search_term": row["search_term"],
                                          "reason": LATE_MATCH_REASON})
            else:
                matching.append(row)
        approved = decisions is not None and bool(matching) and not collisions and not blockers
        if approved:
            covered.update(row["id"] for row in matching)
        checked.append({
            "negative": text, "match_type": match_type, "collisions": collisions,
            "uncertain_matches": uncertain_matches,
            "covers": [{"id": row["id"], "search_term": row["search_term"]} for row in matching],
            "spend": spend(matching, summary), "approved": approved,
        })

    period, conflicts = report_period(plan, summary, rows)
    warnings = list(dict.fromkeys(summary["warnings"] + conflicts))
    result = {"keyword_sources": sources, "blockers": blockers, "negatives": checked, "warnings": warnings,
              "cannot_tell": LIMITATIONS}
    if decisions is None:
        return result
    exclusions = [dict(row, reason=decision["exclude_reason"])
                  for row, _, decision, _, _ in prepared if row["id"] in covered]
    manual = []
    for row, _, decision, protected, _ in prepared:
        if row["id"] in covered:
            continue
        if decision["bucket"] == "unsure" or decision.get("check_reason") or (
            decision["bucket"] == "off-target" and not protected
        ):
            reason = decision.get("check_reason") or rejection_by_row.get(row["id"])
            if not reason and row["id"] in late_match_ids:
                reason = LATE_MATCH_REASON
            if not reason:
                reason = "Intent is unclear." if decision["bucket"] == "unsure" else (
                    "No approved negative covers this row." if decision.get("exclude_reason")
                    else "No exclusion reason was recorded."
                )
            manual.append(dict(row, reason=reason))
    cost = spend(exclusions, summary) if not blockers and not conflicts else None
    total_cost = summary["cost"]
    groups = {bucket: [] for bucket in BUCKETS}
    for row, _, decision, _, _ in prepared:
        groups[decision["bucket"]].append(row)
    result.update({
        "buckets": {
            bucket: {"rows": len(group), "spend": spend(group, summary)}
            for bucket, group in groups.items()
        },
        "protected": [{"id": row["id"], "search_term": row["search_term"], "reasons": reasons}
                      for row, _, _, reasons, _ in prepared if reasons],
        "manual_check": manual,
        "exclusions": exclusions,
        "headline": {
            "row_count": len(exclusions), "spend": cost, "period": list(period[:2]) if period else None,
            "share_percent": cost * 100 / total_cost if cost is not None and total_cost else None,
            "monthly_30_day_estimate": cost * 30 / period[2] if cost is not None and period else None,
        },
        "paste_list": [
            {"exact": f"[{item['negative']}]", "phrase": f"\"{item['negative']}\"",
             "broad": item["negative"]}[item["match_type"]]
            for item in checked if item["approved"]
        ],
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", help="the same export used for totals.py")
    parser.add_argument("proposals", help="JSON file with negatives and optional row decisions")
    row_type_overrides(parser)
    args = parser.parse_args()
    try:
        summary, rows = read_export(args.export, row_types=verified_records(args.row_type))
        plan = json.loads(Path(args.proposals).read_text(encoding="utf-8-sig"))
        result = audit(summary, rows, plan)
    except (OSError, ValueError, KeyError, OverflowError, IndexError, ArithmeticError, BadZipFile, ET.ParseError) as error:
        parser.exit(2, f"{error}\n{LIMITATIONS}\n")
    emit(result)


if __name__ == "__main__":
    main()
