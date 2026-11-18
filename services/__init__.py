"""Tầng dịch vụ (không phụ thuộc Qt): nối Core, Generator, Database và Storage."""
from .automation import AutomationService
from .persistence import DbStatus, PersistenceService
from .simulation import OpResult, SimulationService

__all__ = ["AutomationService", "DbStatus", "PersistenceService", "OpResult", "SimulationService"]
