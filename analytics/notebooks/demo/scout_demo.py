"""Helpers for Demo.ipynb — Scout in 10 minutes.

Each public helper queries Trino (Delta Lake on MinIO) and renders a chart
or HTML card. The notebook stays thin; the heavy lifting lives here.
"""

from __future__ import annotations

import os

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import trino
from IPython.display import HTML, display
from matplotlib.ticker import FuncFormatter

# ---------------------------------------------------------------------------
# Trino connection
# ---------------------------------------------------------------------------

TRINO_HOST = os.environ.get("TRINO_HOST", "trino.trino")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
TRINO_SCHEME = os.environ.get("TRINO_SCHEME", "http")
TRINO_USER = os.environ.get("TRINO_USER", "trino")
TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "delta")
TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "default")


def q(sql: str) -> pd.DataFrame:
    """Run a Trino query and return the result as a pandas DataFrame."""
    conn = trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        http_scheme=TRINO_SCHEME,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
        schema=TRINO_SCHEMA,
    )
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

PURPLE = "#667eea"
INDIGO = "#764ba2"
TEAL = "#11998e"
GREEN = "#10b981"
CORAL = "#ff6b6b"
GOLD = "#f6ad55"
SLATE = "#475569"
PALETTE = [PURPLE, TEAL, GOLD, CORAL, "#5b8def", "#a78bfa", "#f472b6", SLATE]

sns.set_theme(style="whitegrid", context="talk")
plt.rcParams.update(
    {
        "axes.titleweight": "bold",
        "axes.titlesize": 14,
        "axes.labelsize": 11,
        "axes.edgecolor": "#cbd5e1",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
    }
)


# ---------------------------------------------------------------------------
# Scale at a glance
# ---------------------------------------------------------------------------


def kpi_cards() -> None:
    """Topline KPI cards. COUNT(*) is over the full table — not gated on dates."""
    kpi = q(
        """
        SELECT
          COUNT(*)                         AS reports,
          COUNT(DISTINCT epic_mrn)         AS patients,
          COUNT(DISTINCT modality)         AS modalities,
          COUNT(DISTINCT sending_facility) AS facilities,
          MIN(requested_dt)                AS earliest,
          MAX(requested_dt)                AS latest
        FROM reports_latest
    """
    ).iloc[0]

    def fmt_int(n):
        return f"{int(n):,}" if pd.notna(n) else "–"

    def fmt_date(t):
        return pd.to_datetime(t).strftime("%b %Y") if pd.notna(t) else "–"

    if pd.notna(kpi["earliest"]) and pd.notna(kpi["latest"]):
        span = (
            pd.to_datetime(kpi["latest"]) - pd.to_datetime(kpi["earliest"])
        ).days / 365.25
        date_range = f"{fmt_date(kpi['earliest'])} → {fmt_date(kpi['latest'])}"
        date_sub = f"~{span:.1f} years of history"
    else:
        date_range = "–"
        date_sub = "no requested_dt populated"

    cards = [
        ("Reports", fmt_int(kpi["reports"]), "in Delta Lake", PURPLE, INDIGO),
        ("Patients", fmt_int(kpi["patients"]), "distinct Epic MRNs", TEAL, GREEN),
        (
            "Modalities",
            fmt_int(kpi["modalities"]),
            "CT, MR, US, XR, …",
            GOLD,
            "#dd6b20",
        ),
        (
            "Facilities",
            fmt_int(kpi["facilities"]),
            "sending HL7 messages",
            CORAL,
            "#c53030",
        ),
        ("Date range", date_range, date_sub, "#5b8def", "#3b5bdb"),
        ("Latency", "seconds", "Trino over Delta Lake", SLATE, "#1f2937"),
    ]

    cards_html = "".join(
        f"""<div style='flex:1 1 180px; min-width:180px;
                       background:linear-gradient(135deg,{c1},{c2});
                       color:white; border-radius:14px; padding:20px 22px;
                       box-shadow:0 6px 18px rgba(15,23,42,0.08);'>
              <div style='font-size:11px; letter-spacing:0.12em;
                          text-transform:uppercase; opacity:0.85;'>{title}</div>
              <div style='font-size:28px; font-weight:700;
                          margin-top:6px; line-height:1.1;'>{value}</div>
              <div style='font-size:12px; opacity:0.85; margin-top:8px;'>{sub}</div>
            </div>"""
        for title, value, sub, c1, c2 in cards
    )
    display(
        HTML(
            f"<div style='display:flex; flex-wrap:wrap; gap:14px; margin:8px 0 24px;'>"
            f"{cards_html}</div>"
        )
    )


# ---------------------------------------------------------------------------
# Modality breakdown
# ---------------------------------------------------------------------------


def plot_modality_volume() -> None:
    """Horizontal bar of reports by modality."""
    df = q(
        """
        SELECT modality, COUNT(*) AS n
        FROM reports_latest
        WHERE modality IS NOT NULL
        GROUP BY modality
        ORDER BY n DESC
    """
    )
    top = df.head(10).iloc[::-1]
    total = int(df["n"].sum())

    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.barh(top["modality"], top["n"], color=PURPLE, edgecolor="white")
    for n, m in zip(top["n"], top["modality"]):
        ax.text(
            n,
            m,
            f"  {int(n):,} ({100 * n / total:.1f}%)",
            va="center",
            fontsize=10,
            color=SLATE,
        )
    ax.set_title("Reports by modality")
    ax.set_xlabel("Reports")
    ax.set_ylabel("")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.margins(x=0.18)
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Volume over time
# ---------------------------------------------------------------------------


def plot_volume_trend(years: int = 5, top_modalities: int = 6) -> None:
    """Stacked monthly volume by modality for the last `years` years."""
    df = q(
        f"""
        WITH top_mods AS (
            SELECT modality
            FROM reports_latest
            WHERE modality IS NOT NULL
            GROUP BY modality
            ORDER BY COUNT(*) DESC
            LIMIT {int(top_modalities)}
        )
        SELECT date_trunc('month', requested_dt) AS month,
               modality,
               COUNT(*) AS n
        FROM reports_latest
        JOIN top_mods USING (modality)
        WHERE requested_dt >= date_add('year', -{int(years)}, current_date)
          AND requested_dt <  current_date
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    )
    if df.empty:
        print(f"No reports in the last {years} years window.")
        return

    df["month"] = pd.to_datetime(df["month"])
    pivot = (
        df.pivot(index="month", columns="modality", values="n").fillna(0).sort_index()
    )
    pivot = pivot[pivot.sum().sort_values(ascending=False).index]

    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.stackplot(
        pivot.index,
        pivot.T.values,
        labels=pivot.columns,
        colors=PALETTE[: len(pivot.columns)],
        alpha=0.92,
    )
    ax.set_title(f"Monthly report volume by modality (last {years} years)")
    ax.set_ylabel("Reports / month")
    ax.set_xlabel("")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(loc="upper left", frameon=False, ncol=len(pivot.columns), fontsize=10)
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Demographics
# ---------------------------------------------------------------------------


def plot_demographics() -> None:
    """Age × modality heatmap, normalized within each modality column."""
    df = q(
        """
        WITH top_mods AS (
            SELECT modality
            FROM reports_latest
            WHERE modality IS NOT NULL
            GROUP BY modality
            ORDER BY COUNT(*) DESC
            LIMIT 7
        ),
        banded AS (
            SELECT
                CASE
                    WHEN patient_age <  18 THEN '0–17'
                    WHEN patient_age <  35 THEN '18–34'
                    WHEN patient_age <  50 THEN '35–49'
                    WHEN patient_age <  65 THEN '50–64'
                    WHEN patient_age <  80 THEN '65–79'
                    ELSE '80+'
                END AS age_band,
                modality
            FROM reports_latest
            JOIN top_mods USING (modality)
            WHERE patient_age IS NOT NULL AND patient_age BETWEEN 0 AND 110
        )
        SELECT age_band, modality, COUNT(*) AS n
        FROM banded
        GROUP BY age_band, modality
    """
    )
    if df.empty:
        print("No demographic data available.")
        return

    age_order = ["0–17", "18–34", "35–49", "50–64", "65–79", "80+"]
    pivot = (
        df.pivot(index="age_band", columns="modality", values="n")
        .reindex(age_order)
        .fillna(0)
    )
    pct = pivot.div(pivot.sum(axis=0), axis=1) * 100
    pct = pct[pct.sum().sort_values(ascending=False).index]

    fig, ax = plt.subplots(figsize=(10, 5.2))
    sns.heatmap(
        pct,
        annot=True,
        fmt=".0f",
        cmap="mako_r",
        cbar_kws={"label": "% of modality"},
        linewidths=0.5,
        linecolor="white",
        ax=ax,
        annot_kws={"size": 10},
    )
    ax.set_title("Age distribution within each modality (% of column)")
    ax.set_xlabel("")
    ax.set_ylabel("Patient age band")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Free-text search
# ---------------------------------------------------------------------------


def _excerpt_card(row: pd.Series) -> str:
    text = (row["impression"] or "").replace("\n", " ").strip()
    label = row["service_name"] or "—"
    return f"""<div style='border-left:3px solid {CORAL}; background:#fff7f5;
                          padding:10px 14px; margin:8px 0; border-radius:0 8px 8px 0;'>
                  <div style='font-size:12px; color:{SLATE}; margin-bottom:4px;'>
                    <b>{row["modality"]}</b> · {label} · {row["requested"]}
                  </div>
                  <div style='font-size:13px; color:#1f2937; line-height:1.4;'>
                    {text}…
                  </div>
                </div>"""


def search_text(pattern: str, n_excerpts: int = 5) -> pd.DataFrame:
    """Regex search across `report_text`. Renders a chart + sample excerpts and
    returns the matches-per-modality DataFrame so you can keep working with it."""
    pattern_sql = pattern.replace("'", "''")

    by_mod = q(
        f"""
        SELECT modality, COUNT(*) AS matches
        FROM reports_latest
        WHERE REGEXP_LIKE(report_text, '(?i){pattern_sql}')
          AND modality IS NOT NULL
        GROUP BY modality
        ORDER BY matches DESC
    """
    )

    if by_mod.empty:
        print(f"No matches for pattern: {pattern!r}")
        return by_mod

    total = int(by_mod["matches"].sum())

    fig, ax = plt.subplots(figsize=(10, 4.2))
    plot = by_mod.head(8).iloc[::-1]
    ax.barh(plot["modality"], plot["matches"], color=CORAL, edgecolor="white")
    for v, m in zip(plot["matches"], plot["modality"]):
        ax.text(v, m, f"  {int(v):,}", va="center", fontsize=10, color=SLATE)
    ax.set_title(f"Reports matching '{pattern}' — {total:,} total")
    ax.set_xlabel("Matching reports")
    ax.set_ylabel("")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.margins(x=0.18)
    plt.tight_layout()
    plt.show()

    samples = q(
        f"""
        SELECT modality, service_name,
               CAST(requested_dt AS DATE) AS requested,
               SUBSTR(COALESCE(report_section_impression, report_text), 1, 280) AS impression
        FROM reports_latest
        WHERE REGEXP_LIKE(report_text, '(?i){pattern_sql}')
          AND COALESCE(report_section_impression, report_text) IS NOT NULL
        ORDER BY RAND()
        LIMIT {int(n_excerpts)}
    """
    )

    cards_html = "".join(_excerpt_card(r) for _, r in samples.iterrows())
    display(
        HTML(
            "<div style='margin-top:12px;'><b>Random impression excerpts:</b></div>"
            + cards_html
        )
    )

    return by_mod


# ---------------------------------------------------------------------------
# Structured diagnoses
# ---------------------------------------------------------------------------


def plot_top_diagnoses(top_n: int = 15) -> None:
    """Top ICD codes across all reports via UNNEST(diagnoses)."""
    df = q(
        f"""
        SELECT diagnosis_code,
               MIN(diagnosis_code_text) AS label,
               COUNT(*) AS n
        FROM reports_latest,
             UNNEST(diagnoses) AS t(diagnosis_code, diagnosis_code_text, diagnosis_code_coding_system)
        WHERE diagnosis_code IS NOT NULL
          AND diagnosis_code <> ''
        GROUP BY diagnosis_code
        ORDER BY n DESC
        LIMIT {int(top_n)}
    """
    )

    def trim(label, code, max_len=58):
        if not label:
            return code
        base = label.strip()
        if len(base) > max_len:
            base = base[: max_len - 1].rstrip() + "…"
        return f"{code} · {base}"

    df["display"] = [trim(l, c) for c, l in zip(df["diagnosis_code"], df["label"])]
    plot = df.iloc[::-1]

    fig, ax = plt.subplots(figsize=(11, 6.2))
    ax.barh(plot["display"], plot["n"], color=TEAL, edgecolor="white")
    for v, lbl in zip(plot["n"], plot["display"]):
        ax.text(v, lbl, f"  {int(v):,}", va="center", fontsize=9, color=SLATE)
    ax.set_title(f"Top {top_n} diagnosis codes across all reports")
    ax.set_xlabel("Reports tagged with this code")
    ax.set_ylabel("")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.margins(x=0.22)
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Turnaround time
# ---------------------------------------------------------------------------


def plot_tat() -> None:
    """Median + P90 order-to-report hours by modality."""
    df = (
        q(
            """
        WITH base AS (
            SELECT modality,
                   CAST(date_diff('second',
                                  COALESCE(requested_dt, observation_dt),
                                  COALESCE(results_report_status_change_dt, message_dt)
                        ) AS DOUBLE) / 3600.0 AS hours
            FROM reports_latest
            WHERE modality IS NOT NULL
              AND COALESCE(requested_dt, observation_dt) IS NOT NULL
              AND COALESCE(results_report_status_change_dt, message_dt) IS NOT NULL
        )
        SELECT modality,
               COUNT(*)                      AS reports,
               approx_percentile(hours, 0.5) AS median_h,
               approx_percentile(hours, 0.9) AS p90_h
        FROM base
        WHERE hours BETWEEN 0 AND 720
        GROUP BY modality
        ORDER BY reports DESC
        LIMIT 8
    """
        )
        .sort_values("median_h")
        .reset_index(drop=True)
    )

    if df.empty:
        print("No TAT data available.")
        return

    y = np.arange(len(df))
    h = 0.38

    fig, ax = plt.subplots(figsize=(11, 5.4))
    ax.barh(
        y - h / 2,
        df["median_h"],
        height=h,
        color=PURPLE,
        edgecolor="white",
        label="Median",
    )
    ax.barh(
        y + h / 2, df["p90_h"], height=h, color=CORAL, edgecolor="white", label="P90"
    )

    for i, row in df.iterrows():
        ax.text(
            row["median_h"],
            i - h / 2,
            f"  {row['median_h']:.1f}h",
            va="center",
            fontsize=9,
            color=SLATE,
        )
        ax.text(
            row["p90_h"],
            i + h / 2,
            f"  {row['p90_h']:.1f}h",
            va="center",
            fontsize=9,
            color=SLATE,
        )

    ax.set_yticks(y)
    ax.set_yticklabels(df["modality"])
    ax.set_xlabel("Hours from order to signed report")
    ax.set_title("Order-to-report turnaround by modality")
    ax.legend(frameon=False, loc="lower right")
    ax.margins(x=0.18)
    plt.tight_layout()
    plt.show()

    display(
        df.assign(
            reports=lambda d: d["reports"].map("{:,}".format),
            median_h=lambda d: d["median_h"].round(1),
            p90_h=lambda d: d["p90_h"].round(1),
        ).rename(columns={"median_h": "median (h)", "p90_h": "P90 (h)"})
    )
