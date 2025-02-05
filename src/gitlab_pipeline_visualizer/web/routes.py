import json
from flask import jsonify, render_template, request, Blueprint
from flask_cors import cross_origin
from ..utils import (
    DEFAULT_MERMAID_CONFIG,
    fetch_pipeline_data,
    parse_gitlab_url,
    prepare_graphql_query,
)
from ..logger import setup_logging
from ..GitLabPipelineVisualizer import GitLabPipelineVisualizer

logger = setup_logging(0)
routes = Blueprint("root", "root")


@routes.route("/")
def index():
    return render_template("index.html", default_config=DEFAULT_MERMAID_CONFIG)


@routes.route("/get_query")
@cross_origin(methods=["GET"])
def get_query():
    try:
        project_path = request.args.get("project_path")
        pipeline_id = request.args.get("pipeline_id")
        next_page_cursor = request.args.get("next_page_cursor")
    except Exception as e:
        logger.exception(e)
        return jsonify({"error": "Missing parameters"}), 400
    return jsonify({"graphql_query": prepare_graphql_query(project_path, pipeline_id, next_page_cursor)})


@routes.route("/visualize", methods=["POST"])
@cross_origin(methods=["POST"])
def visualize():
    try:
        # Get common form data
        mode = request.form.get("mode", "timeline")
        mermaid_config = request.form.get("mermaid_config", DEFAULT_MERMAID_CONFIG)

        # Determine input method and get pipeline data
        if request.form.get("pipeline_data", "").strip():
            # Direct pipeline data input
            try:
                pipeline_data = request.form.get("pipeline_data")
                if not pipeline_data:
                    raise ValueError("Pipeline data is empty or invalid")
                pipeline_data = json.loads(pipeline_data)
            except Exception as e:
                raise ValueError(f"Invalid pipeline data format: {e}") from e
        else:
            # GitLab credentials input
            gitlab_url = request.form.get("url")
            gitlab_token = request.form.get("token")

            if not gitlab_url or not gitlab_token:
                raise ValueError(
                    "Either the pipeline data or both GitLab URL and token are required when not providing pipeline data"
                )

            # Parse the GitLab URL and fetch data
            base_url, project_path, pipeline_id = parse_gitlab_url(gitlab_url)
            pipeline_data = fetch_pipeline_data(base_url, gitlab_token, project_path, pipeline_id)

        # Create visualizer instance
        visualizer = GitLabPipelineVisualizer(pipeline_data)

        # Generate diagram
        mermaid_content = visualizer.generate_mermaid_content(mode)

        # Generate all three formats
        return jsonify(
            {
                "raw": visualizer.generate_mermaid(mermaid_content, mermaid_config),
                "editUrl": visualizer.generate_mermaid_live_url(mermaid_content, mermaid_config, "edit"),
                "viewUrl": visualizer.generate_mermaid_live_url(mermaid_content, mermaid_config, "view"),
                "jpgUrl": visualizer.generate_mermaid_ink_url(mermaid_content, mermaid_config, "jpg"),
                "pngUrl": visualizer.generate_mermaid_ink_url(mermaid_content, mermaid_config, "png"),
                "svgUrl": visualizer.generate_mermaid_ink_url(mermaid_content, mermaid_config, "svg"),
                "webpUrl": visualizer.generate_mermaid_ink_url(mermaid_content, mermaid_config, "webp"),
                "pdfUrl": visualizer.generate_mermaid_ink_url(mermaid_content, mermaid_config, "pdf"),
            }
        )

    except Exception as e:
        logger.exception(e)
        return jsonify({"error": e}), 400
