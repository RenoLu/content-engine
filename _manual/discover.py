"""Phase 1: select today's trending repo exactly as the pipeline would, enrich
it (README + links), and dump the facts to _manual/repo.json. No store writes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from content_engine.config import load_settings
from content_engine.ranking import RepoRanker, readme_filter_reason
from content_engine.research import RepoResearcher
from content_engine.sources.github import GitHubTrendingSource
from content_engine.storage import Store

s = load_settings()
src = GitHubTrendingSource(s)
candidates = src.fetch_candidates()
print(f"candidates: {len(candidates)}", file=sys.stderr)

used = set()
try:
    used = Store(s.db_path).used_repo_names()
except Exception as e:  # noqa: BLE001
    print(f"(no store / used set: {e})", file=sys.stderr)

ranker = RepoRanker(s)
ranked = ranker.prefilter_and_score(candidates, used)
researcher = RepoResearcher(s)

chosen = None
for repo in ranker.eligible(ranked)[:12]:
    researcher.enrich(repo)
    reason = readme_filter_reason(repo, s)
    if reason:
        print(f"skip {repo.full_name}: {reason}", file=sys.stderr)
        continue
    chosen = repo
    break

if chosen is None:
    print("NO_CANDIDATE", file=sys.stderr)
    sys.exit(2)

out = Path(__file__).parent / "repo.json"
out.write_text(json.dumps(chosen.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

print("=== SELECTED ===")
print(f"full_name : {chosen.full_name}")
print(f"desc      : {chosen.description}")
print(f"url       : {chosen.html_url}")
print(f"homepage  : {chosen.homepage}")
print(f"language  : {chosen.language}")
print(f"stars     : {chosen.stars}   forks: {chosen.forks}   open_issues: {chosen.open_issues}")
print(f"topics    : {chosen.topics}")
print(f"license   : {chosen.license}")
print(f"created   : {chosen.created_at}   pushed: {chosen.pushed_at}")
print(f"readme_len: {chosen.readme_len}")
print(f"score     : {chosen.score:.3f}  breakdown={chosen.score_breakdown}")
print(f"links     : {chosen.extracted_links[:10]}")
print(f"\nwrote {out}")
