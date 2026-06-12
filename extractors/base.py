from abc import ABC, abstractmethod

from models import ExtractionCandidate


class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, product: str, attribute: str, source) -> list[ExtractionCandidate]:
        ...
