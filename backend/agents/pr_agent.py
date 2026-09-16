from backend.state import ReviewState
from backend.tools.github_handler import GitHubHandler, decide_action

def pr_agent_node(state: ReviewState) -> ReviewState:
    """Opens PRs for high confidence findings, Issues for medium/low"""

    if state.get("error"):
        print("Skipping GitHub actions — pipeline has errors")
        return state

    all_findings = state.get("all_findings", [])
    if not all_findings:
        print("No findings to act on")
        state["pr_urls"] = []
        return state

    handler = GitHubHandler()
    pr_urls = []

    pr_findings = []
    issue_findings = []
    report_only = []

    # Categorize findings
    for f in all_findings:
        action = decide_action(f)
        if action == "open_pr":
            pr_findings.append(f)
        elif action == "open_issue":
            issue_findings.append(f)
        else:
            report_only.append(f)

    print(f"\nAction breakdown:")
    print(f"  PRs to open:    {len(pr_findings)}")
    print(f"  Issues to open: {len(issue_findings)}")
    print(f"  Report only:    {len(report_only)}")

    # Open PRs
    print(f"\nOpening PRs...")
    for finding in pr_findings:
        code_fix = finding.get("code_fix", {})
        if not code_fix or "error" in code_fix:
            # Downgrade to issue if no valid fix
            issue_findings.append(finding)
            continue

        print(f"  PR: {finding['file']} line {finding['line']}...")
        url = handler.create_pr_for_finding(state["repo_url"], finding)
        if url:
            pr_urls.append({
                "type": "pr",
                "url": url,
                "file": finding["file"],
                "line": finding["line"],
                "severity": finding["severity"],
                "issue": finding["issue"]
            })
            print(f"  [OK] {url}")

    # Open Issues
    print(f"\nOpening Issues...")
    for finding in issue_findings:
        print(f"  Issue: {finding['file']} line {finding['line']}...")
        repo_url = state.get("repo_url", "")
        if not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            print("  [WARN] Invalid repository URL, skipping issue creation.")
            continue
        try:
            url = handler.create_issue_for_finding(repo_url, finding)
        except Exception as e:
            print(f"  [ERROR] Failed to create issue: {e}")
            continue
        if url:
            pr_urls.append({
                "type": "issue",
                "url": url,
                "file": finding["file"],
                "line": finding["line"],
                "severity": finding["severity"],
                "issue": finding["issue"]
            })
            print(f"  [OK] {url}")

    state["pr_urls"] = pr_urls

    opened_prs = len([x for x in pr_urls if x["type"] == "pr"])
    opened_issues = len([x for x in pr_urls if x["type"] == "issue"])
    print(f"\nGitHub actions complete:")
    print(f"  PRs opened:    {opened_prs}")
    print(f"  Issues opened: {opened_issues}")

    return state