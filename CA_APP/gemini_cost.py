"""Lightweight Gemini token-usage tracker for estimating per-chapter ingestion cost.

Each Gemini call site records its response.usage_metadata here under a label
(e.g. "mcq_gen", "mock_mcq_gen", "sim_gen", "audio_script_gen", "audio_verify").
Pricing is for gemini-2.5-flash — verify against
https://ai.google.dev/gemini-api/docs/pricing as rates can change.
"""

INPUT_PER_M_USD = 0.30
OUTPUT_PER_M_USD = 2.50  # output pricing includes "thinking" tokens

_log = []


def record(label, usage_metadata):
    if usage_metadata is None:
        return
    _log.append({
        "label": label,
        "input": usage_metadata.prompt_token_count,
        "output": usage_metadata.candidates_token_count,
        "total": usage_metadata.total_token_count,
    })


def reset():
    _log.clear()


def report(usd_to_inr=None):
    by_label = {}
    for entry in _log:
        agg = by_label.setdefault(entry["label"], {"calls": 0, "input": 0, "output": 0})
        agg["calls"] += 1
        agg["input"] += entry["input"]
        agg["output"] += entry["output"]

    lines = []
    total_cost = 0.0
    for label, agg in by_label.items():
        cost = agg["input"] / 1e6 * INPUT_PER_M_USD + agg["output"] / 1e6 * OUTPUT_PER_M_USD
        total_cost += cost
        lines.append(
            f"{label}: {agg['calls']} call(s), "
            f"{agg['input']} in / {agg['output']} out tokens -> ${cost:.4f}"
        )

    suffix = f" (~Rs {total_cost * usd_to_inr:.2f})" if usd_to_inr else ""
    lines.append(f"TOTAL: ${total_cost:.4f}{suffix}")
    return "\n".join(lines)
