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
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import logging

    logging.basicConfig(level=logging.INFO)
    print("\nOpening PRs...")

    def _process_finding(finding):
        code_fix = finding.get("code_fix", {})
        if not code_fix or "error" in code_fix:
            return ("issue", finding)
        # Use debug logging instead of printing potentially sensitive info
        logging.debug("Creating PR for %s line %s", finding.get("file"), finding.get("line"))
        try:
            repo_url = state.get("repo_url")
            if not repo_url:
                raise ValueError("Repository URL missing in state")
            url = handler.create_pr_for_finding(repo_url, finding)
        except Exception as e:
            logging.error("Failed to create PR for %s: %s", finding.get("file"), e)
            return ("issue", finding)
        if url:
            result = {
                "type": "pr",
                "url": url,
                "file": finding.get("file"),
                "line": finding.get("line"),
                "severity": finding.get("severity"),
                "issue": finding.get("issue")
            }
            return ("pr", result)
        return ("issue", finding)

    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(_process_finding, f): f for f in pr_findings}
        for future in as_completed(futures):
            kind, data = future.result()
            if kind == "pr":
                pr_urls.append(data)
                logging.info("[OK] %s", data["url"])
            else:
                issue_findings.append(data)

    # Open Issues
    print(f"\nOpening Issues...")
    for finding in issue_findings:
        print(f"  Issue: {finding['file']} line {finding['line']}...")
        url = handler.create_issue_for_finding(state["repo_url"], finding)
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