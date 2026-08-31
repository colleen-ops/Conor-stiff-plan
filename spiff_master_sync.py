#!/usr/bin/env python3
"""
spiff_master_sync.py — writes the MASTER tab of the Spiff Tracker 2026 sheet.

One row per QB event, YTD:
  Type=FUNDED  -> one row per funded OFFER   (grain fixes the multi-funding double-count)
  Type=APP     -> one row per DEAL created

The monthly tabs never talk to QuickBase. They read MASTER with SUMIFS/COUNTIFS,
so a new qualified app or a new funding shows up on the right month tab on the
next run with zero edits.

Env (GitHub Secrets):
  QB_REALM                      ifundco.quickbase.com
  QB_TOKEN                      QuickBase user token, scoped to app bn5gjsf5n
  GOOGLE_SERVICE_ACCOUNT_JSON   service-account key JSON, whole file
  SPIFF_SHEET_ID                optional; falls back to the constant below
"""

import os
import json
import datetime as dt

import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ---------------------------------------------------------------- config

QB_REALM = os.environ["QB_REALM"]
QB_TOKEN = os.environ["QB_TOKEN"]
SHEET_ID = os.environ.get(
    "SPIFF_SHEET_ID", "1ftADhQWlX2xdhJ-7HpAgs_iSA_AtNHTYlcZh9Izy8BQ"
)

DEALS = "bn5gjsf77"
OFFERS = "bn5gjsf9c"

TAB = "MASTER"
YEAR = dt.date.today().year

# Deal Rep (QB) -> first name as it appears in column A of every monthly tab.
# Add a line here when a rep is hired; nothing else changes.
ROSTER = {
    "Bryan Florian": "Bryan",
    "Calder Malin": "Calder",
    "Henry Voorhees": "Henry",
    "Ryan Moore": "Ryan",
    "Jack Allan": "Jack",
    "Ibn Elmore": "Ibn",
    "Paul Garza": "Paul",
    "Mackenson Jean": "Mackenson",
    "Bill Buescher": "Bill",
}

# QB accounts that are deliberately not on the spiff tabs. Anything with real
# activity that is in neither ROSTER nor here will crash the run on purpose.
IGNORE_REPS = {
    "", "Abe Grazi-1", "Open Rep", "Colleen Jung (Seung Hyun Jung)",
    "Conor Borthwick", "Dusan Sekulic", "Jeffery Lev", "Marijana Valic",
    "Tarek Elnicklawy", "Priscilla Guel", "Makar Cheltsov", "Sean Hsu",
        "Thomas Cooke", "Filip Maric", "Johane Ismond",
    # apps-only, no funded volume, all gone quiet before July 2026
    "Cashana Diggs", "Jarrod Silver", "Jean Hugues Toussaint",
    "Junier Frias", "Kyle Uzelac",
}

# Spiff rule: an app counts unless the deal landed in one of these.
UNQUALIFIED_STATUSES = {"Not Yet / Dead", "Duplicate", "Test"}

HEADER = [
    "Type", "Month", "Rep", "RepQB", "RecordID", "DealID",
    "Business", "Date", "Amount", "Status", "Renewal", "Flag",
]

# ---------------------------------------------------------------- quickbase


def qb_query(table_id, select, where):
    """Paginated QuickBase query. Returns a list of record dicts."""
    url = "https://api.quickbase.com/v1/records/query"
    headers = {
        "QB-Realm-Hostname": QB_REALM,
        "Authorization": f"QB-USER-TOKEN {QB_TOKEN}",
        "Content-Type": "application/json",
    }
    out, skip = [], 0
    while True:
        body = {
            "from": table_id,
            "select": select,
            "where": where,
            "options": {"skip": skip, "top": 1000},
        }
        r = requests.post(url, headers=headers, json=body, timeout=90)
        r.raise_for_status()
        recs = r.json().get("data", [])
        out.extend(recs)
        if len(recs) < 1000:
            return out
        skip += len(recs)


def val(rec, fid, default=""):
    v = rec.get(str(fid), {}).get("value")
    return default if v in (None, "") else v


# ---------------------------------------------------------------- build rows


def build_rows():
    jan1 = f"01-01-{YEAR}"
    rows = []

    # --- funded offers: 51 Funded Date, 27 Funded Amount, 54 RENEWAL?,
    #     16 Rep - Name (verified identical to Deals fid 145 Deal Rep)
    offers = qb_query(
        OFFERS,
        ["3", "9", "10", "16", "51", "27", "54", "6"],
        f"{{51.OAF.'{jan1}'}}",
    )
    for o in offers:
        funded = str(val(o, 51))
        if not funded.startswith(str(YEAR)):
            continue
        rep_qb = str(val(o, 16))
        is_renewal = bool(o.get("54", {}).get("value"))
        rows.append([
            "FUNDED",
            funded[:7],
            ROSTER.get(rep_qb, ""),
            rep_qb,
            val(o, 3),
            val(o, 9),
            str(val(o, 10)),
            funded[:10],
            val(o, 27, 0),
            str(val(o, 6)),
            "YES" if is_renewal else "NO",
            "RENEWAL" if is_renewal else "NEW BIZ",
        ])

    # --- deals created: 1 Date Created, 97 Status, 145 Deal Rep
    deals = qb_query(
        DEALS,
        ["3", "6", "652", "145", "97", "1"],
        f"{{1.OAF.'{jan1}'}}",
    )
    for d in deals:
        created = str(val(d, 1))
        if not created.startswith(str(YEAR)):
            continue
        rep_qb = str(val(d, 145))
        status = str(val(d, 97))
        rows.append([
            "APP",
            created[:7],
            ROSTER.get(rep_qb, ""),
            rep_qb,
            val(d, 3),
            val(d, 3),
            str(val(d, 6) or val(d, 652)),
            created[:10],
            "",
            status,
            "",
            "UNQUALIFIED" if status in UNQUALIFIED_STATUSES else "QUALIFIED",
        ])

    rows.sort(key=lambda r: (r[1], r[0], r[2]))
    return rows


def assert_roster_covers(rows):
    """Crash if a rep produced spiff-eligible activity but maps to no sheet row.

    Without this, an unmapped or renamed rep silently reads as 0 on the tab,
    which is indistinguishable from a genuinely bad month.
    """
    orphans = {}
    for r in rows:
        if r[2] or r[3] in IGNORE_REPS:
            continue
        eligible = (r[0] == "FUNDED" and r[11] == "NEW BIZ") or (
            r[0] == "APP" and r[11] == "QUALIFIED"
        )
        if eligible:
            orphans[r[3]] = orphans.get(r[3], 0) + 1
    if orphans:
        detail = ", ".join(f"{k} ({v})" for k, v in sorted(orphans.items()))
        raise SystemExit(
            f"ROSTER GAP — unmapped reps with spiff-eligible activity: {detail}\n"
            f"Add each to ROSTER (and column A on the month tabs), "
            f"or to IGNORE_REPS if they should not be scored."
        )


# ---------------------------------------------------------------- sheets


def sheets_service():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def ensure_tab(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    if any(s["properties"]["title"] == TAB for s in meta["sheets"]):
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": TAB}}}]},
    ).execute()


def write_master(svc, rows):
    svc.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID, range=f"{TAB}!A:L"
    ).execute()
    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"{TAB}!A1",
        valueInputOption="RAW",
        body={"values": [HEADER] + rows + [[], [f"last sync: {stamp}"]]},
    ).execute()


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    rows = build_rows()
    assert_roster_covers(rows)
    svc = sheets_service()
    ensure_tab(svc)
    write_master(svc, rows)
    funded = sum(1 for r in rows if r[0] == "FUNDED")
    print(
        f"MASTER written: {len(rows)} rows "
        f"({funded} funded offers, {len(rows) - funded} apps)"
    )
