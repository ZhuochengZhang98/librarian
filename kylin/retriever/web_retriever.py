import logging
import time
import json
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional
from uuid import uuid5, NAMESPACE_OID

import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from kylin.utils import SimpleProgressLogger

from .retriever_base import Retriever, RetrieverConfig


logger = logging.getLogger(__name__)


@dataclass
class WebRetrieverConfig(RetrieverConfig):
    timeout: float = 3.0
    retry_times: int = 1
    retry_delay: float = 0.5


@dataclass
class BingRetrieverConfig(WebRetrieverConfig):
    subscription_key: str = "${oc.env:BING_SEARCH_KEY}"
    endpoint: str = "https://api.bing.microsoft.com"


@dataclass
class DuckDuckGoRetrieverConfig(WebRetrieverConfig):
    proxy: Optional[str] = None


@dataclass
class GoogleRetrieverConfig(WebRetrieverConfig):
    subscription_key: str = "${oc.env:GOOGLE_SEARCH_KEY}"
    search_engine_id: str = "${oc.env:GOOGLE_SEARCH_ENGINE_ID}"
    endpoint: str = "https://customsearch.googleapis.com/customsearch/v1"
    proxy: Optional[str] = None


class WebRetriever(Retriever):
    def __init__(self, cfg: WebRetrieverConfig):
        super().__init__(cfg)
        self.timeout = cfg.timeout
        self.retry_times = cfg.retry_times
        self.retry_delay = cfg.retry_delay
        return

    def _search(
        self,
        query: list[str] | str,
        top_k: int = 10,
        delay: float = 0.1,
        **search_kwargs,
    ) -> list[list[dict[str, str]]]:
        if isinstance(query, str):
            query = [query]

        # prepare search method
        retry_times = search_kwargs.get("retry_times", self.retry_times)
        retry_delay = search_kwargs.get("retry_delay", self.retry_delay)
        if retry_times > 1:
            search_method = retry(
                stop=stop_after_attempt(retry_times), wait=wait_fixed(retry_delay)
            )(self.search_item)
        else:
            search_method = self.search_item

        # search
        results = []
        p_logger = SimpleProgressLogger(logger, len(query), self.log_interval)
        for q in query:
            time.sleep(delay)
            p_logger.update(1, "Searching")
            results.append(search_method(q, top_k, **search_kwargs))
        return results

    @abstractmethod
    def search_item(
        self,
        query: list[str],
        top_k: int = 10,
        **search_kwargs,
    ) -> list[dict[str, str]]:
        """Search queries using local retriever.

        Args:
            query (list[str]): Queries to search.
            top_k (int, optional): N documents to return. Defaults to 10.

        Returns:
            list[dict[str, str | list]]: A list of retrieved documents:
                {
                    "retriever": str,
                    "query": str,
                    "url": str,
                    "title": str,
                    "text": str,
                }
        """
        return


class BingRetriever(WebRetriever):
    name = "bing"

    def __init__(self, cfg: BingRetrieverConfig):
        super().__init__(cfg)
        self.endpoint = cfg.endpoint + "/v7.0/search"
        self.headers = {"Ocp-Apim-Subscription-Key": cfg.subscription_key}
        return

    def search_item(
        self,
        query: str,
        top_k: int = 10,
        **search_kwargs,
    ) -> list[dict[str, str]]:
        params = {"q": query, "mkt": "en-US", "count": top_k}
        params.update(search_kwargs)
        response = requests.get(
            self.endpoint,
            headers=self.headers,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        result = [
            {
                "retriever": self.name,
                "query": query,
                "text": i["snippet"],
                "full_text": i["snippet"],
                "source": i["url"],
                "title": i["name"],
            }
            for i in result["webPages"]["value"]
        ]
        return result

    @property
    def fingerprint(self) -> str:
        time_str = time.strftime("%Y-%m", time.gmtime())
        namespace_uuid = uuid5(NAMESPACE_OID, self.name)
        feature_str = self.name + self.endpoint + json.dumps(self.headers)
        return uuid5(namespace_uuid, time_str + feature_str).hex


class DuckDuckGoRetriever(WebRetriever):
    name = "ddg"

    def __init__(self, cfg: DuckDuckGoRetrieverConfig):
        super().__init__(cfg)

        from duckduckgo_search import DDGS

        self.ddgs = DDGS(proxy=cfg.proxy)
        return

    def search_item(
        self,
        query: str,
        top_k: int = 10,
        **search_kwargs,
    ) -> list[str, str]:
        result = self.ddgs.text(query, max_results=top_k, **search_kwargs)
        result = [
            {
                "retriever": self.name,
                "query": query,
                "text": i["body"],
                "full_text": i["body"],
                "source": i["href"],
                "title": i["title"],
            }
            for i in result
        ]
        return result

    @property
    def fingerprint(self) -> str:
        time_str = time.strftime("%Y-%m", time.gmtime())
        namespace_uuid = uuid5(NAMESPACE_OID, self.name)
        return uuid5(namespace_uuid, time_str).hex


class GoogleRetriever(WebRetriever):
    name = "google"

    def __init__(self, cfg: GoogleRetrieverConfig):
        super().__init__(cfg)
        self.endpoint = cfg.endpoint
        self.subscription_key = cfg.subscription_key
        self.engine_id = cfg.search_engine_id
        self.proxy = {
            "http": cfg.proxy,
            "https": cfg.proxy,
        }
        return

    def search_item(
        self,
        query: str,
        top_k: int = 10,
        **search_kwargs,
    ) -> list[dict[str, str]]:
        params = {
            "key": self.subscription_key,
            "cx": self.engine_id,
            "q": query,
            "num": top_k,
        }
        response = requests.get(
            self.endpoint,
            params=params,
            proxies=self.proxy,
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        result = [
            {
                "retriever": self.name,
                "query": query,
                "text": i["snippet"],
                "full_text": i["snippet"],
                "source": i["link"],
                "title": i["title"],
            }
            for i in result["items"]
        ]
        return result

    @property
    def fingerprint(self) -> str:
        time_str = time.strftime("%Y-%m", time.gmtime())
        namespace_uuid = uuid5(NAMESPACE_OID, self.name)
        feature_str = self.engine_id + self.endpoint
        return uuid5(namespace_uuid, time_str + feature_str).hex
