"""Dump repo.json's README + key facts to plain files for the daily writer."""
import json
import pathlib

here = pathlib.Path(__file__).parent
d = json.loads((here / "repo.json").read_text(encoding="utf-8"))
(here / "_readme.md").write_text(d.get("readme_markdown") or "", encoding="utf-8")
facts = [
    f"full_name: {d['full_name']}",
    f"url: {d['html_url']}",
    f"homepage: {d.get('homepage')}",
    f"description: {d['description']}",
    f"topics: {d['topics']}",
    f"stars: {d['stars']}  forks: {d['forks']}  open_issues: {d['open_issues']}",
    f"language: {d['language']}",
    f"license: {d['license']}",
    f"created_at: {d['created_at']}  pushed_at: {d['pushed_at']}",
]
(here / "_facts.txt").write_text("\n".join(facts), encoding="utf-8")
print(f"extracted facts + readme for {d['full_name']} ({d['readme_len']} readme chars)")
