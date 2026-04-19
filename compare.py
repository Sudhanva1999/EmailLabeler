"""Dry-run comparison between Gemini and a local LLM.

Fetches the latest N emails from the configured account, classifies each with
every available LLM provider, and writes a side-by-side report to disk. Never
applies labels and never updates metadata.json.

Usage:
    python compare.py                          # 10 emails, both providers
    python compare.py --limit 25               # 25 emails
    python compare.py --no-local               # skip the local LLM
    python compare.py --output out/cmp.md      # custom output paths
"""

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from src.categorizer import Categorizer, EmailContent
from src.config import CATEGORIES_FILE, ensure_env_file, load_env
from src.email_providers import get_email_provider

console = Console()


def _init_llms(skip_gemini: bool, skip_local: bool) -> dict[str, Categorizer]:
    llms: dict[str, Categorizer] = {}
    if not skip_gemini:
        try:
            from src.llm.gemini import GeminiProvider
            llms["gemini"] = Categorizer(CATEGORIES_FILE, GeminiProvider())
        except Exception as exc:
            console.print(f"[red]Skipping gemini — init failed:[/red] {exc}")
    if not skip_local:
        try:
            from src.llm.local import LocalLLMProvider
            llms["local"] = Categorizer(CATEGORIES_FILE, LocalLLMProvider())
        except Exception as exc:
            console.print(f"[red]Skipping local — init failed:[/red] {exc}")
    return llms


def _classify_one(cat: Categorizer, email) -> dict:
    try:
        r = cat.classify(EmailContent(
            subject=email.subject,
            sender=email.sender,
            snippet=email.snippet,
            body=email.body,
        ))
        return {
            "category": r.category,
            "tags": r.tags,
            "confidence": r.confidence,
            "error": None,
        }
    except Exception as exc:
        return {"category": None, "tags": [], "confidence": 0.0, "error": str(exc)}


def _write_json(path: Path, account: str, fetched_at: datetime, providers: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account": account,
        "fetched_at": fetched_at.isoformat(),
        "providers_compared": providers,
        "email_count": len(rows),
        "emails": rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(path: Path, account: str, fetched_at: datetime, providers: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# LLM Classification Comparison")
    lines.append("")
    lines.append(f"- **Account**: `{account}`")
    lines.append(f"- **Fetched at**: {fetched_at.isoformat()}")
    lines.append(f"- **Providers**: {', '.join(providers)}")
    lines.append(f"- **Emails**: {len(rows)}")

    if len(providers) >= 2:
        a, b = providers[0], providers[1]
        cat_agree = sum(
            1 for r in rows
            if r["results"].get(a, {}).get("category")
            and r["results"][a]["category"] == r["results"].get(b, {}).get("category")
        )
        lines.append(f"- **Category agreement ({a} vs {b})**: {cat_agree}/{len(rows)}")

    lines.append("")
    lines.append("---")

    for i, row in enumerate(rows, 1):
        lines.append("")
        lines.append(f"## #{i} — {row['subject'] or '(no subject)'}")
        lines.append("")
        lines.append(f"- **From**: {row['sender']}")
        lines.append(f"- **Date**: {row['date']}")
        lines.append(f"- **ID**: `{row['id']}`")
        snippet = (row["snippet"] or "").replace("\n", " ").strip()
        if snippet:
            lines.append(f"- **Snippet**: {snippet[:400]}")
        lines.append("")
        lines.append("| Provider | Category | Tags | Confidence | Error |")
        lines.append("|---|---|---|---|---|")
        for name in providers:
            r = row["results"].get(name, {})
            cat = f"`{r.get('category')}`" if r.get("category") else "—"
            tags = ", ".join(r.get("tags") or []) or "—"
            conf = f"{r.get('confidence', 0):.2f}" if r.get("category") else "—"
            err = (r.get("error") or "").replace("|", "\\|")
            lines.append(f"| {name} | {cat} | {tags} | {conf} | {err} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run comparison between Gemini and a local LLM on the same emails."
    )
    parser.add_argument("--limit", type=int, default=10, help="How many recent emails to fetch (default: 10)")
    parser.add_argument("--no-gemini", action="store_true", help="Skip the Gemini cloud provider")
    parser.add_argument("--no-local", action="store_true", help="Skip the local LLM provider")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser.add_argument("--output", type=Path, default=Path(f"comparison_{stamp}.md"),
                        help="Markdown report path")
    parser.add_argument("--json", type=Path, default=Path(f"comparison_{stamp}.json"),
                        help="JSON report path")
    args = parser.parse_args()

    if args.no_gemini and args.no_local:
        console.print("[red]Both providers disabled — nothing to do.[/red]")
        return 1

    ensure_env_file()
    load_env()

    console.print("[bold]Authenticating email provider…[/bold]")
    provider = get_email_provider()
    provider.authenticate()
    console.print(f"Connected as [cyan]{provider.account}[/cyan] ({provider.name})")

    until = datetime.now(timezone.utc)
    console.print(f"[bold]Fetching latest {args.limit} emails…[/bold]")
    emails = list(itertools.islice(provider.fetch_emails(since=None, until=until, order="desc"), args.limit))
    console.print(f"Got [green]{len(emails)}[/green] emails.\n")

    if not emails:
        console.print("[yellow]No emails to compare.[/yellow]")
        return 0

    llms = _init_llms(args.no_gemini, args.no_local)
    if not llms:
        console.print("[red]No LLMs available — check your config.[/red]")
        return 1
    provider_names = list(llms.keys())
    console.print(f"Comparing providers: [magenta]{', '.join(provider_names)}[/magenta]\n")

    rows: list[dict] = []
    for i, email in enumerate(emails, 1):
        console.print(
            f"[bold cyan]#{i}[/bold cyan] {email.subject[:70] or '(no subject)'}  "
            f"[dim]from {email.sender[:40]}[/dim]"
        )
        per_email: dict[str, dict] = {}
        for name, cat in llms.items():
            result = _classify_one(cat, email)
            per_email[name] = result
            if result["error"]:
                console.print(f"   [magenta]{name:<8}[/magenta] [red]ERROR[/red] {result['error']}")
            else:
                tags = f" [{', '.join(result['tags'])}]" if result["tags"] else ""
                console.print(
                    f"   [magenta]{name:<8}[/magenta] → "
                    f"[cyan]{result['category']}[/cyan]{tags} "
                    f"[dim](conf {result['confidence']:.2f})[/dim]"
                )
        rows.append({
            "id": email.id,
            "date": email.date.isoformat(),
            "sender": email.sender,
            "subject": email.subject,
            "snippet": email.snippet,
            "results": per_email,
        })

    _write_json(args.json, provider.account, until, provider_names, rows)
    _write_markdown(args.output, provider.account, until, provider_names, rows)

    console.print(f"\n[bold green]Done.[/bold green]")
    console.print(f"  Markdown → [yellow]{args.output}[/yellow]")
    console.print(f"  JSON     → [yellow]{args.json}[/yellow]")

    if len(provider_names) >= 2:
        a, b = provider_names[0], provider_names[1]
        agree = sum(
            1 for r in rows
            if r["results"][a].get("category")
            and r["results"][a]["category"] == r["results"][b].get("category")
        )
        console.print(f"  Category agreement ({a} vs {b}): [bold]{agree}/{len(rows)}[/bold]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
