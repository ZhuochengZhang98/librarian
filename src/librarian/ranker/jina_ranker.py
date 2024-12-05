import asyncio
from dataclasses import dataclass

import numpy as np
import requests
from omegaconf import MISSING

from librarian.utils import TIME_METER

from .ranker import RankerBase, RankerConfig, RANKERS


@dataclass
class JinaRankerConfig(RankerConfig):
    model: str = "jina-reranker-v2-base-multilingual"
    base_url: str = "https://api.jina.ai/v1/rerank"
    api_key: str = MISSING


@RANKERS("jina", config_class=JinaRankerConfig)
class JinaRanker(RankerBase):
    def __init__(self, cfg: JinaRankerConfig) -> None:
        super().__init__(cfg)
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        }
        self.base_url = cfg.base_url
        self._data_template = {
            "model": cfg.model,
            "query": "",
            "top_n": 0,
            "documents": [],
        }
        return

    @TIME_METER("jina_rank")
    def _rank(self, query: str, candidates: list[str]) -> tuple[np.ndarray, np.ndarray]:
        data = self._data_template.copy()
        data["query"] = query
        data["documents"] = candidates
        data["top_n"] = len(candidates)
        response = requests.post(self.base_url, json=data, headers=self.headers)
        response.raise_for_status()
        scores = [i["relevance_score"] for i in response.json()["results"]]
        return None, scores

    @TIME_METER("jina_rank")
    async def _async_rank(
        self, query: str, candidates: list[str]
    ) -> tuple[np.ndarray, np.ndarray]:
        data = self._data_template.copy()
        data["query"] = query
        data["documents"] = candidates
        data["top_n"] = len(candidates)
        response = await asyncio.create_task(
            asyncio.to_thread(
                requests.post, self.base_url, json=data, headers=self.headers
            )
        )
        response.raise_for_status()
        scores = [i["relevance_score"] for i in response.json()["results"]]
        return None, scores