"""Assess individual news items, retaining explicit selection evidence."""
import re
from backend.ai_influence import analyze
from backend.ai_settings import load_settings
from backend.extractor import mentions

LABELS = {"eligible": "Selected: direct company news", "indirect": "Indirect mention",
          "sector": "Sector-wide news", "bundled": "Multiple companies in one item",
          "uncertain": "Uncertain - review needed"}
DIRECT = re.compile(r"credit|rat(?:ing|ings)|capacity|invest(?:ment|ments)?|order.book|revenue|ebitda|buyback|contract|lowest bidder", re.I)


def news_items(text):
    # Company headings start items. Ordinary paragraph breaks retain item context.
    starts = [m.start() for m in re.finditer(r"(?m)^\s*[^\n:]{2,90}:\s*", text)]
    if not starts:
        return [text.strip()] if text.strip() else []
    boundaries = sorted(set([0] + starts + [len(text)]))
    return [text[a:b].strip() for a, b in zip(boundaries, boundaries[1:]) if text[a:b].strip()]


def contradicts(answer, candidate):
    category = answer["selection_category"]
    if not answer.get("selection_evidence"):
        return True
    eligible = answer["direct_operations"] and not answer["bundled"]
    if (category == "eligible") != eligible:
        return True
    if (category == "bundled") != answer["bundled"]:
        return True
    # A standalone company heading with an explicit operational event merits a second look.
    return category != "eligible" and any(
        candidate["symbol"] in mentions(item.split(":", 1)[0]) and DIRECT.search(item)
        for item in candidate["news_items"] if ":" in item
    )


def apply_selection(text, result):
    rows = result["stocks"]
    if not rows:
        return result
    for row in rows:
        row.update(auto_selected=False, selection_category="uncertain",
                   selection_reason="Uncertain - review needed: selection assessment unavailable.")
    try:
        if not load_settings().api_key or len(rows) > 50:
            raise ValueError("Selection analysis unavailable")
        items = [(item, mentions(item)) for item in news_items(text)]
        candidates = [{**{key: row[key] for key in ("id", "article", "symbol", "stock")},
                       "news_items": [item for item, stocks in items if row["symbol"] in stocks]}
                      for row in rows]
        answers = analyze("Use each candidate's separate news_items.", candidates, selection=True)
        by_id = {answer["id"]: answer for answer in answers}
        review = [{**candidate, "review_previous": by_id[candidate["id"]]} for candidate in candidates
                  if contradicts(by_id[candidate["id"]], candidate)]
        if review:
            try:
                checked = analyze("Second review: verify eligibility against the supplied news items.", review, selection=True)
                by_id.update({answer["id"]: answer for answer in checked})
            except Exception:
                for candidate in review:
                    by_id[candidate["id"]]["selection_category"] = "uncertain"
                    by_id[candidate["id"]]["reason"] = "Contradictory assessment; second review unavailable."
        for row in rows:
            answer = by_id[row["id"]]
            category = answer["selection_category"]
            consistent = (category == "eligible") == (answer["direct_operations"] and not answer["bundled"])
            if not consistent or ((category == "bundled") != answer["bundled"]):
                category = "uncertain"
            row.update(auto_selected=category == "eligible", selection_category=category,
                       selection_reason=LABELS[category] + ": " + answer["reason"][:1000],
                       selection_evidence=answer.get("selection_evidence", ""))
    except Exception:
        result["selection_notice"] = "Automatic selection review could not be completed. Stocks are unchecked; review manually or retry."
    return result
