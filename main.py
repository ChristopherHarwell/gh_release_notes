import json
from datetime import datetime
import logging
from typing import Any
from flask import Flask, request, jsonify # type: ignore
from integrations.github.client import GitHubClient, extract_release_data, find_previous_tag

app = Flask(__name__)

def save_request_to_json(data):
    """Save request data to a timestamped JSON file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"webhook_data_{timestamp}.json"
    
    # Decode bytes to string if necessary
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    
    # Parse string to JSON if it's a JSON string
    try:
        json_data = json.loads(data)
    except:
        json_data = {"raw_data": data}
    
    # Save with pretty formatting
    with open(filename, 'w') as f:
        json.dump(json_data, f, indent=4)
    return filename

@app.route("/", methods=["POST"])
def hook():
    # print(request)
    # print(request.data)

    github_client = GitHubClient()
    payload: dict[str, Any] = json.loads(request.data)
    # print(payload)

    owner, repo, tag_name, version_tuple = extract_release_data(payload)

    # Use logging for better visibility than print statements
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    previous_tag = find_previous_tag(github_client, owner, repo, tag_name, logger)

    if not previous_tag:
        # Handle the case where no previous tag is found.
        # For a first release, there might be no previous tag to compare against.
        # In this scenario, you might want to gather all commits up to the current tag,
        # or indicate that this is the initial release.
        logger.info(f"No previous tag found for {owner}/{repo}. Generating release notes for {tag_name} as a new release.")
        # For simplicity, if no previous tag, we'll consider no commits for comparison this time.
        # A more robust solution might fetch all commits up to current_tag if it's the very first release.
        commits = []
    else:
        commits = github_client.get_commits_for_release(owner, repo, previous_tag, tag_name)

    # Categorize commits
    categorized_commits = {
        "feat": [],
        "fix": [],
        "docs": [],
        "style": [],
        "refactor": [],
        "perf": [],
        "test": [],
        "build": [],
        "ci": [],
        "chore": [],
        "revert": [],
        "other": [],
    }

    from integrations.github.constants import CONVENTIONAL_TYPE_MAP # Moved import here to resolve circular dependency if needed

    for commit in commits:
        commit_type = commit.type if commit.type in CONVENTIONAL_TYPE_MAP else "other"
        categorized_commits[commit_type].append({
            "sha": commit.sha,
            "message": commit.message,
            "scope": commit.scope,
            "breaking": commit.breaking,
            "body": commit.body,
            "footer": commit.footer,
            "author": commit.author,
            "date": commit.date.isoformat() # Convert datetime to ISO format string
        })

    release_notes_data = {
        "version": tag_name,
        "date": datetime.now().isoformat(),
        "owner": owner,
        "repo": repo,
        "notes": categorized_commits,
    }

    filename = save_request_to_json(json.dumps(release_notes_data)) # save_request_to_json expects a string
    logger.info(f"Release notes saved to {filename}")

    return jsonify({
        "message": "Release notes generated and saved.",
        "file": filename,
        "release_info": {
            "owner": owner,
            "repo": repo,
            "tag_name": tag_name,
            "version_tuple": version_tuple
        }
    })

if __name__ == "__main__":
    app.run()
