import logging
import re
from argparse import ArgumentParser, Namespace
from copy import deepcopy

import torch

from kylin.retriever import add_args_for_retriever, load_retrievers

from .kylin_prompts import *
from .models import load_generator, load_generation_config
from .utils import SimpleProgressLogger


logger = logging.getLogger(__name__)


class KylinLLMSearcher:
    @staticmethod
    def add_args(parser: ArgumentParser) -> ArgumentParser:
        # kylin arguments
        parser.add_argument(
            "--extract_information",
            default=False,
            action="store_true",
            help="Whether to extract needed information from the user query.",
        )
        parser.add_argument(
            "--rewrite_query",
            default=False,
            action="store_true",
            help="Whether to rewrite the query.",
        )
        parser.add_argument(
            "--verify_context",
            default=False,
            action="store_true",
            help="Whether to verify the retrieved contexts.",
        )
        parser.add_argument(
            "--summary_context",
            default=False,
            action="store_true",
            help="Whether to summarize the retrieved contexts.",
        )
        # searcher arguments
        parser.add_argument(
            "--searcher_type",
            type=str,
            choices=["vllm", "hf", "openai"],
            default="vllm",
            help="The searcher model type.",
        )
        parser.add_argument(
            "--searcher_path",
            type=str,
            default=None,
            help="The path to the kylin searcher model.",
        )
        parser.add_argument(
            "--searcher_max_length",
            type=int,
            default=512,
            help="The maximum length for the searcher.",
        )
        parser.add_argument(
            "--searcher_gpu_utilization",
            type=float,
            default=0.85,
            help="The maximum GPU memory utilization of the searcher. Only used in VLLMGenerator",
        )
        parser.add_argument(
            "--searcher_pipeline_parallel",
            default=False,
            action="store_true",
            help="Enable pipeline parallel for searcher. Only used in HFGenerator",
        )
        parser.add_argument(
            "--searcher_base_url",
            type=str,
            default=None,
            help="Base URL for searcher. Only used in OpenAIGenerator",
        )
        parser.add_argument(
            "--searcher_api_key",
            type=str,
            default="EMPTY",
            help="API key for searcher. Only used in OpenAIGenerator",
        )
        parser.add_argument(
            "--searcher_model_name",
            type=str,
            default=None,
            help="Model Name for searcher. Only used in OpenAIGenerator",
        )
        # generator arguments
        parser.add_argument(
            "--generator_type",
            type=str,
            choices=["vllm", "hf", "openai"],
            default=None,
            help="The generator model type.",
        )
        parser.add_argument(
            "--generator_path",
            type=str,
            default=None,
            help="The path to the kylin generator model.",
        )
        parser.add_argument(
            "--generator_max_length",
            type=int,
            default=512,
            help="The maximum length for the generator.",
        )
        parser.add_argument(
            "--generator_gpu_utilization",
            type=float,
            default=0.85,
            help="The maximum GPU memory utilization of the generator.",
        )
        parser.add_argument(
            "--generator_pipeline_parallel",
            default=False,
            action="store_true",
            help="Enable pipeline parallel for generator. Only used in HFGenerator",
        )
        parser.add_argument(
            "--generator_base_url",
            type=str,
            default=None,
            help="Base URL for generator. Only used in OpenAIGenerator.",
        )
        parser.add_argument(
            "--generator_api_key",
            type=str,
            default="EMPTY",
            help="API key for generator. Only used in OpenAIGenerator.",
        )
        parser.add_argument(
            "--generator_model_name",
            type=str,
            default=None,
            help="Model Name for generator. Only used in OpenAIGenerator.",
        )
        # retriever arguments
        parser = add_args_for_retriever(parser)
        parser.add_argument(
            "--retriever_top_k",
            type=int,
            default=10,
            help="The number of top-k results to return.",
        )
        parser.add_argument(
            "--disable_cache",
            default=False,
            action="store_true",
            help="Whether to disable the cache.",
        )
        return parser

    def __init__(self, args: Namespace) -> None:
        # setup kylin
        self.extract_info = args.extract_information
        self.rewrite = args.rewrite_query
        self.verify = args.verify_context
        self.summary_context = args.summary_context
        self.log_interval = args.log_interval
        self.retriever_top_k = args.retriever_top_k
        self.disable_cache = args.disable_cache

        # load searcher
        self.searcher = load_generator(
            model_type=args.searcher_type,
            model_path=args.searcher_path,
            gpu_memory_utilization=args.searcher_gpu_utilization,
            pipeline_parallel=args.searcher_pipeline_parallel,
            base_url=args.searcher_base_url,
            api_key=args.searcher_api_key,
            model_name=args.searcher_model_name,
        )
        self.searcher_gen_cfg = load_generation_config(
            max_gen_tokens=args.searcher_max_length, model=self.searcher
        )

        # load generator
        if args.generator_type is None:
            self.generator = self.searcher
            self.generator_gen_cfg = self.searcher_gen_cfg
        else:
            self.generator = load_generator(
                model_type=args.generator_type,
                model_path=args.generator_path,
                gpu_memory_utilization=args.generator_gpu_utilization,
                pipeline_parallel=args.generator_pipeline_parallel,
                base_url=args.generator_base_url,
                api_key=args.generator_api_key,
                model_name=args.generator_model_name,
            )
            self.generator_gen_cfg = load_generation_config(
                max_gen_tokens=args.generator_max_length, model=self.generator
            )

        # load retriever
        self.retrievers = load_retrievers(args)
        return

    def answer(self, questions: list[str]) -> tuple[list[str], list[dict[str, object]]]:
        responses = []
        search_tracks = []
        progress = SimpleProgressLogger(logger, len(questions), self.log_interval)
        for question in questions:
            progress.update()
            response, search_track = self._answer(question)
            responses.append(response)
            search_tracks.append(search_track)
        return responses, search_tracks

    def _answer(self, question: str) -> tuple[str, dict[str, object]]:
        # search contexts using kylin searcher
        contexts, search_track = self.search(question)

        # generate the answer using the generator
        result, prompt = self.generate_with_context(question, contexts)
        search_track["generation_prompt"] = prompt
        return result, search_track

    def search(self, question: str) -> tuple[list[dict[str, str]], dict[str, object]]:
        # extract needed information
        search_track = {"question": question}
        search_track["retrieval"] = []
        if self.extract_info:
            info_needed, response = self.determine_needed_information(question)
        else:
            info_needed = [question]

        # search contexts from the retrievers
        contexts = []
        for q in info_needed:
            ctx, ret_history = self.inner_search(q)
            search_track["retrieval"].append(
                {"atom_query": q, "retrieval_history": ret_history}
            )
            contexts.extend(ctx)
        search_track["retrieved_contexts"] = contexts
        return contexts, search_track

    def inner_search(
        self, query: str
    ) -> tuple[list[dict[str, str]], list[list[dict[str, object]]]]:
        """Search from multiple retrievers

        Args:
            query (str): query / information to search

        Returns:
            contexts (list[dict[str, str]]): contexts retrieved from different retrievers.
            retrieval_history (list[list[dict[str, object]]]): retrieval history.
        """
        total_turn_num = 3 if self.verify else 1
        retrieval_history: list[list[dict]] = []
        turn_num = 0
        while turn_num < total_turn_num:
            contexts = []
            retrieval_history.append([])
            for idx, retriever in enumerate(self.retrievers):
                # rewrite query
                if self.rewrite:
                    failing_history = [i[idx] for i in retrieval_history[:turn_num]]
                    query_to_search = self.rewrite_query(
                        info=query,
                        engine_name=retriever,
                        failing_history=failing_history,
                    )
                else:
                    query_to_search = query
                # retrieve
                ctxs = self.retrievers[retriever].search(
                    [query_to_search],
                    top_k=self.retriever_top_k,
                    disable_cache=self.disable_cache,
                )[0]
                # post process
                if "indices" not in ctxs:
                    assert "urls" in ctxs
                    ctxs["indices"] = ctxs["urls"]
                ctxs = [
                    {
                        "query": query_to_search,
                        "retriever": retriever,
                        "text": t,
                        "full_text": t,
                        "source": s,
                    }
                    for t, s in zip(ctxs["texts"], ctxs["indices"])
                ]
                # update history
                retrieval_history[turn_num].append(
                    {
                        "retriever": retriever,
                        "query": query_to_search,
                        "contexts": ctxs,
                    }
                )
                # summary
                if self.summary_context:
                    summarized = self.summarize_contexts(ctxs, query)
                    retrieval_history[turn_num][-1]["summarized_contexts"] = summarized
                    contexts.extend([i for i in summarized if i["text"] is not None])
                else:
                    contexts.extend(ctxs)

            # verify
            verification = self.verify_contexts(contexts, query)
            if verification:
                break
            turn_num += 1
        return contexts, retrieval_history

    def determine_needed_information(
        self, query: str, given_contexts: list[str] = None
    ) -> tuple[list[str], str]:
        prompt = deepcopy(determine_info_prompt)
        prompt.append({"role": "user", "content": f"Question: {query}"})
        response = self.searcher.chat(
            [prompt], generation_config=self.searcher_gen_cfg
        )[0]
        info_needed = re.findall(r"\[\d+\] (.+?)(?=\[\d+\]|\Z)", response)
        return info_needed, response

    def rewrite_query(
        self,
        info: str,
        engine_name: str,
        failing_history: list[dict[str, str | list]] = [],
    ) -> str:
        # Rewrite the query to be more informative
        failing_queries = [i["query"] for i in failing_history]
        user_prompt = f"Query: {info}"
        if (failing_queries is not None) and (len(failing_queries) > 0):
            user_prompt += (
                "\nHere are some queries that failed to retrieve the information, "
                "please avoid using them."
            )
            for q in failing_queries:
                user_prompt += f"\nFailing Query: {q}"
        prompt = deepcopy(rewrite_prompts[engine_name])
        prompt.append({"role": "user", "content": user_prompt})
        query_to_search = self.searcher.chat(
            [prompt], generation_config=self.searcher_gen_cfg
        )[0]
        return query_to_search

    def verify_contexts(self, contexts: list[dict[str, str]], question: str) -> bool:
        if not self.verify:
            return True
        prompt = deepcopy(verify_prompt)
        user_prompt = ""
        for n, ctx in enumerate(contexts):
            user_prompt += f"Context {n}: {ctx['text']}\n\n"
        user_prompt += f"Topic: {question}"
        prompt.append({"role": "user", "content": user_prompt})
        response = self.searcher.chat(
            [prompt], generation_config=self.searcher_gen_cfg
        )[0]
        return "yes" in response.lower()

    def summarize_contexts(
        self, contexts: list[dict[str, str]], question: str
    ) -> list[dict[str, str]]:
        def not_relevant(text: str) -> bool:
            patterns = [
                r"context does not (provide|contain)",
                r"no relevant information",
            ]
            for pattern in patterns:
                if re.match(pattern, text.lower()):
                    return True
            return False

        prompts = []
        for ctx in contexts:
            prompt = deepcopy(summary_prompt)
            prompt.append(
                {
                    "role": "user",
                    "content": f"Context: {ctx['full_text']}\n\nQuestion: {question}",
                }
            )
            prompts.append(prompt)
        responses = self.searcher.chat(prompts, generation_config=self.searcher_gen_cfg)
        # post process
        summarized = deepcopy(contexts)
        for summ, ctx in zip(responses, summarized):
            if not_relevant(summ):
                ctx["text"] = None
            else:
                ctx["text"] = summ
        return summarized

    def generate_with_context(
        self, question: str, contexts: list[str]
    ) -> tuple[str, str]:
        # Generate the answer
        prompt = deepcopy(generate_prompt)
        usr_prompt = ""
        for n, context in enumerate(contexts):
            if "summary" in context:
                ctx = context["summary"]
            else:
                ctx = context["text"]
            usr_prompt += f"Context {n + 1}: {ctx}\n\n"
        usr_prompt += f"Question: {question}"

        prompt.append({"role": "user", "content": usr_prompt})
        response = self.generator.chat(
            [prompt], generation_config=self.generator_gen_cfg
        )[0]
        return response, prompt

    def close(self) -> None:
        for retriever in self.retrievers:
            self.retrievers[retriever].close()
        return
