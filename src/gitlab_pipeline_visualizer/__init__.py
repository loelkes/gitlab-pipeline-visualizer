#!/usr/bin/env python

import argparse
import sys
import traceback
import requests
from .GitLabPipelineVisualizer import GitLabPipelineVisualizer
from .utils import (
    DEFAULT_MERMAID_CONFIG,
    parse_gitlab_url,
    get_mermaid_config,
    fetch_pipeline_data,
    get_token,
    open_url_in_browser,
)

EPILOG = f"""
The GitLab token can be provided in three ways (in order of precedence):

1. Command line argument --token
2. Environment variable GITLAB_TOKEN
3. Configuration file in one of these locations:

   - Windows: %APPDATA%/gitlab-pipeline-visualizer/config
   - Unix: $XDG_CONFIG_HOME/gitlab-pipeline-visualizer/config (or ~/.config/gitlab-pipeline-visualizer/config)
   - Or: ~/.gitlab-pipeline-visualizer

A default Mermaid configuration is provided: {DEFAULT_MERMAID_CONFIG} This
configuration can be overridden in the config file.

Config file example (INI format):
--------------------------------
[gitlab]
token = glpat-XXXXXXXXXXXXXXXXXXXX

# Optional: override default Mermaid configuration
[mermaid]
config = 
    layout: elk
    theme: dark
    gantt:
      useWidth: 1000
--------------------------------

Note: The mermaid configuration must be indented under the 'config =' line. If
the [mermaid] section is omitted, the default configuration shown above will be
used.

The config will be automatically wrapped in the required Mermaid format:
---
config:
  [your configuration]
---

Created by Claude sonnet 3.5 (https://claude.ai) with the help of [Twidi](https://github.com/twidi).
Refactored by [Christian Lölkes](https://github.com/loelkes).
Source code: https://github.com/twidi/gitlab-pipeline-visualizer/
Online version: https://gitlabviz.pythonanywhere.com/
"""

DESCRIPTION = """
Visualize GitLab CI pipeline as a Mermaid diagram.

Two visualization modes are available:
- timeline: shows the execution timeline of jobs (default)
- deps: shows the dependencies between jobs
"""


def cli():
    parser = argparse.ArgumentParser(
        description=DESCRIPTION, formatter_class=argparse.RawTextHelpFormatter, epilog=EPILOG
    )
    parser.add_argument("url", help="GitLab pipeline URL (e.g., https://gitlab.com/group/project/-/pipelines/123)")
    parser.add_argument("--token", help="GitLab private token")
    parser.add_argument(
        "--mode",
        choices=["timeline", "deps"],
        default="timeline",
        help="visualization mode: timeline (default) or deps",
    )
    parser.add_argument(
        "--output",
        choices=["raw", "view", "edit", "jpg", "png", "svg", "webp", "pdf"],
        default="raw",
        help="""output format:
- raw: raw mermaid document (default)
- view: URL to view diagram on mermaid.live
- edit: URL to edit diagram on mermaid.live
- jpg: URL of jpg image on mermaid.ink
- png: URL of png image on mermaid.ink
- webp: URL for webp image on mermaid.ink
- svg: URL for svg image on mermaid.ink
- pdf: URL for pdf on mermaid.ink""",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the URL in your default web browser (only valid with view or edit outputs)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase verbosity (use -v for URLs, -vv for full API responses)",
    )

    args = parser.parse_args()

    # Validate --open usage
    if args.open and args.output == "raw":
        parser.error("--open option can only be used with URL outputs")

    # Get token from args, env, or config
    token = args.token or get_token()
    if not token:
        print(
            "Error: GitLab token not found. Provide it via --token argument, GITLAB_TOKEN environment variable, or configuration file.",
            file=sys.stderr,
        )
        parser.print_help()
        sys.exit(1)

    try:
        gitlab_url, project_path, pipeline_id = parse_gitlab_url(args.url)

        # Get Mermaid config from config file or use default
        mermaid_config = get_mermaid_config()
        pipeline_data = fetch_pipeline_data(gitlab_url, token, project_path, pipeline_id)
        visualizer = GitLabPipelineVisualizer(pipeline_data)

        # Get the mermaid diagram content
        mermaid_content = visualizer.generate_mermaid_content(args.mode)

        # Handle different output formats
        url = None
        if args.output in ("edit", "view"):
            url = visualizer.generate_mermaid_live_url(mermaid_content, mermaid_config, args.output)
            print(url)
        elif args.output in ("jpg", "png", "webp", "svg", "pdf"):
            url = visualizer.generate_mermaid_ink_url(mermaid_content, mermaid_config, args.output)
            print(url)
        else:
            print(visualizer.generate_mermaid(mermaid_content, mermaid_config))

        # Open URL in browser if requested
        if args.open and url:
            open_url_in_browser(url)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Error accessing GitLab API: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error generating diagram:: {e}, {traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
