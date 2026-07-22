"""Sample LlamaIndex agent fixture."""

from llama_index.core import VectorStoreIndex


async def run_agent(query: str) -> dict[str, str]:
    index = VectorStoreIndex.from_documents([])
    engine = index.as_query_engine()
    response = await engine.aquery(query)
    return {"response": str(response)}
