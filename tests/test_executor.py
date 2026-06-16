"""Tests for zotero_arxiv_daily.executor: config normalization, filtering, fetch, E2E."""

from datetime import datetime

import pytest
from omegaconf import OmegaConf, open_dict

from zotero_arxiv_daily.executor import Executor, build_zotero_library_configs, normalize_path_patterns
from zotero_arxiv_daily.protocol import CorpusPaper


# ---------------------------------------------------------------------------
# normalize_path_patterns — migrated from test_include_path.py
# ---------------------------------------------------------------------------


def test_normalize_path_patterns_rejects_single_string_for_include_path():
    with pytest.raises(TypeError, match="config.zotero.include_path must be a list"):
        normalize_path_patterns("2026/survey/**", "include_path")


def test_normalize_path_patterns_accepts_list_config_for_include_path():
    include_path = OmegaConf.create(["2026/survey/**", "2026/reading-group/**"])
    assert normalize_path_patterns(include_path, "include_path") == [
        "2026/survey/**",
        "2026/reading-group/**",
    ]


def test_normalize_path_patterns_rejects_single_string_for_ignore_path():
    with pytest.raises(TypeError, match="config.zotero.ignore_path must be a list"):
        normalize_path_patterns("archive/**", "ignore_path")


def test_normalize_path_patterns_accepts_list_config_for_ignore_path():
    ignore_path = OmegaConf.create(["archive/**", "2025/**"])
    assert normalize_path_patterns(ignore_path, "ignore_path") == ["archive/**", "2025/**"]


def test_normalize_path_patterns_accepts_empty_list():
    assert normalize_path_patterns([], "ignore_path") == []


def test_normalize_path_patterns_accepts_none():
    assert normalize_path_patterns(None, "include_path") is None


# ---------------------------------------------------------------------------
# build_zotero_library_configs
# ---------------------------------------------------------------------------


def test_build_zotero_library_configs_uses_legacy_single_library_config(config):
    libraries = build_zotero_library_configs(config.zotero)

    assert len(libraries) == 1
    assert libraries[0].library_type == "user"
    assert libraries[0].library_id == "000000"
    assert libraries[0].api_key == "fake-zotero-key"
    assert libraries[0].include_path_patterns is None
    assert libraries[0].ignore_path_patterns is None


def test_build_zotero_library_configs_prefers_explicit_libraries(config):
    with open_dict(config):
        config.zotero.libraries = [
            {
                "type": "user",
                "id": "u-1",
                "api_key": "user-key",
                "include_path": ["personal/**"],
                "ignore_path": ["personal/archive/**"],
            },
            {
                "type": "group",
                "id": "g-2",
                "api_key": "group-key",
                "include_path": ["group/active/**"],
                "ignore_path": None,
            },
        ]

    libraries = build_zotero_library_configs(config.zotero)

    assert [lib.library_type for lib in libraries] == ["user", "group"]
    assert [lib.library_id for lib in libraries] == ["u-1", "g-2"]
    assert libraries[0].include_path_patterns == ["personal/**"]
    assert libraries[0].ignore_path_patterns == ["personal/archive/**"]
    assert libraries[1].include_path_patterns == ["group/active/**"]
    assert libraries[1].ignore_path_patterns is None


def test_build_zotero_library_configs_rejects_unknown_library_type(config):
    with open_dict(config):
        config.zotero.libraries = [{"type": "team", "id": "x", "api_key": "k"}]

    with pytest.raises(ValueError, match="must be 'user' or 'group'"):
        build_zotero_library_configs(config.zotero)


# ---------------------------------------------------------------------------
# filter_corpus — migrated from test_include_path.py
# ---------------------------------------------------------------------------


def _make_executor(include_patterns=None, ignore_patterns=None):
    executor = Executor.__new__(Executor)
    executor.include_path_patterns = normalize_path_patterns(include_patterns, "include_path") if include_patterns else None
    executor.ignore_path_patterns = normalize_path_patterns(ignore_patterns, "ignore_path") if ignore_patterns else None
    return executor


def test_filter_corpus_matches_any_path_against_any_pattern():
    executor = _make_executor(include_patterns=["2026/survey/**", "2026/reading-group/**"])
    corpus = [
        CorpusPaper(title="Survey Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a", "archive/misc"]),
        CorpusPaper(title="Reading Group Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["notes/inbox", "2026/reading-group/week-1"]),
        CorpusPaper(title="Excluded Paper", abstract="", added_date=datetime(2026, 1, 3), paths=["2025/other/topic"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Survey Paper", "Reading Group Paper"]


def test_filter_corpus_excludes_papers_matching_ignore_path():
    executor = _make_executor(ignore_patterns=["archive/**", "2025/**"])
    corpus = [
        CorpusPaper(title="Active Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a"]),
        CorpusPaper(title="Archived Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["archive/misc"]),
        CorpusPaper(title="Old Paper", abstract="", added_date=datetime(2026, 1, 3), paths=["2025/other/topic"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Active Paper"]


def test_filter_corpus_ignore_path_takes_precedence_over_include_path():
    executor = _make_executor(include_patterns=["2026/**"], ignore_patterns=["2026/ignore/**"])
    corpus = [
        CorpusPaper(title="Included Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a"]),
        CorpusPaper(title="Ignored Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["2026/ignore/topic-b"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Included Paper"]


def test_filter_corpus_no_filters_returns_all():
    executor = _make_executor()
    corpus = [
        CorpusPaper(title="Paper A", abstract="", added_date=datetime(2026, 1, 1), paths=["foo"]),
        CorpusPaper(title="Paper B", abstract="", added_date=datetime(2026, 1, 2), paths=["bar"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert filtered == corpus


# ---------------------------------------------------------------------------
# fetch_zotero_library_corpus
# ---------------------------------------------------------------------------


def test_fetch_zotero_library_corpus_supports_group_library(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    calls = []
    stub_zot = make_stub_zotero_client()

    def _fake_zotero(library_id, library_type, api_key):
        calls.append((library_id, library_type, api_key))
        return stub_zot

    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", _fake_zotero)

    executor = Executor.__new__(Executor)
    executor.config = config
    library = OmegaConf.create(
        {
            "library_type": "group",
            "library_id": "123456",
            "api_key": "group-key",
            "include_path_patterns": None,
            "ignore_path_patterns": None,
        }
    )

    corpus = executor.fetch_zotero_library_corpus(library)

    assert len(corpus) == 2
    assert calls == [("123456", "group", "group-key")]


# ---------------------------------------------------------------------------
# fetch_zotero_corpus
# ---------------------------------------------------------------------------


def test_fetch_zotero_corpus(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 2
    assert corpus[0].title == "Stub Paper 1"
    assert "survey/topic-a" in corpus[0].paths[0]


def test_fetch_zotero_corpus_paper_with_zero_collections(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    items = [
        {
            "data": {
                "title": "No Collection Paper",
                "abstractNote": "Abstract.",
                "dateAdded": "2026-03-01T00:00:00Z",
                "collections": [],
            }
        }
    ]
    stub_zot = make_stub_zotero_client(items=items)
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 1
    assert corpus[0].paths == []


def test_fetch_zotero_corpus_merges_multiple_libraries_with_per_library_filters(config, monkeypatch):
    with open_dict(config):
        config.zotero.libraries = [
            {
                "type": "user",
                "id": "user-1",
                "api_key": "user-key",
                "include_path": ["personal/keep/**"],
                "ignore_path": ["personal/keep/ignore/**"],
            },
            {
                "type": "group",
                "id": "group-1",
                "api_key": "group-key",
                "include_path": ["group/keep/**"],
                "ignore_path": None,
            },
        ]

    collections_by_library = {
        ("user-1", "user"): [
            {"key": "u_root", "data": {"name": "personal", "parentCollection": False}},
            {"key": "u_keep", "data": {"name": "keep", "parentCollection": "u_root"}},
            {"key": "u_keep_topic", "data": {"name": "topic-a", "parentCollection": "u_keep"}},
            {"key": "u_ignore", "data": {"name": "ignore", "parentCollection": "u_keep"}},
            {"key": "u_ignore_topic", "data": {"name": "topic-b", "parentCollection": "u_ignore"}},
        ],
        ("group-1", "group"): [
            {"key": "g_root", "data": {"name": "group", "parentCollection": False}},
            {"key": "g_keep", "data": {"name": "keep", "parentCollection": "g_root"}},
            {"key": "g_keep_topic", "data": {"name": "topic-c", "parentCollection": "g_keep"}},
        ],
    }
    items_by_library = {
        ("user-1", "user"): [
            {
                "data": {
                    "title": "Keep User Paper",
                    "abstractNote": "Abstract A",
                    "dateAdded": "2026-03-01T00:00:00Z",
                    "collections": ["u_keep_topic"],
                }
            },
            {
                "data": {
                    "title": "Ignore User Paper",
                    "abstractNote": "Abstract B",
                    "dateAdded": "2026-03-02T00:00:00Z",
                    "collections": ["u_ignore_topic"],
                }
            },
        ],
        ("group-1", "group"): [
            {
                "data": {
                    "title": "Keep Group Paper",
                    "abstractNote": "Abstract C",
                    "dateAdded": "2026-03-03T00:00:00Z",
                    "collections": ["g_keep_topic"],
                }
            }
        ],
    }

    class _StubZoteroClient:
        def __init__(self, library_id, library_type):
            self.library_id = library_id
            self.library_type = library_type

        def everything(self, payload):
            return payload

        def collections(self):
            return collections_by_library[(self.library_id, self.library_type)]

        def items(self, itemType=None):
            return items_by_library[(self.library_id, self.library_type)]

    monkeypatch.setattr(
        "zotero_arxiv_daily.executor.zotero.Zotero",
        lambda library_id, library_type, api_key: _StubZoteroClient(library_id, library_type),
    )

    executor = Executor(config)
    corpus = executor.fetch_zotero_corpus()

    assert [paper.title for paper in corpus] == ["Keep User Paper", "Keep Group Paper"]
    assert corpus[0].paths == ["personal/keep/topic-a"]
    assert corpus[1].paths == ["group/keep/topic-c"]


# ---------------------------------------------------------------------------
# E2E: Executor.run()
# ---------------------------------------------------------------------------


def test_run_end_to_end(config, monkeypatch):
    """Full pipeline: Zotero fetch -> filter -> retrieve -> rerank -> TLDR -> email."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import (
        make_sample_corpus,
        make_sample_paper,
        make_stub_openai_client,
        make_stub_smtp,
        make_stub_zotero_client,
    )

    # Config: source=["arxiv"], reranker="api", send_empty=false
    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False

    # 1. Stub pyzotero
    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    # 2. Stub OpenAI (for reranker + TLDR/affiliations)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)
    retrieved = [
        make_sample_paper(title="E2E Paper 1", score=None),
        make_sample_paper(title="E2E Paper 2", score=None),
    ]

    # Import to register the arxiv retriever
    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(
        registered_retrievers["arxiv"],
        "retrieve_papers",
        lambda self: retrieved,
    )

    # 4. Stub SMTP
    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    # 5. Stub sleep (reranker/retriever)
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    # 6. Run
    executor = Executor(config)
    executor.run()

    # Assertions
    assert len(sent) == 1, "Email should have been sent"
    _, _, email_body = sent[0]
    assert "text/html" in email_body


def test_run_no_papers_send_empty_false(config, monkeypatch):
    """When no papers are found and send_empty=false, no email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    executor = Executor(config)
    executor.run()

    assert len(sent) == 0, "No email should be sent when no papers and send_empty=false"


def test_run_no_papers_send_empty_true(config, monkeypatch):
    """When no papers are found and send_empty=true, empty email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = True

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily.reranker.api.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    executor = Executor(config)
    executor.run()

    assert len(sent) == 1, "Email should be sent even with no papers when send_empty=true"
    _, _, body = sent[0]
    assert "text/html" in body
