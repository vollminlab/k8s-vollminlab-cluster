"""Private issue text. The reports repo is private, so model text is allowed here."""


def incident_title(inc) -> str:
    target = "/".join(p for p in (inc.namespace, inc.workload) if p) or "cluster"
    return f"[{inc.reason}] {target}"[:200]


def render_incident(inc, result, gate_lines, pr_url, preview, preview_note) -> str:
    lines = [f"<!-- fp:{inc.fingerprint} -->"]
    if result["status"] != "ok":
        lines += [f"**The investigation did not complete:** `{result['failure_reason']}`", "",
                  "```", result.get("detail", ""), "```"]
    else:
        lines += ["## Summary", result["summary"], "", "## Root cause", result["root_cause"], "",
                  "## Evidence"] + [f"- {e}" for e in result["evidence"]]
        lines += ["", f"**Confidence:** {result['confidence']}  ", f"**Class:** `{result['class']}`"]
        if result.get("injection_suspected"):
            lines += ["", "> **Injection suspected:** cluster data tried to direct the agent. "
                          "Nothing it wrote reached the public repo."]
    if result.get("tool_denials"):
        lines += ["", f"<details><summary>Denied tool calls: {len(result['tool_denials'])}</summary>", ""]
        lines += [f"- `{d}`" for d in result["tool_denials"]] + ["", "</details>"]
    if gate_lines:
        lines += ["", "## PR gates"] + [f"- {g}" for g in gate_lines]
    if pr_url:
        lines += ["", f"**PR opened:** {pr_url}"]
    elif preview:
        lines += ["", f"## The PR that would have been opened ({preview_note})", "",
                  f"Branch `{preview['branch']}`  ", f"Title `{preview['title']}`", "",
                  preview["body"], "", "```diff", preview["patch"], "```"]
    lines += ["", f"Cost: ${float(result.get('cost_usd', 0)):.2f}", "",
              "<details><summary>Incident</summary>", "", "```json", inc.to_json(), "```", "", "</details>"]
    return "\n".join(lines)


def render_finding(finding: dict, fp: str) -> tuple:
    title = f"[sweep] {finding['check']}: {finding['namespace']}/{finding['object']}"[:200]
    body = "\n".join([f"<!-- fp:{fp} -->", f"**{finding['summary']}**", "", finding["detail"]])
    return title, body
