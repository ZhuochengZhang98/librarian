import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import perf_counter
from typing import Optional

import numpy as np

from kylin.utils import Choices, SimpleProgressLogger


logger = logging.getLogger("DenseIndex")


@dataclass
class DenseIndexConfig:
    distance_function: Choices(["IP", "L2"]) = "IP"  # type: ignore
    embedding_size: Optional[int] = None
    index_train_num: int = 1000000
    log_interval: int = 10000
    batch_size: int = 512


class DenseIndex(ABC):
    def __init__(self, index_path: str, cfg: DenseIndexConfig):
        self.distance_function = cfg.distance_function
        self.index_train_num = cfg.index_train_num
        self.index_path = index_path
        self.batch_size = cfg.batch_size
        self.log_interval = cfg.log_interval
        return

    @abstractmethod
    def build_index(self, embeddings: np.ndarray):
        return

    def add_embeddings(self, embeddings: np.ndarray) -> None:
        """Add embeddings to the index."""
        p_logger = SimpleProgressLogger(
            logger, total=embeddings.shape[0], interval=self.log_interval
        )
        for idx in range(0, len(embeddings), self.batch_size):
            p_logger.update(step=self.batch_size, desc="Adding embeddings")
            embeds_to_add = embeddings[idx : idx + self.batch_size]
            self._add_embeddings_batch(embeds_to_add)
        self.serialize()
        return

    @abstractmethod
    def _add_embeddings_batch(self, embeddings: np.ndarray) -> None:
        return

    def search(
        self,
        query: np.ndarray,
        top_k: int = 10,
        **search_kwargs,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Search for the top_k most similar embeddings to the query.

        Args:
            query (np.ndarray): query embeddings. [n, d]
            top_k (int, optional): Number of most similar embeddings to return. Defaults to 10.
            search_kwargs (dict, optional): Additional search arguments. Defaults to {}.

        Returns:
            ids (np.ndarray): [n, k]
            scores (np.ndarray): [n, k]
        """
        scores = []
        indices = []
        p_logger = SimpleProgressLogger(
            logger, total=query.shape[0], interval=self.log_interval
        )
        for idx in range(0, len(query), self.batch_size):
            p_logger.update(step=self.batch_size, desc="Searching")
            q = query[idx : idx + self.batch_size]
            r = self._search_batch(q, top_k, **search_kwargs)
            scores.append(r[1])
            indices.append(r[0])
        scores = np.concatenate(scores, axis=0)
        indices = np.concatenate(indices, axis=0)
        return indices, scores

    @abstractmethod
    def _search_batch(
        self, query: np.ndarray, top_k: int, **search_kwargs
    ) -> tuple[np.ndarray, np.ndarray]:
        return

    @abstractmethod
    def serialize(self):
        """Serialize the index to self.index_path."""
        return

    @abstractmethod
    def deserialize(self) -> None:
        """Deserialize the index from self.index_path."""
        return

    @abstractmethod
    def clean(self) -> None:
        """Clean the index."""
        return

    @property
    @abstractmethod
    def is_trained(self) -> bool:
        """Return True if the index is trained."""
        return

    @property
    @abstractmethod
    def embedding_size(self) -> int:
        """Return the embedding size of the index."""
        return

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of embeddings in the index."""
        return

    def test_accuracy(self, queries: np.ndarray, labels: np.ndarray, top_k: int = 10):
        """Test the top-k accuracy of the index."""
        # search
        start_time = perf_counter()
        retrieved, _ = self.search(queries, top_k)
        end_time = perf_counter()
        time_cost = end_time - start_time

        # compute accuracy
        acc_map = labels.reshape(-1, 1) == retrieved
        top_k_acc = [acc_map[:, : k + 1].sum() / len(queries) for k in range(top_k)]

        # log accuracy and search time
        acc_info_str = "\n".join(
            [f"Top {k + 1} accuracy: {acc*100:.2f}%" for k, acc in enumerate(top_k_acc)]
        )
        logger.info(f"Top k accuracy:\n{acc_info_str}")
        logger.info(f"Search time: {time_cost:.4f} s")
        return top_k_acc
