"""ICAI CA Inter syllabus reference data (section-wise weightages, issued 26-Oct-2023).

All weightages are normalized to "% of a 100-mark paper" so priority scores are
comparable across subjects, even though some papers have sub-parts worth
70/30 or 50/50 marks. Values are the midpoint of each official range.
"""

SYLLABUS_WEIGHTAGE = {
    "Advanced Accounting": {
        "I": ("Framework for Preparation/Presentation of Financial Statements, Accounting Standards", 60.0),
        "II": ("Company Accounts (Schedule III, Buyback, Reconstruction, etc.)", 32.5),
        "III": ("Branch Accounting", 7.5),
    },
    "Cost & Management Accounting": {
        "I": ("Overview of Cost & Management Accounting, Cost Sheets", 12.5),
        "II": ("Ascertainment of Cost (Material, Employee, Overhead, ABC)", 37.5),
        "III": ("Methods of Costing", 22.5),
        "IV": ("Standard Costing, Marginal Costing, Budgets", 27.5),
    },
    "Corporate and Other Laws": {
        "I": ("Preliminary, Incorporation of Company and Matters Incidental Thereto", 17.5),
        "II": ("Prospectus and Allotment of Securities, Share Capital and Debentures", 17.5),
        "III": ("Management & Administration, Declaration and Payment of Dividend, Accounts, Audit and Auditors", 22.75),
        "IV": ("The General Clauses Act, 1897", 11.25),
        "V": ("Interpretation of Statutes", 8.25),
        "VI": ("Foreign Exchange Management Act, 1999", 10.5),
    },
    "Taxation": {
        "I": ("Basic Concepts of Income Tax", 7.5),
        "II": ("Heads of Income", 13.75),
        "III": ("Clubbing of Income, Set-off/Carry Forward of Losses, Deductions", 8.75),
        "IV": ("Advance Tax, TDS, TCS, Filing of Return of Income", 8.75),
        "V": ("Computation of Total Income and Tax Payable under Various Regimes", 11.25),
        "VI": ("GST in India - Introduction", 1.25),
        "VII": ("Levy and Collection of Tax, Supply, Input Tax Credit", 32.5),
        "VIII": ("Registration, Tax Invoice, E-way Bill, Returns", 16.25),
    },
    "Auditing and Ethics": {
        "I": ("Nature, Objective and Scope of Audit", 5.0),
        "II": ("Audit Strategy, Audit Planning and Audit Programme", 10.0),
        "III": ("Risk Assessment and Internal Control", 10.0),
        "IV": ("Audit Evidence", 15.0),
        "V": ("Audit of Items of Financial Statements", 16.0),
        "VI": ("Audit Documentation", 10.0),
        "VII": ("Completion and Review", 10.0),
        "VIII": ("Audit Report", 15.0),
        "IX": ("Audit of Banks, Code of Ethics and Other Aspects", 9.0),
    },
    "Financial Management & Strategic Management": {
        "I": ("Scope and Objectives of Financial Management", 6.25),
        "II": ("Financing Decisions and Cost of Capital", 23.75),
        "III": ("Capital Investment and Dividend Decisions", 11.25),
        "IV": ("Management of Working Capital", 8.75),
        "V": ("Introduction to Strategic Management", 10.0),
        "VI": ("Strategic Analysis: External Environment", 10.0),
        "VII": ("Strategic Analysis: Internal Environment", 10.0),
        "VIII": ("Strategic Choices", 10.0),
        "IX": ("Strategy Implementation and Evaluation", 10.0),
    },
}

# Keyword -> paper name, checked in order (case-insensitive substring match)
_PAPER_KEYWORDS = [
    ("account", "Advanced Accounting"),
    ("cost", "Cost & Management Accounting"),
    ("law", "Corporate and Other Laws"),
    ("corporate", "Corporate and Other Laws"),
    ("tax", "Taxation"),
    ("audit", "Auditing and Ethics"),
    ("financial", "Financial Management & Strategic Management"),
    ("strategic", "Financial Management & Strategic Management"),
    ("fm", "Financial Management & Strategic Management"),
]


def infer_paper(subject_name):
    """Map a free-text subject name to one of the 6 ICAI papers, or None if unrecognized."""
    if not subject_name:
        return None
    name = subject_name.lower()
    for keyword, paper in _PAPER_KEYWORDS:
        if keyword in name:
            return paper
    return None


def suggest_section(paper_name, chapter_name):
    """Heuristically suggest a syllabus section code for a chapter name, or None."""
    if not paper_name or not chapter_name:
        return None
    name = chapter_name.lower()

    if paper_name == "Advanced Accounting":
        if "branch" in name:
            return "III"
        if any(kw in name for kw in ("schedule iii", "buyback", "reconstruction", "company accounts", "amalgamation")):
            return "II"
        if "as-" in name or "as -" in name or name.startswith("as"):
            return "I"
        return "I"

    if paper_name == "Cost & Management Accounting":
        if any(kw in name for kw in ("material", "employee", "overhead", "direct expense", "abc", "activity based")):
            return "II"
        if any(kw in name for kw in ("job", "batch", "process", "service cost", "joint product", "by-product", "operating costing", "contract costing")):
            return "III"
        if any(kw in name for kw in ("standard cost", "marginal cost", "budget")):
            return "IV"
        return "I"

    # No heuristic for other papers yet - leave untagged for manual tagging.
    return None


def section_weight(paper_name, section_code):
    """Return the normalized weight (% of 100-mark paper) for a section, or None."""
    paper = SYLLABUS_WEIGHTAGE.get(paper_name)
    if not paper:
        return None
    entry = paper.get(section_code)
    if not entry:
        return None
    return entry[1]


def section_options(paper_name):
    """Return [(code, "label (~weight%)"), ...] for a dropdown, or [] if paper unknown."""
    paper = SYLLABUS_WEIGHTAGE.get(paper_name)
    if not paper:
        return []
    options = []
    for code, (label, weight) in paper.items():
        options.append((code, f"{code}: {label} (~{weight:g}%)"))
    return options
