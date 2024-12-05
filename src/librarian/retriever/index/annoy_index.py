import os
import math
import shutil
from dataclasses import dataclass

import numpy as np

from librarian.utils import Choices, SimpleProgressLogger, LOGGER_MANAGER

from .index_base import DENSE_INDEX, DenseIndexBase, DenseIndexConfigBase

logger = LOGGER_MANAGER.get_logger("librarian.retrievers.index.annoy")


@dataclass
class AnnoyIndexConfig(DenseIndexConfigBase):
    distance_function: Choices(["IP", "L2", "COSINE", "HAMMING", "MANHATTAN"]) = "IP"  # type: ignore
    n_trees: int = -1  # -1 means auto
    n_jobs: int = -1  # -1 means auto
    search_k: int = -1  # -1 means auto
    on_disk_build: bool = False


@DENSE_INDEX("annoy", config_class=AnnoyIndexConfig)
class AnnoyIndex(DenseIndexBase):
    def __init__(self, cfg: AnnoyIndexConfig, index_path: str) -> None:
        super().__init__(cfg, index_path)
        # check annoy
        try:
            from annoy import AnnoyIndex as AnnIndex

            self.ann = AnnIndex
        except:
            raise ImportError("Please install annoy by running `pip install annoy`")

        # set annoy params
        self.cfg = cfg

        # prepare index
        if os.path.exists(self.index_path):
            self.deserialize()
        else:
            self.index = None
            if not os.path.exists(os.path.dirname(self.index_path)):
                os.makedirs(os.path.dirname(self.index_path))
        return

    def build_index(self, embeddings: np.ndarray) -> None:
        self.clean()
        # prepare index
        match self.distance_function:
            case "IP":
                self.index = self.ann(embeddings.shape[1], "dot")
            case "L2":
                self.index = self.ann(embeddings.shape[1], "euclidean")
            case "COSINE":
                self.index = self.ann(embeddings.shape[1], "angular")
            case "HAMMING":
                self.index = self.ann(embeddings.shape[1], "hamming")
            case "MANHATTAN":
                self.index = self.ann(embeddings.shape[1], "manhattan")
        if self.cfg.on_disk_build:
            self.index.on_disk_build(True)
        # add embeddings
        p_logger = SimpleProgressLogger(
            logger, total=len(embeddings), interval=self.log_interval
        )
        for idx, embed in enumerate(embeddings):
            self.index.add_item(idx, embed)
            p_logger.update(step=1, desc="Adding embeddings")
        # build index
        logger.info("Building index")
        if self.n_trees == -1:
            n_trees = (
                max(1, math.floor(math.log(30000000) // 10))
                * math.floor(math.sqrt(768))
                * 10
            )
        else:
            n_trees = self.n_trees
        self.index.build(n_trees, self.cfg.n_jobs)
        if not self.cfg.on_disk_build:
            self.serialize()
        return

    def _add_embeddings_batch(self, embeddings: np.ndarray) -> None:
        raise NotImplementedError(
            "Annoy does not support adding embeddings. Please retrain the index."
        )

    def _search_batch(
        self,
        query: np.ndarray,
        top_k: int,
        **search_kwargs,
    ) -> tuple[np.ndarray, np.ndarray]:
        query = query.astype("float32")
        indices = []
        scores = []
        search_k = search_kwargs.get("search_k", self.cfg.search_k)
        if search_k == -1:
            search_k = max(top_k, 100) * self.n_trees
        for q in query:
            idx, dis = self.index.get_nns_by_vector(
                q,
                top_k,
                search_k=search_k,
                include_distances=True,
            )
            indices.append(idx)
            scores.append(dis)
        indices = np.array(indices)
        scores = np.array(scores)
        return indices, scores

    def serialize(self) -> None:
        logger.info(f"Serializing index to {self.index_path}")
        if not os.path.exists(os.path.dirname(self.index_path)):
            os.makedirs(os.path.dirname(self.index_path))
        self.index.save(self.index_path)
        return

    def deserialize(self) -> None:
        logger.info(f"Loading index from {self.index_path}")
        self.index.load(self.index_path)
        return

    def clean(self):
        if self.index is not None:
            self.index.unload()
        if os.path.exists(self.index_path):
            shutil.rmtree(self.index_path)
        return

    @property
    def embedding_size(self) -> int:
        if self.index is None:
            raise ValueError("Index is not initialized.")
        return self.index.f

    @property
    def is_trained(self):
        return self.index.get_n_items() > 0

    @property
    def n_trees(self) -> int:
        if self.index is not None:
            return self.index.get_n_trees()
        return self.cfg.n_trees

    def __len__(self) -> int:
        return self.index.get_n_items()