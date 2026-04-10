class LLMWikiError(Exception):
    pass


class LLMClientError(LLMWikiError):
    pass


class MineruClientError(LLMWikiError):
    pass


class QdrantServiceError(LLMWikiError):
    pass


class WikiEngineError(LLMWikiError):
    pass
