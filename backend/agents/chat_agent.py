import json
from backend.llm_config import get_llm
from backend.utils import safe_llm_call

CHAT_SYSTEM_PROMPT = """You are Figent's code review assistant. You have just completed 
an automated code review of a GitHub repository and you know everything about the findings.

The developer you are talking to is: {username}. If they ask who they are or what their name is, tell them their username is {username}.

Repository: {repo_url}
Files Analyzed: {file_count}
Total Issues Found: {total_findings}

SEVERITY BREAKDOWN:
- Critical: {critical_count}
- High: {high_count}  
- Medium: {medium_count}
- Low: {low_count}

GITHUB ACTIONS TAKEN:
- PRs Opened: {prs_opened}
- Issues Opened: {issues_opened}

ALL FINDINGS:
{findings_summary}

PR/ISSUE URLS:
{pr_urls_summary}

Answer the user's questions about this review accurately and helpfully.
Be specific — reference exact file names, line numbers, and findings.
If asked why a PR wasn't opened, explain the confidence threshold system.
If asked about a specific finding, give full details.
Keep responses concise and actionable.
"""

INTENT_PROMPT = """Analyze this user message and identify the intent.

    Message: {message}

    Return one of these intents as a single word:
    - summary (wants overview of review)
    - specific_finding (asking about a particular issue)
    - file_query (asking about a specific file)
    - severity_query (asking about findings of a severity level)
    - pr_query (asking about PRs or issues opened)
    - explanation (wants something explained in detail)
    - general (general question)

    Return only the intent word, nothing else.
    """


def build_chat_context(review_result: dict, username: str = "Developer") -> str:
    """Build the system prompt context from a completed review result"""
    findings = review_result.get("all_findings", [])
    final_report = review_result.get("final_report", {})
    by_severity = final_report.get("by_severity", {})
    pr_urls = review_result.get("pr_urls", [])

    # Build findings summary
    findings_lines = []
    for i, f in enumerate(findings, 1):
        action = "PR opened" if f.get("pr_eligible") else "Issue opened" if not f.get("pr_eligible") else "Report only"
        findings_lines.append(
            f"{i}. [{f['severity'].upper()}] {f['file']} line {f.get('line', '?')} "
            f"(confidence: {f.get('confidence', 0)}%) — {f['issue'][:100]}"
        )

    # Build PR/Issue summary
    pr_lines = []
    for p in pr_urls:
        pr_lines.append(f"- [{p['type'].upper()}] {p['file']} line {p['line']}: {p['url']}")

    prs_opened = len([p for p in pr_urls if p["type"] == "pr"])
    issues_opened = len([p for p in pr_urls if p["type"] == "issue"])

    system_prompt = CHAT_SYSTEM_PROMPT.format(
        username=username,
        repo_url=review_result.get("repo_url", "Unknown"),
        file_count=len(review_result.get("files", [])),
        total_findings=len(findings),
        critical_count=by_severity.get("critical", 0),
        high_count=by_severity.get("high", 0),
        medium_count=by_severity.get("medium", 0),
        low_count=by_severity.get("low", 0),
        prs_opened=prs_opened,
        issues_opened=issues_opened,
        findings_summary="\n".join(findings_lines) or "No findings",
        pr_urls_summary="\n".join(pr_lines) or "No GitHub actions taken"
    )

    return system_prompt


class ChatAgent:
    def __init__(self, review_result: dict, username: str = "Developer"):
        self.llm = get_llm()
        self.review_result = review_result 
        self.system_prompt = build_chat_context(review_result, username)
        self.history = []

    def get_finding_by_index(self, index: int) -> dict:
        """Get full finding details by number"""
        findings = self.review_result.get("all_findings", [])
        if 1 <= index <= len(findings):
            return findings[index - 1]
        return {}

    def get_findings_by_file(self, file_name: str) -> list:
        """Get all findings for a specific file"""
        findings = self.review_result.get("all_findings", [])
        return [f for f in findings if file_name.lower() in f["file"].lower()]

    def get_findings_by_severity(self, severity: str) -> list:
        """Get all findings of a specific severity"""
        findings = self.review_result.get("all_findings", [])
        return [f for f in findings if f["severity"] == severity.lower()]

    def get_pr_eligible_findings(self) -> list:
        """Get findings that were PR eligible"""
        findings = self.review_result.get("all_findings", [])
        return [f for f in findings if f.get("pr_eligible")]
    
    def detect_intent(self, message: str) -> str:
        """Detect what the user is asking about"""
        prompt = INTENT_PROMPT.format(message=message)
        intent = safe_llm_call(self.llm, prompt).strip().lower()
        valid_intents = ["summary", "specific_finding", "file_query",
                        "severity_query", "pr_query", "explanation", "general"]
        return intent if intent in valid_intents else "general"


    def build_contextual_prompt(self, message: str, intent: str) -> str:
        """Add extra context based on detected intent"""
        extra_context = ""

        if intent == "severity_query":
            for sev in ["critical", "high", "medium", "low"]:
                if sev in message.lower():
                    findings = self.get_findings_by_severity(sev)
                    extra_context = f"\nFull {sev} findings:\n"
                    for f in findings:
                        extra_context += (
                            f"- {f['file']} line {f.get('line','?')}: "
                            f"{f['issue']}\n  Fix: {f['fix']}\n"
                        )
                    break

        elif intent == "file_query":
            # Extract filename from message
            findings = self.review_result.get("all_findings", [])
            files = list(set(f["file"] for f in findings))
            for file in files:
                filename = file.split("/")[-1].split("\\")[-1]
                if filename.lower() in message.lower():
                    file_findings = self.get_findings_by_file(filename)
                    extra_context = f"\nFull findings for {file}:\n"
                    for f in file_findings:
                        extra_context += (
                            f"- Line {f.get('line','?')} [{f['severity']}]: "
                            f"{f['issue']}\n  Fix: {f['fix']}\n"
                            f"  Confidence: {f.get('confidence',0)}%\n"
                        )
                    break

        elif intent == "pr_query":
            pr_urls = self.review_result.get("pr_urls", [])
            extra_context = "\nFull GitHub actions:\n"
            for p in pr_urls:
                extra_context += f"- [{p['type'].upper()}] {p['file']} line {p['line']}: {p['url']}\n"

        elif intent == "specific_finding":
            import re
            index = None
            word_map = {
                "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10
            }
            for word, idx in word_map.items():
                if word in message.lower():
                    index = idx
                    break
            
            if not index:
                matches = re.findall(r'\b\d+\b', message)
                if matches:
                    index = int(matches[0])
            
            if index:
                finding = self.get_finding_by_index(index)
                if finding:
                    extra_context = (
                        f"\nFull details for Finding #{index}:\n"
                        f"- File: {finding.get('file')}\n"
                        f"- Line: {finding.get('line', '?')}\n"
                        f"- Severity: {finding.get('severity', 'unknown')}\n"
                        f"- Confidence: {finding.get('confidence', 0)}%\n"
                        f"- Issue: {finding.get('issue')}\n"
                        f"- Recommended Fix: {finding.get('fix')}\n"
                    )
                    code_fix = finding.get("code_fix")
                    if code_fix:
                        extra_context += (
                            f"- Original Code:\n```python\n{code_fix.get('original_code')}\n```\n"
                            f"- Proposed Fixed Code:\n```python\n{code_fix.get('fixed_code')}\n```\n"
                            f"- Code Fix Explanation: {code_fix.get('explanation')}\n"
                        )

        return extra_context

    def chat(self, user_message: str) -> str:
        """Send a message and get a response with intent-aware context"""
        intent = self.detect_intent(user_message)

        extra_context = self.build_contextual_prompt(user_message, intent)

        messages = [{"role": "system", "content": self.system_prompt}]
        if extra_context:
            messages.append({"role": "system", "content": extra_context})
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_message})

        try:
            response = self.llm.invoke(messages)
            assistant_message = response.content

            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": assistant_message})

            return assistant_message

        except Exception as e:
            return f"Chat error: {str(e)}"

    def reset(self):
        """Clear conversation history"""
        self.history = []
    