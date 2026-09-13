"""Versioned Agent1 input transport seam.

V26.9 owns the current contract selection; the historical V22.5.8 implementation
remains available only for historical-version rows, never as a V26.9 semantic fallback.
"""
from src.services.agent_input_transport_v2258_service import *
from src.services.agent_input_transport_v2258_service import AgentInputTransportV2258Error
from src.services.v269_fact_projection_service import (
    ensure_agent1_input_ref,
    resolve_agent_input_ref,
)

AgentInputTransportV2257Error = AgentInputTransportV2258Error
