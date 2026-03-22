"""Supplier Router — selects best API client per part type."""
import logging
from typing import Dict, Any, Optional, List
from app.core.config import settings
from app.integrations.mouser_client import MouserClient
from app.integrations.digikey_client import DigiKeyClient
from app.integrations.misumi_client import MisumiClient
from app.integrations.abb_client import ABBClient

logger = logging.getLogger("supplier_router")

# Singleton clients
_mouser = MouserClient(api_key=settings.MOUSER_API_KEY)
_digikey = DigiKeyClient(client_id=settings.DIGIKEY_CLIENT_ID, client_secret=settings.DIGIKEY_CLIENT_SECRET)
_misumi = MisumiClient(api_key=settings.MISUMI_API_KEY)
_abb = ABBClient()

# Category → preferred supplier order
ROUTING_TABLE = {
    "resistor": [_mouser, _digikey],
    "capacitor": [_mouser, _digikey],
    "inductor": [_mouser, _digikey],
    "ic": [_digikey, _mouser],
    "microcontroller": [_digikey, _mouser],
    "connector": [_mouser, _digikey, _misumi],
    "led": [_mouser, _digikey],
    "sensor": [_digikey, _mouser],
    "diode": [_mouser, _digikey],
    "transistor": [_mouser, _digikey],
    "relay": [_mouser, _digikey, _abb],
    "switch": [_mouser, _digikey],
    "bearing": [_misumi],
    "bolt": [_misumi],
    "screw": [_misumi],
    "nut": [_misumi],
    "washer": [_misumi],
    "shaft": [_misumi],
    "motor": [_abb, _misumi],
    "drive": [_abb],
    "plc": [_abb],
}

DEFAULT_CLIENTS = [_mouser, _digikey, _misumi]


def route_query(
    query: str,
    category: str = "",
    mpn: str = "",
    quantity: int = 1,
) -> List[Dict[str, Any]]:
    """Route a part query to the best supplier APIs and aggregate results."""
    clients = ROUTING_TABLE.get(category.lower(), DEFAULT_CLIENTS)
    all_results = []

    search_term = mpn if mpn else query

    for client in clients:
        try:
            results = client.search_part(search_term, quantity)
            all_results.extend(results)
        except Exception as e:
            logger.warning(f"{client.name} failed for '{search_term}': {e}")

    return all_results
