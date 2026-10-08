"""In-memory ticket inventory.

Intentionally simple: state lives in process memory and resets on restart.
A lock protects the counter because uvicorn can serve requests concurrently.
"""

import threading

EVENT_NAME = "Adventure Park General Admission"
TICKET_PRICE = 49.99
INITIAL_INVENTORY = 5000


class SoldOutError(Exception):
    """Raised when a purchase asks for more tickets than are available."""


class Inventory:
    def __init__(self, initial: int = INITIAL_INVENTORY):
        self._initial = initial
        self._available = initial
        self._sold = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> int:
        return self._available

    @property
    def sold(self) -> int:
        return self._sold

    def purchase(self, quantity: int) -> int:
        """Reserve `quantity` tickets and return the remaining inventory."""
        with self._lock:
            if quantity > self._available:
                raise SoldOutError(
                    f"Requested {quantity} tickets but only {self._available} available"
                )
            self._available -= quantity
            self._sold += quantity
            return self._available

    def reset(self) -> None:
        """Restore starting inventory (used by tests)."""
        with self._lock:
            self._available = self._initial
            self._sold = 0


inventory = Inventory()
