"""Offline, conservative extraction from article text; no price prediction service."""
import re

import csv
from pathlib import Path

CATALOGUE_PATH = Path(__file__).resolve().parent / "data" / "EQUITY_L.csv"


def load_companies():
    companies = {}
    with CATALOGUE_PATH.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            symbol, name = row["SYMBOL"].strip(), row["NAME OF COMPANY"].strip()
            if symbol and name:
                # Keep the CSV's official name for display; allow omission of legal suffixes.
                short_name = re.sub(r"\s+(?:limited|ltd\.?)$", "", name, flags=re.I).strip()
                companies[symbol] = list(dict.fromkeys([name, short_name]))
    return companies


COMPANIES = load_companies()
NAME_SYMBOLS = {}
for symbol, names in COMPANIES.items():
    for name in names:
        NAME_SYMBOLS.setdefault(name.casefold(), set()).add(symbol)
# Longest names first prevents a shorter company name matching within another name.
NAME_PATTERN = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(name) for name in sorted(NAME_SYMBOLS, key=len, reverse=True)
) + r")(?!\w)", re.I)
SYMBOL_PATTERN = re.compile(r"(?<![\w&-])(?:" + "|".join(
    re.escape(symbol) for symbol in sorted(COMPANIES, key=len, reverse=True)
) + r")(?![\w&-])")

POSITIVE = re.compile(
    r"\b(?:profit\w*|earnings|revenue|sales|margin\w*)\b.{0,35}\b(?:rose|rise\w*|grew|grow\w*|jump\w*|surge\w*|increas\w*)\b"
    r"|\b(?:record profits?|beats? (?:estimates|expectations)|wins?\b.{0,35}\b(?:order|contract)\w*|"
    r"upgrad\w*|buyback|approval|approved|dividend hike)\b", re.I)
NEGATIVE = re.compile(
    r"\b(?:profit\w*|earnings|revenue|sales|margin\w*)\b.{0,35}\b(?:fell|fall\w*|drop\w*|declin\w*|slump\w*|decreas\w*)\b"
    r"|\b(?:loss(?:es)?|fraud|lawsuit|probe|penalt\w*|fine[ds]?|default\w*|bankrupt\w*|"
    r"downgrad\w*|recall\w*|miss(?:es|ed)? (?:estimates|expectations)|rejected)\b", re.I)
UNCERTAIN = re.compile(r"\b(?:not|no|never|denies|denied|may|might|could|rumou?r\w*|alleg\w*|if)\b", re.I)


def mentions(text):
    symbols = set()
    for match in NAME_PATTERN.finditer(text):
        symbols.update(NAME_SYMBOLS[match.group().casefold()])
    symbols.update(match.group() for match in SYMBOL_PATTERN.finditer(text))
    return {symbol: COMPANIES[symbol][0] for symbol in sorted(symbols)}



def extract_articles(text):
    # Blank lines remain part of the same article; a separator preserves paragraphs.
    articles = [part.strip() for part in re.split(r"(?m)^\s*---+\s*$", text) if part.strip()]
    rows, unmatched = [], []
    for number, article in enumerate(articles, 1):
        stocks = mentions(article)
        if not stocks:
            unmatched.append(number)
        clauses = re.split(r"[.!?;\n]+|\b(?:while|whereas|but)\b", article)
        clause_subjects = [(clause, mentions(clause)) for clause in clauses]
        for symbol, name in stocks.items():
            signals, evidence = set(), []
            for clause, subjects in clause_subjects:
                if symbol not in subjects:
                    continue
                evidence.append(clause.strip())
                # Multiple subjects or uncertainty cannot be confidently attributed.
                if len(subjects) != 1 or UNCERTAIN.search(clause):
                    continue
                if POSITIVE.search(clause):
                    signals.add("positive")
                if NEGATIVE.search(clause):
                    signals.add("negative")
            influence = next(iter(signals)) if len(signals) == 1 else "neutral"
            rows.append({"id": f"{number}:{symbol}", "article": number, "stock": name,
                         "analysis_source": "keywords" if signals else "pending_ai",
                         "reason": "Mixed keyword signals." if len(signals) > 1 else "",
                         "symbol": symbol, "influence": influence,
                         "evidence": " · ".join(evidence)[:600]})
    return {"stocks": rows, "article_count": len(articles), "unmatched_articles": unmatched}
