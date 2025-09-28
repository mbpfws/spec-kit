#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer",
#     "rich",
#     "platformdirs",
#     "readchar",
#     "httpx",
# ]
# ///
"""
Specify CLI - Setup tool for Specify projects

Usage:
    uvx specify-cli.py init <project-name>
    uvx specify-cli.py init --here

Or install globally:
    uv tool install --from specify-cli.py specify-cli
    specify init <project-name>
    specify init --here
"""

import os
import subprocess
import sys
import zipfile
import tempfile
import shutil
import shlex
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import typer
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich.live import Live
from rich.align import Align
from rich.table import Table
from rich.tree import Tree
from typer.core import TyperGroup

# For cross-platform keyboard input
import readchar
import ssl
import truststore

ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
client = httpx.Client(verify=ssl_context)

def _github_token(cli_token: str | None = None) -> str | None:
    """Return sanitized GitHub token (cli arg takes precedence) or None."""
    return ((cli_token or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()) or None

def _github_auth_headers(cli_token: str | None = None) -> dict:
    """Return Authorization header dict only when a non-empty token exists."""
    token = _github_token(cli_token)
    return {"Authorization": f"Bearer {token}"} if token else {}

# Constants
AI_CHOICES = {
    "copilot": "GitHub Copilot",
    "claude": "Claude Code",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
    "qwen": "Qwen Code",
    "opencode": "opencode",
    "codex": "Codex CLI",
    "windsurf": "Windsurf",
    "kilocode": "Kilo Code",
    "auggie": "Auggie CLI",
    "roo": "Roo Code",
}
# Add script type choices
SCRIPT_TYPE_CHOICES = {"sh": "POSIX Shell (bash/zsh)", "ps": "PowerShell"}

CLASSIFICATION_STATE_DIRNAME = Path(".specify") / "state"
CLASSIFICATION_FILENAME = "project-classification.json"
CONFIG_FILE_MARKERS = {
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "composer.json",
    "Gemfile",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "next.config.js",
    "vite.config.js",
    "vite.config.ts",
    "tsconfig.json",
    "Makefile",
}
MAX_CLASSIFICATION_SCAN = 800
VALID_PROJECT_TYPES = {"auto", "greenfield", "brownfield", "ongoing"}
SCAN_EXCLUDE_DIRS = {".git", ".specify", "__pycache__", "node_modules", ".venv", ".idea", ".vscode"}


def _safe_git_commit_count(project_path: Path) -> int:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(result.stdout.strip() or "0")
    except Exception:
        return 0


def _scan_project_state(project_path: Path) -> dict:
    file_count = 0
    config_hits: list[str] = []
    sample_paths: list[str] = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in SCAN_EXCLUDE_DIRS]
        for name in files:
            file_count += 1
            full_path = Path(root) / name
            rel_path = full_path.relative_to(project_path)
            if len(sample_paths) < 6:
                sample_paths.append(str(rel_path))
            if name in CONFIG_FILE_MARKERS and len(config_hits) < 12:
                config_hits.append(str(rel_path))
            if file_count >= MAX_CLASSIFICATION_SCAN:
                break
        if file_count >= MAX_CLASSIFICATION_SCAN:
            break

    has_specify = (project_path / ".specify").exists()
    has_specs_dir = (project_path / "specs").exists()
    git_commits = _safe_git_commit_count(project_path)

    return {
        "file_count": file_count,
        "config_hits": config_hits,
        "sample_paths": sample_paths,
        "has_specify": has_specify,
        "has_specs_dir": has_specs_dir,
        "git_commits": git_commits,
    }


def classify_project_state(project_path: Path, override: str = "auto", debug: bool = False) -> dict:
    override_normalized = (override or "auto").lower()
    if override_normalized not in VALID_PROJECT_TYPES:
        raise typer.BadParameter(
            f"Invalid project type '{override}'. Choose from {', '.join(sorted(VALID_PROJECT_TYPES))}."
        )

    project_path = project_path.resolve()
    path_exists = project_path.exists()

    if not path_exists:
        detected = {
            "project_type": "greenfield",
            "confidence": 0.98,
            "reason": "target directory does not exist yet",
            "stats": {"file_count": 0, "git_commits": 0, "config_hits": []},
            "signals": ["Target directory absent: treating as new project"],
            "safeguard_required": False,
            "requires_confirmation": False,
        }
    else:
        scan = _scan_project_state(project_path)
        file_count = scan["file_count"]
        config_hits = scan["config_hits"]
        git_commits = scan["git_commits"]
        has_specify = scan["has_specify"]
        has_specs_dir = scan["has_specs_dir"]

        auto_type = "greenfield"
        if has_specify or has_specs_dir:
            auto_type = "ongoing"
        if git_commits >= 20 or len(config_hits) >= 2 or file_count >= 120:
            auto_type = "brownfield"
        elif auto_type != "ongoing" and (git_commits >= 1 or len(config_hits) >= 1 or file_count >= 12):
            auto_type = "ongoing"

        confidence = 0.75
        if auto_type == "greenfield":
            confidence = 0.95 if file_count == 0 else 0.8
        elif auto_type == "ongoing":
            confidence = 0.65 + (0.05 * min(git_commits, 4))
            confidence += 0.05 * min(len(config_hits), 2)
            if has_specify:
                confidence += 0.1
            confidence = min(confidence, 0.9)
        elif auto_type == "brownfield":
            confidence = 0.7
            if git_commits >= 20:
                confidence += 0.1
            if len(config_hits) >= 2:
                confidence += 0.1
            if file_count >= 120:
                confidence += 0.05
            confidence = min(confidence, 0.98)

        detected = {
            "project_type": auto_type,
            "confidence": confidence,
            "reason": "heuristic analysis",
            "stats": {
                "file_count": file_count,
                "git_commits": git_commits,
                "config_hits": config_hits,
                "sample_paths": scan["sample_paths"],
                "has_specify": has_specify,
                "has_specs_dir": has_specs_dir,
            },
            "signals": [],
            "safeguard_required": auto_type == "brownfield",
            "requires_confirmation": path_exists and file_count > 0 and auto_type != "greenfield",
        }

        detected["signals"].append(f"Files detected: {file_count}")
        if git_commits:
            detected["signals"].append(f"Git commits detected: {git_commits}")
        if config_hits:
            detected["signals"].append(
                "Config markers: " + ", ".join(config_hits[:4]) + (" …" if len(config_hits) > 4 else "")
            )
        if has_specify:
            detected["signals"].append("Existing .specify directory present")
        if has_specs_dir:
            detected["signals"].append("Existing specs/ directory present")

    override_applied = override_normalized != "auto"
    if override_applied:
        detected["signals"].append(f"Manual override requested: {override_normalized}")
        detected["override_origin"] = override_normalized
        detected["project_type"] = override_normalized
        detected["confidence"] = 0.99
        detected["reason"] = "manual override"
        detected["requires_confirmation"] = detected.get("requires_confirmation", False) or override_normalized in {"brownfield", "ongoing"}
        detected["safeguard_required"] = override_normalized == "brownfield"

    migration_recommendations: list[str] = []
    if detected["project_type"] == "brownfield":
        migration_recommendations = [
            "Create a backup branch before initializing templates",
            "Document mapping between existing architecture and Spec Kit artifacts",
            "Review dependency versions to avoid template overwrites",
        ]
    elif detected["project_type"] == "ongoing":
        migration_recommendations = [
            "Align new artifacts with existing feature directories",
            "Verify SPECIFY_FEATURE matches current working branch",
        ]

    detected.update(
        {
            "analysis_timestamp": datetime.utcnow().isoformat() + "Z",
            "confidence_score": int(round(detected["confidence"] * 100)),
            "migration_recommendations": migration_recommendations,
            "override_applied": override_applied,
            "existing_files_count": detected["stats"]["file_count"] if path_exists else 0,
        }
    )

    warnings: list[str] = []
    if detected["project_type"] == "brownfield" and detected["stats"]["git_commits"] == 0:
        warnings.append("Significant file volume without git history detected")
    if detected["project_type"] == "greenfield" and detected["existing_files_count"] > 0:
        warnings.append("Non-empty directory classified as greenfield; review before proceeding")
    if override_applied:
        warnings.append("Project type forced via --project-type")
    detected["warnings"] = warnings

    if debug:
        console.print(Panel(json.dumps(detected, indent=2), title="Classification Debug", border_style="magenta"))

    return detected


def persist_classification(project_path: Path, classification: dict) -> None:
    try:
        state_dir = project_path / CLASSIFICATION_STATE_DIRNAME
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / CLASSIFICATION_FILENAME
        payload = classification.copy()
        payload["persisted_at"] = datetime.utcnow().isoformat() + "Z"
        with target.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except Exception as exc:
        console.print(Panel(f"Warning: failed to persist classification ({exc})", border_style="yellow"))


def render_classification_panel(classification: dict) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="left", style="yellow", width=18)
    table.add_column(justify="left", style="white")

    project_label = classification["project_type"].replace("_", " ").title()
    table.add_row("Project Type", project_label)
    table.add_row("Confidence", f"{classification['confidence_score']}/100")
    stats = classification.get("stats", {})
    table.add_row("Files Scanned", str(stats.get("file_count", 0)))
    table.add_row("Git Commits", str(stats.get("git_commits", 0)))

    config_hits = stats.get("config_hits", [])
    if config_hits:
        table.add_row("Config Files", ", ".join(config_hits[:3]) + (" …" if len(config_hits) > 3 else ""))

    summary_lines: list[str] = []
    for recommendation in classification.get("migration_recommendations", []):
        summary_lines.append(f"• {recommendation}")
    if not summary_lines:
        summary_lines.append("No additional safeguards required.")

    panel_body = Table.grid()
    panel_body.add_row(table)
    panel_body.add_row("")
    panel_body.add_row("Recommendations:")
    for line in summary_lines:
        panel_body.add_row(f"  {line}")

    if classification.get("warnings"):
        panel_body.add_row("")
        panel_body.add_row("Warnings:")
        for warning in classification["warnings"]:
            panel_body.add_row(f"  • {warning}")

    return Panel(panel_body, title="Project Classification", border_style="yellow", padding=(1, 2))

# Claude CLI local installation path after migrate-installer
CLAUDE_LOCAL_PATH = Path.home() / ".claude" / "local" / "claude"

# ASCII Art Banner
BANNER = """
███████╗██████╗ ███████╗ ██████╗██╗███████╗██╗   ██╗
██╔════╝██╔══██╗██╔════╝██╔════╝██║██╔════╝╚██╗ ██╔╝
███████╗██████╔╝█████╗  ██║     ██║█████╗   ╚████╔╝ 
╚════██║██╔═══╝ ██╔══╝  ██║     ██║██╔══╝    ╚██╔╝  
███████║██║     ███████╗╚██████╗██║██║        ██║   
╚══════╝╚═╝     ╚══════╝ ╚═════╝╚═╝╚═╝        ╚═╝   
"""

TAGLINE = "GitHub Spec Kit - Spec-Driven Development Toolkit"
class StepTracker:
    """Track and render hierarchical steps without emojis, similar to Claude Code tree output.
    Supports live auto-refresh via an attached refresh callback.
    """
    def __init__(self, title: str):
        self.title = title
        self.steps = []  # list of dicts: {key, label, status, detail}
        self.status_order = {"pending": 0, "running": 1, "done": 2, "error": 3, "skipped": 4}
        self._refresh_cb = None  # callable to trigger UI refresh

    def attach_refresh(self, cb):
        self._refresh_cb = cb

    def add(self, key: str, label: str):
        if key not in [s["key"] for s in self.steps]:
            self.steps.append({"key": key, "label": label, "status": "pending", "detail": ""})
            self._maybe_refresh()

    def start(self, key: str, detail: str = ""):
        self._update(key, status="running", detail=detail)

    def complete(self, key: str, detail: str = ""):
        self._update(key, status="done", detail=detail)

    def error(self, key: str, detail: str = ""):
        self._update(key, status="error", detail=detail)

    def skip(self, key: str, detail: str = ""):
        self._update(key, status="skipped", detail=detail)

    def _update(self, key: str, status: str, detail: str):
        for s in self.steps:
            if s["key"] == key:
                s["status"] = status
                if detail:
                    s["detail"] = detail
                self._maybe_refresh()
                return
        # If not present, add it
        self.steps.append({"key": key, "label": key, "status": status, "detail": detail})
        self._maybe_refresh()

    def _maybe_refresh(self):
        if self._refresh_cb:
            try:
                self._refresh_cb()
            except Exception:
                pass

    def render(self):
        tree = Tree(f"[cyan]{self.title}[/cyan]", guide_style="grey50")
        for step in self.steps:
            label = step["label"]
            detail_text = step["detail"].strip() if step["detail"] else ""

            # Circles (unchanged styling)
            status = step["status"]
            if status == "done":
                symbol = "[green]●[/green]"
            elif status == "pending":
                symbol = "[green dim]○[/green dim]"
            elif status == "running":
                symbol = "[cyan]○[/cyan]"
            elif status == "error":
                symbol = "[red]●[/red]"
            elif status == "skipped":
                symbol = "[yellow]○[/yellow]"
            else:
                symbol = " "

            if status == "pending":
                # Entire line light gray (pending)
                if detail_text:
                    line = f"{symbol} [bright_black]{label} ({detail_text})[/bright_black]"
                else:
                    line = f"{symbol} [bright_black]{label}[/bright_black]"
            else:
                # Label white, detail (if any) light gray in parentheses
                if detail_text:
                    line = f"{symbol} [white]{label}[/white] [bright_black]({detail_text})[/bright_black]"
                else:
                    line = f"{symbol} [white]{label}[/white]"

            tree.add(line)
        return tree



MINI_BANNER = """
╔═╗╔═╗╔═╗╔═╗╦╔═╗╦ ╦
╚═╗╠═╝║╣ ║  ║╠╣ ╚╦╝
╚═╝╩  ╚═╝╚═╝╩╚   ╩ 
"""

def get_key():
    """Get a single keypress in a cross-platform way using readchar."""
    key = readchar.readkey()
    
    # Arrow keys
    if key == readchar.key.UP:
        return 'up'
    if key == readchar.key.DOWN:
        return 'down'
    
    # Enter/Return
    if key == readchar.key.ENTER:
        return 'enter'
    
    # Escape
    if key == readchar.key.ESC:
        return 'escape'
        
    # Ctrl+C
    if key == readchar.key.CTRL_C:
        raise KeyboardInterrupt

    return key



def select_with_arrows(options: dict, prompt_text: str = "Select an option", default_key: str = None) -> str:
    """
    Interactive selection using arrow keys with Rich Live display.
    
    Args:
        options: Dict with keys as option keys and values as descriptions
        prompt_text: Text to show above the options
        default_key: Default option key to start with
        
    Returns:
        Selected option key
    """
    option_keys = list(options.keys())
    if default_key and default_key in option_keys:
        selected_index = option_keys.index(default_key)
    else:
        selected_index = 0
    
    selected_key = None

    def create_selection_panel():
        """Create the selection panel with current selection highlighted."""
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan", justify="left", width=3)
        table.add_column(style="white", justify="left")
        
        for i, key in enumerate(option_keys):
            if i == selected_index:
                table.add_row("▶", f"[cyan]{key}[/cyan] [dim]({options[key]})[/dim]")
            else:
                table.add_row(" ", f"[cyan]{key}[/cyan] [dim]({options[key]})[/dim]")
        
        table.add_row("", "")
        table.add_row("", "[dim]Use ↑/↓ to navigate, Enter to select, Esc to cancel[/dim]")
        
        return Panel(
            table,
            title=f"[bold]{prompt_text}[/bold]",
            border_style="cyan",
            padding=(1, 2)
        )
    
    console.print()

    def run_selection_loop():
        nonlocal selected_key, selected_index
        with Live(create_selection_panel(), console=console, transient=True, auto_refresh=False) as live:
            while True:
                try:
                    key = get_key()
                    if key == 'up':
                        selected_index = (selected_index - 1) % len(option_keys)
                    elif key == 'down':
                        selected_index = (selected_index + 1) % len(option_keys)
                    elif key == 'enter':
                        selected_key = option_keys[selected_index]
                        break
                    elif key == 'escape':
                        console.print("\n[yellow]Selection cancelled[/yellow]")
                        raise typer.Exit(1)
                    
                    live.update(create_selection_panel(), refresh=True)

                except KeyboardInterrupt:
                    console.print("\n[yellow]Selection cancelled[/yellow]")
                    raise typer.Exit(1)

    run_selection_loop()

    if selected_key is None:
        console.print("\n[red]Selection failed.[/red]")
        raise typer.Exit(1)

    # Suppress explicit selection print; tracker / later logic will report consolidated status
    return selected_key



console = Console()


class BannerGroup(TyperGroup):
    """Custom group that shows banner before help."""
    
    def format_help(self, ctx, formatter):
        # Show banner before help
        show_banner()
        super().format_help(ctx, formatter)


app = typer.Typer(
    name="specify",
    help="Setup tool for Specify spec-driven development projects",
    add_completion=False,
    invoke_without_command=True,
    cls=BannerGroup,
)


def show_banner():
    """Display the ASCII art banner."""
    # Create gradient effect with different colors
    banner_lines = BANNER.strip().split('\n')
    colors = ["bright_blue", "blue", "cyan", "bright_cyan", "white", "bright_white"]
    
    styled_banner = Text()
    for i, line in enumerate(banner_lines):
        color = colors[i % len(colors)]
        styled_banner.append(line + "\n", style=color)
    
    console.print(Align.center(styled_banner))
    console.print(Align.center(Text(TAGLINE, style="italic bright_yellow")))
    console.print()


@app.callback()
def callback(ctx: typer.Context):
    """Show banner when no subcommand is provided."""
    # Show banner only when no subcommand and no help flag
    # (help is handled by BannerGroup)
    if ctx.invoked_subcommand is None and "--help" not in sys.argv and "-h" not in sys.argv:
        show_banner()
        console.print(Align.center("[dim]Run 'specify --help' for usage information[/dim]"))
        console.print()


def run_command(cmd: list[str], check_return: bool = True, capture: bool = False, shell: bool = False) -> Optional[str]:
    """Run a shell command and optionally capture output."""
    try:
        if capture:
            result = subprocess.run(cmd, check=check_return, capture_output=True, text=True, shell=shell)
            return result.stdout.strip()
        else:
            subprocess.run(cmd, check=check_return, shell=shell)
            return None
    except subprocess.CalledProcessError as e:
        if check_return:
            console.print(f"[red]Error running command:[/red] {' '.join(cmd)}")
            console.print(f"[red]Exit code:[/red] {e.returncode}")
            if hasattr(e, 'stderr') and e.stderr:
                console.print(f"[red]Error output:[/red] {e.stderr}")
            raise
        return None


def check_tool_for_tracker(tool: str, tracker: StepTracker) -> bool:
    """Check if a tool is installed and update tracker."""
    if shutil.which(tool):
        tracker.complete(tool, "available")
        return True
    else:
        tracker.error(tool, "not found")
        return False


def check_tool(tool: str, install_hint: str) -> bool:
    """Check if a tool is installed."""
    
    # Special handling for Claude CLI after `claude migrate-installer`
    # See: https://github.com/mbpfws/spec-kit/issues/123
    # The migrate-installer command REMOVES the original executable from PATH
    # and creates an alias at ~/.claude/local/claude instead
    # This path should be prioritized over other claude executables in PATH
    if tool == "claude":
        if CLAUDE_LOCAL_PATH.exists() and CLAUDE_LOCAL_PATH.is_file():
            return True
    
    if shutil.which(tool):
        return True
    else:
        return False


def is_git_repo(path: Path = None) -> bool:
    """Check if the specified path is inside a git repository."""
    if path is None:
        path = Path.cwd()
    
    if not path.is_dir():
        return False

    try:
        # Use git command to check if inside a work tree
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=True,
            capture_output=True,
            cwd=path,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def init_git_repo(project_path: Path, quiet: bool = False) -> bool:
    """Initialize a git repository in the specified path.
    quiet: if True suppress console output (tracker handles status)
    """
    try:
        original_cwd = Path.cwd()
        os.chdir(project_path)
        if not quiet:
            console.print("[cyan]Initializing git repository...[/cyan]")
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit from Specify template"], check=True, capture_output=True)
        if not quiet:
            console.print("[green]✓[/green] Git repository initialized")
        return True
        
    except subprocess.CalledProcessError as e:
        if not quiet:
            console.print(f"[red]Error initializing git repository:[/red] {e}")
        return False
    finally:
        os.chdir(original_cwd)


def get_git_history_length(project_path: Path) -> int:
    """Get number of git commits for maturity assessment"""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=project_path,
            check=True,
            capture_output=True,
            text=True
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0


def detect_config_files(project_path: Path) -> list:
    """Detect configuration files that indicate project complexity"""
    config_patterns = [
        '*.json', '*.yaml', '*.yml', '*.toml', '*.ini', '*.conf',
        'Dockerfile', 'docker-compose.*', '.env*', 'Makefile'
    ]
    config_files = []
    for pattern in config_patterns:
        config_files.extend(project_path.glob(pattern))
    return config_files


def detect_tech_stack(project_path: Path, tech_patterns: dict) -> dict:
    """Detect technology stack from file patterns"""
    for filename, tech_info in tech_patterns.items():
        if (project_path / filename).exists():
            return tech_info
    return {'type': 'unknown', 'confidence': 0.1}


def calculate_confidence(file_count: int, git_commits: int, 
                        config_complexity: int, tech_stack: dict) -> int:
    """Calculate brownfield confidence score (0-100)"""
    score = 0
    
    # File count scoring (max 30 points)
    if file_count > 20: score += 30
    elif file_count > 10: score += 20
    elif file_count > 5: score += 10
    
    # Git history scoring (max 30 points)
    if git_commits > 50: score += 30
    elif git_commits > 20: score += 20
    elif git_commits > 10: score += 10
    
    # Configuration complexity (max 20 points)
    if config_complexity > 10: score += 20
    elif config_complexity > 5: score += 15
    elif config_complexity > 2: score += 10
    
    # Technology stack detection (max 20 points)
    if tech_stack.get('confidence', 0) > 0.8: score += 20
    elif tech_stack.get('confidence', 0) > 0.5: score += 10
    
    return min(score, 100)


def determine_project_type(confidence_score: int, existing_files: list) -> str:
    """Determine project type based on confidence and files"""
    if confidence_score > 70:
        return 'mature_brownfield'
    elif confidence_score > 40:
        return 'moderate_brownfield'
    elif confidence_score > 20:
        return 'light_brownfield'
    else:
        return 'greenfield'


def generate_migration_path(confidence_score: int) -> list:
    """Generate migration recommendations based on confidence score"""
    if confidence_score > 70:
        return [
            'Full project analysis recommended',
            'Create comprehensive backup',
            'Document existing architecture',
            'Plan gradual migration strategy'
        ]
    elif confidence_score > 40:
        return [
            'Selective integration approach',
            'Preserve core functionality',
            'Add spec-kit incrementally'
        ]
    else:
        return [
            'Standard spec-kit initialization',
            'Minimal interference with existing code'
        ]


def analyze_existing_project(project_path: Path) -> dict:
    """Detect technology stacks and project maturity"""
    # Technology detection patterns (existing file group compatible)
    tech_patterns = {
        'package.json': {'type': 'nodejs', 'confidence': 0.9},
        'requirements.txt': {'type': 'python', 'confidence': 0.9},
        'pyproject.toml': {'type': 'python', 'confidence': 0.95},
        'Cargo.toml': {'type': 'rust', 'confidence': 0.95},
        'pom.xml': {'type': 'java', 'confidence': 0.9},
        'build.gradle': {'type': 'java', 'confidence': 0.9},
        'go.mod': {'type': 'go', 'confidence': 0.95},
        'composer.json': {'type': 'php', 'confidence': 0.9},
        'Gemfile': {'type': 'ruby', 'confidence': 0.9},
        'mix.exs': {'type': 'elixir', 'confidence': 0.9}
    }
    
    # Project structure analysis (non-destructive)
    existing_files = list(project_path.iterdir())
    git_history = get_git_history_length(project_path)
    config_files = detect_config_files(project_path)
    
    # Confidence scoring (0-100 scale)
    confidence_score = calculate_confidence(
        file_count=len(existing_files),
        git_commits=git_history,
        config_complexity=len(config_files),
        tech_stack=detect_tech_stack(project_path, tech_patterns)
    )
    
    return {
        'confidence_score': confidence_score,
        'project_type': determine_project_type(confidence_score, existing_files),
        'safeguard_required': confidence_score > 30,  # User confirmation threshold
        'migration_recommendations': generate_migration_path(confidence_score),
        'existing_files_count': len(existing_files),
        'git_commits': git_history,
        'config_files_count': len(config_files)
    }


def download_template_from_github(ai_assistant: str, download_dir: Path, *, script_type: str = "sh", verbose: bool = True, show_progress: bool = True, client: httpx.Client = None, debug: bool = False, github_token: str = None) -> Tuple[Path, dict]:
    repo_owner = "mbpfws"
    repo_name = "spec-kit"
    if client is None:
        client = httpx.Client(verify=ssl_context)
    
    if verbose:
        console.print("[cyan]Fetching latest release information...[/cyan]")
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/latest"
    
    try:
        response = client.get(
            api_url,
            timeout=30,
            follow_redirects=True,
            headers=_github_auth_headers(github_token),
        )
        status = response.status_code
        if status != 200:
            msg = f"GitHub API returned {status} for {api_url}"
            if debug:
                msg += f"\nResponse headers: {response.headers}\nBody (truncated 500): {response.text[:500]}"
            raise RuntimeError(msg)
        try:
            release_data = response.json()
        except ValueError as je:
            raise RuntimeError(f"Failed to parse release JSON: {je}\nRaw (truncated 400): {response.text[:400]}")
    except Exception as e:
        console.print(f"[red]Error fetching release information[/red]")
        console.print(Panel(str(e), title="Fetch Error", border_style="red"))
        raise typer.Exit(1)
    
    # Find the template asset for the specified AI assistant
    assets = release_data.get("assets", [])
    pattern = f"spec-kit-template-{ai_assistant}-{script_type}"
    matching_assets = [
        asset for asset in assets
        if pattern in asset["name"] and asset["name"].endswith(".zip")
    ]

    asset = matching_assets[0] if matching_assets else None

    if asset is None:
        console.print(f"[red]No matching release asset found[/red] for [bold]{ai_assistant}[/bold] (expected pattern: [bold]{pattern}[/bold])")
        asset_names = [a.get('name', '?') for a in assets]
        console.print(Panel("\n".join(asset_names) or "(no assets)", title="Available Assets", border_style="yellow"))
        raise typer.Exit(1)

    download_url = asset["browser_download_url"]
    filename = asset["name"]
    file_size = asset["size"]
    
    if verbose:
        console.print(f"[cyan]Found template:[/cyan] {filename}")
        console.print(f"[cyan]Size:[/cyan] {file_size:,} bytes")
        console.print(f"[cyan]Release:[/cyan] {release_data['tag_name']}")

    zip_path = download_dir / filename
    if verbose:
        console.print(f"[cyan]Downloading template...[/cyan]")
    
    try:
        with client.stream(
            "GET",
            download_url,
            timeout=60,
            follow_redirects=True,
            headers=_github_auth_headers(github_token),
        ) as response:
            if response.status_code != 200:
                body_sample = response.text[:400]
                raise RuntimeError(f"Download failed with {response.status_code}\nHeaders: {response.headers}\nBody (truncated): {body_sample}")
            total_size = int(response.headers.get('content-length', 0))
            with open(zip_path, 'wb') as f:
                if total_size == 0:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                else:
                    if show_progress:
                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                            console=console,
                        ) as progress:
                            task = progress.add_task("Downloading...", total=total_size)
                            downloaded = 0
                            for chunk in response.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                                downloaded += len(chunk)
                                progress.update(task, completed=downloaded)
                    else:
                        for chunk in response.iter_bytes(chunk_size=8192):
                            f.write(chunk)
    except Exception as e:
        console.print(f"[red]Error downloading template[/red]")
        detail = str(e)
        if zip_path.exists():
            zip_path.unlink()
        console.print(Panel(detail, title="Download Error", border_style="red"))
        raise typer.Exit(1)
    if verbose:
        console.print(f"Downloaded: {filename}")
    metadata = {
        "filename": filename,
        "size": file_size,
        "release": release_data["tag_name"],
        "asset_url": download_url
    }
    return zip_path, metadata


def copy_local_templates(local_templates_path: str, project_path: Path, ai_assistant: str, script_type: str, is_current_dir: bool = False, *, verbose: bool = True, tracker: StepTracker | None = None) -> Path:
    """Copy templates from local directory instead of downloading from GitHub."""
    import shutil
    
    local_path = Path(local_templates_path).resolve()
    if not local_path.exists():
        raise RuntimeError(f"Local templates directory does not exist: {local_path}")
    
    # Template structure mapping
    template_dirs = {
        "claude": ".claude",
        "gemini": ".gemini", 
        "copilot": ".github",
        "cursor": ".cursor",
        "qwen": ".qwen",
        "opencode": ".opencode",
        "codex": ".codex",
        "windsurf": ".windsurf",
        "kilocode": ".kilocode",
        "auggie": ".augment",
        "roo": ".roo"
    }
    
    if tracker:
        tracker.start("local-copy", f"using local templates from {local_path}")
    
    try:
        # Copy .specify directory (templates, scripts, memory)
        specify_src = local_path / ".specify"
        if specify_src.exists():
            specify_dst = project_path / ".specify"
            if specify_dst.exists():
                shutil.rmtree(specify_dst)
            shutil.copytree(specify_src, specify_dst)
            
            # Update script permissions on Unix systems
            if os.name != "nt":
                scripts_dir = specify_dst / "scripts"
                if scripts_dir.exists():
                    for script_file in scripts_dir.rglob("*.sh"):
                        script_file.chmod(0o755)
        
        # Copy agent-specific directory
        if ai_assistant in template_dirs:
            agent_dir = template_dirs[ai_assistant]
            agent_src = local_path / agent_dir
            if agent_src.exists():
                agent_dst = project_path / agent_dir
                if agent_dst.exists():
                    shutil.rmtree(agent_dst)
                shutil.copytree(agent_src, agent_dst)
        
        if tracker:
            tracker.complete("local-copy", f"copied templates to {project_path}")
        
        if verbose and not tracker:
            console.print(f"[green]Local templates copied successfully[/green]")
            
    except Exception as e:
        if tracker:
            tracker.error("local-copy", str(e))
        raise RuntimeError(f"Failed to copy local templates: {e}")
    
    return project_path


def download_and_extract_template(project_path: Path, ai_assistant: str, script_type: str, is_current_dir: bool = False, *, verbose: bool = True, tracker: StepTracker | None = None, client: httpx.Client = None, debug: bool = False, github_token: str = None, local_templates: Optional[str] = None) -> Path:
    """Download the latest release and extract it to create a new project.
    Returns project_path. Uses tracker if provided (with keys: fetch, download, extract, cleanup)
    """
    # Use local templates if provided
    if local_templates:
        return copy_local_templates(
            local_templates,
            project_path,
            ai_assistant,
            script_type,
            is_current_dir,
            verbose=verbose,
            tracker=tracker
        )
    
    current_dir = Path.cwd()
    
    # Step: fetch + download combined
    if tracker:
        tracker.start("fetch", "contacting GitHub API")
    try:
        zip_path, meta = download_template_from_github(
            ai_assistant,
            current_dir,
            script_type=script_type,
            verbose=verbose and tracker is None,
            show_progress=(tracker is None),
            client=client,
            debug=debug,
            github_token=github_token
        )
        if tracker:
            tracker.complete("fetch", f"release {meta['release']} ({meta['size']:,} bytes)")
            tracker.add("download", "Download template")
            tracker.complete("download", meta['filename'])
    except Exception as e:
        if tracker:
            tracker.error("fetch", str(e))
        else:
            if verbose:
                console.print(f"[red]Error downloading template:[/red] {e}")
        raise
    
    if tracker:
        tracker.add("extract", "Extract template")
        tracker.start("extract")
    elif verbose:
        console.print("Extracting template...")
    
    try:
        # Create project directory only if not using current directory
        if not is_current_dir:
            project_path.mkdir(parents=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # List all files in the ZIP for debugging
            zip_contents = zip_ref.namelist()
            if tracker:
                tracker.start("zip-list")
                tracker.complete("zip-list", f"{len(zip_contents)} entries")
            elif verbose:
                console.print(f"[cyan]ZIP contains {len(zip_contents)} items[/cyan]")
            
            # For current directory, extract to a temp location first
            if is_current_dir:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    zip_ref.extractall(temp_path)
                    
                    # Check what was extracted
                    extracted_items = list(temp_path.iterdir())
                    if tracker:
                        tracker.start("extracted-summary")
                        tracker.complete("extracted-summary", f"temp {len(extracted_items)} items")
                    elif verbose:
                        console.print(f"[cyan]Extracted {len(extracted_items)} items to temp location[/cyan]")
                    
                    # Handle GitHub-style ZIP with a single root directory
                    source_dir = temp_path
                    if len(extracted_items) == 1 and extracted_items[0].is_dir():
                        source_dir = extracted_items[0]
                        if tracker:
                            tracker.add("flatten", "Flatten nested directory")
                            tracker.complete("flatten")
                        elif verbose:
                            console.print(f"[cyan]Found nested directory structure[/cyan]")
                    
                    # Copy contents to current directory
                    for item in source_dir.iterdir():
                        dest_path = project_path / item.name
                        if item.is_dir():
                            if dest_path.exists():
                                if verbose and not tracker:
                                    console.print(f"[yellow]Merging directory:[/yellow] {item.name}")
                                # Recursively copy directory contents
                                for sub_item in item.rglob('*'):
                                    if sub_item.is_file():
                                        rel_path = sub_item.relative_to(item)
                                        dest_file = dest_path / rel_path
                                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                                        shutil.copy2(sub_item, dest_file)
                            else:
                                shutil.copytree(item, dest_path)
                        else:
                            if dest_path.exists() and verbose and not tracker:
                                console.print(f"[yellow]Overwriting file:[/yellow] {item.name}")
                            shutil.copy2(item, dest_path)
                    if verbose and not tracker:
                        console.print(f"[cyan]Template files merged into current directory[/cyan]")
            else:
                # Extract directly to project directory (original behavior)
                zip_ref.extractall(project_path)
                
                # Check what was extracted
                extracted_items = list(project_path.iterdir())
                if tracker:
                    tracker.start("extracted-summary")
                    tracker.complete("extracted-summary", f"{len(extracted_items)} top-level items")
                elif verbose:
                    console.print(f"[cyan]Extracted {len(extracted_items)} items to {project_path}:[/cyan]")
                    for item in extracted_items:
                        console.print(f"  - {item.name} ({'dir' if item.is_dir() else 'file'})")
                
                # Handle GitHub-style ZIP with a single root directory
                if len(extracted_items) == 1 and extracted_items[0].is_dir():
                    # Move contents up one level
                    nested_dir = extracted_items[0]
                    temp_move_dir = project_path.parent / f"{project_path.name}_temp"
                    # Move the nested directory contents to temp location
                    shutil.move(str(nested_dir), str(temp_move_dir))
                    # Remove the now-empty project directory
                    project_path.rmdir()
                    # Rename temp directory to project directory
                    shutil.move(str(temp_move_dir), str(project_path))
                    if tracker:
                        tracker.add("flatten", "Flatten nested directory")
                        tracker.complete("flatten")
                    elif verbose:
                        console.print(f"[cyan]Flattened nested directory structure[/cyan]")
                    
    except Exception as e:
        if tracker:
            tracker.error("extract", str(e))
        else:
            if verbose:
                console.print(f"[red]Error extracting template:[/red] {e}")
                if debug:
                    console.print(Panel(str(e), title="Extraction Error", border_style="red"))
        # Clean up project directory if created and not current directory
        if not is_current_dir and project_path.exists():
            shutil.rmtree(project_path)
        raise typer.Exit(1)
    else:
        if tracker:
            tracker.complete("extract")
    finally:
        if tracker:
            tracker.add("cleanup", "Remove temporary archive")
        # Clean up downloaded ZIP file
        if zip_path.exists():
            zip_path.unlink()
            if tracker:
                tracker.complete("cleanup")
            elif verbose:
                console.print(f"Cleaned up: {zip_path.name}")
    
    return project_path


def ensure_executable_scripts(project_path: Path, tracker: StepTracker | None = None) -> None:
    """Ensure POSIX .sh scripts under .specify/scripts (recursively) have execute bits (no-op on Windows)."""
    if os.name == "nt":
        return  # Windows: skip silently
    scripts_root = project_path / ".specify" / "scripts"
    if not scripts_root.is_dir():
        return
    failures: list[str] = []
    updated = 0
    for script in scripts_root.rglob("*.sh"):
        try:
            if script.is_symlink() or not script.is_file():
                continue
            try:
                with script.open("rb") as f:
                    if f.read(2) != b"#!":
                        continue
            except Exception:
                continue
            st = script.stat(); mode = st.st_mode
            if mode & 0o111:
                continue
            new_mode = mode
            if mode & 0o400: new_mode |= 0o100
            if mode & 0o040: new_mode |= 0o010
            if mode & 0o004: new_mode |= 0o001
            if not (new_mode & 0o100):
                new_mode |= 0o100
            os.chmod(script, new_mode)
            updated += 1
        except Exception as e:
            failures.append(f"{script.relative_to(scripts_root)}: {e}")
    if tracker:
        detail = f"{updated} updated" + (f", {len(failures)} failed" if failures else "")
        tracker.add("chmod", "Set script permissions recursively")
        (tracker.error if failures else tracker.complete)("chmod", detail)
    else:
        if updated:
            console.print(f"[cyan]Updated execute permissions on {updated} script(s) recursively[/cyan]")
        if failures:
            console.print("[yellow]Some scripts could not be updated:[/yellow]")
            for f in failures:
                console.print(f"  - {f}")

@app.command()
def init(
    project_name: str = typer.Argument(None, help="Name for your new project directory (optional if using --here)"),
    ai_assistant: str = typer.Option(None, "--ai", help="AI assistant to use: claude, gemini, copilot, cursor, qwen, opencode, codex, windsurf, kilocode, or auggie"),
    script_type: str = typer.Option(None, "--script", help="Script type to use: sh or ps"),
    ignore_agent_tools: bool = typer.Option(False, "--ignore-agent-tools", help="Skip checks for AI agent tools like Claude Code"),
    no_git: bool = typer.Option(False, "--no-git", help="Skip git repository initialization"),
    here: bool = typer.Option(False, "--here", help="Initialize project in the current directory instead of creating a new one"),
    force: bool = typer.Option(False, "--force", help="Force merge/overwrite when using --here (skip confirmation)"),
    skip_tls: bool = typer.Option(False, "--skip-tls", help="Skip SSL/TLS verification (not recommended)"),
    debug: bool = typer.Option(False, "--debug", help="Show verbose diagnostic output for network and extraction failures"),
    github_token: str = typer.Option(None, "--github-token", help="GitHub token to use for API requests (or set GH_TOKEN or GITHUB_TOKEN environment variable)"),
    project_type: str = typer.Option("auto", "--project-type", help="Override project classification (auto, greenfield, brownfield, ongoing)"),
    classification_debug: bool = typer.Option(False, "--classification-debug", help="Print classification diagnostics"),
    local_templates: Optional[str] = typer.Option(None, "--local-templates", help="Use local templates directory instead of downloading from GitHub"),
):
    """
    Initialize a new Specify project from the latest template.
    
    This command will:
    1. Check that required tools are installed (git is optional)
    2. Let you choose your AI assistant (Claude Code, Gemini CLI, GitHub Copilot, Cursor, Qwen Code, opencode, Codex CLI, Windsurf, Kilo Code, or Auggie CLI)
    3. Download the appropriate template from GitHub
    4. Extract the template to a new project directory or current directory
    5. Initialize a fresh git repository (if not --no-git and no existing repo)
    6. Optionally set up AI assistant commands
    
    Examples:
        specify init my-project
        specify init my-project --ai claude
        specify init my-project --ai gemini
        specify init my-project --ai copilot --no-git
        specify init my-project --ai cursor
        specify init my-project --ai qwen
        specify init my-project --ai opencode
        specify init my-project --ai codex
        specify init my-project --ai windsurf
        specify init my-project --ai auggie
        specify init --ignore-agent-tools my-project
        specify init --here --ai claude
        specify init --here --ai codex
        specify init --here
        specify init --here --force  # Skip confirmation when current directory not empty
    """
    # Show banner first
    show_banner()
    
    # Validate arguments
    if here and project_name:
        console.print("[red]Error:[/red] Cannot specify both project name and --here flag")
        raise typer.Exit(1)
    
    if not here and not project_name:
        console.print("[red]Error:[/red] Must specify either a project name or use --here flag")
        raise typer.Exit(1)
    
    # Determine project directory
    if here:
        project_name = Path.cwd().name
        project_path = Path.cwd()
    else:
        project_path = Path(project_name).resolve()
        # Check if project directory already exists
        if project_path.exists():
            error_panel = Panel(
                f"Directory '[cyan]{project_name}[/cyan]' already exists\n"
                "Please choose a different project name or remove the existing directory.",
                title="[red]Directory Conflict[/red]",
                border_style="red",
                padding=(1, 2)
            )
            console.print()
            console.print(error_panel)
            raise typer.Exit(1)
    
    # Create formatted setup info with column alignment
    current_dir = Path.cwd()
    
    setup_lines = [
        "[cyan]Specify Project Setup[/cyan]",
        "",
        f"{'Project':<15} [green]{project_path.name}[/green]",
        f"{'Working Path':<15} [dim]{current_dir}[/dim]",
        f"{'Project Type':<15} [yellow]{classification['project_type'].replace('_', ' ').title()}[/yellow]",
        f"{'Confidence':<15} [yellow]{classification['confidence_score']}/100[/yellow]",
    ]
    
    # Add target path only if different from working dir
    if not here:
        setup_lines.append(f"{'Target Path':<15} [dim]{project_path}[/dim]")
    
    console.print(Panel("\n".join(setup_lines), border_style="cyan", padding=(1, 2)))
    
    # Check git only if we might need it (not --no-git)
    # Only set to True if the user wants it and the tool is available
    should_init_git = False
    if not no_git:
        should_init_git = check_tool("git", "https://git-scm.com/downloads")
        if not should_init_git:
            console.print("[yellow]Git not found - will skip repository initialization[/yellow]")

    # AI assistant selection
    if ai_assistant:
        if ai_assistant not in AI_CHOICES:
            console.print(f"[red]Error:[/red] Invalid AI assistant '{ai_assistant}'. Choose from: {', '.join(AI_CHOICES.keys())}")
            raise typer.Exit(1)
        selected_ai = ai_assistant
    else:
        # Use arrow-key selection interface
        selected_ai = select_with_arrows(
            AI_CHOICES, 
            "Choose your AI assistant:", 
            "copilot"
        )
    
    # Check agent tools unless ignored
    if not ignore_agent_tools:
        agent_tool_missing = False
        install_url = ""
        if selected_ai == "claude":
            if not check_tool("claude", "https://docs.anthropic.com/en/docs/claude-code/setup"):
                install_url = "https://docs.anthropic.com/en/docs/claude-code/setup"
                agent_tool_missing = True
        elif selected_ai == "gemini":
            if not check_tool("gemini", "https://github.com/google-gemini/gemini-cli"):
                install_url = "https://github.com/google-gemini/gemini-cli"
                agent_tool_missing = True
        elif selected_ai == "qwen":
            if not check_tool("qwen", "https://github.com/QwenLM/qwen-code"):
                install_url = "https://github.com/QwenLM/qwen-code"
                agent_tool_missing = True
        elif selected_ai == "opencode":
            if not check_tool("opencode", "https://opencode.ai"):
                install_url = "https://opencode.ai"
                agent_tool_missing = True
        elif selected_ai == "codex":
            if not check_tool("codex", "https://github.com/openai/codex"):
                install_url = "https://github.com/openai/codex"
                agent_tool_missing = True
        elif selected_ai == "auggie":
            if not check_tool("auggie", "https://docs.augmentcode.com/cli/setup-auggie/install-auggie-cli"):
                install_url = "https://docs.augmentcode.com/cli/setup-auggie/install-auggie-cli"
                agent_tool_missing = True
        # GitHub Copilot and Cursor checks are not needed as they're typically available in supported IDEs

        if agent_tool_missing:
            error_panel = Panel(
                f"[cyan]{selected_ai}[/cyan] not found\n"
                f"Install with: [cyan]{install_url}[/cyan]\n"
                f"{AI_CHOICES[selected_ai]} is required to continue with this project type.\n\n"
                "Tip: Use [cyan]--ignore-agent-tools[/cyan] to skip this check",
                title="[red]Agent Detection Error[/red]",
                border_style="red",
                padding=(1, 2)
            )
            console.print()
            console.print(error_panel)
            raise typer.Exit(1)
    
    # Determine script type (explicit, interactive, or OS default)
    if script_type:
        if script_type not in SCRIPT_TYPE_CHOICES:
            console.print(f"[red]Error:[/red] Invalid script type '{script_type}'. Choose from: {', '.join(SCRIPT_TYPE_CHOICES.keys())}")
            raise typer.Exit(1)
        selected_script = script_type
    else:
        # Auto-detect default
        default_script = "ps" if os.name == "nt" else "sh"
        # Provide interactive selection similar to AI if stdin is a TTY
        if sys.stdin.isatty():
            selected_script = select_with_arrows(SCRIPT_TYPE_CHOICES, "Choose script type (or press Enter)", default_script)
        else:
            selected_script = default_script
    
    console.print(f"[cyan]Selected AI assistant:[/cyan] {selected_ai}")
    console.print(f"[cyan]Selected script type:[/cyan] {selected_script}")
    
    # Download and set up project
    # New tree-based progress (no emojis); include earlier substeps
    tracker = StepTracker("Initialize Specify Project")
    # Flag to allow suppressing legacy headings
    sys._specify_tracker_active = True
    # Pre steps recorded as completed before live rendering
    tracker.add("precheck", "Check required tools")
    tracker.complete("precheck", "ok")
    tracker.add("ai-select", "Select AI assistant")
    tracker.complete("ai-select", f"{selected_ai}")
    tracker.add("script-select", "Select script type")
    tracker.complete("script-select", selected_script)
    
    # Project classification step
    tracker.add("classification", "Project classification")
    tracker.complete("classification", f"{classification['project_type']} ({classification['confidence_score']}/100)")
    
    for key, label in [
        ("fetch", "Fetch latest release"),
        ("download", "Download template"),
        ("extract", "Extract template"),
        ("zip-list", "Archive contents"),
        ("extracted-summary", "Extraction summary"),
        ("chmod", "Ensure scripts executable"),
        ("cleanup", "Cleanup"),
        ("git", "Initialize git repository"),
        ("final", "Finalize")
    ]:
        tracker.add(key, label)

    # Use transient so live tree is replaced by the final static render (avoids duplicate output)
    with Live(tracker.render(), console=console, refresh_per_second=8, transient=True) as live:
        tracker.attach_refresh(lambda: live.update(tracker.render()))
        try:
            # Create a httpx client with verify based on skip_tls
            verify = not skip_tls
            local_ssl_context = ssl_context if verify else False
            local_client = httpx.Client(verify=local_ssl_context)

            download_and_extract_template(project_path, selected_ai, selected_script, here, verbose=False, tracker=tracker, client=local_client, debug=debug, github_token=github_token, local_templates=local_templates)

            # Ensure scripts are executable (POSIX)
            ensure_executable_scripts(project_path, tracker=tracker)

            persist_classification(project_path, classification)

            # Git step
            if not no_git:
                tracker.start("git")
                if is_git_repo(project_path):
                    tracker.complete("git", "existing repo detected")
                elif should_init_git:
                    if init_git_repo(project_path, quiet=True):
                        tracker.complete("git", "initialized")
                    else:
                        tracker.error("git", "init failed")
                else:
                    tracker.skip("git", "git not available")
            else:
                tracker.skip("git", "--no-git flag")

            tracker.complete("final", "project ready")
        except Exception as e:
            tracker.error("final", str(e))
            console.print(Panel(f"Initialization failed: {e}", title="Failure", border_style="red"))
            if debug:
                _env_pairs = [
                    ("Python", sys.version.split()[0]),
                    ("Platform", sys.platform),
                    ("CWD", str(Path.cwd())),
                ]
                _label_width = max(len(k) for k, _ in _env_pairs)
                env_lines = [f"{k.ljust(_label_width)} → [bright_black]{v}[/bright_black]" for k, v in _env_pairs]
                console.print(Panel("\n".join(env_lines), title="Debug Environment", border_style="magenta"))
            if not here and project_path.exists():
                shutil.rmtree(project_path)
            raise typer.Exit(1)
        finally:
            # Force final render
            pass

    # Final static tree (ensures finished state visible after Live context ends)
    console.print(tracker.render())
    console.print("\n[bold green]Project ready.[/bold green]")
    
    # Agent folder security notice
    agent_folder_map = {
        "claude": ".claude/",
        "gemini": ".gemini/",
        "cursor": ".cursor/",
        "qwen": ".qwen/",
        "opencode": ".opencode/",
        "codex": ".codex/",
        "windsurf": ".windsurf/",
        "kilocode": ".kilocode/",
        "auggie": ".augment/",
        "copilot": ".github/",
        "roo": ".roo/"
    }
    
    if selected_ai in agent_folder_map:
        agent_folder = agent_folder_map[selected_ai]
        security_notice = Panel(
            f"Some agents may store credentials, auth tokens, or other identifying and private artifacts in the agent folder within your project.\n"
            f"Consider adding [cyan]{agent_folder}[/cyan] (or parts of it) to [cyan].gitignore[/cyan] to prevent accidental credential leakage.",
            title="[yellow]Agent Folder Security[/yellow]",
            border_style="yellow",
            padding=(1, 2)
        )
        console.print()
        console.print(security_notice)
    
    # Project-type specific next steps
    steps_lines = []
    if not here:
        steps_lines.append(f"1. Go to the project folder: [cyan]cd {project_name}[/cyan]")
        step_num = 2
    else:
        steps_lines.append("1. You're already in the project directory!")
        step_num = 2

    if classification["project_type"] in {"brownfield", "ongoing"}:
        label = "BROWNFIELD PROJECT INTEGRATION" if classification["project_type"] == "brownfield" else "ONGOING PROJECT ALIGNMENT"
        steps_lines.append(f"{step_num}. [yellow]{label}[/yellow]")
        recommendations = classification.get("migration_recommendations", [])
        if recommendations:
            for idx, recommendation in enumerate(recommendations, start=1):
                steps_lines.append(f"   {step_num}.{idx} {recommendation}")
        else:
            steps_lines.append(f"   {step_num}.1 Review existing artifacts before applying templates")
        step_num += 1

    # Add Codex-specific setup step if needed
    if selected_ai == "codex":
        codex_path = project_path / ".codex"
        quoted_path = shlex.quote(str(codex_path))
        if os.name == "nt":  # Windows
            cmd = f"setx CODEX_HOME {quoted_path}"
        else:  # Unix-like systems
            cmd = f"export CODEX_HOME={quoted_path}"
        
        steps_lines.append(f"{step_num}. Set [cyan]CODEX_HOME[/cyan] environment variable before running Codex: [cyan]{cmd}[/cyan]")
        step_num += 1

    steps_lines.append(f"{step_num}. Start using slash commands with your AI agent:")
    steps_lines.append("   2.1 [cyan]/constitution[/] - Establish project principles")
    steps_lines.append("   2.2 [cyan]/specify[/] - Create specifications")
    steps_lines.append("   2.3 [cyan]/clarify[/] - Clarify and de-risk specification (run before [cyan]/plan[/cyan])")
    steps_lines.append("   2.4 [cyan]/plan[/] - Create implementation plans")
    steps_lines.append("   2.5 [cyan]/tasks[/] - Generate actionable tasks")
    steps_lines.append("   2.6 [cyan]/analyze[/] - Validate alignment & surface inconsistencies (read-only)")
    steps_lines.append("   2.7 [cyan]/implement[/] - Execute implementation")

    steps_panel = Panel("\n".join(steps_lines), title="Next Steps", border_style="cyan", padding=(1,2))
    console.print()
    console.print(steps_panel)

    if selected_ai == "codex":
        warning_text = """[bold yellow]Important Note:[/bold yellow]

Custom prompts do not yet support arguments in Codex. You may need to manually specify additional project instructions directly in prompt files located in [cyan].codex/prompts/[/cyan].

For more information, see: [cyan]https://github.com/openai/codex/issues/2890[/cyan]"""
        
        warning_panel = Panel(warning_text, title="Slash Commands in Codex", border_style="yellow", padding=(1,2))
        console.print()
        console.print(warning_panel)

@app.command()
def check():
    """Check that all required tools are installed."""
    show_banner()
    console.print("[bold]Checking for installed tools...[/bold]\n")

    tracker = StepTracker("Check Available Tools")
    
    tracker.add("git", "Git version control")
    tracker.add("claude", "Claude Code CLI")
    tracker.add("gemini", "Gemini CLI")
    tracker.add("qwen", "Qwen Code CLI")
    tracker.add("code", "Visual Studio Code")
    tracker.add("code-insiders", "Visual Studio Code Insiders")
    tracker.add("cursor-agent", "Cursor IDE agent")
    tracker.add("windsurf", "Windsurf IDE")
    tracker.add("kilocode", "Kilo Code IDE")
    tracker.add("opencode", "opencode")
    tracker.add("codex", "Codex CLI")
    tracker.add("auggie", "Auggie CLI")
    
    git_ok = check_tool_for_tracker("git", tracker)
    claude_ok = check_tool_for_tracker("claude", tracker)  
    gemini_ok = check_tool_for_tracker("gemini", tracker)
    qwen_ok = check_tool_for_tracker("qwen", tracker)
    code_ok = check_tool_for_tracker("code", tracker)
    code_insiders_ok = check_tool_for_tracker("code-insiders", tracker)
    cursor_ok = check_tool_for_tracker("cursor-agent", tracker)
    windsurf_ok = check_tool_for_tracker("windsurf", tracker)
    kilocode_ok = check_tool_for_tracker("kilocode", tracker)
    opencode_ok = check_tool_for_tracker("opencode", tracker)
    codex_ok = check_tool_for_tracker("codex", tracker)
    auggie_ok = check_tool_for_tracker("auggie", tracker)

    console.print(tracker.render())

    console.print("\n[bold green]Specify CLI is ready to use![/bold green]")

    if not git_ok:
        console.print("[dim]Tip: Install git for repository management[/dim]")
    if not (claude_ok or gemini_ok or cursor_ok or qwen_ok or windsurf_ok or kilocode_ok or opencode_ok or codex_ok or auggie_ok):
        console.print("[dim]Tip: Install an AI assistant for the best experience[/dim]")


def main():
    app()


if __name__ == "__main__":
    main()