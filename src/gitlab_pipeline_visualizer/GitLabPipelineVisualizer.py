import base64
import json
import re
import zlib
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from operator import itemgetter
from .utils import wrap_mermaid_config


class GitLabPipelineVisualizer:
    def __init__(self, pipeline_data):
        self.pipeline_data = pipeline_data["data"]["project"]["pipeline"]

    def deduplicate_jobs(self, jobs):
        """Handle multiple runs of the same job, numbering them and adjusting dependencies.

        For multiple runs of the same job:
        - Add numbered suffixes to identifiers (job_1, job_2, etc.)
        - Force each run after the first to depend on the previous run
        - For jobs depending on a duplicated job, select the last run that ended before
          the dependent job starts

        Args:
            jobs (list): List of job dictionaries

        Returns:
            list: Updated list of jobs with adjusted identifiers and dependencies
        """
        job_runs = defaultdict(list)
        processed_jobs = []

        # First pass: group jobs by base identifier and add numbered suffixes
        for job in jobs:
            base_identifier = job["identifier"]
            runs = job_runs[base_identifier]
            run_number = len(runs) + 1

            # Create a new job with numbered identifier
            new_job = job.copy()
            new_job["base_identifier"] = base_identifier
            new_job["identifier"] = f"{base_identifier}_{run_number}"

            # Force dependency on previous run (except for first run)
            if run_number > 1:
                new_job["needs"] = [f"{base_identifier}_{run_number-1}"]

            runs.append(new_job)
            processed_jobs.append(new_job)

        # Second pass: update dependencies for jobs that depend on duplicated jobs
        for job in processed_jobs:
            updated_needs = []
            job_start = job["startedAt"]

            for need in job["needs"]:
                if need in job_runs:  # This is a duplicated job
                    # Get all runs that ended before this job started (or all, in case we don't have some)
                    eligible_runs = [
                        run for run in job_runs[need] if run["finishedAt"] and run["finishedAt"] < job_start
                    ] or job_runs[need]
                    updated_needs.append(eligible_runs[-1]["identifier"])
                else:
                    # Non-duplicated job - keep as is
                    updated_needs.append(need)

            job["needs"] = updated_needs

        return processed_jobs

    def get_ordered_stages(self, pipeline_data):
        """Get ordered list of stages, including .pre and .post."""
        # Get stages from pipeline data
        stages = [stage["name"] for stage in pipeline_data["stages"]["nodes"]]

        # Check for .pre and .post in jobs
        job_stages = set(job["stage"]["name"] for job in pipeline_data["jobs"]["nodes"])

        # Add .pre at the beginning if it exists in jobs
        if ".pre" in job_stages:
            stages.insert(0, ".pre")

        # Add .post at the end if it exists in jobs
        if ".post" in job_stages:
            stages.append(".post")

        return stages

    def build_dependency_graph(self, jobs, ordered_stages):
        """Build a complete dependency graph based on scheduling type and needs.

        Args:
            jobs (dict): Dictionary of job data, keyed by job identifier
            ordered_stages (list): List of stages in order

        Returns:
            dict: Graph of job dependencies where each key is a job identifier and each value
                  is a list of job identifiers it depends on
        """
        dependencies = {}

        # Create mapping of stages to jobs
        stage_jobs = defaultdict(list)
        for job_id, job in jobs.items():
            stage_jobs[job["stage"]].append(job_id)

        for job_id, job in jobs.items():
            if job["needs"]:
                # If needs are specified, always use them regardless of scheduling type
                dependencies[job_id] = list(job["needs"])
            else:
                # No explicit needs - check scheduling type
                if job.get("schedulingType") == "dag":
                    dependencies[job_id] = []
                else:
                    # Depend on all previous stage jobs
                    deps = set()
                    if job["stage"] in ordered_stages:
                        stage_idx = ordered_stages.index(job["stage"])
                        if stage_idx > 0:
                            for prev_stage in reversed(ordered_stages[:stage_idx]):
                                if prev_stage in stage_jobs:
                                    deps.update(stage_jobs[prev_stage])
                                    break
                    dependencies[job_id] = list(deps)

        return dependencies

    def remove_transitive_dependencies(self, jobs):
        """Remove transitive dependencies from job dependencies.

        If job C depends on jobs A and B, and B also depends on A,
        then we can remove A from C's dependencies since it's redundant.

        Args:
            jobs (dict): Dictionary of job data including dependencies

        Returns:
            dict: Updated jobs dictionary with transitive dependencies removed
        """
        # Create a deep copy to avoid modifying the original
        updated_jobs = deepcopy(jobs)

        # For each job
        for job in updated_jobs.values():
            direct_deps = set(job["needs"])
            indirect_deps = set()

            # Find all indirect dependencies
            for dep in direct_deps:
                if dep in updated_jobs:
                    indirect_deps.update(updated_jobs[dep]["needs"])

            # Remove indirect dependencies from direct dependencies
            job["needs"] = list(direct_deps - indirect_deps)

        return updated_jobs

    def name_to_identifier(self, name):
        return re.sub(r"\W+|^(?=\d)", "_", name)

    def str_to_datetime(self, str_datetime):
        return datetime.fromisoformat(str_datetime.replace("Z", "+00:00")) if str_datetime else None

    def normalize_jobs(self, jobs_data):
        """Simplify jobs data and order them by `startedAt`, removing alls that
        are not in running/success/failed status"""
        # remove needs not present in jobs, it may be optional needs but we
        # don't have the optional flag available in the graphql data
        jobs_names = {job["name"] for job in jobs_data}

        return sorted(
            [
                job
                | {
                    "queuedAt": self.str_to_datetime(job["queuedAt"]),
                    "startedAt": (started_at := self.str_to_datetime(job["startedAt"])),
                    "finishedAt": (
                        self.str_to_datetime(job["finishedAt"])
                        if job["finishedAt"]
                        else started_at + timedelta(seconds=job["duration"])
                    ),
                    "identifier": self.name_to_identifier(job["name"]),
                    "stage": job["stage"]["name"],
                    "needs": (
                        [
                            self.name_to_identifier(node["name"])
                            for node in job["needs"]["nodes"]
                            if node["name"] in jobs_names
                        ]
                        if job.get("needs", {}).get("nodes", [])
                        else []
                    ),
                }
                for job in jobs_data
                if job["status"] in ("SUCCESS", "FAILED", "RUNNING")
                and job.get("queuedAt")
                and job.get("startedAt")
                and job.get("duration")
            ],
            key=itemgetter("startedAt"),
        )

    def process_pipeline_data(self):
        """Process pipeline data and extract dependencies and job information."""
        jobs_data = self.pipeline_data["jobs"]["nodes"]

        # Get ordered stages
        ordered_stages = self.get_ordered_stages(self.pipeline_data)

        # Normalize the jobs
        jobs = self.normalize_jobs(jobs_data)
        jobs = self.deduplicate_jobs(jobs)
        jobs_dict = {job["identifier"]: job for job in jobs}
        jobs_dict = self.remove_transitive_dependencies(jobs_dict)
        dependencies = self.build_dependency_graph(jobs_dict, ordered_stages)
        return ordered_stages, jobs_dict, dependencies

    def format_duration(self, duration_seconds, job_status):
        """Format duration in a Mermaid-friendly way: (Xmn SS) or (SS)"""
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)

        status_part = f", {job_status.lower()}" if job_status == "RUNNING" else ""

        if minutes == 0:
            return f"({seconds}s{status_part})"
        return f"({minutes}mn {seconds:02d}{status_part})"

    def generate_mermaid_deps(self):
        """Generate a Mermaid state diagram representation of the pipeline dependencies."""
        stages, jobs, dependencies = self.process_pipeline_data()

        mermaid = [
            "stateDiagram-v2",
            "",
            "    %% Style definitions",
            "    classDef failed fill:#f2dede,stroke:#a94442,color:#a94442",
            "    classDef running fill:#f2f1de,stroke:#A89642,color:#A89642",
            "",
            '    state "Pipeline dependencies" as pipeline {',
            "",
            "    %% States with duration",
        ]
        # Add job states with duration
        for job_id, job in jobs.items():
            mermaid.append(
                f'    state "{job["name"]}<br>{self.format_duration(job["duration"], job["status"])}" as {job_id}'
            )
        mermaid.append(["", "    %% Dependencies"])

        # Add dependencies
        for job_id, job in jobs.items():
            if not dependencies.get(job_id, []):
                # Jobs with no dependencies start from [*]
                mermaid.append(f"    [*] --> {job_id}")
            else:
                mermaid.append([f"    {dep} --> {job_id}" for dep in dependencies[job_id]])

        # Close the pipeline state
        mermaid.append("    }")

        # Add class declarations for failed jobs after the state definition
        for job_id, job in jobs.items():
            if job["status"] == "FAILED":
                mermaid.append(f"class {job_id} failed")
            if job["status"] == "RUNNING":
                mermaid.append(f"class {job_id} running")

        return "\n".join(mermaid)

    def calculate_job_order(self, jobs, dependencies):
        """Calculate execution order of jobs based on dependencies and start times.

        Args:
            jobs (dict): Dictionary of jobs with their details including startedAt times
            dependencies (dict): Dictionary mapping job IDs to lists of dependency job IDs

        Returns:
            list: Job identifiers in execution order
        """
        # Start with jobs that have no dependencies
        independent_jobs = [job_id for job_id in jobs if job_id not in dependencies or not dependencies[job_id]]

        # Sort independent jobs by start time
        independent_jobs.sort(key=lambda job_id: jobs[job_id]["startedAt"])

        ordered_jobs = []
        processed_jobs = set()

        def process_job_and_dependents(job_id):
            if job_id in processed_jobs:
                return

            processed_jobs.add(job_id)
            ordered_jobs.append(job_id)

            # Find jobs that depend on this one
            dependents = []
            for dependent_id, deps in dependencies.items():
                if job_id in deps and dependent_id not in processed_jobs:
                    dependents.append(dependent_id)

            dependents.sort(key=lambda job_id: jobs[dependent_id]["startedAt"])

            # Process dependents that have all dependencies met
            for dependent_id in dependents:
                if all(dep in processed_jobs for dep in dependencies[dependent_id]):
                    process_job_and_dependents(dependent_id)

        # Process all independent jobs and their dependents
        for job_id in independent_jobs:
            process_job_and_dependents(job_id)

        return ordered_jobs

    @staticmethod
    def clean_gantt_name(name):
        return name.replace(":", " ")

    def generate_mermaid_timeline(self):
        """Generate a Mermaid Gantt chart showing pipeline execution timeline."""
        stages, jobs, dependencies = self.process_pipeline_data()

        # Find earliest start time
        start_times = [job["startedAt"] for job in jobs.values() if job["startedAt"] is not None]
        if not start_times:
            raise ValueError("No jobs with timing information found")

        pipeline_start = min(start_times)

        # Basic Gantt chart setup
        mermaid = [
            "gantt",
            "    title Pipeline timeline",
            "    dateFormat  HH:mm:ss",
            "    axisFormat  %H:%M:%S",
            "    todayMarker off",
            "",
            "    section Jobs",
        ]

        # Get jobs in execution order
        ordered_jobs = self.calculate_job_order(jobs, dependencies)

        # Helper to format job status for Mermaid
        def get_gantt_tags(job):
            return "crit" if job["status"] == "FAILED" else "active" if job["status"] == "RUNNING" else ""

        # Add each job to the timeline
        for job_id in ordered_jobs:
            job = jobs[job_id]

            if job["startedAt"] and job["finishedAt"]:
                # Calculate relative start time from pipeline start
                relative_start = (job["startedAt"] - pipeline_start).total_seconds()
                start_time = f"{int(relative_start // 3600):02d}:{int((relative_start % 3600) // 60):02d}:{int(relative_start % 60):02d}"
                formatted_duration = self.format_duration(job["duration"], job["status"])
                gantt_tags = get_gantt_tags(job)
                gantt_tags_part = f"{gantt_tags}, " if gantt_tags else ""
                mermaid.append(
                    f"    {self.clean_gantt_name(job['name'])} {formatted_duration:<10} :{gantt_tags_part}{job_id}, {start_time}, {job['duration']}s"
                )

        return "\n".join(mermaid)

    def generate_mermaid_content(self, mode):
        """Generate raw Mermaid content based on the selected mode."""
        if mode == "deps":
            return self.generate_mermaid_deps()
        if mode == "timeline":
            return self.generate_mermaid_timeline()
        else:
            raise ValueError(f"Unsupported mode: {mode}")

    @classmethod
    def generate_mermaid(cls, mermaid_content, mermaid_config):
        """Generate a complete Mermaid diagram by combining configuration and content."""
        wrapped_config = wrap_mermaid_config(mermaid_config)
        return wrapped_config + mermaid_content

    @classmethod
    def pako(cls, data):
        compress = zlib.compressobj(9, zlib.DEFLATED, 15, 8, zlib.Z_DEFAULT_STRATEGY)
        return compress.compress(data) + compress.flush()

    @classmethod
    def generate_mermaid_encoded_string(cls, mermaid_content, mermaid_config):
        """Generate the encoded part for an URL for mermaid.live with the diagram content."""
        mermaid_content = cls.generate_mermaid(mermaid_content, mermaid_config)
        # Create the state object expected by mermaid.live
        state = {"code": mermaid_content, "mermaid": '{"theme":"default"}', "autoSync": True, "updateDiagram": True}
        # Encode the state object
        pako = cls.pako(json.dumps(state).encode())
        return "pako:" + base64.urlsafe_b64encode(pako).decode().rstrip("=")

    @classmethod
    def generate_mermaid_live_url(cls, mermaid_content, mermaid_config, mode):
        """Generate a URL for mermaid.live with the diagram content."""
        encoded_string = cls.generate_mermaid_encoded_string(mermaid_content, mermaid_config)
        return f"https://mermaid.live/{mode}#{encoded_string}"

    @classmethod
    def generate_mermaid_ink_url(cls, mermaid_content, mermaid_config, mode):
        encoded_string = cls.generate_mermaid_encoded_string(mermaid_content, mermaid_config)
        path = "pdf" if mode == "pdf" else "img"
        if mode == "jpeg":
            pass
        querystring = "?fit" if mode == "pdf" else f"?type={mode}"
        return f"https://mermaid.ink/{path}/{encoded_string}{querystring}"
