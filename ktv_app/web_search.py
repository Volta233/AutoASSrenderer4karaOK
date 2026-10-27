"""Public web search for lyric sources; no model API is involved."""


def search_web(query):
    from ddgs import DDGS

    return DDGS(timeout=10).text(query, max_results=4, backend="auto")
