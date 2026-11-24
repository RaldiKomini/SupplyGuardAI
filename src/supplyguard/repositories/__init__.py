"""Query-focused repositories for operational facts."""

from supplyguard.repositories.master_data import PostgresMasterDataService
from supplyguard.repositories.operational import OperationalRepository

__all__ = ["OperationalRepository", "PostgresMasterDataService"]
