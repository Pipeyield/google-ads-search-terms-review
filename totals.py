"""Read a Google Ads search terms export and recompute its detail totals."""

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import io
import json
from pathlib import Path
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

COLUMNS = {
    "search term": "search_term",
    "cost": "cost",
    "clicks": "clicks",
    "conversions": "conversions",
    "campaign": "campaign",
    "ad group": "ad_group",
    "ad group name": "ad_group",
    "keyword": "keyword",
    "match type": "match_type",
    "added/excluded": "added_excluded",
    "date": "date",
    "day": "date",
    "start date": "start_date",
    "end date": "end_date",
    "currency code": "currency",
    "currency": "currency",
}
METRICS = ("cost", "clicks", "conversions")
# Common export columns that do not participate in this review.
IGNORED_COLUMNS = {"", "impressions", "impr.", "ctr", "avg. cpc", "conv. rate",
                   "conversion rate", "cost / conv.", "conv. value", "all conv.",
                   "all conv. value", "campaign type", "network", "device"}
REVIEW_COLUMN = re.compile(
    r"(?:(?:verified|confirmed|reviewed|reviewer|manual) )*(?:(?:row|record) )?"
    r"(?:type|role|classification|decision)(?: field)?|bucket|(?:keep|check|exclude) reason"
)
HEADER_ERROR = "Please supply the report with English column headings including Search term."
NUMBER_ERROR = "Please export the metrics without thousands separators and with one consistent decimal separator."
SWAP_SEPARATORS = str.maketrans(".,", ",.")
SYMBOL_CODES = {
    "$": {"USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN", "TWD"},
    "€": {"EUR"}, "£": {"GBP", "GIP", "FKP", "SHP"}, "¥": {"JPY", "CNY"},
}
DETAIL_FIELDS = {"campaign", "ad_group", "keyword", "match_type", "added_excluded", "date"}
NUMBER = re.compile(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?P<sep>[, \u00a0\u202f])[0-9]{3}(?:(?P=sep)[0-9]{3})*)(?:\.[0-9]+)?")
MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")
MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class InputError(ValueError):
    """The input does not support a reliable result."""


def numeric_text(value):
    cleaned = value.strip()
    if cleaned and cleaned[0] in SYMBOL_CODES:
        cleaned = cleaned[1:].strip()
    return cleaned


def cost_currency(value, code):
    code = code.upper()
    symbol_codes = SYMBOL_CODES.get(value[:1])
    if code:
        if re.fullmatch("[A-Z]{3}", code) and (symbol_codes is None or code in symbol_codes):
            return code
    elif symbol_codes and len(symbol_codes) == 1:
        return next(iter(symbol_codes))
    return None


def parse_number(value, decimal_comma=False):
    cleaned = numeric_text(value)
    if decimal_comma:
        cleaned = cleaned.translate(SWAP_SEPARATORS)
    if not NUMBER.fullmatch(cleaned):
        raise InputError(f"unrecognized or missing number {value!r}")
    return Decimal(re.sub(r"[, \u00a0\u202f]", "", cleaned))


def uses_decimal_comma(records, fields, spreadsheet):
    indexes = [i for i, field in enumerate(fields) if field in METRICS]
    dot_possible = comma_possible = True
    saw_separator = False
    for record in records:
        for i in indexes:
            if i >= len(record):
                continue  # Row width is checked when reading the record.
            value = numeric_text(record[i])
            if "." not in value and "," not in value:
                continue
            saw_separator = True
            if spreadsheet:
                # Excel stores numeric cells with a period regardless of display locale.
                # Ask for numeric cells if text would require a locale assumption.
                if "," in value:
                    raise InputError("Please save the Excel metrics as numeric cells or export a delimited text report.")
                continue
            dot_possible = dot_possible and bool(NUMBER.fullmatch(value))
            comma_possible = comma_possible and bool(NUMBER.fullmatch(value.translate(SWAP_SEPARATORS)))
            if not (dot_possible or comma_possible):
                raise InputError(NUMBER_ERROR)
    if saw_separator and not spreadsheet and dot_possible and comma_possible:
        raise InputError(NUMBER_ERROR)
    return not dot_possible


def parse_date(value):
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
        first, second, year = map(int, value.split("/"))
        if first != second and 1 <= first <= 12 and 1 <= second <= 12:
            raise InputError(f"ambiguous date {value!r}; confirm the date order and supply ISO dates")
        month, day = (second, first) if first > 12 else (first, second)
        return date(year, month, day)
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", value)
    if match:
        month, day, year = match.groups()
        for i, name in enumerate(MONTHS, 1):
            if month.lower() in (name, name[:3]):
                return date(int(year), i, int(day))
    raise InputError(f"unrecognized date {value!r}")


def preamble_range(lines):
    for cells in lines:
        line = ",".join(cells).strip()
        line = re.sub(r"^(?:date range|date)\s*[:,]\s*", "", line, flags=re.I)
        parts = re.split(r"\s+to\s+|\s*[–—]\s*|\s+-\s*", line, maxsplit=1, flags=re.I)
        try:
            start, end = map(parse_date, parts if len(parts) == 2 else parts * 2)
        except ValueError:
            continue
        if end < start:
            raise InputError("the export date range ends before it starts")
        return start, end
    return None


def detail_dates(rows, warnings):
    dates = []
    for row in rows:
        for field in ("date", "start_date", "end_date"):
            if field in row:
                try:
                    dates.append((row["id"], field, parse_date(row[field])))
                except ValueError as error:
                    warnings.append(f"row {row['id']}, {field}: {error}")
    return dates


def window_conflicts(dates, start, end):
    return [f"row {row_id}, {field}: {day} is outside the reporting window {start} to {end}"
            for row_id, field, day in dates if not start <= day <= end]


def delimited(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        content = raw.decode("utf-16")
    elif raw.startswith(b"S\x00e\x00") or b"\x00" in raw[1:20:2]:
        content = raw.decode("utf-16-le")
    elif b"\x00" in raw[:20:2]:
        content = raw.decode("utf-16-be")
    else:
        content = raw.decode("utf-8-sig")
    best = None
    for delimiter in (",", "\t", ";"):
        try:
            rows = list(csv.reader(io.StringIO(content, newline=""), delimiter=delimiter, strict=True))
        except csv.Error:
            continue
        for index, row in enumerate(rows):
            fields = header(row)
            if "search_term" in fields:
                rank = (index, -sum(field is not None for field in fields))
                if best is None or rank < best[0]:
                    best = rank, rows, index, fields
                break
    if best is None:
        raise InputError(HEADER_ERROR)
    return best[1:]


def header(cells):
    return [COLUMNS.get(" ".join(cell.strip().casefold().split())) for cell in cells]


def cell_text(element):
    if element is None:
        return ""
    return "".join(child.text or "" for child in element.iter(f"{MAIN}t"))


def excel_cost_symbols(styles):
    """Return the visible currency symbol, empty for plain numbers, or None for an unresolved format."""
    custom = {int(fmt.get("numFmtId")): fmt.get("formatCode", "")
              for fmt in styles.findall(f"{MAIN}numFmts/{MAIN}numFmt")}
    symbols = []
    for style in styles.findall(f"{MAIN}cellXfs/{MAIN}xf"):
        fmt_id = int(style.get("numFmtId", "0"))
        if fmt_id in (0, 1, 2, 3, 4, 37, 38, 39, 40):
            symbols.append("")
            continue
        fmt = custom.get(fmt_id, "")
        # A locale tag can include a symbol, e.g. [$$-409] or [$€-407].
        fmt = re.sub(r"\[\$([$€£¥]?)(?:-[\w-]+)?\]", r"\1", fmt)
        markers = {char for char in fmt if char in SYMBOL_CODES}
        if fmt and len(markers) <= 1 and re.fullmatch(r'[#0?,. ();@_*!\\"$€£¥+\-]+', fmt):
            symbols.append(next(iter(markers), ""))
        else:
            symbols.append(None)
    return symbols


def excel_cells(row, strings):
    """Yield each cell at its worksheet column, including gaps in sparse rows."""
    index = 0
    for cell in row.findall(f"{MAIN}c"):
        letters = re.match(r"[A-Z]+", cell.get("r", ""))
        if letters:
            index = 0
            for letter in letters[0]:
                index = index * 26 + ord(letter) - ord("A") + 1
            index -= 1
        kind = cell.get("t")
        value = cell.findtext(f"{MAIN}v") or ""
        if kind == "inlineStr":
            value = cell_text(cell.find(f"{MAIN}is"))
        elif kind == "s":
            value = strings[int(value)]
        yield index, cell, kind, value
        index += 1


def read_sheet(sheet, strings, header_index, fields, use_1904, style_symbols):
    rows = []
    cost_symbols = {}
    record_numbers = []
    date_columns = {i for i, field in enumerate(fields) if field in ("date", "start_date", "end_date")}
    cost_index = fields.index("cost") if "cost" in fields else None
    column_styles = [(int(col.get("min")), int(col.get("max")), int(col.get("style")))
                     for col in sheet.findall(f"{MAIN}cols/{MAIN}col") if col.get("style")]
    for position, row in enumerate(sheet.findall(f".//{MAIN}sheetData/{MAIN}row")):
        record = int(row.get("r") or (record_numbers[-1] + 1 if record_numbers else 1))
        record_numbers.append(record)
        cells = []
        for index, cell, kind, value in excel_cells(row, strings):
            cells.extend([""] * (index + 1 - len(cells)))
            if position > header_index and index in date_columns and kind == "d":
                value = value.split("T", 1)[0]
            elif position > header_index and index in date_columns and kind in (None, "n") and value:
                epoch = date(1904, 1, 1) if use_1904 else date(1899, 12, 30)
                value = (epoch + timedelta(days=int(Decimal(value)))).isoformat()
            elif kind in (None, "n") and value and "e" in value.lower():
                number = Decimal(value)
                if number.is_finite():
                    value = format(number, "f")
            if position > header_index and index == cost_index and kind in (None, "n") and value:
                style = cell.get("s", row.get("s"))
                if style is None:
                    style = next((s for first, last, s in column_styles if first <= index + 1 <= last), 0)
                style = int(style)
                cost_symbols[record] = style_symbols[style] if 0 <= style < len(style_symbols) else None
            if cell.find(f"{MAIN}f") is not None and not value:
                raise InputError("the workbook contains a formula without a saved value")
            cells[index] = value
        rows.append(cells)
    return rows, header_index, fields, record_numbers, cost_symbols


def workbook(source):
    with ZipFile(source) as archive:
        def xml(path):
            return ET.fromstring(archive.read(path))

        book = xml("xl/workbook.xml")
        properties = book.find(f"{MAIN}workbookPr")
        use_1904 = properties is not None and properties.get("date1904") in ("1", "true")
        links = {r.get("Id"): r.get("Target") for r in xml("xl/_rels/workbook.xml.rels")}
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [cell_text(item) for item in xml("xl/sharedStrings.xml")]
        sheets = []
        for entry in book.findall(f"{MAIN}sheets/{MAIN}sheet"):
            target = links[entry.get(f"{REL}id")]
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            sheet = xml(path)
            # Find the report by its header before handling formulas on unrelated sheets.
            for position, row in enumerate(sheet.findall(f".//{MAIN}sheetData/{MAIN}row")):
                values = []
                for index, _, _, value in excel_cells(row, strings):
                    values.extend([""] * (index + 1 - len(values)))
                    values[index] = value
                fields = header(values)
                if "search_term" in fields:
                    sheets.append((sheet, position, fields))
                    break
        if not sheets:
            raise InputError(HEADER_ERROR)
        if len(sheets) != 1:
            raise InputError("expected one sheet with a 'Search term' header; export the intended tab on its own")
        symbols = excel_cost_symbols(xml("xl/styles.xml")) if "xl/styles.xml" in archive.namelist() else [""]
        sheet, position, fields = sheets[0]
        return read_sheet(sheet, strings, position, fields, use_1904, symbols)


def row_type_overrides(parser):
    parser.add_argument("--row-type", action="append", default=[], metavar="RECORD=TYPE",
                        help="reviewer's verified detail or summary decision for a disputed source record")


def verified_records(arguments):
    records = {}
    for value in arguments:
        match = re.fullmatch(r"([1-9][0-9]*)=(detail|summary)", value)
        if not match:
            raise InputError("--row-type requires RECORD=detail or RECORD=summary")
        record = int(match[1])
        if record in records:
            raise InputError(f"duplicate row type decision for record {record}")
        records[record] = match[2]
    return records


def read_export(source, *, header_mapping=None, row_types=None):
    row_types = dict(row_types or {})
    path = None if source == "-" else Path(source)
    spreadsheet = path is not None and path.suffix.casefold() == ".xlsx"
    cost_symbols = {}
    if spreadsheet:
        cells, index, fields, record_numbers, cost_symbols = workbook(path)
    else:
        if path is not None and path.suffix.casefold() == ".xls":
            raise InputError("save the Excel export as .xlsx, CSV or tab-delimited text first")
        cells, index, fields = delimited(sys.stdin.buffer.read() if path is None else path.read_bytes())
        record_numbers = range(1, len(cells) + 1)
    line = cells[index]
    if header_mapping is None:
        header_mapping = {}
    header_mapping.update(zip(line, fields))
    for label, field in zip(line, fields):
        name = " ".join(label.casefold().replace("_", " ").replace("-", " ").split())
        if field is None and name not in IGNORED_COLUMNS and not REVIEW_COLUMN.fullmatch(name):
            raise InputError("Please use English headings for review columns and omit unrecognized extra columns.")
    known = [field for field in fields if field is not None]
    if len(set(known)) != len(known):
        raise InputError("the header contains duplicate report columns")
    reported_range = preamble_range(cells[:index])
    data = []
    warnings = []
    absent = [field for field in ("cost", "clicks", "conversions", "campaign", "ad_group", "keyword")
              if field not in known]
    term_index = fields.index("search_term")
    context_columns = [i for i, field in enumerate(fields) if field in DETAIL_FIELDS]
    decimal_comma = uses_decimal_comma(cells[index + 1:], fields, spreadsheet)

    def has_detail_context(line):
        return any(i < len(line) and line[i].strip() not in ("", "--") for i in context_columns)

    bad_metrics = set()
    skipped_totals = 0
    ambiguous_records = []
    currencies = set()
    for record, line in zip(record_numbers[index + 1:], cells[index + 1:]):
        if not any(value.strip() for value in line):
            continue
        term = line[term_index].strip() if term_index < len(line) else ""
        if not spreadsheet and len(line) != len(fields):
            raise InputError(f"record {record} has {len(line)} fields, expected {len(fields)}; check for truncation")
        if spreadsheet and any(value.strip() for value in line[len(fields):]):
            raise InputError(f"record {record} has data beyond the report header")
        row = {key: (line[i].strip() if i < len(line) else "")
               for i, key in enumerate(fields) if key is not None}
        currency = None
        if "cost" in row:
            symbol = cost_symbols.get(record, "")
            if symbol is not None:
                currency = cost_currency(symbol + row["cost"], row.get("currency", ""))
            if spreadsheet and row["cost"] and currency is None:
                currencies.add(None)
                warnings.append(f"record {record}: check the displayed currency of the Excel cost "
                                f"against its currency code {row.get('currency', '')!r}")
        if term.casefold() == "total" or term.casefold().startswith("total:"):
            role = row_types.pop(record, None)
            if role not in (None, "detail", "summary"):
                raise InputError(f"record {record}: row type must be detail or summary")
            has_detail = has_detail_context(line)
            if role == "summary":
                skipped_totals += 1
                warnings.append(f"record {record}: skipped confirmed summary {term!r}; check against the original report")
                continue
            if not role and not has_detail:
                skipped_totals += 1
                ambiguous_records.append({"record": record, "values": row})
                warnings.append(f"record {record}: skipped {term!r}; its type is ambiguous (search or summary). "
                                f"Check the original report and pass --row-type {record}=detail or "
                                f"--row-type {record}=summary to every subsequent run.")
                continue
            if not role:
                warnings.append(f"record {record}: retained {term!r} as a search term; verify against the source")
        if not term or term in ("...", "…"):
            raise InputError(f"record {record} has no usable search term; check for truncation")
        if len(known) > 1 and term.casefold() == "search term" and header(line) == fields:
            raise InputError(f"record {record} repeats the header; supply one complete export")
        row["id"] = len(data) + 1
        if "cost" in row:
            currencies.add(currency)
        for key in METRICS:
            if key not in row:
                continue
            try:
                row[key] = parse_number(row[key], decimal_comma)
                if key == "clicks" and row[key] != row[key].to_integral_value():
                    raise InputError("clicks must be a whole number")
            except InputError as error:
                warnings.append(f"row {row['id']}, {key}: {error}")
                row[key] = None
                bad_metrics.add(key)
        data.append(row)
    if row_types:
        raise InputError(f"row type decisions do not identify summary-like records: {sorted(row_types)}")
    if None in currencies or len(currencies) > 1:
        warnings.append("Costs have conflicting or unresolved currencies. Combined spend is withheld. "
                        "Request a separate export for each currency with its currency code.")
        bad_metrics.add("cost")
    if "keyword" in known:
        empty_keywords = sum(not row["keyword"] for row in data)
        if empty_keywords:
            warnings.append(f"Keyword is blank in {empty_keywords} rows; confirm the inventory if available")
    date_warnings = []
    dates = detail_dates(data, date_warnings)
    warnings.extend(date_warnings)
    date_range = reported_range
    if reported_range:
        warnings.extend(window_conflicts(dates, *reported_range))
    elif dates and not date_warnings:
        days = [day for _, _, day in dates]
        date_range = min(days), max(days)
    if date_range is None:
        absent.append("date_range")
    summary = {
        "header_mapping": header_mapping,
        "row_count": len(data),
        "date_range": [day.isoformat() for day in date_range] if date_range else None,
        "date_range_source": "report header" if reported_range else "row dates" if date_range else None,
        "cost": None,
        "clicks": None,
        "conversions": None,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "missing": absent,
        "warnings": warnings,
        "skipped_total_rows": skipped_totals,
        "ambiguous_records": ambiguous_records,
    }
    for key in METRICS:
        if not ambiguous_records and key in known and key not in bad_metrics:
            total = sum((r[key] for r in data), Decimal(0))
            summary[key] = int(total) if key == "clicks" else total
    return summary, data


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", help="CSV, TSV, XLSX, or - for pasted delimited text on stdin")
    parser.add_argument("--rows", action="store_true", help="include every parsed detail row with its review ID")
    row_type_overrides(parser)
    args = parser.parse_args()
    mapping = {}
    try:
        summary, rows = read_export(args.export, header_mapping=mapping, row_types=verified_records(args.row_type))
    except (OSError, ValueError, KeyError, OverflowError, IndexError, ArithmeticError, BadZipFile, ET.ParseError) as error:
        emit({"header_mapping": mapping})
        parser.exit(2, f"{error}\n")
    emit({"summary": summary, "rows": rows} if args.rows else summary)


if __name__ == "__main__":
    main()
