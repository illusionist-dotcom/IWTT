"""Local HTTP application and static-file server for IWTT.

v0.2 adds the Recall & Graph Intelligence routes.  They live in separate
``_*_api_v02`` dispatchers so the v0.1 route table above them is untouched and
the diff stays reviewable; ``/api/search`` in particular still calls
``Vault.search()`` and returns exactly what it returned in v0.1.
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .ai import AIUnavailable, provider_from_environment
from .store import APP_VERSION, ConflictError, ValidationError, Vault

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
MAX_REQUEST_BYTES = 20 * 1024 * 1024
LOG = logging.getLogger("iwtt")


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def _qstr(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return (query.get(key, [default])[0] or default)


def _qint(query: dict[str, list[str]], key: str, default: int) -> int:
    raw = query.get(key, [None])[0]
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} must be an integer") from exc


def _qlist(query: dict[str, list[str]], key: str) -> list[str] | None:
    """Comma-separated multi-value parameter; absent means 'no filter'."""
    raw = query.get(key)
    if not raw:
        return None
    values = [part.strip() for chunk in raw for part in chunk.split(",") if part.strip()]
    return values or None


class IWTTHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, vault: Vault):
        super().__init__(address, handler)
        self.vault = vault


class RequestHandler(BaseHTTPRequestHandler):
    server: IWTTHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "IWTT/0.2"

    def log_message(self, fmt: str, *args) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    @property
    def vault(self) -> Vault:
        return self.server.vault

    def _send_bytes(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, KeyError):
            status = 404
        elif isinstance(exc, AIUnavailable):
            status = 503
        elif isinstance(exc, (ValidationError, ValueError, TypeError, json.JSONDecodeError)):
            status = 400
        else:
            LOG.exception("IWTT request failed", exc_info=exc)
            self._send_json(500, {"error": "Internal server error"})
            return
        message = str(exc)
        if isinstance(exc, KeyError) and message.startswith("'") and message.endswith("'"):
            message = message[1:-1]
        self._send_json(status, {"error": message})

    def _read_json(self):
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValidationError("Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValidationError("Request body is too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Request body must be valid UTF-8 JSON") from exc

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path.startswith("/api/"):
            try:
                self._get_api(path, query)
            except Exception as exc:
                self._error(exc)
            return
        self._serve_static(path)

    def _get_api(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/health":
            provider = self.vault.ai_provider
            self._send_json(200, {
                "application": "Illusionist World of Thoughts & Tricks",
                "shortName": "IWTT",
                "version": APP_VERSION,
                "storage": "SQLite",
                "dataDirectory": str(self.vault.data_dir),
                "databasePath": str(self.vault.db_path),
                "ai": {"available": bool(getattr(provider, "available", False)),
                       "provider": getattr(provider, "name", "NoAIProvider"),
                       "model": getattr(provider, "model", None)},
                "recall": {
                    "available": True,
                    "requiresAI": False,
                    "requiresNetwork": False,
                    "embeddingsEnabled": False,
                    "note": (
                        "Recall V2 is deterministic lexical + fuzzy + graph retrieval over "
                        "derived indexes. It is not semantic search and does not claim to "
                        "understand meaning."
                    ),
                },
            })
            return
        if path == "/api/stats":
            self._send_json(200, self.vault.stats())
            return
        if path == "/api/ai/status":
            provider = self.vault.ai_provider
            self._send_json(200, {
                "available": bool(getattr(provider, "available", False)),
                "provider": getattr(provider, "name", "NoAIProvider"),
                "model": getattr(provider, "model", None),
                "boundary": "No thoughts leave this machine unless an endpoint is explicitly configured and an AI action is invoked.",
            })
            return
        if path == "/api/thoughts":
            status = query.get("status", ["ACTIVE"])[0]
            thought_type = query.get("type", [None])[0]
            self._send_json(200, {"thoughts": self.vault.list_thoughts(status, thought_type)})
            return
        if path == "/api/search":
            q = query.get("q", [""])[0]
            include_graveyard = _truthy(query.get("include_graveyard", ["true"])[0])
            self._send_json(200, {"query": q, "results": self.vault.search(q, include_graveyard)})
            return
        if path == "/api/timeline":
            limit = int(query.get("limit", [100])[0])
            self._send_json(200, {"events": self.vault.timeline(limit)})
            return
        if path == "/api/relationships":
            status = query.get("status", ["LIVE"])[0]
            self._send_json(200, {"relationships": self.vault.list_relationships(status)})
            return
        if path == "/api/demo/status":
            self._send_json(200, self.vault.demo_status())
            return
        if path == "/api/data/export.json":
            include_derived = _truthy(query.get("include_derived", ["false"])[0])
            payload = self.vault.export_vault(include_derived=include_derived)
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            filename = "iwtt-vault-export.json"
            self._send_bytes(200, body, "application/json; charset=utf-8", {
                "Content-Disposition": f'attachment; filename="{filename}"'
            })
            return
        if path == "/api/data/export.md":
            body = self.vault.export_markdown().encode("utf-8")
            self._send_bytes(200, body, "text/markdown; charset=utf-8", {
                "Content-Disposition": 'attachment; filename="iwtt-vault-export.md"'
            })
            return
        thought_match = re.fullmatch(r"/api/thoughts/([^/]+)", path)
        if thought_match:
            self._send_json(200, {"thought": self.vault.get_thought(thought_match.group(1))})
            return
        if self._get_api_v02(path, query):
            return
        self._send_json(404, {"error": "API route not found"})

    # ------------------------------------------------------------------ v0.2 --
    # Recall & Graph Intelligence GET routes.  Read-only except where a call
    # refreshes the derived index, which is always rebuildable.
    # ------------------------------------------------------------------

    def _recall_kwargs(self, query: dict[str, list[str]]) -> dict:
        kwargs: dict = {}
        if _qstr(query, "q") or "q" in query:
            kwargs["query"] = _qstr(query, "q")
        limit = _qint(query, "limit", 0)
        if limit:
            kwargs["limit"] = limit
        if "include_graveyard" in query:
            kwargs["include_graveyard"] = _truthy(_qstr(query, "include_graveyard", "true"))
        for key, name in (("types", "types"), ("tags", "tags"), ("statuses", "statuses")):
            values = _qlist(query, key)
            if values is not None:
                kwargs[name] = values
        tag_mode = _qstr(query, "tag_mode")
        if tag_mode:
            kwargs["tag_mode"] = tag_mode
        time_expression = _qstr(query, "time")
        if time_expression:
            kwargs["time_expression"] = time_expression
        time_field = _qstr(query, "time_field")
        if time_field:
            kwargs["time_field"] = time_field
        if "include_demo" in query:
            kwargs["include_demo"] = _truthy(_qstr(query, "include_demo", "true"))
        return kwargs

    def _get_api_v02(self, path: str, query: dict[str, list[str]]) -> bool:
        vault = self.vault
        if path == "/api/recall":
            raw_query = _qstr(query, "q")
            if not raw_query.strip():
                raise ValidationError("q is required")
            kwargs = self._recall_kwargs(query)
            kwargs.pop("query", None)
            outcome = vault.recall(raw_query, **kwargs)
            self._send_json(
                200, outcome.to_dict(include_diagnostics=_truthy(_qstr(query, "diagnostics")))
            )
            return True
        if path == "/api/recall/find":
            kwargs = self._recall_kwargs(query)
            kwargs.pop("query", None)
            self._send_json(200, vault.find_my_thought(_qstr(query, "q"), **kwargs))
            return True
        if path == "/api/recall/config":
            self._send_json(200, vault.recall_config())
            return True
        if path == "/api/recall/index":
            self._send_json(200, vault.recall_index_stats())
            return True
        if path == "/api/recall/diagnostics":
            self._send_json(200, vault.recall_diagnostics(
                _qstr(query, "q"), _qstr(query, "thought_id") or None
            ))
            return True
        if path == "/api/recall/learning":
            self._send_json(200, {
                "stats": vault.recall_learning_stats(),
                "learnedQueries": vault.learned_queries(_qint(query, "limit", 100)),
                "boundary": (
                    "Query-outcome memory is DERIVED. It never touches a canonical table, "
                    "it decays over time, it can be reset at any moment, and recording "
                    "feedback never confirms an AI-suggested relationship."
                ),
            })
            return True
        if path == "/api/recall/learning/history":
            self._send_json(200, {"history": vault.recall_learning_history(_qint(query, "limit", 200))})
            return True
        if path == "/api/clusters":
            self._send_json(200, {
                "clusters": vault.clusters(),
                "authority": "AUTOMATICALLY_DISCOVERED_CLUSTER",
                "canonical": False,
                "note": (
                    "Clusters are a derived reading of the graph, recomputed from canonical "
                    "relationships. They are never stored as canonical truth and never rename "
                    "or regroup a thought's own meaning."
                ),
            })
            return True
        if path == "/api/graph/view":
            self._send_json(200, vault.graph_view_model(
                root_ids=_qlist(query, "root") or _qlist(query, "root_ids"),
                max_nodes=_qint(query, "max_nodes", 200),
                include_graveyard=_truthy(_qstr(query, "include_graveyard", "true")),
                include_suggested=_truthy(_qstr(query, "include_suggested", "true")),
            ))
            return True
        if path == "/api/graph/path":
            source = _qstr(query, "source")
            target = _qstr(query, "target")
            if not source or not target:
                raise ValidationError("source and target are required")
            self._send_json(200, vault.find_path(
                source, target, directed=not _truthy(_qstr(query, "undirected"))
            ))
            return True
        if path == "/api/external/foundation":
            self._send_json(200, vault.external_foundation())
            return True
        neighbourhood_match = re.fullmatch(r"/api/graph/neighbourhood/([^/]+)", path)
        if neighbourhood_match:
            self._send_json(200, vault.thought_neighbourhood(
                neighbourhood_match.group(1), depth=_qint(query, "depth", 1)
            ))
            return True
        lineage_match = re.fullmatch(r"/api/graph/lineage/([^/]+)", path)
        if lineage_match:
            self._send_json(200, vault.thought_lineage(
                lineage_match.group(1), max_depth=_qint(query, "max_depth", 3)
            ))
            return True
        return False

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            payload = self._read_json()
            self._post_api(path, payload)
        except Exception as exc:
            self._error(exc)

    def _post_api(self, path: str, payload) -> None:
        if path == "/api/thoughts":
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be an object")
            thought = self.vault.create_thought(
                payload.get("originalText", payload.get("text")),
                payload.get("type", "THOUGHT"),
                payload.get("title", ""),
                payload.get("tags", []),
            )
            self._send_json(201, {"thought": thought})
            return
        if path == "/api/relationships":
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be an object")
            relationship = self.vault.create_relationship(
                payload.get("sourceId"), payload.get("targetId"),
                payload.get("type", "RELATED_TO"), payload.get("provenance", "USER_CONFIRMED"),
                payload.get("metadata", {}),
            )
            self._send_json(201, {"relationship": relationship})
            return
        if path == "/api/data/import":
            result = self.vault.import_vault(payload)
            self._send_json(201, result)
            return
        if path == "/api/data/backup":
            backup_path = self.vault.backup()
            body = backup_path.read_bytes()
            self._send_bytes(200, body, "application/vnd.sqlite3", {
                "Content-Disposition": f'attachment; filename="{backup_path.name}"',
                "X-IWTT-Backup-Name": backup_path.name,
            })
            return
        if path == "/api/data/derived/rebuild":
            self._send_json(200, self.vault.rebuild_search_index())
            return
        if path == "/api/demo/seed":
            self._send_json(201, self.vault.seed_demo())
            return
        thought_match = re.fullmatch(r"/api/thoughts/([^/]+)/(graveyard|resurrect|tags|ai/summary)", path)
        if thought_match:
            thought_id, action = thought_match.groups()
            if action == "graveyard":
                self._send_json(200, {"thought": self.vault.move_to_graveyard(thought_id)})
                return
            if action == "resurrect":
                self._send_json(200, {"thought": self.vault.resurrect(thought_id)})
                return
            if action == "tags":
                if not isinstance(payload, dict):
                    raise ValidationError("Request body must be an object")
                self._send_json(201, {"thought": self.vault.add_tag(thought_id, payload.get("name"))})
                return
            if action == "ai/summary":
                provider = self.vault.ai_provider
                if not getattr(provider, "available", False):
                    raise AIUnavailable("No AI provider is configured. Basic IWTT works without AI.")
                thought = self.vault.get_thought(thought_id)
                summary = provider.summarize(thought)
                artifact = self.vault.save_derived_artifact(
                    thought_id, "AI_SUMMARY", {"summary": summary},
                    provider.name, getattr(provider, "model", None), [thought_id],
                )
                self._send_json(201, {"artifact": artifact})
                return
        relationship_match = re.fullmatch(r"/api/relationships/([^/]+)/confirm", path)
        if relationship_match:
            self._send_json(200, {"relationship": self.vault.confirm_relationship(relationship_match.group(1))})
            return
        if self._post_api_v02(path, payload):
            return
        self._send_json(404, {"error": "API route not found"})

    # ------------------------------------------------------------------ v0.2 --
    # Recall POST routes.  These write DERIVED rows only.  None of them can
    # confirm a relationship, edit a thought, or touch canonical history — the
    # one confirmation route remains v0.1's /api/relationships/<id>/confirm.
    # ------------------------------------------------------------------

    def _post_api_v02(self, path: str, payload) -> bool:
        vault = self.vault
        if path == "/api/recall":
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be an object")
            raw_query = str(payload.get("query") or payload.get("q") or "")
            if not raw_query.strip():
                raise ValidationError("query is required")
            outcome = vault.recall(
                raw_query,
                limit=payload.get("limit"),
                include_graveyard=payload.get("includeGraveyard"),
                types=payload.get("types"),
                tags=payload.get("tags"),
                statuses=payload.get("statuses"),
                tag_mode=payload.get("tagMode"),
                time_expression=payload.get("time") or payload.get("timeExpression"),
                time_field=payload.get("timeField"),
                include_demo=payload.get("includeDemo"),
                explain=bool(payload.get("explain", True)),
                record_opened=bool(payload.get("recordOpened", False)),
            )
            self._send_json(200, outcome.to_dict())
            return True
        if path == "/api/recall/find":
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be an object")
            raw_query = str(payload.get("query") or payload.get("q") or "")
            self._send_json(200, vault.find_my_thought(
                raw_query,
                limit=payload.get("limit"),
                include_graveyard=payload.get("includeGraveyard"),
                record_opened=bool(payload.get("recordOpened", False)),
            ))
            return True
        if path == "/api/recall/feedback":
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be an object")
            record = vault.record_recall_feedback(
                query=str(payload.get("query") or ""),
                thought_id=str(payload.get("thoughtId") or ""),
                feedback=str(payload.get("feedback") or ""),
                rank=payload.get("rank"),
                score=payload.get("score"),
                note=str(payload.get("note") or ""),
            )
            self._send_json(201, {
                "feedback": record,
                "canonical": False,
                "note": (
                    "Stored in the derived recall_query_memory table only. Reset it at any "
                    "time with DELETE /api/recall/learning; it decays on a 180-day half-life."
                ),
            })
            return True
        if path == "/api/recall/index/rebuild":
            body = payload if isinstance(payload, dict) else {}
            self._send_json(200, vault.rebuild_recall_indexes(
                full=bool(body.get("full", True)),
                rebuild_clusters=bool(body.get("rebuildClusters", True)),
            ))
            return True
        return False

    def do_PATCH(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            payload = self._read_json()
            match = re.fullmatch(r"/api/thoughts/([^/]+)", path)
            if not match:
                self._send_json(404, {"error": "API route not found"})
                return
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be an object")
            unknown = set(payload) - {"currentText", "title", "type"}
            if unknown:
                raise ValidationError(f"Unsupported thought fields: {', '.join(sorted(unknown))}")
            thought = self.vault.update_thought(
                match.group(1), payload.get("currentText"), payload.get("title"), payload.get("type")
            )
            self._send_json(200, {"thought": thought})
        except Exception as exc:
            self._error(exc)

    def do_DELETE(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/data/derived":
                self._send_json(200, self.vault.clear_derived_data())
                return
            if path == "/api/demo":
                self._send_json(200, self.vault.remove_demo())
                return
            relationship_match = re.fullmatch(r"/api/relationships/([^/]+)", path)
            if relationship_match:
                self._send_json(200, {"relationship": self.vault.remove_relationship(relationship_match.group(1))})
                return
            tag_match = re.fullmatch(r"/api/thoughts/([^/]+)/tags/([^/]+)", path)
            if tag_match:
                thought = self.vault.remove_tag(tag_match.group(1), tag_match.group(2))
                self._send_json(200, {"thought": thought})
                return
            if path == "/api/recall/learning":
                result = self.vault.reset_recall_learning()
                result["note"] = (
                    "Every derived query-outcome signal has been deleted. Nothing canonical "
                    "was touched, and recall keeps working — it simply stops being influenced "
                    "by past feedback until you give some again."
                )
                self._send_json(200, result)
                return
            self._send_json(404, {"error": "API route not found"})
        except Exception as exc:
            self._error(exc)

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        candidate = (WEB_ROOT / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._send_bytes(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if not candidate.is_file():
            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")
            return
        content_type, _ = mimetypes.guess_type(str(candidate))
        content_type = content_type or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send_bytes(200, candidate.read_bytes(), content_type)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local IWTT v0.2 Thought Vault")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=os.environ.get("IWTT_DATA_DIR", str(Path.home() / ".iwtt")))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    provider = provider_from_environment()
    vault = Vault(data_dir / "thought-vault.sqlite3", ai_provider=provider)
    server = IWTTHTTPServer((args.host, args.port), RequestHandler, vault)
    LOG.info("IWTT v%s serving on http://%s:%s", APP_VERSION, args.host, args.port)
    LOG.info("SQLite vault: %s", vault.db_path)
    if getattr(provider, "available", False):
        LOG.warning("Optional AI provider explicitly configured: %s / %s", provider.name, provider.model)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping IWTT")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
