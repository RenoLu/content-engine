"""Stage the top N eligible fresh AI repos for batch authoring.

Dedups against repos already in the queue / published dirs and a small set of
already-published repos that predate the queue. Writes each pick's facts + README
under _manual/batch/<NN>/ for a writer (subagent) to author from.

Usage:  python _manual/discover_batch.py [N]
"""
import json
import shutil
import sys
from pathlib import Path

from content_engine.config import load_settings
from content_engine.ranking import RepoRanker, readme_filter_reason
from content_engine.research import RepoResearcher
from content_engine.sources.github import GitHubTrendingSource

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
HERE = Path(__file__).parent
BATCH = HERE / "batch"
KNOWN_PUBLISHED = {"Significant-Gravitas/AutoGPT", "openclaw/openclaw"}


def _excluded() -> set[str]:
    names = set(KNOWN_PUBLISHED)
    for d in (HERE / "queue", HERE / "published"):
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    names.add(json.loads(f.read_text(encoding="utf-8")).get("repo", ""))
                except Exception:
                    pass
    return {n for n in names if n}


s = load_settings()
candidates = GitHubTrendingSource(s).fetch_candidates()
exclude = _excluded()
ranker = RepoRanker(s)
ranked = ranker.prefilter_and_score(candidates, exclude)
researcher = RepoResearcher(s)

if BATCH.exists():
    shutil.rmtree(BATCH)
BATCH.mkdir(parents=True)

picked = []
for repo in ranker.eligible(ranked):
    if len(picked) >= N:
        break
    researcher.enrich(repo)
    if readme_filter_reason(repo, s):
        continue
    idx = len(picked) + 1
    d = BATCH / f"{idx:02d}"
    d.mkdir()
    (d / "_readme.md").write_text(repo.readme_markdown or "", encoding="utf-8")
    facts = [
        f"full_name: {repo.full_name}", f"url: {repo.html_url}",
        f"homepage: {repo.homepage}", f"description: {repo.description}",
        f"topics: {repo.topics}",
        f"stars: {repo.stars}  forks: {repo.forks}  open_issues: {repo.open_issues}",
        f"language: {repo.language}", f"license: {repo.license}",
        f"created_at: {repo.created_at}  pushed_at: {repo.pushed_at}",
    ]
    (d / "_facts.txt").write_text("\n".join(facts), encoding="utf-8")
    (d / "repo.json").write_text(json.dumps(repo.to_dict(), ensure_ascii=False), encoding="utf-8")
    picked.append(repo.full_name)
    print(f"{idx:02d}: {repo.full_name}  ({repo.stars} stars, created {repo.created_at}, {repo.readme_len} readme chars)")

print(f"staged {len(picked)} repos under {BATCH}")
