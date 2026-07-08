"""Bluesky (AT Protocol) engagement adapter.

Reuses the same auth as the Bluesky publisher: create a session with handle +
app password, then act via ``com.atproto.repo.createRecord``:
  * like   -> collection ``app.bsky.feed.like``  with a strong-ref subject
  * follow -> collection ``app.bsky.graph.follow`` with the author DID
  * reply  -> collection ``app.bsky.feed.post``   with a reply ref

Discovery uses ``app.bsky.feed.searchPosts``. Sessions are created lazily and
cached for the adapter's lifetime so a whole run shares one token.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import BaseAdapter
from .models import ActionResult, ActionType, Target


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class BlueskyAdapter(BaseAdapter):
    name = "bluesky"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session: tuple[str, str] | None = None  # (accessJwt, did)

    def _pds(self) -> str:
        return self.settings.get_env("BLUESKY_PDS_URL", "https://bsky.social").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.settings.get_env("BLUESKY_HANDLE")) and bool(
            self.settings.get_env("BLUESKY_APP_PASSWORD")
        )

    def _auth(self) -> tuple[str, str]:
        if self._session is None:
            resp = self.client().post(
                f"{self._pds()}/xrpc/com.atproto.server.createSession",
                json={
                    "identifier": self.settings.get_env("BLUESKY_HANDLE"),
                    "password": self.settings.get_env("BLUESKY_APP_PASSWORD"),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._session = (data["accessJwt"], data["did"])
        return self._session

    def _headers(self) -> dict:
        jwt, _ = self._auth()
        return {"Authorization": f"Bearer {jwt}"}

    # ---- discovery -------------------------------------------------------
    def discover(self, queries: list[str], limit: int) -> list[Target]:
        if not self.is_configured():
            return []
        out: list[Target] = []
        seen: set[str] = set()
        _, my_did = self._auth()
        for q in queries:
            try:
                resp = self.client().get(
                    f"{self._pds()}/xrpc/app.bsky.feed.searchPosts",
                    headers=self._headers(),
                    params={"q": q, "limit": min(limit, 25), "sort": "latest"},
                )
                resp.raise_for_status()
                posts = resp.json().get("posts", [])
            except Exception:  # discovery is best-effort per query
                continue
            for p in posts:
                uri = p.get("uri", "")
                author = p.get("author", {})
                did = author.get("did", "")
                if not uri or uri in seen or did == my_did:
                    continue
                seen.add(uri)
                out.append(Target(
                    platform=self.name,
                    key=uri,
                    text=(p.get("record", {}) or {}).get("text", ""),
                    url=self._web_url(author.get("handle", ""), uri),
                    author_id=did,
                    author_handle=author.get("handle", ""),
                    uri=uri,
                    cid=p.get("cid", ""),
                ))
        return out

    @staticmethod
    def _web_url(handle: str, uri: str) -> str:
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        return f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""

    def _create_record(self, collection: str, record: dict) -> dict:
        _, did = self._auth()
        resp = self.client().post(
            f"{self._pds()}/xrpc/com.atproto.repo.createRecord",
            headers=self._headers(),
            json={"repo": did, "collection": collection, "record": record},
        )
        resp.raise_for_status()
        return resp.json()

    # ---- actions ---------------------------------------------------------
    def _do_like(self, target: Target) -> ActionResult:
        self._create_record("app.bsky.feed.like", {
            "$type": "app.bsky.feed.like",
            "createdAt": _now(),
            "subject": {"uri": target.uri, "cid": target.cid},
        })
        return self._result(target, ActionType.LIKE, "executed", url=target.url)

    def _do_follow(self, target: Target) -> ActionResult:
        self._create_record("app.bsky.graph.follow", {
            "$type": "app.bsky.graph.follow",
            "createdAt": _now(),
            "subject": target.author_id,
        })
        return self._result(target, ActionType.FOLLOW, "executed",
                            url=f"https://bsky.app/profile/{target.author_handle}")

    def _do_reply(self, target: Target, comment: str) -> ActionResult:
        ref = {"uri": target.uri, "cid": target.cid}
        data = self._create_record("app.bsky.feed.post", {
            "$type": "app.bsky.feed.post",
            "text": comment,
            "createdAt": _now(),
            "langs": ["en"],
            "reply": {"root": ref, "parent": ref},
        })
        _, my_did = self._auth()
        handle = self.settings.get_env("BLUESKY_HANDLE")
        rkey = data.get("uri", "").rsplit("/", 1)[-1]
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else target.url
        return self._result(target, ActionType.REPLY, "executed", url=url)
