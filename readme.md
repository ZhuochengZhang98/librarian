# Librarian: An agent powered search engine.



## TODO
- [ ] optimize keyword searcher
- [ ] try to merge TextUnit/RetrievedContext/Passage
- [ ] Reproduce new methods
  - [ ] Searcher
    - [ ] SelfRAG
    - [ ] AutoRAG
- [ ] performance optimization
  - [ ] add custom static DP for faster encoding / reranking



## Overview


## Installation

### Install from pip
```bash
pip install librarian
```


### Install from source
```bash
pip install pybind11

git clone https://tencent.zhangzhuocheng.top:3000/zhangzhuocheng/kylin
cd librarian
pip install ./
```

## Usage


## Tested HF Models

### Tested Encoders
- jinaai/jina-embeddings-v3
- BAAI/bge-m3
- facebook/contriever
- nomic-ai/nomic-embed-text-v1.5
- sentence-transformers/msmarco-MiniLM-L-12-v3

### Tested ReRankers
- unicamp-dl/InRanker-base
- colbert-ir/colbertv2.0
- jinaai/Jina-colbert-v2
- jinaai/jina-reranker-v2-base-multilingual
- BAAI/bge-reranker-v2-m3
- intfloat/e5-base-v2

### Tested Generators

