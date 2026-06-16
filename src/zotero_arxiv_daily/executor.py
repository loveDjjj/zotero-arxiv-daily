from dataclasses import dataclass
from datetime import datetime
import random

from loguru import logger
from omegaconf import DictConfig, ListConfig
from openai import OpenAI
from pyzotero import zotero
from tqdm import tqdm

from .construct_email import render_email
from .protocol import CorpusPaper
from .reranker import get_reranker_cls
from .retriever import get_retriever_cls
from .utils import glob_match, send_email


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


@dataclass
class ZoteroLibraryConfig:
    library_type: str
    library_id: str
    api_key: str
    include_path_patterns: list[str] | None
    ignore_path_patterns: list[str] | None


def _as_plain_list(value):
    if value is None:
        return None
    if isinstance(value, ListConfig):
        return list(value)
    return value


def build_zotero_library_configs(zotero_config: DictConfig) -> list[ZoteroLibraryConfig]:
    libraries = _as_plain_list(zotero_config.get("libraries"))
    if libraries:
        normalized_libraries = []
        for index, library in enumerate(libraries):
            library_type = library.get("type")
            if library_type not in {"user", "group"}:
                raise ValueError(
                    f"config.zotero.libraries[{index}].type must be 'user' or 'group', got {library_type!r}."
                )

            library_id = library.get("id")
            api_key = library.get("api_key")
            if library_id in (None, ""):
                raise ValueError(f"config.zotero.libraries[{index}].id must be provided.")
            if api_key in (None, ""):
                raise ValueError(f"config.zotero.libraries[{index}].api_key must be provided.")

            normalized_libraries.append(
                ZoteroLibraryConfig(
                    library_type=library_type,
                    library_id=str(library_id),
                    api_key=str(api_key),
                    include_path_patterns=normalize_path_patterns(
                        library.get("include_path"), f"libraries[{index}].include_path"
                    ),
                    ignore_path_patterns=normalize_path_patterns(
                        library.get("ignore_path"), f"libraries[{index}].ignore_path"
                    ),
                )
            )
        return normalized_libraries

    user_id = zotero_config.get("user_id")
    api_key = zotero_config.get("api_key")
    if user_id in (None, "") or api_key in (None, ""):
        raise ValueError(
            "config.zotero.user_id and config.zotero.api_key must be provided when config.zotero.libraries is empty."
        )

    return [
        ZoteroLibraryConfig(
            library_type="user",
            library_id=str(user_id),
            api_key=str(api_key),
            include_path_patterns=normalize_path_patterns(zotero_config.get("include_path"), "include_path"),
            ignore_path_patterns=normalize_path_patterns(zotero_config.get("ignore_path"), "ignore_path"),
        )
    ]


class Executor:
    def __init__(self, config: DictConfig):
        self.config = config
        self.zotero_libraries = build_zotero_library_configs(config.zotero)
        self.include_path_patterns = None
        self.ignore_path_patterns = None
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)

    def _get_zotero_libraries(self) -> list[ZoteroLibraryConfig]:
        libraries = getattr(self, "zotero_libraries", None)
        if libraries is None:
            libraries = build_zotero_library_configs(self.config.zotero)
            self.zotero_libraries = libraries
        return libraries

    def fetch_zotero_library_corpus(self, library: ZoteroLibraryConfig) -> list[CorpusPaper]:
        zot = zotero.Zotero(library.library_id, library.library_type, library.api_key)
        collections = zot.everything(zot.collections())
        collections = {c["key"]: c for c in collections}
        corpus = zot.everything(zot.items(itemType="conferencePaper || journalArticle || preprint"))
        corpus = [c for c in corpus if c["data"]["abstractNote"] != ""]

        def get_collection_path(col_key: str) -> str:
            if col_key not in collections:
                return ""
            if parent_key := collections[col_key]["data"]["parentCollection"]:
                parent_path = get_collection_path(parent_key)
                current_name = collections[col_key]["data"]["name"]
                return f"{parent_path}/{current_name}" if parent_path else current_name
            return collections[col_key]["data"]["name"]

        for item in corpus:
            paths = [get_collection_path(col) for col in item["data"]["collections"]]
            item["paths"] = [path for path in paths if path]

        return [
            CorpusPaper(
                title=item["data"]["title"],
                abstract=item["data"]["abstractNote"],
                added_date=datetime.strptime(item["data"]["dateAdded"], "%Y-%m-%dT%H:%M:%SZ"),
                paths=item["paths"],
                library_type=library.library_type,
                library_id=library.library_id,
            )
            for item in corpus
        ]

    def filter_corpus(
        self,
        corpus: list[CorpusPaper],
        include_patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
    ) -> list[CorpusPaper]:
        include_patterns = getattr(self, "include_path_patterns", None) if include_patterns is None else include_patterns
        ignore_patterns = getattr(self, "ignore_path_patterns", None) if ignore_patterns is None else ignore_patterns

        if include_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {include_patterns}")
            corpus = [
                paper
                for paper in corpus
                if any(
                    glob_match(path, pattern)
                    for path in paper.paths
                    for pattern in include_patterns
                )
            ]

        if ignore_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {ignore_patterns}")
            corpus = [
                paper
                for paper in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in paper.paths
                    for pattern in ignore_patterns
                )
            ]

        if include_patterns or ignore_patterns:
            samples = random.sample(corpus, min(5, len(corpus))) if corpus else []
            sample_text = "\n".join([paper.title + " - " + "\n".join(paper.paths) for paper in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{sample_text}\n...")
        return corpus

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        all_corpus = []
        for library in self._get_zotero_libraries():
            logger.info(f"Fetching zotero corpus from {library.library_type} library {library.library_id}")
            corpus = self.fetch_zotero_library_corpus(library)
            corpus = self.filter_corpus(
                corpus,
                include_patterns=library.include_path_patterns,
                ignore_patterns=library.ignore_path_patterns,
            )
            logger.info(
                f"Fetched {len(corpus)} zotero papers from {library.library_type} library {library.library_id}"
            )
            all_corpus.extend(corpus)
        logger.info(f"Fetched {len(all_corpus)} zotero papers in total")
        return all_corpus

    def run(self):
        corpus = self.fetch_zotero_corpus()
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return
        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            reranked_papers = reranked_papers[: self.config.executor.max_paper_num]
            logger.info("Generating TLDR and affiliations...")
            for paper in tqdm(reranked_papers):
                paper.generate_tldr(self.openai_client, self.config.llm)
                paper.generate_affiliations(self.openai_client, self.config.llm)
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
        logger.info("Sending email...")
        email_content = render_email(reranked_papers)
        send_email(self.config, email_content)
        logger.info("Email sent successfully")
