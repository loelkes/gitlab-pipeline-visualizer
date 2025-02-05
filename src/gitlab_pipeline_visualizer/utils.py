#!/usr/bin/env python

import configparser
import json
import os
import webbrowser
from pathlib import Path
from textwrap import dedent, indent
from urllib.parse import urlparse
import requests
from .logger import setup_logging

logger = setup_logging(0)

DEFAULT_MERMAID_CONFIG = """\
gantt:
  useWidth: 1600
"""

GRAPHQL_QUERY = """\
query GetPipelineJobs {
  project(fullPath: "%(PROJECT_PATH)s") {
    pipeline(id: "gid://gitlab/Ci::Pipeline/%(PIPELINE_ID)s") {
      stages {
        nodes {
          name
        }
      }
      jobs(statuses: [SUCCESS, FAILED, RUNNING]%(CURSOR)s) {
        nodes {
          name
          status
          stage {
            name
          }
          schedulingType
          needs {
            nodes {
              name
            }
          }
          startedAt
          finishedAt
          duration
          queuedAt
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}"""


def prepare_graphql_query(project_path, pipeline_id, next_page_cursor=None):
    return GRAPHQL_QUERY % {
        "PROJECT_PATH": project_path,
        "PIPELINE_ID": pipeline_id,
        "CURSOR": f', after: "{next_page_cursor}"' if next_page_cursor else "",
    }


def fetch_pipeline_data(gitlab_url, gitlab_token, project_path, pipeline_id):
    """Fetch pipeline data using GraphQL.

    Args:
        gitlab_url (str): Base GitLab URL
        gitlab_token (str): GitLab API token
        project_path (str): Full project path
        pipeline_id (str): Pipeline ID

    Returns:
        dict: Full API response data
    """
    headers = {
        "Authorization": f"Bearer {gitlab_token}",
        "Content-Type": "application/json",
    }

    has_next_page = True
    next_page_cursor = None
    url = f"{gitlab_url}/api/graphql"

    json_data = None

    while has_next_page:
        logger.info(f"Calling {url} for {project_path=}, {pipeline_id=}, {next_page_cursor=}")
        response = requests.post(
            url,
            headers=headers,
            json={"query": prepare_graphql_query(project_path, pipeline_id, next_page_cursor)},
        )
        try:
            page_data = response.json()
            pagination_data = page_data["data"]["project"]["pipeline"]["jobs"].pop("pageInfo", None) or {}
            logger.debug(json.dumps(response.json(), indent=2))
        except Exception:
            logger.debug(response.content)
            raise

        if json_data:
            json_data["data"]["project"]["pipeline"]["jobs"]["nodes"].extend(
                page_data["data"]["project"]["pipeline"]["jobs"]["nodes"]
            )
        else:
            json_data = page_data
        has_next_page = pagination_data.get("hasNextPage")
        next_page_cursor = pagination_data.get("endCursor") if has_next_page else None

        response.raise_for_status()

    return json_data


def get_config_paths():
    """Get configuration file paths based on the OS."""
    if os.name == "nt":  # Windows
        config_home = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        paths = [
            Path(config_home) / "gitlab-pipeline-visualizer" / "config",
            Path.home() / ".gitlab-pipeline-visualizer",
        ]
    else:  # Unix-like
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        paths = [
            Path(xdg_config_home) / "gitlab-pipeline-visualizer" / "config",
            Path.home() / ".gitlab-pipeline-visualizer",
        ]

    return paths


def get_config():
    """
    Get configuration from config file.
    Returns: configparser.ConfigParser object
    """
    config = configparser.ConfigParser()
    for config_path in get_config_paths():
        if config_path.is_file():
            config.read(config_path)
    return config


def get_token():
    """
    Get GitLab token from environment or config file.
    Returns: token string or None if not found
    """
    # Check environment variable
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        return token

    # Check config files
    config = get_config()
    try:
        return config["gitlab"]["token"]
    except (KeyError, configparser.Error):
        return None


def get_mermaid_config():
    """Get Mermaid configuration from config file or use default.

    Returns: mermaid config string
    """
    config = get_config()
    try:
        config_str = config["mermaid"]["config"].strip()
    except (KeyError, configparser.Error):
        config_str = DEFAULT_MERMAID_CONFIG
    return config_str


def wrap_mermaid_config(config_str):
    """Wrap the config in the required Mermaid format."""
    config_str = indent(dedent(config_str).strip("\n"), "  ")
    return f"---\nconfig:\n{config_str}\n---\n"


def parse_gitlab_url(url):
    """
    Parse a GitLab pipeline URL to extract gitlab url, project path and pipeline ID.
    Example URL: https://gitlab.com/magency/products/iva/-/pipelines/1543446796

    Returns: (gitlab_url, project_path, pipeline_id)
    Raises: ValueError if URL format is invalid
    """
    # Parse the URL
    parsed = urlparse(url)

    # Get gitlab url
    gitlab_url = f"{parsed.scheme}://{parsed.netloc}"

    # Split the path into components and remove empty strings
    path_parts = [p for p in parsed.path.split("/") if p]

    # Check if path matches expected format:
    # [project_parts...] '-' 'pipelines' pipeline_id
    try:
        pipeline_index = path_parts.index("pipelines")
        if pipeline_index < 2 or path_parts[pipeline_index - 1] != "-":
            raise ValueError()

        # Pipeline ID is the last component
        pipeline_id = path_parts[pipeline_index + 1]
        if not pipeline_id.isdigit():
            raise ValueError()

        # Project path is everything before the '-'
        project_path = "/".join(path_parts[: pipeline_index - 1])

        return gitlab_url, project_path, pipeline_id

    except (ValueError, IndexError) as error:
        raise ValueError(
            "Invalid GitLab pipeline URL path format. "
            "Expected format: https://GITLAB_HOST/GROUP/PROJECT/-/pipelines/PIPELINE_ID"
        ) from error


def open_url_in_browser(url):
    """Open URL in the default web browser."""

    webbrowser.open(url)
