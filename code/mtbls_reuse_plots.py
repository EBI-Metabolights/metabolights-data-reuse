"""Generate the individual plots of confirmed MetaboLights data reuse.

Read the deduplicated publication-dataset workbook and the saved
MTBLS_metadata sheet. Keep rows manually labelled Reuse, Benchmark or
Reanalysis. Publication plots count each Publication ID once; dataset
plots count each distinct confirmed publication-dataset pair. The input
must already have repeated pairs removed by the counts helper.

Publication ID is prepared from DOI, PMCID or title by that helper. Modality
and release year come from dataset metadata. Save PNG files under outdir;
the default is a separate regenerated_plots/ folder in this release.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl

# -----------------------------
# Shared font sizes
# -----------------------------
TITLE_FS = 28
LABEL_FS = 24
TICK_FS = 18
LEGEND_FS = 18
CELL_FS = 18

mpl.rcParams.update({
    "font.size": TICK_FS,
    "axes.titlesize": TITLE_FS,
    "axes.labelsize": LABEL_FS,
    "xtick.labelsize": TICK_FS,
    "ytick.labelsize": TICK_FS,
    "legend.fontsize": LEGEND_FS,
})

def format_mod_label(label: str) -> str:
    """Add line breaks to long modality labels and labels combining several methods."""
    s = str(label)
    # Split the long other/unspecified MS label over two lines.
    s = s.replace("MS (other/unspecified)", "MS (other/\nunspecified)")

    # Put each method in a combined label on a separate line.
    if "&" in s:
        s = s.replace("&", "&\n")
    return s


def format_primary_legend_label(label: str) -> str:
    """Split category names longer than two words across two legend lines."""
    s = str(label).strip()
    words = s.split()
    if len(words) <= 2:
        return s
    mid = len(words) // 2
    return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])

def modality_sort_key(name: str):
    """Order LC-MS, NMR, GC-MS, combined methods, other MS, Unknown, then other labels."""
    s = str(name)
    # Ignore display line breaks when deciding label order.
    s0 = s.replace("\n", "")
    if s0 in ["LC-MS", "NMR", "GC-MS"]:
        base_order = {"LC-MS": 0, "NMR": 1, "GC-MS": 2}
        return (0, base_order[s0], s0)
    if "&" in s0:
        return (1, 0, s0)
    if s0.startswith("MS (other/") or "MS (other/" in s0:
        return (2, 0, s0)
    if s0 == "Unknown":
        return (3, 0, s0)
    return (4, 0, s0)

def sort_modalities(labels):
    """Sort modality labels with the same ordering used throughout these plots."""
    return sorted(list(labels), key=modality_sort_key)
# -----------------------------
# Input and output paths relative to this release
# -----------------------------
def parse_args() -> argparse.Namespace:
    """Read workbook and output paths, using the files supplied with this release by default."""
    package_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Generate confirmed MTBLS reuse plots.")
    parser.add_argument(
        "--review-xlsx",
        type=Path,
        default=package_dir / "results" / "mtbls_review_deduplicated_publication_study_records.xlsx",
    )
    parser.add_argument(
        "--metadata-xlsx",
        type=Path,
        default=package_dir / "data" / "mtbls_reuse_reviewed_records.xlsx",
    )
    parser.add_argument("--outdir", type=Path, default=package_dir / "regenerated_plots")
    return parser.parse_args()


ARGS = parse_args()
REVIEW_XLSX = ARGS.review_xlsx
META_XLSX = ARGS.metadata_xlsx
OUTDIR = ARGS.outdir
OUTDIR.mkdir(parents=True, exist_ok=True)

# -----------------------------
# Okabe-Ito colours used consistently across the figures
# -----------------------------
# Use the same colour for a label even when its position changes
# between plots.
PRIMARY_COLORS = {
    "Data Integration or Meta-analysis": "#E69F00",
    "Educational Use or Protocol Illustration": "#56B4E9",
    "Method or Tool Development": "#009E73",
    "Secondary Biological Analysis": "#F0E442",
}
MODALITY_COLORS = {
    "LC-MS": "#E69F00",
    "NMR": "#56B4E9",
    "GC-MS": "#009E73",
    "LC-MS&GC-MS": "#F0E442",
    "LC-MS&GC-MS&NMR": "#0072B2",
    "LC-MS&NMR": "#D55E00",
    "MS (other/unspecified)": "#CC79A7",
}
DOMAIN_COLORS = {
    "Both": "#E69F00",
    "Lipidomics": "#56B4E9",
    "Metabolomics": "#009E73",
    "Other": "#F0E442",
}


def mapped_colors(labels, mapping: dict[str, str]) -> list[str]:
    """Keep the assigned colour for each known label; use grey for unrecognised labels."""
    return [mapping.get(str(label), "#999999") for label in labels]

# -----------------------------
# Input column names and confirmed screening labels
# -----------------------------
YEAR_COL = "Year"

CONFIRMED_REUSE = {"Reuse", "Benchmark", "Reanalysis"}

# -----------------------------
# Label formatting, counting and figure-saving helpers
# -----------------------------

def normalize_title(s) -> str:
    """Remove surrounding spaces and one final full stop for display-title comparisons."""
    s = str(s).strip()
    if s.endswith("."):
        s = s[:-1]
    return s


def resolve_column(columns: pd.Index, *names: str) -> str:
    """Return the first supported column name from the current or older workbook formats."""
    for name in names:
        if name in columns:
            return name
    raise ValueError(f"Expected one of {names}; available columns: {list(columns)}")


def wrap_two_lines(label: str) -> str:
    """Split labels longer than two words into two lines near their middle."""
    words = str(label).split()
    if len(words) <= 2:
        return str(label)
    mid = len(words) // 2
    return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])


def split_subcats(s) -> list[str]:
    """Split comma- or semicolon-separated labels and ignore missing or empty text."""
    if s is None:
        return []
    s = str(s).strip()
    if s.lower() in {"nan", ""}:
        return []
    parts = re.split(r"[;,]+", s)
    return [p.strip() for p in parts if p.strip()]


def mode_first(series: pd.Series, default: str) -> str:
    """Choose the most frequent nonmissing label, or default when no label exists.

    For tied text labels, take the first sorted value returned by pandas mode,
    so the same data always receive the same publication-level assignment.
    """
    m = series.mode(dropna=True)
    if len(m) == 0:
        return default
    return str(m.iloc[0])


def savefig(fig, filename: str) -> Path:
    """Save a PNG at 300 dpi with tight borders, close the figure and return its path."""
    out = OUTDIR / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out

# -----------------------------
# Read the deduplicated publication-dataset table and select confirmed MTBLS links.
# -----------------------------

df0 = pd.read_excel(REVIEW_XLSX)

NOTES_COL = resolve_column(
    df0.columns,
    "Reviewer.Notes (e.g., how the data was used)",
    "Reviewer.Notes",
)
PRIMARY_COL = resolve_column(df0.columns, "Reviewer.Primary (pick)", "Primary (pick)")
DOMAIN_COL = resolve_column(df0.columns, "Reviewer.Domain (pick)", "Domain (pick)")
SUBCAT_COL = resolve_column(
    df0.columns,
    "Reviewer.Subcats (free text)",
    "Subcategories (free text)",
)

# Keep a cleaned title for display; count publications using Publication ID.
if "Title" not in df0.columns:
    raise ValueError("Expected a 'Title' column in the deduplicated review file.")
if "Publication ID" not in df0.columns:
    raise ValueError("Expected a 'Publication ID' column in the deduplicated review file.")

df0["Title_norm"] = df0["Title"].apply(normalize_title)

# Retain only the three manual decisions that count as confirmed reuse.
df = df0[df0[NOTES_COL].isin(CONFIRMED_REUSE)].copy()

# Retain links to MetaboLights datasets.
if "StudyIDs_split" not in df.columns:
    raise ValueError("Expected column: StudyIDs_split")

df = df[df["StudyIDs_split"].astype(str).str.contains("MTBLS", na=False)].copy()

# Drop missing publication years and convert the remaining years to integers.
if YEAR_COL in df.columns:
    df_year = df.dropna(subset=[YEAR_COL]).copy()
    df_year[YEAR_COL] = df_year[YEAR_COL].astype(int)
else:
    df_year = df.copy()

# -----------------------------
# Read dataset metadata and derive analytical modality and public release year.
# -----------------------------

meta = pd.read_excel(META_XLSX, sheet_name="MTBLS_metadata")

# Read the dataset release year, which differs from publication year.
if "MTBLS.ReleaseDate" in meta.columns:
    meta["ReleaseYear"] = pd.to_datetime(meta["MTBLS.ReleaseDate"], errors="coerce").dt.year
else:
    meta["ReleaseYear"] = np.nan

# Search technology, platform and study-descriptor text for method names.
for c in ["MTBLS.Technologies", "MTBLS.Platforms", "MTBLS.Descriptors"]:
    if c not in meta.columns:
        meta[c] = ""

meta["_text_all"] = (
    meta[["MTBLS.Technologies", "MTBLS.Platforms", "MTBLS.Descriptors"]]
    .fillna("")
    .astype(str)
    .agg(" ".join, axis=1)
    .str.lower()
    .str.replace("-", " ", regex=False)
)

MS_WORDS = [
    "mass spectrometry", " orbitrap", " exactive", " qtof", " tof",
    "triple quadrupole", "quadrupole", "ft icr", "fticr"
]

def _contains_any(text: str, words: list[str]) -> bool:
    """Check whether at least one supplied term occurs in the text."""
    t = text or ""
    return any(w in t for w in words)


def infer_modality(text: str, accession: str | None = None) -> str:
    """Assign LC-MS, GC-MS, NMR or other MS from dataset metadata text.

    Join multiple recognised methods with ampersands in a fixed order. Return
    Unknown when none is recognised. MTBLS28 is assigned LC-MS explicitly from
    its study description, matching the result generator's classification.
    """
    if str(accession).upper() == "MTBLS28":
        return "LC-MS"

    t = (text or "").lower()
    t = t.replace("-", " ").replace("/", " ").replace("_", " ")

    has_nmr = ("nuclear magnetic resonance" in t) or (" nmr" in t) or ("nmr " in t)
    has_lc = ("liquid chromatography" in t) or ("lc ms" in t) or ("lcms" in t)
    has_gc = ("gas chromatography" in t) or ("gc ms" in t) or ("gcms" in t)

    # Recognise general mass-spectrometry terms when no specific method is found.
    has_ms = _contains_any(t, MS_WORDS) or ("mass spectrometry" in t) or (" ms " in f" {t} ")

    labels = []
    if has_lc:
        labels.append("LC-MS")
    if has_gc:
        labels.append("GC-MS")
    if has_nmr:
        labels.append("NMR")

    if not labels and has_ms:
        labels.append("MS (other/unspecified)")

    if not labels:
        return "Unknown"

    order = {"LC-MS": 1, "GC-MS": 2, "NMR": 3, "MS (other/unspecified)": 4, "Unknown": 99}
    labels = sorted(dict.fromkeys(labels), key=lambda x: order.get(x, 999))

    return "&".join(labels)


meta["Modality"] = meta.apply(lambda r: infer_modality(r["_text_all"], r.get("MTBLS.Accession")), axis=1)
meta_mod = meta[["MTBLS.Accession", "Modality", "ReleaseYear"]].drop_duplicates().copy()

# Attach dataset modality and release year to each confirmed link.
# Keep links without matching metadata; their missing modality receives the MS label below.
df_m = df_year.merge(meta_mod, how="left", left_on="StudyIDs_split", right_on="MTBLS.Accession")
df_m["Primary"] = df_m.get(PRIMARY_COL)
df_m["Domain"] = df_m.get(DOMAIN_COL)
df_m["Modality"] = df_m["Modality"].fillna("MS (other/unspecified)")

# Count each publication once, using its most frequent linked-dataset modality.
pub_tbl = (
    df_m.groupby("Publication ID", as_index=False)
       .agg(
           Year=(YEAR_COL, "first"),
           Primary=(PRIMARY_COL, "first"),
           Domain=(DOMAIN_COL, "first"),
           Modality=("Modality", lambda x: mode_first(x, "MS (other/unspecified)"))
       )
)

# ============================================================
# PLOT 01: Reuse over time by primary category (unique publications)
# ============================================================

t1 = (
    pub_tbl.dropna(subset=["Year"]).assign(Year=lambda d: d["Year"].astype(int))
    .groupby(["Year", "Primary"]).size().unstack(fill_value=0).sort_index()
)

fig, ax = plt.subplots(figsize=(16, 9))
t1.plot(kind="bar", stacked=True, ax=ax, color=mapped_colors(t1.columns, PRIMARY_COLORS))
ax.set_title("Reuse over time by primary category", fontsize=TITLE_FS)
ax.set_xlabel("Publication year", fontsize=LABEL_FS)
ax.set_ylabel("Unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", rotation=45, labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
handles, labels = ax.get_legend_handles_labels()
labels = [format_primary_legend_label(l) for l in labels]

leg = ax.legend(handles, labels, title="Primary category", fontsize=18)
leg.get_title().set_fontsize(18)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_reuse_over_time_by_primary_category_unique_publications.png")

# ============================================================
# PLOT 02: Reuse over time by domain (unique publications)
# ============================================================

t2 = (
    pub_tbl.dropna(subset=["Year"]).assign(Year=lambda d: d["Year"].astype(int))
    .groupby(["Year", "Domain"]).size().unstack(fill_value=0).sort_index()
)

fig, ax = plt.subplots(figsize=(16, 9))
t2.plot(kind="bar", stacked=True, ax=ax, color=mapped_colors(t2.columns, DOMAIN_COLORS))
ax.set_title("Reuse over time by domain", fontsize=TITLE_FS)
ax.set_xlabel("Publication year", fontsize=LABEL_FS)
ax.set_ylabel("Unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", rotation=45, labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
leg = ax.legend(title="Domain", fontsize=LEGEND_FS)
leg.get_title().set_fontsize(18)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_reuse_over_time_by_domain_unique_publications.png")

# ============================================================
# PLOT 03: Reuse over time by analytical modality (unique publications)
# ============================================================

t3 = (
    pub_tbl.dropna(subset=["Year"]).assign(Year=lambda d: d["Year"].astype(int))
    .groupby(["Year", "Modality"]).size().unstack(fill_value=0).sort_index()
)

# Display single methods first, combined methods next, then other or unknown methods.

def _mod_sort_key(name: str):
    """Order annual-plot columns with single methods first and combined methods next."""
    s = str(name)

    # Start with LC-MS, NMR and GC-MS, in that order.
    if s in ["LC-MS", "NMR", "GC-MS"]:
        base_order = {"LC-MS": 0, "NMR": 1, "GC-MS": 2}
        return (0, base_order[s], s)

    # Place labels containing several methods next.
    if "&" in s:
        return (1, 0, s)

    # Place other or unspecified MS after the combined methods.
    if "MS (other/" in s or s.startswith("MS (other/") or s.startswith("MS (other"):
        return (2, 0, s)

    # Finish with Unknown and any remaining labels.
    if s == "Unknown":
        return (3, 0, s)

    return (4, 0, s)

cols = sorted(list(t3.columns), key=_mod_sort_key)
t3 = t3[cols]

fig, ax = plt.subplots(figsize=(16, 9))
t3.plot(kind="bar", stacked=True, ax=ax, color=mapped_colors(t3.columns, MODALITY_COLORS))
ax.set_title("Reuse over time by analytical modality", fontsize=TITLE_FS)
ax.set_xlabel("Publication year", fontsize=LABEL_FS)
ax.set_ylabel("Unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", rotation=45, labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
leg = ax.legend(title="Modality", fontsize=LEGEND_FS)
leg.get_title().set_fontsize(18)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_reuse_over_time_by_analytical_modality_unique_publications.png")

# ============================================================
# PLOT 04: Overall reuse by primary category (unique publications)
# ============================================================

p4 = pub_tbl["Primary"].value_counts().sort_values(ascending=False)
labels = [wrap_two_lines(x) for x in p4.index.astype(str)]

fig, ax = plt.subplots(figsize=(12, 9))
ax.bar(labels, p4.values, color=mapped_colors(p4.index, PRIMARY_COLORS))
ax.set_title("Overall reuse by primary category", fontsize=TITLE_FS)
ax.set_xlabel("Primary category", fontsize=LABEL_FS)
ax.set_ylabel("Unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", rotation=30, labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_overall_reuse_by_primary_category_unique_publications.png")

# ============================================================
# PLOT 05: Overall reuse by analytical modality (unique publications)
# ============================================================

p5 = pub_tbl["Modality"].value_counts().sort_values(ascending=False)

fig, ax = plt.subplots(figsize=(12, 9))
ax.bar([format_mod_label(x) for x in p5.index.astype(str)], p5.values, color=mapped_colors(p5.index, MODALITY_COLORS))
ax.set_title("Overall reuse by analytical modality", fontsize=TITLE_FS)
ax.set_xlabel("Analytical modality", fontsize=LABEL_FS)
ax.set_ylabel("Unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", rotation=45, labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_overall_reuse_by_analytical_modality_unique_publications.png")

# ============================================================
# PLOT 06: Overall reuse by domain (unique publications)
# ============================================================

p6 = pub_tbl["Domain"].value_counts().sort_values(ascending=False)

fig, ax = plt.subplots(figsize=(12, 9))
ax.bar(p6.index.astype(str), p6.values, color=mapped_colors(p6.index, DOMAIN_COLORS))
ax.set_title("Overall reuse by domain", fontsize=TITLE_FS)
ax.set_xlabel("Domain", fontsize=LABEL_FS)
ax.set_ylabel("Unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", rotation=45, labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_overall_reuse_by_domain_unique_publications.png")

# ============================================================
# PLOT 07: Cumulative reuse over time (unique publications) with markers
# ============================================================

p7 = pub_tbl.dropna(subset=["Year"]).assign(Year=lambda d: d["Year"].astype(int))
ycounts = p7.groupby("Year")["Publication ID"].nunique().sort_index()
cum = ycounts.cumsum()

fig, ax = plt.subplots(figsize=(16, 9))
ax.plot(cum.index, cum.values, marker="o")
ax.set_title("Cumulative reuse over time", fontsize=TITLE_FS)
ax.set_xlabel("Publication year", fontsize=LABEL_FS)
ax.set_ylabel("Cumulative unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_cumulative_reuse_over_time_unique_publications.png")

# ============================================================
# PLOT 08: Cumulative reuse over time by primary category (unique publications) with markers
# ============================================================

p8 = (
    pub_tbl.dropna(subset=["Year"]).assign(Year=lambda d: d["Year"].astype(int))
    .groupby(["Year", "Primary"]).size().unstack(fill_value=0).sort_index()
)

cum_p8 = p8.cumsum()

fig, ax = plt.subplots(figsize=(16, 9))
cols = list(cum_p8.columns)
colors = mapped_colors(cols, PRIMARY_COLORS)
for i, c in enumerate(cols):
    ax.plot(cum_p8.index, cum_p8[c], marker="o", label=str(c), color=colors[i])

ax.set_title("Cumulative reuse over time by primary category", fontsize=TITLE_FS)
ax.set_xlabel("Publication year", fontsize=LABEL_FS)
ax.set_ylabel("Cumulative unique reused publications", fontsize=LABEL_FS)
ax.tick_params(axis="x", labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
handles, labels = ax.get_legend_handles_labels()
labels = [format_primary_legend_label(l) for l in labels]

leg = ax.legend(handles, labels, title="Primary category", fontsize=18)
leg.get_title().set_fontsize(16)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_cumulative_reuse_over_time_by_primary_category_unique_publications.png")

# ============================================================
# PLOT 09: Top 20 datasets by distinct confirmed publication-dataset links
#         Split bars by primary category and label the dataset modality.
# ============================================================

# Count confirmed publication-dataset pairs for each accession.
study_counts = df_m.groupby("StudyIDs_split").size().sort_values(ascending=False)
top20 = study_counts.head(20).index.tolist()

df_top = df_m[df_m["StudyIDs_split"].isin(top20)].copy()

study_primary = df_top.groupby(["StudyIDs_split", "Primary"]).size().unstack(fill_value=0)
study_primary = study_primary.loc[top20]

# Label each dataset with its modality from the metadata table.
mod_map = meta_mod.set_index("MTBLS.Accession")["Modality"].to_dict()
modalities = [mod_map.get(study, "MS (other/unspecified)") for study in study_primary.index]

fig, ax = plt.subplots(figsize=(16, 9))
colors = mapped_colors(study_primary.columns, PRIMARY_COLORS)
left = np.zeros(len(study_primary), dtype=float)

for i, cat in enumerate(study_primary.columns):
    vals = study_primary[cat].values.astype(float)
    ax.barh(range(len(study_primary)), vals, left=left, color=colors[i], label=str(cat))
    for yi, (l, v) in enumerate(zip(left, vals)):
        if v > 0:
            ax.text(l + v / 2, yi, str(int(v)), va="center", ha="center", fontsize=10)
    left += vals

ax.set_xlim(0, float(left.max()) + 2)

for yi, total in enumerate(left):
    ax.text(float(total) + 0.3, yi, modalities[yi], va="center", fontsize=12)

ax.set_yticks(range(len(study_primary)))
ax.set_yticklabels(study_primary.index, fontsize=12)

ax.set_title("Top 20 reused MTBLS studies (reuse associations)", fontsize=TITLE_FS)
ax.set_xlabel("Number of confirmed reuse publication-dataset matches", fontsize=LABEL_FS)
ax.set_ylabel("MTBLS study", fontsize=LABEL_FS)
ax.tick_params(axis="x", labelsize=TICK_FS)
handles, labels = ax.get_legend_handles_labels()
labels = [format_primary_legend_label(l) for l in labels]

leg = ax.legend(handles, labels, title="Primary category", fontsize=18, loc="upper right")
leg.get_title().set_fontsize(16)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_top20_reused_studies_all_publications_stacked_by_primary.png")

# ============================================================
# PLOT 10A / 10B: Primary × Modality heatmaps
#  - 10A: Unique publications
#  - 10B: Distinct confirmed publication-dataset pairs
#  Use the common method ordering, including labels with several methods.
#  Darker cells indicate larger counts, with contrasting text for readability.
# ============================================================


def plot_heatmap(table: pd.DataFrame, title: str, filename: str):
    # Retain all method labels, including labels with several methods.
    """Draw a category-by-modality count table in a fixed-size heatmap.

    Order modality columns consistently. Darker cells represent larger counts;
    use light text on dark cells and dark text on light cells.
    """
    table = table.copy()
    # Apply the common ordering to modality columns.
    table = table.reindex(columns=sort_modalities(table.columns))

    data = table.values.astype(float)
    vmax = float(data.max()) if data.size else 1.0

    nrows, ncols = table.shape
    fig, ax = plt.subplots(figsize=(16, 9))

    cmap = plt.get_cmap("Blues")  # Small counts are light; large counts are dark.
    norm = mpl.colors.Normalize(vmin=0.0, vmax=vmax)
    im = ax.imshow(data, cmap=cmap, norm=norm, aspect="auto")

    cbar = fig.colorbar(im)
    cbar.ax.tick_params(labelsize=TICK_FS)

    ax.set_xticks(np.arange(ncols))
    ax.set_xticklabels([format_mod_label(c) for c in table.columns], rotation=45, ha="right", fontsize=TICK_FS)

    ylabels = [wrap_two_lines(y) for y in table.index]
    ax.set_yticks(np.arange(len(ylabels)))
    ax.set_yticklabels(ylabels, fontsize=TICK_FS)

    thresh = 0.55
    for i in range(nrows):
        for j in range(ncols):
            val = int(table.iloc[i, j])
            intensity = 0.0 if vmax == 0 else (val / vmax)
            color = "white" if intensity >= thresh else "black"
            ax.text(j, i, val, ha="center", va="center", fontsize=CELL_FS, color=color)

    ax.set_title(title)
    plt.tight_layout(rect=[0, 0, 0.85, 1])
    savefig(fig, filename)


# 10A: Count each publication once.
heat_unique = pd.crosstab(pub_tbl["Primary"], pub_tbl["Modality"])
plot_heatmap(
    heat_unique,
    "Primary × Modality (Unique publications)",
    "MTBLS_primary_vs_modality_unique_publications_heatmap.png",
)

# 10B: Count each distinct confirmed publication-dataset pair.
heat_all = pd.crosstab(df_m["Primary"], df_m["Modality"])
plot_heatmap(
    heat_all,
    "Primary × Modality (confirmed reuse publication-dataset matches)",
    "MTBLS_primary_vs_modality_all_publication_matches_heatmap.png",
)

# ============================================================
# PLOT 11: Primary × Subcategories heatmap (Unique publications)
#  - Read the supported manual sub-category column selected above.
#  - Retain sub-categories with at least five publications across the whole table.
#  - Do not add a new catch-all sub-category.
# ============================================================

# Gather the recorded primary category and sub-category text once per publication.
pub_sc = (
    df_m.groupby("Publication ID", as_index=False)
       .agg(
           Primary=("Primary", "first"),
           Subcats=(SUBCAT_COL, lambda x: ";".join([str(v) for v in x.dropna().unique()]))
       )
)

rows = []
for _, r in pub_sc.iterrows():
    subs = split_subcats(r["Subcats"])
    if not subs:
        subs = ["(none)"]
    for sc in subs:
        rows.append((r["Publication ID"], r["Primary"], sc))

long_sc = pd.DataFrame(rows, columns=["Publication ID", "Primary", "Subcat"]).drop_duplicates()
ct_ps = pd.crosstab(long_sc["Primary"], long_sc["Subcat"])

THRESH = 5
keep_cols = [c for c in ct_ps.columns if ct_ps[c].sum() >= THRESH]
ct_ps = ct_ps[keep_cols].copy()
ct_ps = ct_ps.loc[:, ct_ps.sum(axis=0).sort_values(ascending=False).index]

# Draw the primary-category and sub-category heatmap.

def plot_heatmap_general(table: pd.DataFrame, title: str, filename: str, figsize=(24, 12), xfontsize=20, yfontsize=20, cellfontsize=24):
        # Use the common method ordering for method labels in either direction.
    """Draw a count heatmap, enlarging it when more labels need space.

    Apply the common modality ordering when rows or columns contain modality
    labels. Print every cell count and adjust text colour for readability.
    """
    if any(str(c).startswith(('LC-MS','GC-MS','NMR','MS (other')) or '&' in str(c) for c in table.columns):
        table = table.reindex(columns=sort_modalities(table.columns))
    if any(str(i).startswith(('LC-MS','GC-MS','NMR','MS (other')) or '&' in str(i) for i in table.index):
        table = table.reindex(index=sort_modalities(table.index))
    data = table.values.astype(float)
    vmax = float(data.max()) if data.size else 1.0

    nrows, ncols = table.shape
    # Increase the figure dimensions when more row or column labels need space.
    fig_w = max(figsize[0], 0.65 * ncols)
    fig_h = max(figsize[1], 0.50 * nrows)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    cmap = plt.get_cmap("Blues")
    norm = mpl.colors.Normalize(vmin=0.0, vmax=vmax)
    im = ax.imshow(data, cmap=cmap, norm=norm, aspect="auto")

    cbar = fig.colorbar(im)
    cbar.ax.tick_params(labelsize=TICK_FS)

    ax.set_xticks(np.arange(table.shape[1]))
    ax.set_xticklabels([format_mod_label(c) for c in table.columns], rotation=45, ha="right", fontsize=max(TICK_FS, xfontsize))

    # Apply line breaks to long row labels as well as column labels.
    ax.set_yticks(np.arange(table.shape[0]))
    ax.set_yticklabels([format_mod_label(wrap_two_lines(i)) for i in table.index], fontsize=max(TICK_FS, yfontsize))

    thresh = 0.55
    for i in range(table.shape[0]):
        for j in range(table.shape[1]):
            val = int(table.iloc[i, j])
            intensity = 0.0 if vmax == 0 else (val / vmax)
            color = "white" if intensity >= thresh else "black"
            ax.text(j, i, val, ha="center", va="center", fontsize=max(CELL_FS, cellfontsize), color=color)

    ax.set_title(title, fontsize=TITLE_FS)
    plt.tight_layout(rect=[0, 0, 0.85, 1])
    savefig(fig, filename)


plot_heatmap_general(
    ct_ps,
    f"Primary × Subcategories (unique publications, threshold ≥{THRESH})",
    "MTBLS_primary_vs_subcategories_heatmap_unique_publications.png",
)

# ============================================================
# PLOT 12: Modality × Subcategories heatmap
#  - 12A: Unique publications
#  - 12B: Distinct confirmed publication-dataset pairs
#  - Retain sub-categories with at least five publications or pairs across the table.
#  - Show LC-MS, NMR and GC-MS before combined and other method labels.
# ============================================================

# 12A: Assign one modality and count each publication once per sub-category.
pub_ms_sc = (
    df_m.groupby("Publication ID", as_index=False)
       .agg(
           Modality=("Modality", lambda x: mode_first(x, "MS (other/unspecified)")),
           Subcats=(SUBCAT_COL, lambda x: ";".join([str(v) for v in x.dropna().unique()]))
       )
)

rows = []
for _, r in pub_ms_sc.iterrows():
    subs = split_subcats(r["Subcats"])
    if not subs:
        subs = ["(none)"]
    for sc in subs:
        rows.append((r["Publication ID"], r["Modality"], sc))

long_ms_sc = pd.DataFrame(rows, columns=["Publication ID", "Modality", "Subcat"]).drop_duplicates()
ct_ms_sc = pd.crosstab(long_ms_sc["Modality"], long_ms_sc["Subcat"])

keep_cols = [c for c in ct_ms_sc.columns if ct_ms_sc[c].sum() >= THRESH]
ct_ms_sc = ct_ms_sc[keep_cols].copy()
ct_ms_sc = ct_ms_sc.loc[:, ct_ms_sc.sum(axis=0).sort_values(ascending=False).index]

plot_heatmap_general(
    ct_ms_sc,
    f"Modality × Subcategories (unique publications, threshold ≥{THRESH})",
    "MTBLS_modality_vs_subcategories_heatmap_unique_publications.png",
)

# 12B: Count every distinct confirmed publication-dataset pair per sub-category.
rows = []
for _, r in df_m.iterrows():
    subs = split_subcats(r.get(SUBCAT_COL))
    if not subs:
        subs = ["(none)"]
    for sc in subs:
        rows.append((r["Modality"], sc))

long_all = pd.DataFrame(rows, columns=["Modality", "Subcat"])
ct_ms_sc_all = pd.crosstab(long_all["Modality"], long_all["Subcat"])

keep_cols = [c for c in ct_ms_sc_all.columns if ct_ms_sc_all[c].sum() >= THRESH]
ct_ms_sc_all = ct_ms_sc_all[keep_cols].copy()
ct_ms_sc_all = ct_ms_sc_all.loc[:, ct_ms_sc_all.sum(axis=0).sort_values(ascending=False).index]

plot_heatmap_general(
    ct_ms_sc_all,
    f"Modality × Subcategories (confirmed reuse publication-dataset matches, threshold ≥{THRESH})",
    "MTBLS_modality_vs_subcategories_heatmap_all_publication_matches.png",
)

# ============================================================
# PLOT 15: Group the top 20 reused datasets by their repository release year.
# ============================================================

# Add release year to the selected top-20 dataset links.
meta_release = meta_mod[["MTBLS.Accession", "ReleaseYear"]].drop_duplicates().copy()

df_top20 = df_m[df_m["StudyIDs_split"].isin(top20)].merge(
    meta_release, how="left", left_on="StudyIDs_split", right_on="MTBLS.Accession"
)
# Read either joined release-year column when pandas adds _x or _y suffixes.
if "ReleaseYear" not in df_top20.columns:
    if "ReleaseYear_y" in df_top20.columns:
        df_top20["ReleaseYear"] = df_top20["ReleaseYear_y"]
    elif "ReleaseYear_x" in df_top20.columns:
        df_top20["ReleaseYear"] = df_top20["ReleaseYear_x"]

df_top20 = df_top20.dropna(subset=["ReleaseYear"]).copy()
df_top20["ReleaseYear"] = df_top20["ReleaseYear"].astype(int)

pivot = (
    df_top20.groupby(["ReleaseYear", "StudyIDs_split", "Primary"]).size().unstack(fill_value=0)
)

pivot["_total"] = pivot.sum(axis=1)
pivot = pivot.sort_index(level=0)

ordered_index = []
for yr in sorted(pivot.index.get_level_values(0).unique()):
    sub = pivot.xs(yr, level=0).sort_values("_total", ascending=False)
    for sid in sub.index:
        ordered_index.append((yr, sid))

pivot = pivot.loc[ordered_index].drop(columns=["_total"])

fig, ax = plt.subplots(figsize=(20, 9))
colors = mapped_colors(pivot.columns, PRIMARY_COLORS)
left = np.zeros(len(pivot), dtype=float)

for i, cat in enumerate(pivot.columns):
    vals = pivot[cat].values.astype(float)
    ax.barh(range(len(pivot)), vals, left=left, color=colors[i], label=str(cat))
    for yi, (l, v) in enumerate(zip(left, vals)):
        if v > 0:
            ax.text(l + v / 2, yi, str(int(v)), va="center", ha="center", fontsize=10)
    left += vals

ax.set_xlim(0, float(left.max()) + 2)

labels = [f"{yr}  {sid}" for yr, sid in pivot.index]
ax.set_yticks(range(len(pivot)))
ax.set_yticklabels(labels, fontsize=12)

# Label each dataset with the modality recorded in its metadata.
for yi, ((yr, sid), total) in enumerate(zip(pivot.index, left)):
    ax.text(float(total) + 0.3, yi, mod_map.get(sid, "MS (other/unspecified)"), va="center", fontsize=12)

ax.set_title("Top 20 reused MTBLS studies grouped by release year (reuse associations)", fontsize=TITLE_FS)
ax.set_xlabel("Number of confirmed reuse publication-dataset matches", fontsize=LABEL_FS)
ax.set_ylabel("Release year and MTBLS study", fontsize=LABEL_FS)
ax.tick_params(axis="x", labelsize=TICK_FS)
handles, labels = ax.get_legend_handles_labels()
labels = [format_primary_legend_label(l) for l in labels]

leg = ax.legend(handles, labels, title="Primary category", fontsize=18, loc="upper right")
leg.get_title().set_fontsize(14)
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_top20_reused_studies_grouped_by_release_year_all_publications.png")

# ============================================================
# PLOT 16: Count the selected top-20 datasets in each release year.
# ============================================================

freq = (
    df_top20.drop_duplicates(subset=["StudyIDs_split"])
           .groupby("ReleaseYear")["StudyIDs_split"].nunique().sort_index()
)

fig, ax = plt.subplots(figsize=(16, 9))
ax.bar(freq.index.astype(int), freq.values)
ax.set_title("Release year distribution of Top 20 studies", fontsize=TITLE_FS)
ax.set_xlabel("Release year", fontsize=LABEL_FS)
ax.set_ylabel("Number of Top20 studies", fontsize=LABEL_FS)
ax.tick_params(axis="x", labelsize=TICK_FS)
ax.tick_params(axis="y", labelsize=TICK_FS)
ax.yaxis.set_major_locator(MaxNLocator(integer=True))
plt.tight_layout(rect=[0, 0, 0.85, 1])
savefig(fig, "MTBLS_top20_release_year_frequency.png")

print(f"Done. Plots exported to: {OUTDIR.resolve()}")
